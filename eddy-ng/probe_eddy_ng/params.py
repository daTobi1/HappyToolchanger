# EDDY-ng parameter definitions
# This file may be distributed under the terms of the GNU GPLv3 license.
from __future__ import annotations

import re
import numpy as np

from dataclasses import dataclass, field
from typing import List, ClassVar

from ._compat import configerror, ConfigWrapper, scipy


@dataclass
class ProbeEddyParams:
    probe_speed: float = 5.0
    lift_speed: float = 10.0
    move_speed: float = 50.0
    home_trigger_height: float = 2.0
    home_trigger_safe_start_offset: float = 1.0
    home_trigger_safe_time_offset: float = 0.100
    calibration_z_max: float = 15.0
    reg_drive_current: int = 0
    tap_drive_current: int = 0
    tap_start_z: float = 3.0
    tap_target_z: float = -0.250
    tap_mode: str = "butter"
    tap_threshold: float = 250.0
    tap_speed: float = 3.0
    tap_adjust_z: float = 0.0
    tap_samples: int = 3
    tap_max_samples: int = 5
    tap_samples_stddev: float = 0.020
    tap_use_median: bool = False
    tap_time_position: float = 0.3
    scan_sample_time: float = 0.100
    scan_sample_time_delay: float = 0.050
    calibration_points: int = 150
    tap_butter_lowcut: float = 5.0
    tap_butter_highcut: float = 25.0
    tap_butter_order: int = 2
    x_offset: float = 0.0
    y_offset: float = 0.0
    allow_unsafe: bool = False
    write_tap_plot: bool = False
    write_every_tap_plot: bool = False
    max_errors: int = 0
    debug: bool = True
    tap_trigger_safe_start_height: float = 1.5
    # Mesh path settings
    mesh_path: str = "snake"
    mesh_direction: str = "x"
    mesh_runs: int = 1
    mesh_height: float = 3.0
    # Backlash compensation
    z_backlash: float = 0.0
    # Alpha-beta filter
    filter_alpha: float = 0.5
    filter_beta: float = 1e-6
    # Temperature compensation (loaded from saved config)
    temperature_compensation: str = ""
    _warning_msgs: List[str] = field(default_factory=list)

    @staticmethod
    def str_to_floatlist(s):
        if s is None:
            return None
        try:
            return [float(v) for v in re.split(r"\s*,\s*|\s+", s)]
        except:
            raise configerror(f"Can't parse '{s}' as list of floats")

    def is_default_butter_config(self):
        return self.tap_butter_lowcut == 5.0 and self.tap_butter_highcut == 25.0 and self.tap_butter_order == 2

    def load_from_config(self, config: ConfigWrapper):
        mode_choices = ["wma", "butter"]

        self.probe_speed = config.getfloat("probe_speed", self.probe_speed, above=0.0)
        self.lift_speed = config.getfloat("lift_speed", self.lift_speed, above=0.0)
        self.move_speed = config.getfloat("move_speed", self.move_speed, above=0.0)
        self.home_trigger_height = config.getfloat("home_trigger_height", self.home_trigger_height, minval=1.0)
        self.home_trigger_safe_start_offset = config.getfloat(
            "home_trigger_safe_start_offset",
            self.home_trigger_safe_start_offset,
            minval=0.5,
        )
        self.calibration_z_max = config.getfloat("calibration_z_max", self.calibration_z_max, above=0.0)

        self.reg_drive_current = config.getint("reg_drive_current", 0, minval=0, maxval=31)
        self.tap_drive_current = config.getint("tap_drive_current", 0, minval=0, maxval=31)

        self.tap_start_z = config.getfloat("tap_start_z", self.tap_start_z, above=0.0)
        self.tap_target_z = config.getfloat("tap_target_z", self.tap_target_z)
        self.tap_speed = config.getfloat("tap_speed", self.tap_speed, above=0.0)
        self.tap_adjust_z = config.getfloat("tap_adjust_z", self.tap_adjust_z)
        self.calibration_points = config.getint("calibration_points", self.calibration_points)

        self.tap_mode = config.getchoice("tap_mode", mode_choices, self.tap_mode)
        default_tap_threshold = 1000.0  # for wma
        if self.tap_mode == "butter":
            default_tap_threshold = 250.0
        self.tap_threshold = config.getfloat("tap_threshold", default_tap_threshold)

        self.scan_sample_time = config.getfloat("scan_sample_time", self.scan_sample_time, above=0.0)
        self.scan_sample_time_delay = config.getfloat("scan_sample_time_delay", self.scan_sample_time_delay, minval=0.0)

        self.tap_butter_lowcut = config.getfloat("tap_butter_lowcut", self.tap_butter_lowcut, above=0.0)
        self.tap_butter_highcut = config.getfloat(
            "tap_butter_highcut",
            self.tap_butter_highcut,
            above=self.tap_butter_lowcut,
        )
        self.tap_butter_order = config.getint("tap_butter_order", self.tap_butter_order, minval=1)

        self.tap_samples = config.getint("tap_samples", self.tap_samples, minval=1)
        self.tap_max_samples = config.getint("tap_max_samples", self.tap_max_samples, minval=self.tap_samples)
        self.tap_samples_stddev = config.getfloat("tap_samples_stddev", self.tap_samples_stddev, above=0.0)
        self.tap_use_median = config.getboolean("tap_use_median", self.tap_use_median)
        self.tap_trigger_safe_start_height = config.getfloat(
            "tap_trigger_safe_start_height",
            -1.0,
            above=0.0,
        )
        self.tap_time_position = config.getfloat("tap_time_position", self.tap_time_position, minval=0.0, maxval=1.0)

        if self.tap_trigger_safe_start_height == -1.0:  # sentinel
            self.tap_trigger_safe_start_height = self.home_trigger_height / 2.0

        self.allow_unsafe = config.getboolean("allow_unsafe", self.allow_unsafe)
        self.write_tap_plot = config.getboolean("write_tap_plot", self.write_tap_plot)
        self.write_every_tap_plot = config.getboolean("write_every_tap_plot", self.write_every_tap_plot)
        self.debug = config.getboolean("debug", self.debug)

        self.max_errors = config.getint("max_errors", self.max_errors)

        self.x_offset = config.getfloat("x_offset", self.x_offset)
        self.y_offset = config.getfloat("y_offset", self.y_offset)

        # Mesh path settings
        mesh_path_choices = ["snake", "alternating_snake", "spiral", "random"]
        self.mesh_path = config.getchoice("mesh_path", mesh_path_choices, self.mesh_path)
        mesh_dir_choices = ["x", "y"]
        self.mesh_direction = config.getchoice("mesh_direction", mesh_dir_choices, self.mesh_direction)
        self.mesh_runs = config.getint("mesh_runs", self.mesh_runs, minval=1)
        self.mesh_height = config.getfloat("mesh_height", self.mesh_height, above=0.0)

        # Backlash
        self.z_backlash = config.getfloat("z_backlash", self.z_backlash, minval=0.0)

        # Alpha-beta filter
        self.filter_alpha = config.getfloat("filter_alpha", self.filter_alpha, minval=0.0, maxval=1.0)
        self.filter_beta = config.getfloat("filter_beta", self.filter_beta, minval=0.0, maxval=1.0)

        # Temperature compensation
        self.temperature_compensation = config.get("temperature_compensation", "")

        self.validate(config)

    def validate(self, config: ConfigWrapper = None):
        printer = config.get_printer()
        req_cal_z_max = self.home_trigger_safe_start_offset + self.home_trigger_height + 1.0
        if self.calibration_z_max < req_cal_z_max:
            raise printer.config_error(
                f"calibration_z_max must be at least home_trigger_safe_start_offset+home_trigger_height+1.0 ({self.home_trigger_safe_start_offset:.3f}+{self.home_trigger_height:.3f}+1.0={req_cal_z_max:.3f})"
            )
        if self.x_offset == 0.0 and self.y_offset == 0.0 and not self.allow_unsafe:
            raise printer.config_error("ProbeEddy: x_offset and y_offset are both 0.0; is the sensor really mounted at the nozzle?")

        if self.home_trigger_height <= self.tap_trigger_safe_start_height:
            raise printer.config_error("ProbeEddy: home_trigger_height must be greater than tap_trigger_safe_start_height")

        need_scipy = False
        if self.tap_mode == "butter" and not self.is_default_butter_config():
            need_scipy = True

        if need_scipy and not scipy:
            raise printer.config_error(
                "ProbeEddy: butter mode with custom filter parameters requires scipy, which is not available; please install scipy, use the defaults, or use wma mode"
            )


