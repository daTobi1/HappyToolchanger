# eddy-ng scaffolding -- do not edit
# This file allows Klipper to import the ldc1612_ng sensor driver.
import os, sys

try:
    # Try pip-installed package first
    from eddy_ng.ldc1612_ng import *  # noqa: F401,F403
except (ImportError, SystemError):
    # Fallback: import directly from the eddy-ng repo directory
    for _path in [
        os.path.join(os.path.expanduser("~"), "eddy-ng"),
        os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    ]:
        if os.path.isfile(os.path.join(_path, "ldc1612_ng.py")):
            if _path not in sys.path:
                sys.path.insert(0, _path)
            break
    from ldc1612_ng import *  # noqa: F401,F403
