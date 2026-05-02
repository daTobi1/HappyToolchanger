# eddy-ng: Enhanced Eddy current probe support for Klipper and Kalico
# This file may be distributed under the terms of the GNU GPLv3 license.
from __future__ import annotations

try:
    from eddy_ng._version import version as __version__
except ImportError:
    __version__ = "unknown"

__all__ = ["__version__"]
