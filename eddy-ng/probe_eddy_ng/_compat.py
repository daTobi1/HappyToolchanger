# Klipper/Kalico compatibility imports
# This file may be distributed under the terms of the GNU GPLv3 license.
from __future__ import annotations

try:
    from klippy import mcu, pins, chelper
    from klippy.printer import Printer
    from klippy.configfile import ConfigWrapper
    from klippy.configfile import error as configerror
    from klippy.gcode import GCodeCommand
    from klippy.toolhead import ToolHead
    from klippy.extras import probe, manual_probe, bed_mesh
    from klippy.extras.homing import HomingMove

    IS_KALICO = True
    HAS_PROBE_RESULT_TYPE = False
except ImportError:
    import os, sys, importlib
    # Stock Klipper: modules are in klippy/ and klippy/extras/
    # We need to import extras modules as part of the 'extras' package
    # because they use relative imports (e.g. probe.py: from . import manual_probe)
    import mcu  # type: ignore[no-redef]
    import pins  # type: ignore[no-redef]
    import chelper  # type: ignore[no-redef]
    from klippy import Printer  # type: ignore[no-redef]
    from configfile import ConfigWrapper  # type: ignore[no-redef]
    from configfile import error as configerror  # type: ignore[no-redef]
    from gcode import GCodeCommand  # type: ignore[no-redef]
    from toolhead import ToolHead  # type: ignore[no-redef]

    # Import extras modules via their package path so relative imports work.
    # Klipper's probe.py uses 'from . import manual_probe' which requires
    # it to be loaded as 'extras.probe', not as a top-level 'probe' module.
    probe = importlib.import_module("extras.probe")
    manual_probe = importlib.import_module("extras.manual_probe")
    bed_mesh = importlib.import_module("extras.bed_mesh")
    HomingMove = importlib.import_module("extras.homing").HomingMove

    IS_KALICO = False
    HAS_PROBE_RESULT_TYPE = hasattr(manual_probe, "ProbeResult")

# Import the sensor driver from the repo (on sys.path via scaffolding)
ldc1612_ng = importlib.import_module("ldc1612_ng")

try:
    import plotly  # noqa
except ImportError:
    plotly = None  # type: ignore[assignment]

try:
    import scipy  # noqa
except ImportError:
    scipy = None  # type: ignore[assignment]
