# EDDY-ng probe package
# This file may be distributed under the terms of the GNU GPLv3 license.
from __future__ import annotations

from ._compat import ConfigWrapper
from .probe import ProbeEddy
from .params import ProbeEddyParams, ProbeEddyProbeResult
from .frequency_map import ProbeEddyFrequencyMap
from .sampler import ProbeEddySampler
from .endstop import ProbeEddyEndstopWrapper
from .scanning import ProbeEddyScanningProbe
from .bed_mesh_helper import BedMeshScanHelper
from .alpha_beta_filter import AlphaBetaFilter
from .temperature_compensation import TemperatureCompensationModel, TempCompCoefficients
from .backlash import estimate_backlash, BacklashResult
from .mesh_paths import generate_mesh_path
from .streaming import DataStreamer

__all__ = [
    "ProbeEddy",
    "ProbeEddyParams",
    "ProbeEddyProbeResult",
    "ProbeEddyFrequencyMap",
    "ProbeEddySampler",
    "ProbeEddyEndstopWrapper",
    "ProbeEddyScanningProbe",
    "BedMeshScanHelper",
    "AlphaBetaFilter",
    "TemperatureCompensationModel",
    "TempCompCoefficients",
    "BacklashResult",
    "estimate_backlash",
    "generate_mesh_path",
    "DataStreamer",
    "load_config_prefix",
]


def load_config_prefix(config: ConfigWrapper):
    return ProbeEddy(config)
