# eddy-ng scaffolding -- do not edit
# This file allows Klipper to discover the eddy-ng probe_eddy_ng package.
import os, sys

try:
    # Try pip-installed package first
    from eddy_ng.probe_eddy_ng import load_config_prefix  # noqa: F401
except (ImportError, SystemError):
    # Fallback: import directly from the eddy-ng repo directory
    for _path in [
        os.path.join(os.path.expanduser("~"), "eddy-ng"),
        os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    ]:
        if os.path.isfile(os.path.join(_path, "probe_eddy_ng", "__init__.py")):
            if _path not in sys.path:
                sys.path.insert(0, _path)
            break
    from probe_eddy_ng import load_config_prefix  # noqa: F401