@dataclass
class ProbeEddyProbeResult:
    samples: List[float]
    mean: float = 0.0
    median: float = 0.0
    min_value: float = 0.0
    max_value: float = 0.0
    tstart: float = 0.0
    tend: float = 0.0
    errors: int = 0

    USE_MEAN_FOR_VALUE: ClassVar[bool] = False

    @property
    def valid(self):
        return len(self.samples) > 0

    @property
    def value(self):
        return self.mean if self.USE_MEAN_FOR_VALUE else self.median

    @property
    def stddev(self):
        stddev_sum = np.sum([(s - self.value) ** 2.0 for s in self.samples])
        return float((stddev_sum / len(self.samples)) ** 0.5)

    @classmethod
    def make(cls, times: List[float], heights: List[float], errors: int = 0) -> ProbeEddyProbeResult:
        h = np.array(heights)
        return ProbeEddyProbeResult(
            samples=h.tolist(),
            mean=float(np.mean(h)),
            median=float(np.median(h)),
            min_value=float(np.min(h)),
            max_value=float(np.max(h)),
            tstart=float(times[0]),
            tend=float(times[-1]),
            errors=errors
        )

    def __format__(self, spec):
        if spec == "v":
            return f"{self.value:.3f}"
        if self.USE_MEAN_FOR_VALUE:
            value = f"{self.mean:.3f}"
            extra = f"med={self.median:.3f}"
        else:
            value = f"{self.median:.3f}"
            extra = f"avg={self.mean:.3f}"

        return f"{value} ({extra}, {self.min_value:.3f} to {self.max_value:.3f}, [{self.stddev:.3f}])"
