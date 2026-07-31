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
    make_pipe,
    tcp_connect,
    recv_line,
    free_port,
)
