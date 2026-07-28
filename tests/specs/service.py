TESTS = [
    {"name": "Filters",         "path": "/api/filters",            "keys": ["filters"],      "timeout": 60},
    {"name": "Settings",        "path": "/api/settings",           "keys": ["settings_raw"], "timeout": 45},
    {"name": "Diag ALL",        "path": "/api/diag/ALL",           "keys": ["data"],         "timeout": 45},
    {"name": "Service CMD",     "method": "POST", "path": "/api/service/cmd", "body": {"cmd": "HELP"}, "keys": ["response"], "timeout": 30},
    {"name": "Service Enter",   "method": "POST", "path": "/api/service/enter", "keys": ["status", "state"], "timeout": 30},
    {"name": "Service State",   "path": "/api/service/state",      "keys": ["state"]},
    {"name": "Service Exit",    "method": "POST", "path": "/api/service/exit",  "keys": ["status", "state"], "timeout": 15},
]
