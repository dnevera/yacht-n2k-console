"""In-memory ring buffer for CAN error events.

Stores last N error frames detected in live traffic (e.g. PGN 126993
Heartbeat with State:Error). Exposed via REST API /api/errors.
"""

import time
import re
import threading
from typing import Dict, Any, List, Optional, Callable
from ydnu02 import N2KPGNDecoder


class ErrorLogger:
    """Thread-safe CAN error event logger with ring buffer storage.

    Ring buffer size: 500 events (default).
    Each event: {id, timestamp, time_str, src, device_name, pgn, pgn_name, raw, decoded, error_fields}.

    Locking:
        Receives external lock reference (owned by DeviceManager facade).
    """

    def __init__(self, lock: Optional[threading.Lock] = None, max_size: int = 500):
        self._lock = lock or threading.Lock()
        self._max_size = max_size
        self._error_log: List[Dict[str, Any]] = []
        self._next_error_id: int = 1

    def record(self, parsed: Dict[str, Any], get_dev_name: Optional[Callable[[int], str]] = None) -> None:
        """Record a CAN error event into the in-memory ring buffer."""
        info = parsed.get("info", {})
        src = info.get("src", 0)
        pgn = info.get("pgn", 0)
        decoded = parsed.get("decoded", "") or ""

        dev_name = get_dev_name(src) if get_dev_name else f"Device SA:{src}"

        # Extract specific error key-value pairs
        error_fields = []
        for match in re.finditer(r'(?:^|\s+)([\w\s-]+?):([^\s:]+(?:\s+[^\s:]+)*(?=\s+[\w\s-]+?:|$))', decoded):
            k, v = match.group(1).strip(), match.group(2).strip()
            if re.search(r'error|fault|fail|bus off', v, re.IGNORECASE):
                error_fields.append({"key": k, "val": v})

        with self._lock:
            entry = {
                "id": self._next_error_id,
                "timestamp": time.time(),
                "time_str": parsed.get("time", ""),
                "src": src,
                "device_name": dev_name,
                "pgn": pgn,
                "pgn_name": N2KPGNDecoder.pgn_name(pgn),
                "raw": parsed.get("raw", ""),
                "decoded": decoded,
                "error_fields": error_fields,
            }
            self._next_error_id += 1
            self._error_log.append(entry)
            if len(self._error_log) > self._max_size:
                self._error_log.pop(0)

    def get_log(self, limit: int = 100, src: Optional[int] = None) -> Dict[str, Any]:
        """
        Return recorded CAN error events (most recent first).

        Args:
            limit: Max number of records to return.
            src: Optional source address to filter by.
        """
        with self._lock:
            logs = list(self._error_log)
        if src is not None:
            logs = [e for e in logs if e["src"] == src]
        logs = list(reversed(logs[-limit:]))
        return {"count": len(self._error_log), "errors": logs}

    def clear(self) -> Dict[str, Any]:
        """Clear the error history buffer."""
        with self._lock:
            self._error_log.clear()
        return {"status": "cleared", "count": 0}
