# Prevent pytest from collecting Klipper plugin modules (they require klippy)
collect_ignore_glob = [
    "probe_eddy_ng.py",
    "probe_eddy_ng/*",
    "ldc1612_ng.py",
    "install.py",
    "__init__.py",
]
