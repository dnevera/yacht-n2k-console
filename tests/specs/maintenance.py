TESTS = [
    {"name": "List Backups",    "path": "/api/backups",            "keys": ["backups"]},
    {"name": "Create Backup",   "method": "POST", "path": "/api/backup", "keys": ["status", "filepath"], "timeout": 60},
    {"name": "Verify Backups",  "path": "/api/backups",            "keys": ["backups"]},
    {"name": "Reset HW (deny)", "method": "POST", "path": "/api/reset/hardware", "body": {"confirm": "WRONG"}, "status": 400},
]
