"""Routes package — lazy access to device_mgr to avoid circular imports."""


def get_device_mgr():
    """Get the single device manager instance (prevents duplicate module instances)."""
    import sys
    if "__main__" in sys.modules and hasattr(sys.modules["__main__"], "device_mgr"):
        return sys.modules["__main__"].device_mgr
    import app
    return app.device_mgr

def get_mopeka_scanner():
    """Get the single Mopeka scanner instance."""
    import sys
    if "__main__" in sys.modules and hasattr(sys.modules["__main__"], "mopeka_scanner"):
        return sys.modules["__main__"].mopeka_scanner
    import app
    return getattr(app, "mopeka_scanner", getattr(app.app.state, "mopeka_scanner", None))

def get_ble_registry():
    """Get the single BLE registry instance."""
    import sys
    if "__main__" in sys.modules and hasattr(sys.modules["__main__"], "ble_registry"):
        return sys.modules["__main__"].ble_registry
    import app
    return getattr(app, "ble_registry", getattr(app.app.state, "ble_registry", None))

def get_gobius_poller():
    """Get the single Gobius BLE poller instance."""
    import sys
    if "__main__" in sys.modules and hasattr(sys.modules["__main__"], "gobius_poller"):
        return sys.modules["__main__"].gobius_poller
    import app
    return getattr(app, "gobius_poller", getattr(app.app.state, "gobius_poller", None))

