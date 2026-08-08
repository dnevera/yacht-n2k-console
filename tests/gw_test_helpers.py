import os

# Auto-load local .env file for integration tests if present
_env_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), '.env')
if os.path.exists(_env_path):
    with open(_env_path, 'r', encoding='utf-8') as _f:
        for _line in _f:
            _line = _line.strip()
            if _line and not _line.startswith('#') and '=' in _line:
                _k, _v = _line.split('=', 1)
                os.environ.setdefault(_k.strip(), _v.strip())

"""
gw_test_helpers.py — shared utilities for ydnu02_tcp_gateway test suite.

All test modules import from here. pytest fixtures re-exported in conftest.py.
No production code lives here.

Mini-prompt: если тест не может найти хелпер — добавь его сюда и перечитай conftest.py.
Architecture: Active Onboarding (ISO Request PGN 59904), no passive cache.
"""
import importlib
import importlib.util
import os
import socket
import sys
import threading
import types
import unittest

# ── Path setup ────────────────────────────────────────────────────────────────

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

GATEWAY_PATH = os.path.join(_ROOT, "ydnu02_tcp_gateway", "ydnu02_tcp_gateway.py")
DEVICE_PATH  = os.path.join(_ROOT, "ydnu02_tcp_gateway", "ydnu02_gateway_device.py")

# ── Common test fixtures ──────────────────────────────────────────────────────

# Valid CAN_FRAME_ASCII NMEA line used across many tests
VALID_LINE = b"01:43:22.648 R 19F2115C 00 30 5C 64 00 00 00 FF\n"

# ISO Claim frame — PGN 60928 (0xEE00), SA=0x5C=92
ISO_CLAIM_LINE = b"00:00:00.000 R 18EEFF5C 39 30 A0 5C 74 21 A7 2C\n"


# ── Module loaders ────────────────────────────────────────────────────────────

def load_gateway(serial_port: str = "/dev/null") -> types.ModuleType:
    """Load ydnu02_tcp_gateway with patched constants. Does NOT start threads.

    Each call returns a fresh module instance, fully isolated from other tests.
    Mini-prompt: never import the module directly — use this to avoid global state bleed.
    """
    spec = importlib.util.spec_from_file_location("ydnu02_tcp_gateway", GATEWAY_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mod.SERIAL_PORT = serial_port
    mod.clients = set()
    mod.clients_lock = threading.Lock()
    mod.serial_lock = threading.Lock()
    mod.serial_instance = None
    return mod


def load_device() -> types.ModuleType:
    """Load ydnu02_gateway_device module in isolation.

    Mini-prompt: returns a fresh module each call — no singleton state.
    """
    spec = importlib.util.spec_from_file_location("ydnu02_gateway_device", DEVICE_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ── Network helpers ───────────────────────────────────────────────────────────

def can_bind_socket() -> bool:
    """Return True if real TCP sockets are available (False in macOS sandbox)."""
    try:
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
        return True
    except (PermissionError, OSError):
        return False


def free_port() -> int:
    """Return an unused local TCP port number."""
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def tcp_connect(port: int, timeout: float = 2.0) -> socket.socket:
    """Open a TCP client socket connected to 127.0.0.1:port."""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(timeout)
    s.connect(("127.0.0.1", port))
    return s


def recv_line(sock: socket.socket, timeout: float = 3.0) -> bytes:
    """Read bytes from sock until a newline is received."""
    sock.settimeout(timeout)
    buf = b""
    while b"\n" not in buf:
        chunk = sock.recv(256)
        if not chunk:
            raise ConnectionError("Socket closed before newline")
        buf += chunk
    return buf.split(b"\n")[0] + b"\n"


def make_pipe():
    """Return a (server_conn, client_sock) connected socket pair on a random port."""
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.bind(("127.0.0.1", 0))
    server.listen(1)
    port = server.getsockname()[1]
    client = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    client.connect(("127.0.0.1", port))
    conn, _ = server.accept()
    server.close()
    return conn, client


# ── Pi5 live-hardware reachability (priority check) ──────────────────────────

def _resolve_pi5_host() -> str:
    """Resolve the configured Pi5 host from env vars (no hardcoded IPs in git)."""
    return os.getenv("GW_HOST") or os.getenv("HA_HOST") or os.getenv("SSH_HOST") or ""


#: Cache so the (possibly slow, timeout-bound) network probe below only ever
#: runs ONCE per test session/process — every subsequent call (e.g. from
#: NEEDS_PI5 decorators on many test methods) reuses the cached verdict instead
#: of re-attempting a connection per test.
_pi5_reachability_cache = {}


def is_pi5_reachable(port: int = 8080, timeout: float = 1.5) -> bool:
    """Priority check: is the Pi5 (gateway/HA host) actually present on the network?

    Cached per (host, port) so it only probes once, even though many live test
    methods depend on this. If the host is unreachable, callers should skip all
    tests that need it and only run local (non-hardware) tests.
    """
    host = _resolve_pi5_host()
    if not host:
        _pi5_reachability_cache.setdefault(("", port), False)
        return False
    key = (host, port)
    if key in _pi5_reachability_cache:
        return _pi5_reachability_cache[key]
    try:
        with socket.create_connection((host, port), timeout=timeout):
            result = True
    except OSError:
        result = False
    _pi5_reachability_cache[key] = result
    return result


def pi5_status_message(port: int = 8080) -> str:
    """Human-readable banner describing current Pi5 reachability, for terminal output."""
    host = _resolve_pi5_host()
    if not host:
        return (
            "[pi5-check] No Pi5 host configured (set GW_HOST/HA_HOST/SSH_HOST in .env) — "
            "skipping live hardware tests, running local tests only."
        )
    if is_pi5_reachable(port=port):
        return f"[pi5-check] Pi5 ({host}) is reachable on the network — live hardware tests enabled."
    return (
        f"[pi5-check] Pi5 ({host}) NOT found on the network — "
        "skipping live hardware tests, running local tests only."
    )


# ── Skip decorators ───────────────────────────────────────────────────────────

#: Skip test if real TCP sockets are not available (macOS sandbox).
NEEDS_NETWORK = unittest.skipUnless(
    can_bind_socket(),
    "No network in sandbox — run with BypassSandbox or on Pi",
)

#: Skip test if the Pi5 (gateway/HA host) is not reachable on the network.
#: Evaluated once (cached) so the whole suite prints/skips fast instead of
#: letting every live test hit its own multi-second connection timeout.
NEEDS_PI5 = unittest.skipUnless(
    is_pi5_reachable(),
    "Pi5 not reachable on the network (set GW_HOST/HA_HOST/SSH_HOST) — "
    "skipping live hardware tests, running local tests only.",
)
