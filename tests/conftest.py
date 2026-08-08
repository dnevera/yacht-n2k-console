"""
conftest.py — pytest session fixtures for ydnu02_tcp_gateway test suite.

All shared helpers live in gw_test_helpers.py — imported and re-exported here
so pytest picks them up automatically without explicit imports in test files.

Mini-prompt: add new session-scoped fixtures here; add new helpers to gw_test_helpers.py.
"""
import pytest
from tests.gw_test_helpers import (  # noqa: F401 — re-export for pytest
    load_gateway,
    load_device,
    VALID_LINE,
    ISO_CLAIM_LINE,
    NEEDS_NETWORK,
    NEEDS_PI5,
    is_pi5_reachable,
    pi5_status_message,
    make_pipe,
    tcp_connect,
    recv_line,
    free_port,
)


def pytest_sessionstart(session):
    """Priority check, run once before any test executes: probe whether the Pi5
    (gateway/HA host) is present on the network and print a clear banner about
    it, so it's immediately obvious in the terminal whether live hardware tests
    will run or be skipped (falling back to local-only tests).

    Uses the terminal reporter directly so the message is always visible,
    regardless of pytest's stdout capture settings (i.e. even without -s).
    """
    reporter = session.config.pluginmanager.get_plugin("terminalreporter")
    message = pi5_status_message()
    if reporter is not None:
        reporter.write_line(message)
    else:
        print(f"\n{message}\n")
