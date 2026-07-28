TESTS = [
    {"name": "FW Latest",       "path": "/api/firmware/latest",    "keys": ["status", "latest_version", "download_url"], "timeout": 15},
    {"name": "FW Progress",     "path": "/api/firmware/progress",  "keys": ["stage", "percent"]},
    {"name": "FW Files",        "path": "/api/firmware/files",     "keys": ["files"]},
]
