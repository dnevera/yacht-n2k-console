TESTS = [
    {"name": "Info (cached)",   "path": "/api/info",             "keys": ["firmware_version", "serial_number", "state", "port"]},
    {"name": "Info (force)",    "path": "/api/info?force=true",  "keys": ["firmware_version", "state"]},
    {"name": "Mode AUTO",       "method": "POST", "path": "/api/mode/auto",  "keys": ["status", "message"]},
    {"name": "Mode RAW",        "method": "POST", "path": "/api/mode/raw",   "keys": ["status", "message"]},
    {"name": "Mode INVALID",    "method": "POST", "path": "/api/mode/xxx",   "status": 400},
    {"name": "Silent ON",       "method": "POST", "path": "/api/silent/on",  "keys": ["status", "message"]},
    {"name": "Silent OFF",      "method": "POST", "path": "/api/silent/off", "keys": ["status", "message"]},
]
