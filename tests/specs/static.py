TESTS = [
    {"name": "HTML",            "path": "/"},
    {"name": "CSS",             "path": "/static/css/style.css"},
    {"name": "JS core",         "path": "/static/js/core.js"},
    {"name": "JS dashboard",    "path": "/static/js/dashboard.js"},
    {"name": "JS monitor",      "path": "/static/js/monitor.js"},
    {"name": "JS scan",         "path": "/static/js/scan.js"},
    {"name": "JS service",      "path": "/static/js/service.js"},
    {"name": "JS maintenance",  "path": "/static/js/maintenance.js"},
    # Cleanup: restore AUTO mode
    {"name": "Restore AUTO",    "method": "POST", "path": "/api/mode/auto", "keys": ["status"]},
]
