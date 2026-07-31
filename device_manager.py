"""Backward compatibility wrapper for device_manager module."""

from device_manager.manager import DeviceManager
from device_manager.tcp_connection import (
    TCPProxyConnection,
    ProxyControlClient,
    _PROXY_HOST,
    _PROXY_DATA_PORT,
    _PROXY_CTRL_PORT,
)

__all__ = [
    "DeviceManager",
    "TCPProxyConnection",
    "ProxyControlClient",
    "_PROXY_HOST",
    "_PROXY_DATA_PORT",
    "_PROXY_CTRL_PORT",
]
