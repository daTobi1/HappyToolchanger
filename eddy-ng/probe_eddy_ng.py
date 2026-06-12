# EDDY-ng
#
# Copyright (C) 2025  Vladimir Vukicevic <vladimir@pobox.com>
#
# Based on original probe_eddy_current code by:
# Copyright (C) 2020-2024  Kevin O'Connor <kevin@koconnor.net>
#
# This file may be distributed under the terms of the GNU GPLv3 license.
from __future__ import annotations

import os
import logging
import math
import bisect
import re
import traceback
import json
import pickle
import base64
import time
import numpy as np
import numpy.polynomial as npp
from itertools import combinations
from functools import cmp_to_key

from dataclasses import dataclass, field
from typing import (
    Any,
    Dict,
    List,
    Optional,
    Tuple,
    ClassVar,
    final,
)

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
    import mcu
    import pins
    import chelper
    from klippy import Printer
    from configfile import ConfigWrapper
    from configfile import error as configerror
    from gcode import GCodeCommand
    from toolhead import ToolHead
    from . import probe, manual_probe, bed_mesh
    from .homing import HomingMove

    IS_KALICO = False
    HAS_PROBE_RESULT_TYPE = hasattr(manual_probe, "ProbeResult")

from . import ldc1612_ng

try:
    import plotly  # noqa
except ImportError:
    plotly = None

try:
    import scipy  # noqa
except ImportError:
    scipy = None

# In this file, a couple of conventions are used (for sanity).
# Variables are named according to:
# - "height" is always a physical height as detected by the probe in mm
# - "z" is always a z axis position (which may or may not match height)
# - "freq" is always a frequency value (float)
# - "freqval" is always an encoded frequency value, as communicated to/from the sensor (int)

# There are three distinct operations/phases. Homing Z via the virtual
# endstop is the only operation that can happen while Z is not homed:
#
# 1. Homing Z using a virtual probe endstop. This is largely handled by
#    ProbeEddyEndstopWrapper. It sets up the sensor to trigger when a certain
#    frequency is crossed, and then lets a HomingMove continue that moves the
#    toolhead down. When that frequency is hit, it triggers, and Klipper stops
#    the toolhead from moving down. The time point when it triggers is set as
#    the z=trigger_height (which is home_trigger_height in the configurable
#    params). Z should be accurate enough at this point. This operation can be run
#    when the bed/toolhead are cold or hot.
#
# Once Z is homed, two additional operations become available:
#
# 2. Probing at either a single point or multiple points. This is used for
#    Quad Gantry Leveling, Bed Mesh, and other similar operations. This is
#    largely handled by the ProbeEddyScannigProbe class -- one is returned
#    from the ProbeEddy `probe` object when `start_probe_session` is called.
#    For Eddy probes, there is no reason to move the toolhead up and down at
#    each probe point: the measured distance between the sensor and the build
#    plate can be read directly. This class starts gathering sample data when
#    the session starts and records the times when there's a sample that we
#    care about, along with the toolhead position, whenever a caller calls
#    `run_probe`. If this is a `rapid_scan` scan, then a callback is attached
#    to the current motion so that we can save the movement's time and position
#    without actually waiting for it. If it's in normal mode, then the toolhead
#    will pause at each position. In both cases, the results are obtained by
#    calling `pull_probed_results`, which returns an array of results at each
#    point that `run_probe` was called for, in order.
#
#    PROBE_STATIC HOME_Z=1 can be used to set the toolhead's Z position
#    based on the current height reading from the probe while the toolhead is
#    static, leading to a more accurate result than a regular homing operation
#    (which involves movement).
#
# 3. A "tap" to fine-tune the Z offset. This should be run with the bed at print
#    temperature and soaked for a bit. The nozzle should also be warm but not so
#    hot that filament risks oozing out. The nozzle also must be clean. 150C
#    is a good temperature to both clean and tap at.
#
#    This operation will identify the exact position of the Z axis
#    when the nozzle touches the bed, which means that a precise Z offset
#    can be set.
#
# The eddy current response and readings depend on temperature of both the target
# (bed) and the sensor (coil). EddyNG does not do any temperature compensation. Instead
# it relies on the "tap" operation to get an accurate reference point for z=0 regardless
# of temperatures. Empirically, small offsets from a reference point can still be read
# accurately from the sensor, even if the absolute value is incorrect at temperature.
# For example, taking sensor readings at Z=2 when perfectly homed via tap may read as
# 1.9 due to temperatures, which is not correct. However, raising the toolhead to Z=2.1
# will raise the sensor reading to 2.0; likewise, lowering the toolhead to Z=1.9 will
# lower the sensor reading to 1.8.
#
# Care in macros should be taken to not invalidate the Z offset set after a tap
# by relying on absolute sensor readings.
#


@dataclass
class ProbeEddyParams:
    # The speed at which to perform normal homing operations
    probe_speed: float = 5.0
    # The speed at which to lift the toolhead during probing operations
    lift_speed: float = 10.0
    # The speed at which to move in the xy plane (typically only for calibration)
    move_speed: float = 50.0
    # The height at which the virtual endstop should trigger. A value
    # between 1.0 and 3.0 is recommended, with 2.0 or 2.5 being good
    # choices.
    home_trigger_height: float = 2.0
    # The amount higher the probe needs to detect the toolhead is at  in order to
    # allow homing to begin. For example, if the trigger height is 2.0, and the
    # start offset is 1.5, then homing will abort if the sensor detects the
    # toolhead is below 3.5mm off the print bed.
    home_trigger_safe_start_offset: float = 1.0
    # The amount of time that must elapse from the start of probing until the
    # safe start position is crossed. This is to make sure there are some values
    # that are above the safe position before it's crossed, to ensure that homing
    # doesn't begin with the toolhead too low.
    home_trigger_safe_time_offset: float = 0.100
    # The maximum z value to calibrate from. 15.0 is fine as a default, calibrating
    # at higher values is not needed. Calibration will start with the first
    # valid height.
    calibration_z_max: float = 15.0
    # The "drive current" for the LDC1612 sensor. This value is typically
    # sensor specific and depends on the coil design and the operating distance.
    # A good starting value for BTT Eddy is 15. A good value can be obtained
    # by placing the toolhead ~10mm above the bed and running LDC_NG_CALIBRATE_
    # DRIVE_CURRENT.
    reg_drive_current: int = 0
    # The drive current to use for tap operations. If not set, the `reg_drive_current`
    # value will be used. Tapping involves reading values much closer to the print
    # bed than basic homing, and may require a different, typically higher,
    # drive current. For example, BTT Eddy performs best with this value at 16.
    # Note that the sensor needs to be calibrated for both drive currents separately.
    # Pass the DRIVE_CURRENT argument to EDDY_NG_CALIBRATE.
    tap_drive_current: int = 0
    # The Z position at which to start a tap-home operation. This height may
    # need to be fine-tuned to ensure that the sensor can provide readings across the
    # entire tap range (i.e. from this value down to tap_target_z), which in turn
    # will depend on the tap_drive_current. When the tap_drive_current is
    # increased, the sensor may not be able to read values at higher heights.
    # For example, BTT Eddy typically cannot work with heights above 3.5mm with
    # a drive current of 16.
    #
    # Note that all of these values are in terms of offsets from the nozzle
    # to the toolhead. The actual sensor coil is mounted higher -- but must be placed
    # between 2.5 and 3mm above the nozzle, ideally around 2.75mm. If there are
    # amplitude errors, try raising or lowering the sensor coil slightly.
    tap_start_z: float = 3.0
    # The target Z position for a tap operation. This is the lowest position that
    # the toolhead may travel to in case of a failed tap. Do not set this very low,
    # as it will cause your toolhead to try to push through your build plate in
    # the case of a failed tap. A value like -0.250 is no worse than moving the
    # nozzle down one or two notches too far when doing manual Z adjustment.
    tap_target_z: float = -0.250
    # the tap mode to use. 'wma' is a derivative of weighted moving average,
    # 'butter' is a butterworth filter
    tap_mode: str = "butter"
    # The threshold at which to detect a tap. This value is raw sensor value
    # specific. A good value can be obtained by running [....] and examining
    # the graph. See [calibration docs coming soon].
    #
    # The meaning of this depends on tap_mode, and the value will be different
    # if a different tap_mode is used.  You can experiment to arrive at this
    # value. Typically, a lower value will make tap detection more sensitive,
    # but might lead to false positives (too early detections). A higher value
    # may cause the detection to wait too long or miss a tap entirely.
    # You can pass a THRESHOLD parameter to the TAP command to experiment to
    # find a good value.
    #
    # You may also need to use different thresholds for different build plates.
    # Note that the default value of this threshold depends on the tap_mode.
    tap_threshold: float = 250.0
    # The speed at which a tap operation should be performed at. This shouldn't
    # be much slower than 3.0, but you can experiment with lower or higher values.
    # Don't go too high though, because Klipper needs some small amount of time
    # to react to a tap trigger, and the toolhead will still be moving at this
    # speed even past the tap point. So, consider any speed you'd feel comfortable
    # triggering a toolhead move to tap_target_z at.
    tap_speed: float = 3.0
    # A static additional amount to add to the computed tap Z offset. Use this if
    # the computed tap is a bit too high or too low for your taste. Positive
    # values will raise the toolhead, negative values will lower it.
    tap_adjust_z: float = 0.0
    # The number of times to do a tap, averaging the results.
    tap_samples: int = 3
    # The maximum number of tap samples.
    tap_max_samples: int = 5
    # The maximum standard deviation for any 3 samples to be considered valid.
    tap_samples_stddev: float = 0.020
    # Use the median value instead of the mean
    tap_use_median: bool = False
    # Where in the time range of tap detection start to the time the threshold
    # is crossed should the tap be placed. 0.0 places it at the earliest start
    # of tap detection; 1.0 places it at the point where the threshold is hit.
    # A value between 0.2-0.5 generally results in more consistent tap position detection,
    # but you may want to adjust this for your configuration. This is a number
    # in the range of 0.0 to 1.0.
    tap_time_position: float = 0.3

    # When probing multiple points (not rapid scan), how long to sample for at each probe point,
    # after a scan_sample_time_delay delay. The total dwell time at each probe point is
    # scan_sample_time + scan_sample_time_delay.
    scan_sample_time: float = 0.100
    # When probing multiple points (not rapid scan), how long to delay at each probe point
    # before the scan_sample_time kicks in.
    scan_sample_time_delay: float = 0.050
    # number of points to save for calibration
    calibration_points: int = 150
    # configuration for butterworth filter
    tap_butter_lowcut: float = 5.0
    tap_butter_highcut: float = 25.0
    tap_butter_order: int = 2
    # Probe position relative to toolhead
    x_offset: float = 0.0
    y_offset: float = 0.0
    # Bed mesh scan path type
    mesh_path: str = "snake"
    # Bed mesh scan direction
    mesh_direction: str = "x"
    # Number of mesh scan passes
    mesh_runs: int = 1
    # Bed mesh scan height
    mesh_height: float = 2.0
    # remove some safety checks, largely for testing/development
    allow_unsafe: bool = False
    # whether to write the tap plot for the last tap
    write_tap_plot: bool = False
    # whether to write the tap plot for every tap
    write_every_tap_plot: bool = False
    # maximum number of errors to allow in a row on the sensor
    max_errors: int = 0
    # whether to print lots of verbose debug info to the log
    debug: bool = True

    tap_trigger_safe_start_height: float = 1.5

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

        # for 'butter'
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

        mesh_path_choices = ["snake", "alternating_snake", "spiral", "random"]
        mesh_dir_choices = ["x", "y"]
        self.mesh_path = config.getchoice("mesh_path", mesh_path_choices, self.mesh_path)
        self.mesh_direction = config.getchoice("mesh_direction", mesh_dir_choices, self.mesh_direction)
        self.mesh_runs = config.getint("mesh_runs", self.mesh_runs, minval=1)
        self.mesh_height = config.getfloat("mesh_height", self.mesh_height, above=0.0)

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


@final
class ProbeEddy:
    def __init__(self, config: ConfigWrapper):
        logging.info("Hello from ProbeEddyNG")

        self._printer: Printer = config.get_printer()
        self._reactor = self._printer.get_reactor()
        self._gcode = self._printer.lookup_object("gcode")
        self._full_name = config.get_name()
        self._name = self._full_name.split()[-1]

        sensors = {
            "ldc1612": ldc1612_ng.LDC1612_ng,
            "btt_eddy": ldc1612_ng.LDC1612_ng,
            "cartographer": ldc1612_ng.LDC1612_ng,
            "mellow_fly": ldc1612_ng.LDC1612_ng,
            "ldc1612_internal_clk": ldc1612_ng.LDC1612_ng,
        }
        sensor_type = config.getchoice("sensor_type", {s: s for s in sensors})

        self._sensor_type = sensor_type
        self._sensor = sensors[sensor_type](config)
        self._mcu = self._sensor.get_mcu()
        self._toolhead: ToolHead = None  # filled in _handle_connect
        self._trapq = None

        self.params = ProbeEddyParams()
        self.params.load_from_config(config)

        # figure out if either of these comes from the autosave section
        # so we can sort out what we want to write out later on
        asfc = self._printer.lookup_object("configfile").autosave.fileconfig
        self._saved_reg_drive_current = asfc.getint(self._full_name, "reg_drive_current", fallback=None)
        self._saved_tap_drive_current = asfc.getint(self._full_name, "tap_drive_current", fallback=None)

        # in case there's legacy drive currents
        old_saved_reg_drive_current = asfc.getint(self._full_name, "saved_reg_drive_current", fallback=0)
        old_saved_tap_drive_current = asfc.getint(self._full_name, "saved_tap_drive_current", fallback=0)

        self._reg_drive_current = self.params.reg_drive_current or old_saved_reg_drive_current or self._sensor._drive_current
        self._tap_drive_current = self.params.tap_drive_current or old_saved_tap_drive_current or self._reg_drive_current

        # at what minimum physical height to start homing. It must be above the safe start position,
        # because we need to move from the start through the safe start position
        self._home_start_height = self.params.home_trigger_height + self.params.home_trigger_safe_start_offset + 1.0

        # physical offsets between probe and nozzle
        self.offset = {
            "x": self.params.x_offset,
            "y": self.params.y_offset,
        }

        version = config.getint("calibration_version", default=-1)
        calibration_bad = False
        if version == -1:
            if config.get("calibrated_drive_currents", None) is not None:
                calibration_bad = True
        elif version != ProbeEddyFrequencyMap.calibration_version:
            calibration_bad = True

        calibrated_drive_currents = config.getintlist("calibrated_drive_currents", [])

        self._dc_to_fmap: Dict[int, ProbeEddyFrequencyMap] = {}
        if not calibration_bad:
            for dc in calibrated_drive_currents:
                fmap = ProbeEddyFrequencyMap(self)
                if fmap.load_from_config(config, dc):
                    self._dc_to_fmap[dc] = fmap
        else:
            for dc in calibrated_drive_currents:
                # read so that there are no warnings about unknown fields
                _ = config.get(f"calibration_{dc}")
            self.params._warning_msgs.append("EDDYng calibration: calibration data invalid, please recalibrate")

        # Our virtual endstop wrapper -- used for homing.
        self._endstop_wrapper = ProbeEddyEndstopWrapper(self)

        # There can only be one active sampler at a time
        self._sampler: ProbeEddySampler = None
        self._last_sampler: ProbeEddySampler = None
        self.save_samples_path = None
        self._streamer = DataStreamer()

        # The last tap Z value, in absolute axis terms. Used for status.
        self._last_tap_z = 0.0
        # The last gcode offset applied after tap, either the tap
        # value, or 0.0 if HOME_Z=1
        self._last_tap_gcode_adjustment = 0.0

        # This class emulates "PrinterProbe". We use some existing helpers to implement
        # functionality like start_session
        # Skip if tool_probe_endstop already registered as probe
        self._is_primary_probe = False
        try:
            self._printer.add_object("probe", self)
            self._is_primary_probe = True
        except:
            pass

        self._bed_mesh_helper = BedMeshScanHelper(self, config)

        # Only register probe commands if we are the primary probe object
        if self._is_primary_probe and hasattr(probe, "ProbeCommandHelper"):
            self._cmd_helper = probe.ProbeCommandHelper(config, self, self._endstop_wrapper.query_endstop)
        else:
            self._cmd_helper = None

        # when doing a scan, what's the offset between probe readings at the bed
        # scan height and the accurate bed height, based on the last tap.
        self._tap_offset = 0.0
        self._last_probe_result = 0.0
        self._temp_comp = None

        # runtime configurable
        self._tap_adjust_z = self.params.tap_adjust_z

        # define our own commands
        self._dummy_gcode_cmd: GCodeCommand = self._gcode.create_gcode_command("", "", {})
        self.define_commands(self._gcode)

        self._printer.register_event_handler("gcode:command_error", self._handle_command_error)
        self._printer.register_event_handler("klippy:connect", self._handle_connect)

        # patch bed_mesh because Klipper
        if not IS_KALICO:
            bed_mesh.ProbeManager.start_probe = bed_mesh_ProbeManager_start_probe_override

    def _log_error(self, msg):
        logging.error(f"{self._name}: {msg}")
        self._gcode.respond_raw(f"!! EDDYng: {msg}\n")

    def _log_warning(self, msg):
        logging.warning(f"{self._name}: {msg}")
        self._gcode.respond_raw(f"!! EDDYng: {msg}\n")

    def _log_msg(self, msg):
        logging.info(f"{self._name}: {msg}")
        self._gcode.respond_info(f"{msg}", log=False)

    def _log_info(self, msg):
        logging.info(f"{self._name}: {msg}")

    def _log_debug(self, msg):
        if self.params.debug:
            logging.info(f"{self._name}: {msg}")

    def define_commands(self, gcode):
        gcode.register_command("PROBE_EDDY_NG_STATUS", self.cmd_STATUS, self.cmd_STATUS_help)
        gcode.register_command(
            "PROBE_EDDY_NG_CALIBRATE",
            self.cmd_CALIBRATE,
            self.cmd_CALIBRATE_help,
        )
        gcode.register_command(
            "PROBE_EDDY_NG_CALIBRATION_STATUS",
            self.cmd_CALIBRATION_STATUS,
            self.cmd_CALIBRATION_STATUS_help,
        )
        gcode.register_command(
            "PROBE_EDDY_NG_SETUP",
            self.cmd_SETUP,
            self.cmd_SETUP_help,
        )
        gcode.register_command(
            "PROBE_EDDY_NG_CLEAR_CALIBRATION",
            self.cmd_CLEAR_CALIBRATION,
            self.cmd_CLEAR_CALIBRATION_help,
        )
        gcode.register_command("PROBE_EDDY_NG_PROBE", self.cmd_PROBE, self.cmd_PROBE_help)
        gcode.register_command(
            "PROBE_EDDY_NG_PROBE_STATIC",
            self.cmd_PROBE_STATIC,
            self.cmd_PROBE_STATIC_help,
        )
        gcode.register_command(
            "PROBE_EDDY_NG_PROBE_ACCURACY",
            self.cmd_PROBE_ACCURACY,
            self.cmd_PROBE_ACCURACY_help,
        )
        gcode.register_command("PROBE_EDDY_NG_TAP", self.cmd_TAP, self.cmd_TAP_help)
        gcode.register_command(
            "PROBE_EDDY_NG_CALIBRATE_THRESHOLD",
            self.cmd_CALIBRATE_THRESHOLD,
            self.cmd_CALIBRATE_THRESHOLD_help,
        )
        gcode.register_command(
            "PROBE_EDDY_NG_SET_TAP_OFFSET",
            self.cmd_SET_TAP_OFFSET,
            "Set or clear the tap offset for the bed mesh scan and other probe operations",
        )
        gcode.register_command(
            "PROBE_EDDY_NG_SET_TAP_ADJUST_Z",
            self.cmd_SET_TAP_ADJUST_Z,
            "Set the tap adjustment value",
        )
        gcode.register_command(
            "PROBE_EDDY_NG_TEST_DRIVE_CURRENT",
            self.cmd_TEST_DRIVE_CURRENT,
            "Test a drive current.",
        )
        gcode.register_command(
            "PROBE_EDDY_NG_OPTIMIZE_DRIVE_CURRENT",
            self.cmd_OPTIMIZE_DRIVE_CURRENT,
            self.cmd_OPTIMIZE_DRIVE_CURRENT_help,
        )
        gcode.register_command("Z_OFFSET_APPLY_PROBE", None)
        gcode.register_command(
            "Z_OFFSET_APPLY_PROBE",
            self.cmd_Z_OFFSET_APPLY_PROBE,
            "Apply the current G-Code Z offset to tap_adjust_z",
        )

        # some handy aliases while I'm debugging things to save my fingers
        gcode.register_command(
            "PES",
            self.cmd_STATUS,
            self.cmd_STATUS_help + " (alias for PROBE_EDDY_NG_STATUS)",
        )
        gcode.register_command(
            "PEP",
            self.cmd_PROBE,
            self.cmd_PROBE_help + " (alias for PROBE_EDDY_NG_PROBE)",
        )
        gcode.register_command(
            "PEPS",
            self.cmd_PROBE_STATIC,
            self.cmd_PROBE_STATIC_help + " (alias for PROBE_EDDY_NG_PROBE_STATIC)",
        )
        gcode.register_command(
            "PETAP",
            self.cmd_TAP,
            self.cmd_TAP_help + " (alias for PROBE_EDDY_NG_TAP)",
        )

        gcode.register_command("EDDYNG_BED_MESH_EXPERIMENTAL", self.cmd_MESH, "")
        gcode.register_command("EDDYNG_START_STREAM_EXPERIMENTAL", self.cmd_START_STREAM, "")
        gcode.register_command("EDDYNG_STOP_STREAM_EXPERIMENTAL", self.cmd_STOP_STREAM, "")

        gcode.register_command(
            "PROBE_EDDY_NG_TEMPERATURE_CALIBRATE",
            self.cmd_TEMPERATURE_CALIBRATE,
            "Calibrate temperature compensation model",
        )
        gcode.register_command(
            "PROBE_EDDY_NG_ESTIMATE_BACKLASH",
            self.cmd_ESTIMATE_BACKLASH,
            "Estimate Z-axis backlash using statistical analysis",
        )
        gcode.register_command(
            "PROBE_EDDY_NG_AXIS_TWIST_CALIBRATE",
            self.cmd_AXIS_TWIST_CALIBRATE,
            "Calibrate axis twist compensation using tap (fully automatic)",
        )
        gcode.register_command(
            "PROBE_EDDY_NG_STREAM",
            self.cmd_STREAM,
            "Manage data streaming (ACTION=START|STOP|CANCEL|STATUS)",
        )
        gcode.register_command(
            "PROBE_EDDY_NG_MODEL",
            self.cmd_MODEL,
            "Manage named calibration models (ACTION=SAVE|LOAD|LIST|DELETE)",
        )

    def _get_bed_center(self):
        th = self._printer.lookup_object("toolhead")
        kin = th.get_kinematics()
        center_x = center_y = None
        try:
            bm = self._printer.lookup_object("bed_mesh")
            bmc = bm.bmc
            if hasattr(bmc, 'zero_reference_pos') and bmc.zero_reference_pos is not None:
                center_x, center_y = bmc.zero_reference_pos
            elif hasattr(bmc, 'mesh_min') and hasattr(bmc, 'mesh_max'):
                center_x = (bmc.mesh_min[0] + bmc.mesh_max[0]) / 2.0
                center_y = (bmc.mesh_min[1] + bmc.mesh_max[1]) / 2.0
        except Exception:
            pass
        if center_x is None or center_y is None:
            xrange = kin.rails[0].get_range()
            yrange = kin.rails[1].get_range()
            center_x = (xrange[0] + xrange[1]) / 2.0
            center_y = (yrange[0] + yrange[1]) / 2.0
        return float(center_x), float(center_y)

    def _handle_command_error(self, gcmd=None):
        try:
            if self._sampler is not None:
                self._sampler.finish()
        except:
            logging.exception("EDDYng handle_command_error: sampler.finish() failed")

    def _handle_connect(self):
        self._toolhead = self._printer.lookup_object("toolhead")
        self._trapq = self._toolhead.get_trapq()
        for msg in self.params._warning_msgs:
            self._log_warning(msg)

    def _get_trapq_position(self, print_time: float) -> Tuple[Tuple[float, float, float], float]:
        ffi_main, ffi_lib = chelper.get_ffi()
        data = ffi_main.new("struct pull_move[1]")
        count = ffi_lib.trapq_extract_old(self._trapq, data, 1, 0.0, print_time)
        if not count:
            return None, None
        move = data[0]
        move_time = max(0.0, min(move.move_t, print_time - move.print_time))
        dist = (move.start_v + 0.5 * move.accel * move_time) * move_time
        pos = (
            move.start_x + move.x_r * dist,
            move.start_y + move.y_r * dist,
            move.start_z + move.z_r * dist,
        )
        velocity = move.start_v + move.accel * move_time
        return pos, velocity

    def _get_trapq_height(self, print_time: float) -> float:
        th_pos, _ = self._get_trapq_position(print_time)
        if th_pos is None:
            return None
        return th_pos[2]

    def current_drive_current(self) -> int:
        return self._sensor.get_drive_current()

    def reset_drive_current(self, tap=False):
        dc = self._tap_drive_current if tap else self._reg_drive_current
        if dc == 0:
            raise self._printer.command_error(f"Unknown {'tap' if tap else 'homing'} drive current")
        self._sensor.set_drive_current(dc)

    def map_for_drive_current(self, dc: Optional[int] = None) -> ProbeEddyFrequencyMap:
        if dc is None:
            dc = self.current_drive_current()
        if dc not in self._dc_to_fmap:
            raise self._printer.command_error(f"Drive current {dc} not calibrated")
        return self._dc_to_fmap[dc]

    # helpers to forward to the map
    def height_to_freq(self, height: float, drive_current: Optional[int] = None) -> float:
        if drive_current is None:
            drive_current = self.current_drive_current()
        return self.map_for_drive_current(drive_current).height_to_freq(height)

    def freq_to_height(self, freq: float, drive_current: Optional[int] = None) -> float:
        if drive_current is None:
            drive_current = self.current_drive_current()
        return self.map_for_drive_current(drive_current).freq_to_height(freq)

    def calibrated(self, drive_current: Optional[int] = None) -> bool:
        if drive_current is None:
            drive_current = self.current_drive_current()
        return drive_current in self._dc_to_fmap and self._dc_to_fmap[drive_current].calibrated()

    def _print_time_now(self):
        return self._mcu.estimated_print_time(self._reactor.monotonic())

    def _z_homed(self):
        curtime = self._reactor.monotonic()
        kin_status = self._printer.lookup_object("toolhead").get_kinematics().get_status(curtime)
        return "z" in kin_status["homed_axes"]

    def _xy_homed(self):
        curtime = self._reactor.monotonic()
        kin_status = self._printer.lookup_object("toolhead").get_kinematics().get_status(curtime)
        return "x" in kin_status["homed_axes"] and "y" in kin_status["homed_axes"]

    def _z_hop(self, by=5.0):
        if by < 0.0:
            raise self._printer.command_error("Z hop must be positive")
        toolhead: ToolHead = self._printer.lookup_object("toolhead")
        curpos = toolhead.get_position()
        curpos[2] = curpos[2] + by
        toolhead.manual_move(curpos, self.params.probe_speed)

    def _set_toolhead_position(self, pos, homing_axes):
        # klipper changed homing_axes to be a "xyz" string instead
        # of a tuple randomly on jan10 without support for the old
        # syntax
        func = self._toolhead.set_position
        kind = type(func.__defaults__[0])
        if kind is str:
            # new
            homing_axes_str = "".join(["xyz"[axis] for axis in homing_axes])
            return self._toolhead.set_position(pos, homing_axes=homing_axes_str)
        else:
            # old
            return self._toolhead.set_position(pos, homing_axes=homing_axes)

    def _z_not_homed(self):
        kin = self._toolhead.get_kinematics()
        # klipper got rid of this
        if hasattr(kin, "note_z_not_homed"):
            kin.note_z_not_homed()
        else:
            try:
                kin.clear_homing_state("z")
            except TypeError:
                raise self._printer.command_error(
                    "clear_homing_state failed: please update Klipper, your klipper is from the brief 5 day window where this was broken"
                )

    def save_config(self):
        configfile = self._printer.lookup_object("configfile")
        configfile.remove_section(self._full_name)

        configfile.set(
            self._full_name,
            "calibrated_drive_currents",
            str.join(", ", [str(dc) for dc in self._dc_to_fmap.keys()]),
        )
        configfile.set(
            self._full_name,
            "calibration_version",
            str(ProbeEddyFrequencyMap.calibration_version),
        )

        if self.params.reg_drive_current != self._reg_drive_current or self.params.reg_drive_current == self._saved_reg_drive_current:
            configfile.set(self._full_name, "reg_drive_current", str(self._reg_drive_current))

        if self.params.tap_drive_current != self._tap_drive_current or self.params.tap_drive_current == self._saved_tap_drive_current:
            configfile.set(self._full_name, "tap_drive_current", str(self._tap_drive_current))

        for _, fmap in self._dc_to_fmap.items():
            fmap.save_calibration()

        self._log_msg("Calibration saved. Issue a SAVE_CONFIG to write the values to your config file and restart Klipper.")

    def start_sampler(self, *args, **kwargs) -> ProbeEddySampler:
        if self._sampler:
            raise self._printer.command_error("EDDYng: Already sampling! (This shouldn't happen; FIRMWARE_RESTART to fix)")
        self._sampler = ProbeEddySampler(self, *args, **kwargs)
        self._sampler.start()
        return self._sampler

    def sampler_is_active(self):
        return self._sampler is not None and self._sampler.active()

    # Called by samplers when they're finished
    def _sampler_finished(self, sampler: ProbeEddySampler, **kwargs):
        if self._sampler is not sampler:
            raise self._printer.command_error("EDDYng finishing sampler that's not active")

        self._last_sampler = sampler
        self._sampler = None

        if self.save_samples_path is not None:
            with open(self.save_samples_path, "w") as data_file:
                times = sampler.times
                raw_freqs = sampler.raw_freqs
                freqs = sampler.freqs
                heights = sampler.heights

                data_file.write("time,frequency,z,kin_z,kin_v,raw_f,trigger_time,tap_start_time\n")
                trigger_time = kwargs.get("trigger_time", "")
                tap_start_time = kwargs.get("tap_start_time", "")
                for i in range(len(times)):
                    past_pos, past_v = self._get_trapq_position(times[i])
                    past_k_z = past_pos[2] if past_pos is not None else ""
                    past_v = past_v if past_v is not None else ""
                    data_file.write(f"{times[i]},{freqs[i]},{heights[i] if heights else ''},{past_k_z},{past_v},{raw_freqs[i]},{trigger_time},{tap_start_time}\n")
            logging.info(f"Wrote {len(times)} samples to {self.save_samples_path}")
            self.save_samples_path = None

    def cmd_MESH(self, gcmd: GCodeCommand):
        self._bed_mesh_helper.scan()

    cmd_STATUS_help = "Query the last raw coil value and status"

    def cmd_STATUS(self, gcmd: GCodeCommand):
        result = self._sensor.read_one_value()

        status = result.status
        freqval = result.freqval
        freq = result.freq
        height = -math.inf

        err = ""
        if freqval > 0x0FFFFFFF:
            height = -math.inf
            freq = 0.0
            err = f"ERROR: {bin(freqval >> 28)} "
        elif freq <= 0.0:
            err += "(Zero frequency) "
        elif self.calibrated():
            height = self.freq_to_height(freq)
        else:
            err += "(Not calibrated) "

        gcmd.respond_info(
            f"Last coil value: {freq:.2f} ({height:.3f}mm) raw: {hex(freqval)} {err}status: {hex(status)} {self._sensor.status_to_str(status)}"
        )

    cmd_PROBE_ACCURACY_help = "Probe accuracy"

    def cmd_PROBE_ACCURACY(self, gcmd: GCodeCommand):
        if not self._z_homed():
            raise self._printer.command_error("Must home Z before PROBE_ACCURACY")

        # How long to read at each sample time
        duration: float = gcmd.get_float("DURATION", 0.100, above=0.0)
        # whether to check +/- 1mm positions for accuracy
        start_z: float = gcmd.get_float("Z", 5.0)
        offsets: str = gcmd.get("OFFSETS", None)

        probe_speed = gcmd.get_float("SPEED", self.params.probe_speed, above=0.0)
        lift_speed = gcmd.get_float("LIFT_SPEED", self.params.lift_speed, above=0.0)

        probe_zs = [start_z]

        if offsets is not None:
            probe_zs.extend([float(v) + start_z for v in offsets.split(",")])
        else:
            probe_zs.extend(np.arange(0.5, start_z, 0.5).tolist())

        probe_zs.sort()
        probe_zs.reverse()

        # drive current to use
        old_drive_current = self.current_drive_current()
        drive_current: int = gcmd.get_int("DRIVE_CURRENT", old_drive_current, minval=0, maxval=31)

        if not self.calibrated(drive_current):
            raise self._printer.command_error(f"Drive current {drive_current} not calibrated")

        th = self._toolhead
        try:
            self._sensor.set_drive_current(drive_current)

            th.manual_move(
                [None, None, probe_zs[0] + 1.0],
                lift_speed,
            )
            th.wait_moves()

            results = []
            ranges = []
            from_zs = []
            stddev_sums = []
            stddev_count = 0

            for pz in probe_zs:
                th.manual_move([None, None, pz], probe_speed)
                th.dwell(0.050)
                th.wait_moves()

                result = self.probe_static_height(duration=duration)
                rangev = result.max_value - result.min_value
                from_z = result.value - pz
                stddev_sum = np.sum([(s - result.value) ** 2.0 for s in result.samples])

                self._log_msg(f"Probe at z={pz:.3f} is {result}")

                stddev_sums.append(stddev_sum)
                stddev_count += len(result.samples)
                results.append(result)
                ranges.append(rangev)
                from_zs.append(from_z)

            if len(results) > 1:
                avg_range = np.mean(ranges)
                avg_from_z = np.mean(from_zs)
                stddev = (np.sum(stddev_sums) / stddev_count) ** 0.5
                gcmd.respond_info(f"Probe spread: {avg_range:.3f}, z deviation: {avg_from_z:.3f}, stddev: {stddev:.3f}")

        finally:
            self._sensor.set_drive_current(old_drive_current)
            th.manual_move(
                [None, None, start_z],
                lift_speed,
            )

    cmd_CLEAR_CALIBRATION_help = "Clear calibration for all drive currents"

    def cmd_CLEAR_CALIBRATION(self, gcmd: GCodeCommand):
        drive_current: int = gcmd.get_int("DRIVE_CURRENT", -1)
        if drive_current == -1:
            self._dc_to_fmap = {}
            gcmd.respond_info("Cleared calibration for all drive currents")
        else:
            if drive_current not in self._dc_to_fmap:
                raise self._printer.command_error(f"Drive current {drive_current} not calibrated")
            del self._dc_to_fmap[drive_current]
            gcmd.respond_info(f"Cleared calibration for drive current {drive_current}")
        self.save_config()

    cmd_CALIBRATION_STATUS_help = "Display information about EDDYng calibration"

    def cmd_CALIBRATION_STATUS(self, gcmd: GCodeCommand):
        for dc in self._dc_to_fmap:
            m = self._dc_to_fmap[dc]
            hmin, hmax = m.height_range
            fmin, fmax = m.freq_range
            fspread = m.freq_spread()
            self._log_msg(
                f"Drive current {dc}: {hmin:.3f} to {hmax:.3f} ({fmin:.1f} to {fmax:.1f}, {fspread:.2f}%; ftoh_high: {m._ftoh_high is not None})"
            )

    def cmd_SET_TAP_OFFSET(self, gcmd: GCodeCommand):
        value = gcmd.get_float("VALUE", None)
        adjust = gcmd.get_float("ADJUST", None)
        tap_offset = self._tap_offset
        if value is not None:
            tap_offset = value
        if adjust is not None:
            tap_offset += adjust
        self._tap_offset = tap_offset
        gcmd.respond_info(f"Set tap offset: {tap_offset:.3f}")

    def cmd_SET_TAP_ADJUST_Z(self, gcmd: GCodeCommand):
        value = gcmd.get_float("VALUE", None)
        adjust = gcmd.get_float("ADJUST", None)
        tap_adjust_z = self._tap_adjust_z
        if value is not None:
            tap_adjust_z = value
        if adjust is not None:
            tap_adjust_z += adjust
        self._tap_adjust_z = tap_adjust_z

        if self.params.tap_adjust_z != self._tap_adjust_z:
            configfile = self._printer.lookup_object("configfile")
            configfile.set(self._full_name, "tap_adjust_z", str(float(self._tap_adjust_z)))

        gcmd.respond_info(f"Set tap_adjust_z: {tap_adjust_z:.3f} (SAVE_CONFIG to make it permanent)")

    def cmd_Z_OFFSET_APPLY_PROBE(self, gcmd: GCodeCommand):
        gcode_move = self._printer.lookup_object("gcode_move")
        offset = gcode_move.get_status()["homing_origin"].z
        offset += self.params.tap_adjust_z
        offset -= self._last_tap_gcode_adjustment
        configfile = self._printer.lookup_object("configfile")
        configfile.set(self._full_name, "tap_adjust_z", f"{offset:.3f}")
        self._log_msg(
            f"{self._name}: new tap_adjust_z: {offset:.3f}\n"
            "The SAVE_CONFIG command will update the printer config file\n"
            "with the above and restart the printer."
        )

    def probe_static_height(self, duration: float = 0.100) -> ProbeEddyProbeResult:
        with self.start_sampler() as sampler:
            now = self._print_time_now()
            sampler.wait_for_sample_at_time(now + (duration + self._sensor._ldc_settle_time))
            sampler.finish()

        if sampler.height_count == 0:
            return ProbeEddyProbeResult([])

        etime = sampler.times[-1]
        stime = etime - duration

        first_idx = bisect.bisect_left(sampler.times, stime)
        if first_idx == len(sampler.times):
            raise self._printer.command_error(f"No samples in time range")

        errors = sampler.error_count
        return ProbeEddyProbeResult.make(sampler.times[first_idx:], sampler.heights[first_idx:], errors=errors)

    cmd_PROBE_help = "Probe the height using the eddy current sensor, moving the toolhead to the home trigger height, or Z if specified."

    def cmd_PROBE(self, gcmd: GCodeCommand):
        if not self._z_homed():
            raise self._printer.command_error("Must home Z before PROBE")

        z: float = gcmd.get_float("Z", self.params.home_trigger_height)

        th = self._printer.lookup_object("toolhead")
        th_pos = th.get_position()
        if th_pos[2] < z:
            th.manual_move([None, None, z + 3.0], self.params.lift_speed)
        th.manual_move([None, None, z], self.params.probe_speed)
        th.dwell(0.100)
        th.wait_moves()

        self.cmd_PROBE_STATIC(gcmd)

    cmd_PROBE_STATIC_help = "Probe the current height using the eddy current sensor without moving the toolhead."

    def cmd_PROBE_STATIC(self, gcmd: GCodeCommand):
        old_drive_current = self.current_drive_current()
        drive_current: int = gcmd.get_int("DRIVE_CURRENT", old_drive_current, minval=0, maxval=31)
        duration: float = gcmd.get_float("DURATION", 0.100, above=0.0)
        save: bool = gcmd.get_int("SAVE", 0) == 1
        home_z: bool = gcmd.get_int("HOME_Z", 0) == 1

        if not self.calibrated(drive_current):
            raise self._printer.command_error(f"Drive current {drive_current} not calibrated")

        try:
            self._sensor.set_drive_current(drive_current)

            if save:
                self.save_samples_path = "/tmp/eddy-probe-static.csv"

            r = self.probe_static_height(duration)

            if self._cmd_helper is not None:
                self._cmd_helper.last_z_result = float(r.value)

            self._last_probe_result = float(r.value)

            if home_z:
                th = self._printer.lookup_object("toolhead")
                th_pos = th.get_position()
                th_pos[2] = r.value
                self._set_toolhead_position(th_pos, [2])
                self._log_debug(f"Homed Z to {r}")
            else:
                self._log_msg(f"Probed {r}")

        finally:
            self._sensor.set_drive_current(old_drive_current)

    cmd_SETUP_help = "Setup"

    def cmd_SETUP(self, gcmd: GCodeCommand):
        if not self._xy_homed():
            raise self._printer.command_error("X and Y must be homed before setup")

        if self._z_homed():
            # z-hop so that manual probe helper doesn't complain if we're already
            # at the right place
            self._z_hop()

        # Move nozzle to bed center before starting manual probe.
        th = self._printer.lookup_object("toolhead")
        center_x, center_y = self._get_bed_center()
        self._log_msg(f"setup: moving nozzle to bed center ({center_x:.0f}, {center_y:.0f})")
        th.manual_move([center_x, center_y, None], self.params.move_speed)
        th.wait_moves()

        # Now reset the axis so that we have a full range to calibrate with
        th_pos = th.get_position()
        # XXX This is proably not correct for some printers?
        zrange = th.get_kinematics().rails[2].get_range()
        th_pos[2] = zrange[1] - 20.0
        self._set_toolhead_position(th_pos, [2])

        manual_probe.ManualProbeHelper(
            self._printer,
            gcmd,
            lambda kin_pos: self.cmd_SETUP_next(gcmd, kin_pos),
        )

    def cmd_SETUP_next(self, gcmd: GCodeCommand, kin_pos: Optional[List[float]]):
        if kin_pos is None:
            # User cancelled ManualProbeHelper
            self._z_not_homed()
            return

        debug = 1 if self.params.debug else 0
        debug = gcmd.get_int("DEBUG", debug) == 1

        # We just did a ManualProbeHelper, so we're going to zero the z-axis
        # to make the following code easier, so it can assume z=0 is actually real zero.
        th = self._printer.lookup_object("toolhead")
        th_pos = th.get_position()
        th_pos[2] = 0.0
        self._set_toolhead_position(th_pos, [2])

        # Note that the default is the default drive current
        drive_current: int = gcmd.get_int(
            "DRIVE_CURRENT",
            self._sensor._default_drive_current,
            minval=0,
            maxval=31,
        )

        max_dc_increase = 0
        if self._sensor_type == "ldc1612" or self._sensor_type == "btt_eddy" or self._sensor_type == "ldc1612_internal_clk":
            max_dc_increase = 5
        max_dc_increase = gcmd.get_int("MAX_DC_INCREASE", max_dc_increase, minval=0, maxval=30)

        # lift up above cal_z_max, and then move over so the probe
        # is over the nozzle position
        th.manual_move(
            [None, None, self.params.calibration_z_max + 3.0],
            self.params.lift_speed,
        )
        th.manual_move(
            [
                th_pos[0] - self.offset["x"],
                th_pos[1] - self.offset["y"],
                None,
            ],
            self.params.move_speed,
        )

        # This is going to automate setup.
        # The setup state machine looks like this:
        # 1. Finding homing drive current
        # 2. Finding tapping drive current
        FINDING_HOMING = 1
        FINDING_TAP = 2
        DONE = 3

        start_drive_current = drive_current
        result_msg = None

        self._log_msg("setup: calibrating homing")
        state = FINDING_HOMING
        while state < DONE:
            mapping, fth_rms, htf_rms = self._create_mapping(
                self.params.calibration_z_max,
                0.0,  # z_target
                self.params.probe_speed,
                self.params.lift_speed,
                drive_current,
                report_errors=debug,
                write_debug_files=debug,
            )

            homing_req_min = 0.5
            homing_req_max = 5.0
            tap_req_min = 0.025
            tap_req_max = 3.0

            ok_for_homing = mapping is not None
            ok_for_tap = mapping is not None

            if ok_for_homing and (mapping.height_range[0] > homing_req_min or mapping.height_range[1] < homing_req_max):
                ok_for_homing = False
            if ok_for_tap and (mapping.height_range[0] > tap_req_min or mapping.height_range[1] < tap_req_max):
                ok_for_tap = False

            if ok_for_homing or ok_for_tap:
                self._log_info(f"dc {drive_current} homing {ok_for_homing} tap {ok_for_tap}, {fth_rms} {htf_rms}")
                if mapping.freq_spread() < 0.30:
                    self._log_warning(
                        f"frequency spread {mapping.freq_spread()} is very low at drive current {drive_current}. (The sensor is probably mounted too high; the height includes any case thickness.)"
                    )
                    ok_for_homing = ok_for_tap = False
                if fth_rms is None or fth_rms > 0.025:
                    self._log_msg(f"calibration error rate is too high ({fth_rms}) at drive current {drive_current}.")
                    ok_for_homing = ok_for_tap = False

            if state == FINDING_HOMING and ok_for_homing:
                self._dc_to_fmap[drive_current] = mapping
                self._reg_drive_current = drive_current
                self._log_msg(f"using {drive_current} for homing.")
                state = FINDING_TAP

            if state == FINDING_TAP and ok_for_tap:
                self._dc_to_fmap[drive_current] = mapping
                self._tap_drive_current = drive_current
                self._log_msg(f"using {drive_current} for tap.")
                state = DONE

            if state == DONE:
                result_msg = "Setup success. Please check whether homing works with G28 Z, then check if tap works with PROBE_EDDY_NG_TAP."
                break

            if drive_current - start_drive_current >= max_dc_increase:
                # we've failed completely
                if state == FINDING_HOMING:
                    result_msg = "Failed to find homing drive current. (Have you checked the sensor height?)"
                elif state == FINDING_TAP:
                    result_msg = "Failed to find tap drive current, but homing is set up. (Have you checked the sensor height?)"
                else:
                    result_msg = "Unknown state?"
                break

            # increase DC and keep going
            drive_current += 1

        if state == DONE:
            self._log_msg(result_msg)
        else:
            self._log_error(result_msg)

        if state > FINDING_HOMING:
            self.reset_drive_current()
            self.save_config()

        self._z_not_homed()

    cmd_CALIBRATE_help = (
        "Calibrate the eddy current sensor. Specify DRIVE_CURRENT to calibrate for a different drive current "
        + "than the default. Specify START_Z to set a different calibration start point."
    )

    def cmd_CALIBRATE(self, gcmd: GCodeCommand):
        if not self._xy_homed():
            raise self._printer.command_error("X and Y must be homed before calibrating")

        if self._z_homed():
            # z-hop so that manual probe helper doesn't complain if we're already
            # at the right place
            self._z_hop()

        # Now reset the axis so that we have a full range to calibrate with
        th = self._printer.lookup_object("toolhead")
        th_pos = th.get_position()
        # XXX This is proably not correct for some printers?
        zrange = th.get_kinematics().rails[2].get_range()
        th_pos[2] = zrange[1] - 20.0
        self._set_toolhead_position(th_pos, [2])

        manual_probe.ManualProbeHelper(
            self._printer,
            gcmd,
            lambda kin_pos: self.cmd_CALIBRATE_next(gcmd, kin_pos),
        )

    def cmd_CALIBRATE_next(self, gcmd: GCodeCommand, kin_pos: Optional[List[float]]):
        th = self._printer.lookup_object("toolhead")
        if kin_pos is None:
            # User cancelled ManualProbeHelper
            self._z_not_homed()
            return

        old_drive_current = self.current_drive_current()
        drive_current: int = gcmd.get_int("DRIVE_CURRENT", old_drive_current, minval=0, maxval=31)
        cal_z_max: float = gcmd.get_float("START_Z", self.params.calibration_z_max, above=2.0)
        z_target: float = gcmd.get_float("TARGET_Z", 0.0)

        probe_speed: float = gcmd.get_float("SPEED", self.params.probe_speed, above=0.0)
        lift_speed: float = gcmd.get_float("LIFT_SPEED", self.params.lift_speed, above=0.0)

        # We just did a ManualProbeHelper, so we're going to zero the z-axis
        # to make the following code easier, so it can assume z=0 is actually real zero.
        # The Eddy sensor calibration is done to nozzle height (not sensor or trigger height).
        th_pos = th.get_position()
        th_pos[2] = 0.0
        self._set_toolhead_position(th_pos, [2])

        th.wait_moves()

        self._log_debug(f"calibrating from {kin_pos}, {th_pos}")

        # lift up above cal_z_max, and then move over so the probe
        # is over the nozzle position
        th.manual_move([None, None, cal_z_max + 3.0], lift_speed)
        th.manual_move(
            [
                th_pos[0] - self.offset["x"],
                th_pos[1] - self.offset["y"],
                None,
            ],
            self.params.move_speed,
        )

        mapping, fth_fit, htf_fit = self._create_mapping(
            cal_z_max,
            z_target,
            probe_speed,
            lift_speed,
            drive_current,
            report_errors=True,
            write_debug_files=True,
        )
        if mapping is None or fth_fit is None or htf_fit is None:
            self._log_error("Calibration failed")
            return

        self._dc_to_fmap[drive_current] = mapping
        self.save_config()

        # reset the Z homing state after alibration
        self._z_not_homed()

    def _create_mapping(
        self,
        z_start: float,
        z_target: float,
        probe_speed: float,
        lift_speed: float,
        drive_current: int,
        report_errors: bool,
        write_debug_files: bool,
    ) -> Tuple[ProbeEddyFrequencyMap, float, float]:
        th = self._printer.lookup_object("toolhead")
        th_pos = th.get_position()

        # move to the start z of the mapping, going up first if we need to for backlash
        if th_pos[2] < z_start:
            th.manual_move([None, None, z_start + 3.0], lift_speed)
        th.manual_move([None, None, z_start], lift_speed)

        old_drive_current = self.current_drive_current()
        try:
            self._sensor.set_drive_current(drive_current)
            times, freqs, heights, vels = self._capture_samples_down_to(z_target, probe_speed)
            th.manual_move([None, None, z_start + 3.0], lift_speed)
        finally:
            self._sensor.set_drive_current(old_drive_current)

        if times is None:
            if report_errors:
                self._log_error(f"Drive current {drive_current}: No samples collected. This could be a hardware issue or an incorrect drive current.")
            else:
                self._log_warning(f"Drive current {drive_current}: Warning: no samples collected.")
            return None, None, None

        # and build a map
        mapping = ProbeEddyFrequencyMap(self)
        fth_fit, htf_fit = mapping.calibrate_from_values(
            drive_current,
            times,
            freqs,
            heights,
            vels,
            report_errors,
            write_debug_files,
        )

        return mapping, fth_fit, htf_fit

    def _capture_samples_down_to(self, z_target: float, probe_speed: float) -> tuple[List[float], List[float], List[float], List[float]]:
        th = self._printer.lookup_object("toolhead")
        th.dwell(0.500)  # give the sensor a bit to settle
        th.wait_moves()

        with self.start_sampler(calculate_heights=False) as sampler:
            first_sample_time = th.get_last_move_time()
            th.manual_move([None, None, z_target], probe_speed)
            last_sample_time = th.get_last_move_time()
            # Can't use wait_for_sample_at_time here, because the tail end of
            # samples might be errors so they won't be passed to the sampler.
            # Should fix that, but for now just wait an extra half second which
            # should be more than enough.
            # sampler.wait_for_sample_at_time(last_sample_time)
            th.dwell(0.500)
            th.wait_moves()
            sampler.finish()

        # the samples are a list of [print_time, freq, dummy_height] tuples
        if sampler.raw_count == 0:
            return None, None, None, None

        freqs = []
        heights = []
        times = []
        vels = []

        for i in range(sampler.raw_count):
            s_t = sampler.times[i]
            s_freq = sampler.freqs[i]
            s_pos, s_v = self._get_trapq_position(s_t)
            s_z = s_pos[2]
            if first_sample_time < s_t < last_sample_time and s_z >= z_target:
                times.append(s_t)
                freqs.append(s_freq)
                heights.append(s_z)
                vels.append(s_v)

        return times, freqs, heights, vels

    def cmd_TEST_DRIVE_CURRENT(self, gcmd: GCodeCommand):
        drive_current: int = gcmd.get_int("DRIVE_CURRENT", self._reg_drive_current, minval=1, maxval=31)
        z_start: float = gcmd.get_float("START_Z", self.params.calibration_z_max, above=2.0)
        z_end: float = gcmd.get_float("TARGET_Z", 0.0)
        debug: bool = gcmd.get_int("DEBUG", 0) == 1
        self._log_msg(f"Testing Z={z_start:.3f} to Z={z_end:.3f}")

        mapping, fth, htf = self._create_mapping(
            z_start,
            z_end,
            self.params.probe_speed,
            self.params.lift_speed,
            drive_current,
            report_errors=False,
            write_debug_files=debug,
        )
        if mapping is None or fth is None or htf is None:
            self._log_error(f"Test failed: drive current {drive_current} is not usable.")

    #
    # Drive current optimization
    #
    cmd_OPTIMIZE_DRIVE_CURRENT_help = (
        "Test all drive currents in a range and select the optimal one for homing and tap. "
        "Includes real tap verification for the top candidates. "
        "Parameters: START_DC (first DC to test, default 1), END_DC (last DC to test, default 31), "
        "TAP_VERIFY (number of test taps per candidate, default 5), "
        "TOP_CANDIDATES (how many top DCs to tap-verify, default 3), "
        "SAVE (1 to auto-save results, default 1)."
    )

    def cmd_OPTIMIZE_DRIVE_CURRENT(self, gcmd: GCodeCommand):
        if not self._z_homed():
            raise self._printer.command_error("Z axis must be homed before drive current optimization")

        default_start = max(1, self._reg_drive_current - 5)
        default_end = min(31, self._reg_drive_current + 7)
        start_dc = gcmd.get_int("START_DC", default_start, minval=0, maxval=31)
        end_dc = gcmd.get_int("END_DC", default_end, minval=start_dc, maxval=31)
        auto_save = gcmd.get_int("SAVE", 1) == 1
        debug = gcmd.get_int("DEBUG", 0) == 1
        tap_verify_count = gcmd.get_int("TAP_VERIFY", 5, minval=0, maxval=20)
        top_n = gcmd.get_int("TOP_CANDIDATES", 3, minval=1, maxval=10)
        tap_mode = gcmd.get("MODE", self.params.tap_mode).lower()

        z_start = self.params.calibration_z_max
        probe_speed = self.params.probe_speed
        lift_speed = self.params.lift_speed

        homing_req_min = 0.5
        homing_req_max = 5.0
        tap_req_min = 0.025
        tap_req_max = 3.0
        min_freq_spread = 0.30
        max_rmse = 0.025

        @dataclass
        class DCResult:
            dc: int
            mapping: ProbeEddyFrequencyMap
            rmse_fth: float
            rmse_htf: float
            freq_spread: float
            height_min: float
            height_max: float
            ok_for_homing: bool = False
            ok_for_tap: bool = False
            homing_score: float = 0.0
            tap_score: float = 0.0
            tap_verified: bool = False
            tap_range: float = math.inf
            tap_stddev: float = math.inf
            tap_median: float = 0.0
            tap_success_rate: float = 0.0

        results: List[DCResult] = []

        self._log_msg(
            f"Optimizing drive current: testing DC {start_dc} to {end_dc}...\n"
            f"This will take a while -- each DC requires a full Z sweep."
        )

        # === Phase 1: Calibration sweep for all DCs ===
        for dc in range(start_dc, end_dc + 1):
            self._log_info(f"Testing drive current {dc}...")

            mapping, fth_rms, htf_rms = self._create_mapping(
                z_start,
                0.0,
                probe_speed,
                lift_speed,
                dc,
                report_errors=False,
                write_debug_files=debug,
            )

            if mapping is None or fth_rms is None:
                self._log_info(f"  DC {dc}: no valid mapping")
                continue

            spread = mapping.freq_spread()
            h_min = mapping.height_range[0]
            h_max = mapping.height_range[1]

            r = DCResult(
                dc=dc,
                mapping=mapping,
                rmse_fth=fth_rms,
                rmse_htf=htf_rms,
                freq_spread=spread,
                height_min=h_min,
                height_max=h_max,
            )

            if h_min <= homing_req_min and h_max >= homing_req_max and spread >= min_freq_spread and fth_rms <= max_rmse:
                r.ok_for_homing = True
                r.homing_score = (1.0 / (1.0 + fth_rms * 100.0)) + (spread / 100.0)

            if h_min <= tap_req_min and h_max >= tap_req_max and spread >= min_freq_spread and fth_rms <= max_rmse:
                r.ok_for_tap = True
                r.tap_score = (1.0 / (1.0 + fth_rms * 100.0)) + (1.0 / (1.0 + h_min * 100.0)) + (spread / 100.0)

            results.append(r)

            status = ""
            if r.ok_for_homing:
                status += " [homing OK]"
            if r.ok_for_tap:
                status += " [tap OK]"
            if not status:
                status = " [not usable]"

            self._log_info(
                f"  DC {dc}: RMSE={fth_rms:.4f}, spread={spread:.2f}%, "
                f"height={h_min:.3f}-{h_max:.3f}{status}"
            )

        # === Phase 2: Tap verification for top candidates ===
        tap_candidates = sorted(
            [r for r in results if r.ok_for_tap],
            key=lambda r: r.tap_score,
            reverse=True,
        )

        if tap_verify_count > 0 and tap_candidates:
            verify_list = tap_candidates[:top_n]
            self._log_msg(
                f"\n=== Phase 2: Tap verification for top {len(verify_list)} candidates "
                f"({tap_mode} mode, {tap_verify_count} taps each) ==="
            )

            threshold = self.params.tap_threshold
            old_dc = self.current_drive_current()

            for r in verify_list:
                self._log_msg(f"Tap-testing DC {r.dc}...")

                self._dc_to_fmap[r.dc] = r.mapping
                self._sensor.set_drive_current(r.dc)

                tapcfg = self._build_tap_config(tap_mode, threshold)
                probe_zs = []
                errors = 0

                for i in range(tap_verify_count):
                    try:
                        tap = self.do_one_tap(
                            start_z=self.params.tap_start_z,
                            target_z=self.params.tap_target_z,
                            tap_speed=self.params.tap_speed,
                            lift_speed=lift_speed,
                            tapcfg=tapcfg,
                        )
                        if tap.error:
                            errors += 1
                            self._log_debug(f"  DC {r.dc} tap {i+1}: error: {tap.error}")
                        else:
                            probe_zs.append(tap.probe_z)
                            self._log_debug(f"  DC {r.dc} tap {i+1}: z={tap.probe_z:.4f}")
                    except Exception as e:
                        errors += 1
                        self._log_debug(f"  DC {r.dc} tap {i+1}: exception: {e}")

                r.tap_success_rate = len(probe_zs) / tap_verify_count if tap_verify_count > 0 else 0.0

                if len(probe_zs) >= 3:
                    z_arr = np.array(probe_zs)
                    r.tap_range = float(z_arr.max() - z_arr.min())
                    r.tap_stddev = float(np.std(z_arr))
                    r.tap_median = float(np.median(z_arr))
                    r.tap_verified = True

                    self._log_msg(
                        f"  DC {r.dc}: {len(probe_zs)}/{tap_verify_count} OK, "
                        f"range={r.tap_range:.4f}mm, stddev={r.tap_stddev:.4f}mm, "
                        f"median={r.tap_median:.4f}mm"
                    )

                    r.tap_score += (1.0 / (1.0 + r.tap_range * 100.0)) + (1.0 / (1.0 + r.tap_stddev * 100.0)) + r.tap_success_rate
                else:
                    self._log_msg(
                        f"  DC {r.dc}: only {len(probe_zs)}/{tap_verify_count} OK "
                        f"({errors} errors) -- tap verification FAILED"
                    )
                    r.tap_score *= 0.1

            self._sensor.set_drive_current(old_dc)

        # === Phase 3: Select best and report ===
        homing_candidates = [r for r in results if r.ok_for_homing]
        tap_candidates = sorted(
            [r for r in results if r.ok_for_tap],
            key=lambda r: r.tap_score,
            reverse=True,
        )

        best_homing = max(homing_candidates, key=lambda r: r.homing_score) if homing_candidates else None
        best_tap = tap_candidates[0] if tap_candidates else None

        msg_lines = ["\n=== Drive Current Optimization Results ===\n"]

        if results:
            msg_lines.append("Tested drive currents:")
            for r in results:
                flags = []
                if r.ok_for_homing:
                    flags.append("homing")
                if r.ok_for_tap:
                    flags.append("tap")
                    if r.tap_verified:
                        flags.append(f"verified: range={r.tap_range:.4f} stddev={r.tap_stddev:.4f} success={r.tap_success_rate:.0%}")
                flag_str = f" [{', '.join(flags)}]" if flags else ""
                marker = ""
                if best_homing and r.dc == best_homing.dc:
                    marker += " << BEST HOMING"
                if best_tap and r.dc == best_tap.dc:
                    marker += " << BEST TAP"
                msg_lines.append(
                    f"  DC {r.dc:2d}: RMSE={r.rmse_fth:.4f}  spread={r.freq_spread:5.2f}%  "
                    f"height={r.height_min:.3f}-{r.height_max:.3f}{flag_str}{marker}"
                )

        msg_lines.append("")
        if best_homing:
            msg_lines.append(
                f"Best for HOMING: DC {best_homing.dc} "
                f"(RMSE={best_homing.rmse_fth:.4f}, spread={best_homing.freq_spread:.2f}%, "
                f"height={best_homing.height_min:.3f}-{best_homing.height_max:.3f})"
            )
        else:
            msg_lines.append("No suitable drive current found for HOMING.")

        if best_tap:
            tap_detail = (
                f"Best for TAP:    DC {best_tap.dc} "
                f"(RMSE={best_tap.rmse_fth:.4f}, spread={best_tap.freq_spread:.2f}%, "
                f"height={best_tap.height_min:.3f}-{best_tap.height_max:.3f}"
            )
            if best_tap.tap_verified:
                tap_detail += (
                    f", tap range={best_tap.tap_range:.4f}mm, "
                    f"stddev={best_tap.tap_stddev:.4f}mm, "
                    f"success={best_tap.tap_success_rate:.0%}"
                )
            tap_detail += ")"
            msg_lines.append(tap_detail)
        else:
            msg_lines.append("No suitable drive current found for TAP.")

        if best_homing is None and best_tap is None:
            msg_lines.append("\nNo usable drive currents found. Check sensor mounting height.")
            self._log_error("\n".join(msg_lines))
            raise self._printer.command_error("Drive current optimization failed: no usable DC found")

        if best_homing:
            self._dc_to_fmap[best_homing.dc] = best_homing.mapping
            self._reg_drive_current = best_homing.dc
        if best_tap:
            self._dc_to_fmap[best_tap.dc] = best_tap.mapping
            self._tap_drive_current = best_tap.dc

        if auto_save and (best_homing or best_tap):
            self.reset_drive_current()
            self.save_config()
            msg_lines.append("\nResults saved. Run SAVE_CONFIG to persist.")

        self._log_msg("\n".join(msg_lines))

    #
    # PrinterProbe interface
    #

    def get_offsets(self, *args, **kwargs):
        # the z offset is the trigger height, because the probe will trigger
        # at z=trigger_height (not at z=0)
        return (
            self.offset["x"],
            self.offset["y"],
            self.params.home_trigger_height,
        )

    def get_probe_params(self, gcmd=None):
        return {
            "probe_speed": self.params.probe_speed,
            "lift_speed": self.params.lift_speed,
            "sample_retract_dist": 0.0,
        }

    def start_probe_session(self, gcmd):
        # G28 Z homing calls start_probe_session via Klipper's
        # _do_home_z_via_probe.  The scanning probe fails when the gantry
        # is far from the bed (ERR_AHE), so use the endstop-based session
        # which handles the full approach from any height.
        if gcmd.get_command() == "G28":
            session = ProbeEddyHomingSession(self, gcmd)
            session._start_session()
            return session
        session = ProbeEddyScanningProbe(self, gcmd)
        session._start_session()
        return session

    def get_status(self, eventtime):
        if self._cmd_helper is not None:
            status = self._cmd_helper.get_status(eventtime)
        else:
            status = dict()
        status.update(
            {
                "name": self._full_name,
                "home_trigger_height": float(self.params.home_trigger_height),
                "tap_offset": float(self._tap_offset),
                "tap_adjust_z": float(self._tap_adjust_z),
                "last_probe_result": float(self._last_probe_result),
                "last_tap_z": float(self._last_tap_z),
            }
        )
        return status

    # Old Probe interface, for Kalico

    def get_lift_speed(self, gcmd=None):
        if gcmd is not None:
            return gcmd.get_float("LIFT_SPEED", self.params.lift_speed, above=0.0)
        return self.params.lift_speed

    def multi_probe_begin(self):
        pass

    def multi_probe_end(self):
        pass

    # This is a mishmash of cmd_PROBE and cmd_PROBE_STATIC. This run_probe
    # is the old one, different than the scanning session run_probe.
    def run_probe(self, gcmd=None, *args: Any, **kwargs: Any):
        z = self.params.home_trigger_height
        duration = 0.100

        if not self._z_homed():
            raise self._printer.command_error("Must home Z before PROBE")

        if not self.calibrated():
            raise self._printer.command_error("Eddy probe not calibrated!")

        th = self._printer.lookup_object("toolhead")
        th_pos = th.get_position()
        if th_pos[2] < z:
            th.manual_move([None, None, z + 3.0], self.params.lift_speed)
        th.manual_move([None, None, z], self.params.lift_speed)
        th.dwell(0.100)
        th.wait_moves()

        r = self.probe_static_height(duration)
        if not r.valid:
            raise self._printer.command_error("Probe captured no samples!")

        height = r.value
        height += self._tap_offset

        # At what Z position would the toolhead be at for the probe to read
        # _home_trigger_height? In other words, if the probe tells us
        # the height is 1.5 when the toolhead is at z=2.0, if the toolhead
        # was moved up to 2.5, then the probe should read 2.0.
        probe_z = z + (z - height)

        return [th_pos[0], th_pos[1], probe_z]

    #
    # Moving the sensor to the correct position
    #
    def _probe_to_start_position_unhomed(self, move_home=False):
        if not self._xy_homed():
            raise self._printer.command_error("xy must be homed")
        if not self.sampler_is_active():
            raise self._printer.command_error("probe_to_start_position_unhomed: no sampler active")
        if not self.calibrated():
            raise self._printer.command_error("EDDYng not calibrated!")

        th = self._printer.lookup_object("toolhead")
        th_pos = th.get_position()

        # debug logging
        th_kin = th.get_kinematics()
        zlim = th_kin.limits[2]
        rail_range = th_kin.rails[2].get_range()
        self._log_debug(
            f"probe to start unhomed: before movement: Z pos {th_pos[2]:.3f}, "
            f"Z limits {zlim[0]:.2f}-{zlim[1]:.2f}, "
            f"rail range {rail_range[0]:.2f}-{rail_range[1]:.2f}"
        )

        start_height_ok_factor = 0.100

        # This is where we want to get to
        start_height = self._home_start_height
        # This is where the probe thinks we are
        now_height = self._sampler.get_height_now()

        # If we can't get a value at all, the sensor is likely too far from the
        # bed to produce valid readings (e.g. after power-on with gantry high).
        # Give the homing move maximum travel by setting Z position near the
        # top of the rail range so it can approach all the way to the bed.
        if now_height is None:
            th_pos[2] = rail_range[1] - 10.0
            self._log_debug(
                f"probe_to_start_position_unhomed: no valid samples, assuming sensor is far from bed"
                f" — setting Z to {th_pos[2]:.3f} for maximum homing travel"
            )
            self._set_toolhead_position(th_pos, [2])
            return

        self._log_debug(f"probe_to_start_position_unhomed: now: {now_height} (start {start_height})")
        if abs(now_height - start_height) <= start_height_ok_factor:
            return

        th = self._printer.lookup_object("toolhead")
        th_pos = th.get_position()

        # If the sensor thinks we're too low we need to move back up before homing
        if now_height < start_height:
            move_up_by = min(start_height, start_height - now_height)
            # give ourselves some room to do so, homing typically doesn't move up,
            # and we should know that we have this room because the sensor tells us we're too low
            th_pos[2] = rail_range[1] - (move_up_by + 10.0)
            self._log_debug(f"probe_to_start_position_unhomed: resetting toolhead to z {th_pos[2]:.3f}")
            self._set_toolhead_position(th_pos, [2])

            n_pos = th.get_position()

            zlim = th_kin.limits[2]
            rail_range = th_kin.rails[2].get_range()
            self._log_debug(
                f"after reset: Z pos {n_pos[2]:.3f}, Z limits {zlim[0]:.2f}-{zlim[1]:.2f}, rail range {rail_range[0]:.2f}-{rail_range[1]:.2f}"
            )

            th_pos[2] += move_up_by
            self._log_debug(f"probe_to_start_position_unhomed: moving toolhead up by {move_up_by:.3f} to {th_pos[2]:.3f}")
            th.manual_move([None, None, th_pos[2]], self.params.probe_speed)
            # TODO: this should just be th.wait_moves()
            self._sampler.wait_for_sample_at_time(th.get_last_move_time())

    def probe_to_start_position(self, z_pos=None):
        self._log_debug(f"probe_to_start_position (tt: {self.params.tap_threshold}, z-homed: {self._z_homed()})")

        # If we're not homed at all, rely on the sensor values to bring us to
        # a good place to start a diving probe from
        if not self._z_homed():
            if z_pos is not None:
                raise self._printer.command_error("Can't probe_to_start_position with an explicit Z without homed Z")
            self._probe_to_start_position_unhomed()
            return

        th = self._printer.lookup_object("toolhead")
        th.wait_moves()
        th_pos = th.get_position()

        # Note home_trigger_height and not home_start_height: if we're homed,
        # we don't need to do another dive and we just want to move to
        # the right position for probing.
        if z_pos is not None:
            start_z = z_pos
        else:
            start_z = self.params.home_trigger_height

        # If we're below, move up a bit beyond and the back down
        # to compensate for backlash
        if th_pos[2] < start_z:
            self._log_debug(f"probe_to_start_position: moving toolhead from {th_pos[2]:.3f} to {(start_z + 1.0):.3f}")
            th_pos[2] = start_z + 1.0
            th.manual_move(th_pos, self.params.lift_speed)

        self._log_debug(f"probe_to_start_position: moving toolhead from {th_pos[2]:.3f} to {start_z:.3f}")
        th_pos[2] = start_z
        th.manual_move(th_pos, self.params.probe_speed)

        th.wait_moves()

    #
    # Tap probe
    #
    cmd_TAP_help = "Calculate a z-offset by touching the build plate."

    def cmd_TAP(self, gcmd: GCodeCommand):
        drive_current = self._sensor.get_drive_current()
        try:
            self.cmd_TAP_next(gcmd)
        finally:
            self._sensor.set_drive_current(drive_current)

    @dataclass
    class TapResult:
        error: Optional[Exception]
        probe_z: float
        toolhead_z: float
        overshoot: float
        tap_time: float
        tap_start_time: float
        tap_end_time: float

    @dataclass
    class TapConfig:
        mode: str
        threshold: float
        sos: Optional[List[List[float]]] = None

    def do_one_tap(
        self,
        start_z: float,
        target_z: float,
        tap_speed: float,
        lift_speed: float,
        tapcfg: ProbeEddy.TapConfig,
    ) -> TapResult:
        self.probe_to_start_position(start_z)

        th = self._printer.lookup_object("toolhead")

        target_position = th.get_position()
        target_position[2] = target_z

        error = None

        try:
            # configure the endstop for tap (gets reset at the end of a tap sequence,
            # also in finally just in case
            self._endstop_wrapper.tap_config = tapcfg

            endstops = [(self._endstop_wrapper, "probe")]
            hmove = HomingMove(self._printer, endstops)

            try:
                probe_position = hmove.homing_move(target_position, tap_speed, probe_pos=True)

                # raise toolhead as soon as tap ends
                finish_z = th.get_position()[2]
                if finish_z < 1.0:
                    th.manual_move([None, None, start_z], lift_speed)

                if hmove.check_no_movement() is not None:
                    raise self._printer.command_error("Probe triggered prior to movement")

                probe_z = probe_position[2]

                self._log_debug(f"tap: probe_z: {probe_z:.3f} finish_z: {finish_z:.3f} moved up to {start_z:.3f}")

                if probe_z - target_z < 0.050:
                    # we detected a tap but it was too close to our target z
                    # to be trusted
                    # TODO: use velocity to determine this
                    return ProbeEddy.TapResult(
                        error=Exception("Tap detected too close to target z"),
                        toolhead_z=finish_z,
                        probe_z=probe_z,
                        overshoot=0.0,
                        tap_time=0.0,
                        tap_start_time=0.0,
                        tap_end_time=0.0,
                    )

            except self._printer.command_error as err:
                if self._printer.is_shutdown():
                    raise self._printer.command_error("Probing failed due to printer shutdown")

                # in case of failure don't leave the toolhead in a bad spot (i.e. in bed)
                finish_z = th.get_position()[2]
                if finish_z < 1.0:
                    th.manual_move([None, None, start_z], lift_speed)

                # If just sensor errors, let the caller handle it
                self._log_error(f"Tap failed with Z at {finish_z:.3f}: {err}")
                if any(x in str(err) for x in ("Sensor error", "Probe completed movement", "Probe triggered prior")):
                    return ProbeEddy.TapResult(
                        error=err,
                        toolhead_z=finish_z,
                        probe_z=0.0,
                        overshoot=0.0,
                        tap_time=0.0,
                        tap_start_time=0.0,
                        tap_end_time=0.0,
                    )
                else:
                    raise
        finally:
            self._endstop_wrapper.tap_config = None

        # The toolhead ended at finish_z, but probe_z is the actual zero.
        # finish_z should be below or equal to probe_z because there will always be
        # a bit of overshoot due to trigger delay, and because we actually
        # fire the trigger later than when the tap starts (and the tap start
        # time is what's used to compute probe_position)
        if finish_z > probe_z:
            raise self._printer.command_error(f"Unexpected: finish_z {finish_z:.3f} is above probe_z {probe_z:.3f} after tap")

        # How much the toolhead overshot the real z=0 position. This is the amount
        # the toolhead is pushing into the build plate.
        overshoot = probe_z - finish_z

        tap_start_time = self._endstop_wrapper.last_tap_start_time
        tap_end_time = self._endstop_wrapper.last_trigger_time
        tap_time = tap_start_time + (tap_end_time - tap_start_time) * self.params.tap_time_position

        return ProbeEddy.TapResult(
            error=error,
            probe_z=probe_z,
            toolhead_z=finish_z,
            overshoot=overshoot,
            tap_time=tap_time,
            tap_start_time=tap_start_time,
            tap_end_time=tap_end_time,
        )

    def _compute_butter_tap(self, sampler):
        if not scipy:
            return None, None

        trigger_freq = self.height_to_freq(self.params.home_trigger_height)

        s_f = np.asarray(sampler.freqs)
        first_one = np.argmax(s_f >= trigger_freq)
        s_t = np.asarray(sampler.times[first_one:])
        s_f = np.asarray(sampler.freqs[first_one:])

        # Detrend: remove approach ramp using linear prediction.
        # Matches the MCU-side detrending in check_sos_tap() so that
        # plots reflect the actual signal the MCU sees.
        alpha = 0.95
        detrended = np.zeros_like(s_f)
        avg_delta = 0.0
        for i in range(1, len(s_f)):
            delta = s_f[i] - s_f[i - 1]
            avg_delta = alpha * avg_delta + (1.0 - alpha) * delta
            predicted = s_f[i - 1] + avg_delta
            detrended[i] = s_f[i] - predicted

        lowcut = self.params.tap_butter_lowcut
        highcut = self.params.tap_butter_highcut
        order = self.params.tap_butter_order

        sos = scipy.signal.butter(
            order,
            [lowcut, highcut],
            btype="bandpass",
            fs=self._sensor._data_rate,
            output="sos",
        )
        filtered = scipy.signal.sosfilt(sos, detrended)

        return s_t, filtered

    def cmd_TAP_next(self, gcmd: Optional[GCodeCommand] = None):
        self._log_debug("\nEDDYng Tap begin")

        if gcmd is None:
            gcmd = self._dummy_gcode_cmd

        orig_drive_current: int = self._sensor.get_drive_current()
        tap_drive_current: int = gcmd.get_int(
            name="DRIVE_CURRENT",
            default=self._tap_drive_current,
            minval=1,
            maxval=31,
        )
        tap_speed: float = gcmd.get_float("SPEED", self.params.tap_speed, above=0.0)
        lift_speed: float = gcmd.get_float("RETRACT_SPEED", self.params.lift_speed, above=0.0)
        tap_start_z: float = gcmd.get_float("START_Z", self.params.tap_start_z, above=2.0)
        target_z: float = gcmd.get_float("TARGET_Z", self.params.tap_target_z)
        tap_threshold: float = gcmd.get_float("THRESHOLD", None)  # None so we have a sentinel value
        tap_threshold = gcmd.get_float("TT", tap_threshold)  # alias for THRESHOLD
        tap_adjust_z = gcmd.get_float("ADJUST_Z", self._tap_adjust_z)
        do_retract = gcmd.get_int("RETRACT", 1) == 1
        samples = gcmd.get_int("SAMPLES", self.params.tap_samples, minval=1)
        max_samples = gcmd.get_int("MAX_SAMPLES", self.params.tap_max_samples, minval=samples)
        samples_stddev = gcmd.get_float("SAMPLES_STDDEV", self.params.tap_samples_stddev, above=0.0)
        use_median: bool = gcmd.get_int("USE_MEDIAN", 1 if self.params.tap_use_median else 0) == 1
        home_z: bool = gcmd.get_int("HOME_Z", 1) == 1
        write_plot_arg: int = gcmd.get_int("PLOT", None)

        mode = gcmd.get("MODE", self.params.tap_mode).lower()
        if mode not in ("wma", "butter"):
            raise self._printer.command_error(f"Invalid mode: {mode}")

        # if the mode is different than the params, then require
        # specifying threshold
        if tap_threshold is None:
            if mode != self.params.tap_mode:
                raise self._printer.command_error(
                    f"THRESHOLD required when mode ({mode}) is different than configured default ({self.params.tap_mode})"
                )
            tap_threshold = self.params.tap_threshold

        if not self._z_homed():
            raise self._printer.command_error("Z axis must be homed before tapping")

        write_tap_plot = self.params.write_tap_plot
        write_every_tap_plot = self.params.write_every_tap_plot and write_tap_plot
        if write_plot_arg is not None:
            write_tap_plot = write_plot_arg > 0
            write_every_tap_plot = write_plot_arg > 1

        tapcfg = ProbeEddy.TapConfig(mode=mode, threshold=tap_threshold)
        # fmt: off
        if mode == "butter":
            if self.params.is_default_butter_config() and self._sensor._data_rate == 250:
                sos = [
                    [ 0.046131802093312926, 0.09226360418662585, 0.046131802093312926, 1.0, -1.3297767184682712, 0.5693902189294331, ],
                    [ 1.0, -2.0, 1.0, 1.0, -1.845000600983779, 0.8637525213328747, ],
                ]
            elif self.params.is_default_butter_config() and self._sensor._data_rate == 500:
                sos = [
                    [ 0.013359200027856505, 0.02671840005571301, 0.013359200027856505, 1.0, -1.686278256753083, 0.753714473246724, ],
                    [ 1.0, -2.0, 1.0, 1.0, -1.9250515947328444, 0.9299234737648037, ],
                ]
            elif scipy:
                sos = scipy.signal.butter(
                    self.params.tap_butter_order,
                    [ self.params.tap_butter_lowcut, self.params.tap_butter_highcut, ],
                    btype="bandpass",
                    fs=self._sensor._data_rate,
                    output="sos",
                ).tolist()
            else:
                raise self._printer.command_error("Scipy is not available, cannot use custom filter, or data rate is not 250 or 500")
            tapcfg.sos = sos
        # fmt: on

        results = []
        tap_z = None
        tap_stddev = None
        tap_overshoot = None
        sample_err_count = 0
        tap = None

        try:
            self._sensor.set_drive_current(tap_drive_current)

            sample_last_err = None

            for sample_i in range(max_samples):
                if self.params.debug:
                    self.save_samples_path = f"/tmp/tap-samples-{sample_i+1}.csv"

                tap = self.do_one_tap(
                    start_z=tap_start_z,
                    target_z=target_z,
                    tap_speed=tap_speed,
                    lift_speed=lift_speed,
                    tapcfg=tapcfg,
                )

                if write_every_tap_plot:
                    try:
                        self._write_tap_plot(tap, sample_i)
                    except Exception as e:
                        self._log_error(f"Failed to write tap plot: {e}")

                if tap.error:
                    if "too close to target z" in str(tap.error):
                        self._log_msg(f"Tap {sample_i+1}: failed: try lowering TARGET_Z by 0.100 (to {target_z - 0.100:.3f})")
                    else:
                        self._log_msg(f"Tap {sample_i+1}: failed ({tap.error})")
                    sample_err_count += 1
                    sample_last_err = tap
                    continue

                results.append(tap)

                self._log_msg(f"Tap {sample_i+1}: z={tap.probe_z:.3f}")
                self._log_debug(
                    f"tap[{sample_i+1}]: {tap.probe_z:.3f} toolhead at: {tap.toolhead_z:.3f} overshoot: {tap.overshoot:.3f} at {tap.tap_time:.4f}s"
                )

                if samples == 1:
                    # only one sample, we're done
                    tap_z = tap.probe_z
                    tap_stddev = 0.0
                    tap_overshoot = tap.overshoot
                    break

                if len(results) >= samples:
                    tap_z, tap_stddev, tap_overshoot = self._compute_tap_z(results, samples, samples_stddev, use_median)
                    if tap_z is not None:
                        break
        finally:
            self.reset_drive_current()
            if write_tap_plot and not write_every_tap_plot and tap:
                try:
                    self._write_tap_plot(tap)
                except Exception as e:
                    self._log_error(f"Failed to write tap plot: {e}")

        th = self._toolhead

        # If we didn't compute a tap_z report the error
        if tap_z is None:
            # raise toolhead on failed tap
            th.manual_move([None, None, tap_start_z], lift_speed)
            err_msg = "Tap failed:"
            if tap_stddev is not None:
                err_msg += f" stddev {tap_stddev:.3f} > {samples_stddev:.3f}."
                err_msg += " Consider adjusting tap_samples, tap_max_samples, or tap_samples_stddev."
            if sample_err_count > 0:
                err_msg += f" {sample_err_count} errors, last: {sample_last_err.error} at toolhead z={sample_last_err.toolhead_z:.3f}"
            self._log_error(err_msg)
            raise self._printer.command_error("Tap failed")

        # Adjust the computed tap_z by the user's tap_adjust_z, typically to raise
        # it to account for flex in the system (otherwise the Z would be too low)
        computed_tap_z = adjusted_tap_z = tap_z + tap_adjust_z
        self._last_tap_z = float(tap_z)

        homed_to_str = ""
        if home_z:
            th_pos = th.get_position()
            th_z = th_pos[2]
            #true_z_zero = - (tap_adjust_z + tap_overshoot)
            true_z_zero = - computed_tap_z
            th_pos[2] = th_pos[2] + true_z_zero
            homed_to_str = f"homed z with true_z_zero={true_z_zero:.3f}, thz={th_z:.3f}, setz={th_pos[2]:.3f}, overshoot={tap_overshoot:.3f}, "
            self._set_toolhead_position(th_pos, [2])
            self._last_tap_gcode_adjustment = 0.0
            adjusted_tap_z = 0.0

        gcode_move = self._printer.lookup_object("gcode_move")
        gcode_delta = adjusted_tap_z - gcode_move.homing_position[2]
        gcode_move.base_position[2] += gcode_delta
        gcode_move.homing_position[2] = adjusted_tap_z
        self._last_tap_gcode_adjustment = adjusted_tap_z

        #
        # Figure out the offset to apply to sensor readings at the home trigger height
        # for future probes.
        #
        # This is actually unrelated to tap, but is related to temperature compensation.
        # Bed mesh is going to read values relative to the probe's z_offset (home_trigger_height).
        # But we can't trust the probe's values directly, because of temperature effects.
        #
        # What we can do though is move the toolhead to that height, take a probe reading,
        # then save the delta there to apply as an offset for bed mesh in the future.
        # That makes this bed height effectively "0", which is fine, because this is
        # what we did tap at to get a height zero reading.
        #
        # Toolhead moves are absolute; they don't take into account the gcode offset.
        # Probes happen at absolute z=z_offset, so this doesn't take into account the
        # tap_z computed above. This does mean that the actual physical height probing happens at
        # is not likely to be exactly the same as the Z position, but all we care about is
        # variance from that position so this should be fine.
        self._sensor.set_drive_current(orig_drive_current)
        th_now = th.get_position()
        th.manual_move([None, None, self.params.home_trigger_height + 1.0], lift_speed)
        th.manual_move([th_now[0] - self.params.x_offset, th_now[1] - self.params.y_offset, None], self.params.move_speed)
        th.manual_move([None, None, self.params.home_trigger_height], self.params.probe_speed)
        th.dwell(0.500)
        th.wait_moves()

        result = self.probe_static_height()
        self._tap_offset = float(self.params.home_trigger_height - result.value)

        self._log_msg(
            f"Probe computed tap at {computed_tap_z:.3f} (tap at z={tap_z:.3f}, "
            f"stddev {tap_stddev:.3f}) with {samples} samples, {homed_to_str}"
            f"sensor offset {self._tap_offset:.3f} at z={self.params.home_trigger_height:.3f}"
        )

        if do_retract:
            th.manual_move([None, None, self._home_start_height], lift_speed)
            th.wait_moves()
            th.flush_step_generation()

        self._log_debug("EDDYng Tap end\n")

    #
    # Auto-threshold calibration
    #
    cmd_CALIBRATE_THRESHOLD_help = (
        "Automatically find the optimal tap threshold by testing ascending values. "
        "Parameters: START (initial threshold), MAX (maximum threshold), "
        "MODE (butter/wma), SPEED (tap speed), VERIFICATION_SAMPLES (number of verification taps)."
    )

    def cmd_CALIBRATE_THRESHOLD(self, gcmd: GCodeCommand):
        if not self._z_homed():
            raise self._printer.command_error("Z axis must be homed before threshold calibration")

        mode = gcmd.get("MODE", self.params.tap_mode).lower()
        if mode not in ("wma", "butter"):
            raise self._printer.command_error(f"Invalid mode: {mode}")

        # Default start/max depend on mode
        if mode == "butter":
            default_start = 50.0
            default_max = 2000.0
        else:
            default_start = 200.0
            default_max = 10000.0

        threshold_start = gcmd.get_float("START", default_start, above=0.0)
        threshold_max = gcmd.get_float("MAX", default_max, above=threshold_start)
        tap_speed = gcmd.get_float("SPEED", self.params.tap_speed, above=0.0)
        screening_samples = gcmd.get_int("SCREENING_SAMPLES", 5, minval=3)
        verification_samples = gcmd.get_int("VERIFICATION_SAMPLES", 10, minval=3, maxval=20)
        req_range = gcmd.get_float("SAMPLE_RANGE", 0.010, above=0.0)
        model_name = gcmd.get("MODEL", "default")

        drive_current = self._sensor.get_drive_current()
        try:
            result = self._find_optimal_threshold(
                mode=mode,
                threshold_start=threshold_start,
                threshold_max=threshold_max,
                tap_speed=tap_speed,
                screening_samples=screening_samples,
                verification_samples=verification_samples,
                req_range=req_range,
            )
        finally:
            self._sensor.set_drive_current(drive_current)

        if result is None:
            self._log_error(
                f"Threshold calibration failed: no reliable threshold found between "
                f"{threshold_start:.0f} and {threshold_max:.0f}. "
                "Try increasing MAX or adjusting your probe setup."
            )
            raise self._printer.command_error("Threshold calibration failed")

        threshold, verify_range, verify_median = result

        # Save the threshold to the config
        configfile = self._printer.lookup_object("configfile")
        configfile.set(self._full_name, "tap_threshold", f"{threshold:.1f}")
        configfile.set(self._full_name, "tap_mode", mode)
        self.params.tap_threshold = threshold
        self.params.tap_mode = mode

        self._log_msg(
            f"Threshold calibration complete!\n"
            f"  Mode: {mode}\n"
            f"  Optimal threshold: {threshold:.1f}\n"
            f"  Verification range: {verify_range:.4f}mm (over {verification_samples} taps)\n"
            f"  Verification median Z: {verify_median:.4f}mm\n"
            f"Run SAVE_CONFIG to persist this threshold."
        )

    @staticmethod
    def _calculate_threshold_step(threshold: float, range_value: float, req_range: float) -> float:
        """Calculate adaptive step size for threshold search."""
        MIN_STEP = 10.0
        MAX_STEP = 500.0
        if range_value is None or range_value > req_range * 10:
            # Far from target or unknown: take larger steps (20%)
            return min(MAX_STEP, max(MIN_STEP, threshold * 0.20))
        # Close to target: take smaller steps (10%)
        return min(MAX_STEP, max(MIN_STEP, threshold * 0.10))

    def _screen_threshold(
        self,
        threshold: float,
        mode: str,
        tap_speed: float,
        sample_count: int,
        req_range: float,
    ) -> Tuple[bool, Optional[float], List[float]]:
        """
        Quick screening of a threshold value.
        Returns (passed, best_range, samples).
        """
        tapcfg = self._build_tap_config(mode, threshold)
        lift_speed = self.params.lift_speed
        start_z = self.params.tap_start_z
        target_z = self.params.tap_target_z
        probe_zs = []

        for i in range(sample_count):
            tap = self.do_one_tap(
                start_z=start_z,
                target_z=target_z,
                tap_speed=tap_speed,
                lift_speed=lift_speed,
                tapcfg=tapcfg,
            )
            if tap.error:
                err_str = str(tap.error)
                if "prior to movement" in err_str or "too close to target" in err_str:
                    # Triggered too early - threshold too low
                    self._log_debug(f"  Screen {threshold:.0f}: tap {i+1} triggered early")
                    return False, None, []
                if "completed movement" in err_str:
                    # Didn't trigger - threshold might be too high
                    self._log_debug(f"  Screen {threshold:.0f}: tap {i+1} didn't trigger")
                    return False, None, []
                # Other error, count as noise
                self._log_debug(f"  Screen {threshold:.0f}: tap {i+1} error: {err_str}")
                continue

            probe_zs.append(tap.probe_z)

        if len(probe_zs) < 3:
            return False, None, probe_zs

        # Find best subset of 3 samples with smallest range
        best_range = math.inf
        req_samples = min(3, len(probe_zs))
        for combo in combinations(probe_zs, req_samples):
            r = max(combo) - min(combo)
            if r < best_range:
                best_range = r

        passed = best_range <= req_range
        self._log_debug(f"  Screen {threshold:.0f}: {len(probe_zs)} samples, best range: {best_range:.4f}, pass: {passed}")
        return passed, best_range, probe_zs

    def _verify_threshold(
        self,
        threshold: float,
        mode: str,
        tap_speed: float,
        verification_samples: int,
        req_range: float,
    ) -> Tuple[bool, float, float]:
        """
        Full verification of a threshold.
        Performs verification_samples complete tap sequences and checks
        that the range of median Z values is within tolerance.
        Returns (passed, median_range, median_z).
        """
        tapcfg = self._build_tap_config(mode, threshold)
        lift_speed = self.params.lift_speed
        start_z = self.params.tap_start_z
        target_z = self.params.tap_target_z
        medians = []
        max_verify_range = req_range * 2.0

        for i in range(verification_samples):
            tap = self.do_one_tap(
                start_z=start_z,
                target_z=target_z,
                tap_speed=tap_speed,
                lift_speed=lift_speed,
                tapcfg=tapcfg,
            )
            if tap.error:
                self._log_debug(f"  Verify {threshold:.0f}: tap {i+1} error: {tap.error}")
                continue

            medians.append(tap.probe_z)
            self._log_debug(f"  Verify {threshold:.0f}: tap {i+1}: z={tap.probe_z:.4f}")

            # Early exit: if range already too large after 2+ samples
            if len(medians) >= 2:
                current_range = max(medians) - min(medians)
                if current_range > max_verify_range:
                    self._log_debug(
                        f"  Verify {threshold:.0f}: early exit, range {current_range:.4f} > {max_verify_range:.4f}"
                    )
                    return False, current_range, 0.0

        if len(medians) < 3:
            return False, math.inf, 0.0

        median_range = max(medians) - min(medians)
        median_z = float(np.median(medians))
        passed = median_range <= max_verify_range

        self._log_debug(
            f"  Verify {threshold:.0f}: {len(medians)} samples, range: {median_range:.4f}, "
            f"median: {median_z:.4f}, pass: {passed}"
        )
        return passed, median_range, median_z

    def _build_tap_config(self, mode: str, threshold: float) -> 'ProbeEddy.TapConfig':
        """Build a TapConfig for the given mode and threshold."""
        tapcfg = ProbeEddy.TapConfig(mode=mode, threshold=threshold)
        if mode == "butter":
            if self.params.is_default_butter_config() and self._sensor._data_rate == 250:
                tapcfg.sos = [
                    [0.046131802093312926, 0.09226360418662585, 0.046131802093312926, 1.0, -1.3297767184682712, 0.5693902189294331],
                    [1.0, -2.0, 1.0, 1.0, -1.845000600983779, 0.8637525213328747],
                ]
            elif self.params.is_default_butter_config() and self._sensor._data_rate == 500:
                tapcfg.sos = [
                    [0.013359200027856505, 0.02671840005571301, 0.013359200027856505, 1.0, -1.686278256753083, 0.753714473246724],
                    [1.0, -2.0, 1.0, 1.0, -1.9250515947328444, 0.9299234737648037],
                ]
            elif scipy:
                tapcfg.sos = scipy.signal.butter(
                    self.params.tap_butter_order,
                    [self.params.tap_butter_lowcut, self.params.tap_butter_highcut],
                    btype="bandpass",
                    fs=self._sensor._data_rate,
                    output="sos",
                ).tolist()
            else:
                raise self._printer.command_error(
                    "Scipy is not available, cannot use custom filter, or data rate is not 250 or 500"
                )
        return tapcfg

    def _find_optimal_threshold(
        self,
        mode: str,
        threshold_start: float,
        threshold_max: float,
        tap_speed: float,
        screening_samples: int,
        verification_samples: int,
        req_range: float,
    ) -> Optional[Tuple[float, float, float]]:
        """
        Ascending threshold search with screening + verification phases.
        Returns (threshold, verify_range, verify_median) or None.
        """
        self._sensor.set_drive_current(self._tap_drive_current)

        threshold = threshold_start
        self._log_msg(
            f"Starting threshold calibration: {mode} mode, "
            f"range {threshold_start:.0f} to {threshold_max:.0f}"
        )

        while threshold <= threshold_max:
            self._log_msg(f"Testing threshold {threshold:.0f}...")

            # Phase 1: Screening
            passed, best_range, samples = self._screen_threshold(
                threshold=threshold,
                mode=mode,
                tap_speed=tap_speed,
                sample_count=screening_samples,
                req_range=req_range,
            )

            if not passed:
                step = self._calculate_threshold_step(threshold, best_range, req_range)
                self._log_msg(f"  Screening failed (range: {best_range if best_range else 'N/A'}), stepping by {step:.0f}")
                threshold += step
                continue

            # Phase 2: Verification
            self._log_msg(f"  Screening passed (range: {best_range:.4f}), running verification...")
            v_passed, v_range, v_median = self._verify_threshold(
                threshold=threshold,
                mode=mode,
                tap_speed=tap_speed,
                verification_samples=verification_samples,
                req_range=req_range,
            )

            if v_passed:
                self._log_msg(f"  Verification passed! Threshold {threshold:.0f} is reliable.")
                return (threshold, v_range, v_median)

            step = self._calculate_threshold_step(threshold, v_range, req_range)
            self._log_msg(f"  Verification failed (range: {v_range:.4f}), stepping by {step:.0f}")
            threshold += step

        return None

    # Compute the average tap_z from a set of tap results using a sliding window.
    # Only considers the most recent `window_size` samples to ensure temporal
    # consistency -- good samples must be clustered together in time, not
    # scattered across a noisy sequence.
    def _compute_tap_z(self, taps: List[ProbeEddy.TapResult], samples: int, req_stddev: float, use_median: bool) -> Tuple[float, float, float]:
        if len(taps) < samples:
            return None, None, None

        # Sliding window: only look at the most recent (samples + 2) results
        # to prevent cherry-picking from temporally scattered good samples.
        max_noisy_samples = 2
        window_size = samples + max_noisy_samples
        window = taps[-window_size:]

        tap_z = math.inf
        std_min = math.inf
        overshoot = math.inf
        for cluster in combinations(window, samples):
            tap_zs = np.array([t.probe_z for t in cluster])
            overshoots = np.array([t.overshoot for t in cluster])
            std = np.std(tap_zs)
            if std < std_min:
                std_min = std
                if use_median:
                    # we need the corresponding overshoot as well, so
                    # can't just use np.median().
                    sorted_indices = np.argsort(tap_zs)
                    idx = len(tap_zs) // 2
                    tap_z = tap_zs[sorted_indices[idx]]
                    overshoot = overshoots[sorted_indices[idx]]
                else:
                    tap_z = np.mean(tap_zs)
                    overshoot = np.mean(overshoots)

        if std_min <= req_stddev:
            return float(tap_z), float(std_min), float(overshoot)
        else:
            return None, float(std_min), None

    # Write a tap plot. This also has logic to compute the averages
    # and the filter mostly-exactly how it's done on the probe MCU itself
    # (vs using numpy or similar) to make these graphs more reprensetative
    def _write_tap_plot(self, tap: ProbeEddy.TapResult, tapnum: int = -1):
        if not plotly:
            return

        if tapnum == -1:
            filename_base = "tap"
        else:
            filename_base = f"tap-{tapnum+1}"
        tapplot_path_png = f"/tmp/{filename_base}.png"
        tapplot_path_html = f"/tmp/{filename_base}.html"

        # delete any old plots to avoid confusion
        if tapplot_path_html and os.path.exists(tapplot_path_html):
            os.remove(tapplot_path_html)
        if tapplot_path_png and os.path.exists(tapplot_path_png):
            os.remove(tapplot_path_png)

        if not self._last_sampler or not self._last_sampler.times:
            return

        s_t = np.asarray(self._last_sampler.times)
        s_f = np.asarray(self._last_sampler.freqs)
        s_z = np.asarray(self._last_sampler.heights)
        s_kinz = np.vectorize(lambda t: self._get_trapq_height(t) or -10)(s_t)

        # Any values below 0.0 are suspect because they were not calibrated,
        # and so are just extrapolated from the fit. Show them differently.
        s_lowz = np.ma.masked_where(s_z >= 0, s_z)
        s_z = np.ma.masked_where(s_z < 0, s_z)

        time_start = s_t.min()

        # normalize times to start at 0
        s_t = s_t - time_start
        tap_start_time = self._last_sampler.memos.get("tap_start_time", time_start) - time_start
        tap_end_time = self._last_sampler.memos.get("trigger_time", time_start) - time_start
        trigger_time = tap_start_time + (tap_end_time - tap_start_time) * self.params.tap_time_position
        tap_threshold = self._last_sampler.memos.get("tap_threshold", 0)

        time_len = s_t.max()

        # compute the butterworth filter, if we have scipy
        if tap is not None and scipy:
            butter_s_t, butter_s_v = self._compute_butter_tap(self._last_sampler)
            butter_s_t = butter_s_t - time_start
        else:
            butter_s_t = butter_s_v = None

        # Do this roughly how the C code does it, to keep the values identical
        # TODO Just report the value from the mcu?
        butter_accum = None
        if butter_s_v is not None:
            # Note: we don't handle freq offset or
            # start this at same point as the C code does
            butter_accum = np.zeros(len(butter_s_v))
            last_value = butter_s_v[0]
            falling = False
            accum_val = 0.0
            for bi, bv in enumerate(butter_s_v):
                if bv <= last_value:
                    falling = True
                    accum_val += last_value - bv
                elif falling and bv > last_value:
                    falling = False
                    accum_val = 0.0
                butter_accum[bi] = accum_val
                last_value = bv

        import plotly.graph_objects as go

        (c_red, c_lt_red) = ('#9e4058', '#C2697F')
        (c_orange, c_lt_orange) = ('#d0641e', '#E68E54')
        (c_yellow, c_lt_yellow) = ('#f9ab0e', '"#FBC559')
        (c_green, c_lt_green) = ('#589e40', '#7FC269')
        (c_blue, c_lt_blue) = ('#2c3778', '#4151B0')
        (c_purple, c_lt_purple) = ('#513965', '#785596')

        fig = go.Figure()

        # fmt: off
        if tap_start_time > 0:
            fig.add_shape(type="line", x0=tap_start_time, x1=tap_start_time, y0=0, y1=1,
                          xref="x", yref="paper", line=dict(color=c_purple, width=2),)
        if trigger_time > 0:
            fig.add_shape(type="line", x0=trigger_time, x1=trigger_time, y0=0, y1=1,
                          xref="x", yref="paper", line=dict(color=c_lt_orange, width=2),)
        if tap_end_time > 0:
            fig.add_shape(type="line", x0=tap_end_time, x1=tap_end_time, y0=0, y1=1,
                          xref="x", yref="paper", line=dict(color=c_purple, width=2),)
        if tap_threshold > 0:
            fig.add_shape(type="line", x0=0, x1=1, y0=tap_threshold, y1=tap_threshold,
                          xref="paper", yref="y3", line=dict(color="gray", width=1, dash="dash"),)

        fig.add_shape(type="line", x0=0, x1=1, y0=tap.probe_z, y1=tap.probe_z,
                      xref="paper", yref="y", line=dict(color=c_lt_orange, width=1),)

        # Computed Z, Toolhead Z, Sensor F
        fig.add_trace(go.Scatter(x=s_t, y=s_z, mode="lines", name="Z", line=dict(color=c_blue)))
        fig.add_trace(go.Scatter(x=s_t, y=s_lowz, mode="lines", name="Z (low)", line=dict(color=c_lt_blue, dash="dash")))
        fig.add_trace(go.Scatter(x=s_t, y=s_kinz, mode="lines", name="KinZ", line=dict(color=c_lt_red)))
        fig.add_trace(go.Scatter(x=s_t, y=s_f, mode="lines", name="Freq", yaxis="y2", line=dict(color=c_orange)))

        # the butter tap if we have the data
        if butter_s_t is not None:
            fig.add_trace(go.Scatter(x=butter_s_t, y=butter_s_v, mode="lines", name="signal", yaxis="y4", line=dict(color=c_green)))
            fig.add_trace(go.Scatter(x=butter_s_t, y=butter_accum, mode="lines", name="threshold", yaxis="y3", line=dict(color="#626b73")))

        fig.update_xaxes(range=[max(0.0, time_len - 0.60), time_len], autorange=False)

        fig.update_layout(
            hovermode="x unified",
            title=dict(text=f"Tap {tapnum+1}: {tap.probe_z:.3f}"),
            yaxis=dict(title="Z", side="right"),  # Z axis
            yaxis2=dict(overlaying="y", title="Freq", tickformat="d", side="left"),  # Freq + WMA
            yaxis3=dict(overlaying="y", side="left", tickformat="d", position=0.2),  # derivatives, tap accum
            yaxis4=dict(overlaying="y", side="right", showticklabels=False),  # filter
            height=800,
        )
        # fmt: on

        timg = 0.0
        thtml = 0.0
        if tapplot_path_png:
            t0 = time.time()
            try:
                fig.write_image(tapplot_path_png)
            except:
                tapplot_path_png = None
            timg = time.time() - t0
        if tapplot_path_html:
            t0 = time.time()
            fig.write_html(tapplot_path_html, include_plotlyjs="cdn")
            thtml = time.time() - t0
        self._log_info(f"Wrote tap plot to {tapplot_path_png or ''} {tapplot_path_html or ''}  [took {timg:.1f}, {thtml:.1f}]")

    def cmd_START_STREAM(self, gcmd):
        self.save_samples_path = "/tmp/stream.csv"
        self._log_info("Eddy sampling enabled")
        self.start_sampler()

    def cmd_STOP_STREAM(self, gcmd):
        self._log_info("Eddy sampling finished")
        self._sampler.finish()
        self._sampler = None

    # ─── New Features ────────────────────────────────────────────────────

    def cmd_STREAM(self, gcmd: GCodeCommand):
        """Manage data streaming sessions with CSV export."""
        action = gcmd.get("ACTION", "STATUS").lower()

        if action == "start":
            file_path = gcmd.get("FILE", None)
            self._streamer.start_session(file_path)
            self.start_sampler()
            self._log_msg(self._streamer.get_status())
        elif action == "stop":
            output = self._streamer.stop_session()
            if self._sampler:
                self._sampler.finish()
                self._sampler = None
            if output:
                self._log_msg(f"Stream saved to {output}")
            else:
                self._log_msg("No stream was active")
        elif action == "cancel":
            self._streamer.cancel_session()
            if self._sampler:
                self._sampler.finish()
                self._sampler = None
            self._log_msg("Stream cancelled")
        elif action == "status":
            self._log_msg(self._streamer.get_status())
        else:
            raise self._printer.command_error(
                f"Unknown ACTION '{action}'. Use START, STOP, CANCEL, or STATUS"
            )

    def cmd_ESTIMATE_BACKLASH(self, gcmd: GCodeCommand):
        """Estimate Z-axis backlash using Welch's t-test."""
        iterations = gcmd.get_int("ITERATIONS", 10, minval=3)
        delta = gcmd.get_float("DELTA", 0.5, minval=0.2, maxval=1.0)
        speed = gcmd.get_float("SPEED", self.params.probe_speed, above=0.0)
        calibrate = gcmd.get_int("CALIBRATE", 0)

        # Need calibration
        fmap = self._dc_to_fmap.get(self._reg_drive_current)
        if fmap is None or not fmap.calibrated():
            raise self._printer.command_error("Calibration required first")

        self._log_msg(f"Estimating backlash: {iterations} iterations, "
                      f"delta={delta:.2f} mm, speed={speed:.1f} mm/s")

        toolhead = self._toolhead
        height = self.params.home_trigger_height

        # Start sampler for height measurements
        self.start_sampler()
        sampler = self._sampler

        def measure_height():
            toolhead.dwell(0.150)
            toolhead.wait_moves()
            return sampler.get_height_now()

        def move_z(z, spd):
            toolhead.manual_move([None, None, z], spd)

        def wait():
            toolhead.wait_moves()

        try:
            result = estimate_backlash(
                measure_height_func=measure_height,
                move_func=move_z,
                wait_func=wait,
                height=height,
                delta=delta,
                iterations=iterations,
                speed=speed,
            )
        finally:
            sampler.finish()
            self._sampler = None

        self._log_msg(
            f"Backlash estimation:\n"
            f"  Mean (approach from below): {result.mean_up:.4f} mm "
            f"(std: {result.std_up:.4f})\n"
            f"  Mean (approach from above): {result.mean_down:.4f} mm "
            f"(std: {result.std_down:.4f})\n"
            f"  t-statistic: {result.t_stat:.3f} "
            f"(df: {result.degrees_of_freedom:.1f})\n"
            f"  Significant: {'YES' if result.significant else 'NO'}\n"
            f"  Backlash: {result.backlash:.4f} mm"
        )

        if calibrate and result.significant and result.backlash > 0:
            self.params.z_backlash = result.backlash
            configfile = self._printer.lookup_object("configfile")
            configfile.set(self._full_name, "z_backlash",
                           f"{result.backlash:.4f}")
            self._log_msg(
                f"z_backlash set to {result.backlash:.4f} mm. "
                "Use SAVE_CONFIG to persist."
            )

    def cmd_TEMPERATURE_CALIBRATE(self, gcmd: GCodeCommand):
        """Calibrate temperature compensation model.

        Heats the bed and collects frequency-temperature data at multiple
        heights to build a drift compensation model.
        """
        min_temp = gcmd.get_float("MIN_TEMP", 40.0, minval=30.0, maxval=50.0)
        max_temp = gcmd.get_float("MAX_TEMP", 60.0, minval=50.0)
        bed_temp = gcmd.get_float("BED_TEMP", 90.0, minval=80.0)
        use_hotend_fan = gcmd.get_int("HOTEND_FAN", 0) == 1
        heights = [1.0, 2.0, 3.0]

        if max_temp < min_temp + 15:
            raise self._printer.command_error(
                "MAX_TEMP must be at least MIN_TEMP + 15"
            )
        if bed_temp < max_temp + 20:
            raise self._printer.command_error(
                "BED_TEMP must be at least MAX_TEMP + 20"
            )

        fmap = self._dc_to_fmap.get(self._reg_drive_current)
        if fmap is None or not fmap.calibrated():
            raise self._printer.command_error("Calibration required first")

        toolhead = self._toolhead
        gcode = self._gcode
        reactor = self._reactor
        data_per_height: dict = {}

        # Move probe to bed center before starting.
        center_x, center_y = self._get_bed_center()
        self._log_msg(
            f"Moving probe to bed center ({center_x:.0f}, {center_y:.0f})"
        )
        toolhead.manual_move([center_x, center_y, None], self.params.move_speed)
        toolhead.wait_moves()

        self._log_msg(
            f"Temperature calibration: {min_temp:.0f}-{max_temp:.0f}C "
            f"at bed {bed_temp:.0f}C across {len(heights)} heights"
        )
        if use_hotend_fan:
            self._log_msg("Hotend fan cooling enabled (M104 S80 during cooldown)")
        self._log_msg("This will take a while. Do not touch the printer.")

        for h_idx, height in enumerate(heights):
            self._log_msg(f"\n--- Phase {h_idx + 1}/{len(heights)}: "
                          f"height {height:.0f} mm ---")

            # Cooldown phase — raise probe high for faster cooling
            cooldown_z = max(height, 15.0)
            self._log_msg(f"Cooling down (Z={cooldown_z:.0f}mm)...")
            gcode.run_script_from_command("M140 S0")     # bed off
            gcode.run_script_from_command("M106 S255")   # part fan on
            if use_hotend_fan:
                gcode.run_script_from_command("M104 S80")  # trigger heater_fan
            toolhead.manual_move([None, None, cooldown_z], self.params.lift_speed)
            toolhead.wait_moves()

            # Wait for cooldown (tolerance +2C, longer timeout for large beds)
            self._wait_for_temperature(min_temp + 2, direction="cool", timeout=1200.0)

            # Heatup phase — lower to measurement height
            self._log_msg(f"Heating bed to {bed_temp:.0f}C...")
            gcode.run_script_from_command(f"M140 S{bed_temp:.0f}")
            gcode.run_script_from_command("M106 S0")   # part fan off
            if use_hotend_fan:
                gcode.run_script_from_command("M104 S0")  # hotend off -> heater_fan off
            toolhead.manual_move([None, None, height], self.params.lift_speed)
            toolhead.wait_moves()

            # Collect samples during heatup
            samples = []
            self.start_sampler()
            sampler = self._sampler

            try:
                self._wait_for_temperature(min_temp - 1, direction="heat")

                self._log_msg(f"Collecting data {min_temp:.0f}-{max_temp:.0f}C...")
                last_log = time.time()

                while True:
                    reactor.pause(reactor.monotonic() + 0.25)
                    # Read current sensor value
                    freq = sampler.get_last_freq()
                    temp = self._get_coil_temperature()

                    if freq is not None and temp is not None:
                        samples.append((freq, temp))

                    if temp is not None and temp >= max_temp:
                        break

                    if time.time() - last_log > 30.0:
                        self._log_msg(f"  Temp: {temp:.1f}C, "
                                      f"{len(samples)} samples")
                        last_log = time.time()
            finally:
                sampler.finish()
                self._sampler = None

            self._log_msg(f"  Collected {len(samples)} samples at "
                          f"height {height:.0f} mm")
            data_per_height[height] = samples

        # Turn off heaters and fans
        gcode.run_script_from_command("M140 S0")
        gcode.run_script_from_command("M104 S0")
        gcode.run_script_from_command("M106 S0")

        # Fit model
        self._log_msg("Fitting temperature compensation model...")
        ref_freq = fmap.get_reference_frequency()
        ref_temp = self._get_coil_temperature() or 25.0

        coeff = fit_temperature_model(data_per_height, ref_freq, ref_temp)
        if coeff is None:
            raise self._printer.command_error(
                "Temperature model fitting failed. "
                "Check logs for details."
            )

        # Save
        self._temp_comp = TemperatureCompensationModel(coeff)
        configfile = self._printer.lookup_object("configfile")
        save_temp_comp_to_config(configfile, self._full_name, coeff)
        self._log_msg(
            "Temperature compensation model saved.\n"
            "Use SAVE_CONFIG to persist."
        )

    def cmd_AXIS_TWIST_CALIBRATE(self, gcmd: GCodeCommand):
        """Calibrate axis twist compensation using tap.

        Taps at multiple points along X or Y axis to measure Z variation
        caused by gantry twist, then saves the compensation values to
        [axis_twist_compensation].

        AXIS=X (default): probes along X at constant Y
        AXIS=Y: probes along Y at constant X
        AXIS=BOTH: runs X first, then Y, saves both
        """
        if not self._z_homed():
            raise self._printer.command_error("Must home all axes first (G28)")

        axis = gcmd.get("AXIS", "X").upper()
        if axis not in ("X", "Y", "BOTH"):
            raise self._printer.command_error(
                f"AXIS must be X, Y, or BOTH (got: {axis})"
            )
        sample_count = gcmd.get_int("SAMPLE_COUNT", 7, minval=3, maxval=20)
        samples_per_point = gcmd.get_int("SAMPLES", self.params.tap_samples, minval=1)
        bed_temp = gcmd.get_float("BED_TEMP", 0.0, minval=0.0)
        hotend_temp = gcmd.get_float("HOTEND_TEMP", 0.0, minval=0.0)

        gcode = self._gcode

        # Heat to print temperature if requested
        if bed_temp > 0 or hotend_temp > 0:
            self._log_msg("Heating to print temperature for axis twist calibration...")
            if bed_temp > 0:
                self._log_msg(f"  Bed: {bed_temp:.0f}C")
                gcode.run_script_from_command(f"M190 S{bed_temp:.0f}")
            if hotend_temp > 0:
                if hotend_temp > 170:
                    self._log_msg(
                        f"  WARNING: Hotend {hotend_temp:.0f}C may cause ooze. "
                        f"Consider using 150C max."
                    )
                self._log_msg(f"  Hotend: {hotend_temp:.0f}C")
                gcode.run_script_from_command(f"M109 S{hotend_temp:.0f}")
            # Thermal soak — let frame expand consistently
            self._log_msg("Thermal soak (60s)...")
            self._reactor.pause(self._reactor.monotonic() + 60.0)
            self._log_msg("Thermal soak complete, starting calibration.")

        axes_to_run = ["X", "Y"] if axis == "BOTH" else [axis]
        all_results = {}

        for current_axis in axes_to_run:
            result = self._run_axis_twist_for_axis(
                gcmd, current_axis, sample_count, samples_per_point
            )
            all_results[current_axis] = result

        # Save to config
        configfile = self._printer.lookup_object("configfile")

        if "X" in all_results:
            r = all_results["X"]
            comp_str = ", ".join(f"{c:.6f}" for c in r["compensations"])
            configfile.set("axis_twist_compensation", "z_compensations", comp_str)
            configfile.set("axis_twist_compensation",
                           "calibrate_start_x", f"{r['start']:.1f}")
            configfile.set("axis_twist_compensation",
                           "calibrate_end_x", f"{r['end']:.1f}")
            configfile.set("axis_twist_compensation",
                           "calibrate_y", f"{r['fixed_pos']:.1f}")
            configfile.set("axis_twist_compensation",
                           "compensation_start_x", f"{r['start']:.1f}")
            configfile.set("axis_twist_compensation",
                           "compensation_end_x", f"{r['end']:.1f}")

        if "Y" in all_results:
            r = all_results["Y"]
            comp_str = ", ".join(f"{c:.6f}" for c in r["compensations"])
            configfile.set("axis_twist_compensation",
                           "zy_compensations", comp_str)
            configfile.set("axis_twist_compensation",
                           "calibrate_start_y", f"{r['start']:.1f}")
            configfile.set("axis_twist_compensation",
                           "calibrate_end_y", f"{r['end']:.1f}")
            configfile.set("axis_twist_compensation",
                           "calibrate_x", f"{r['fixed_pos']:.1f}")
            configfile.set("axis_twist_compensation",
                           "compensation_start_y", f"{r['start']:.1f}")
            configfile.set("axis_twist_compensation",
                           "compensation_end_y", f"{r['end']:.1f}")

        # Turn off heaters if we heated
        if bed_temp > 0 or hotend_temp > 0:
            gcode.run_script_from_command("M140 S0")
            gcode.run_script_from_command("M104 S0")
            self._log_msg("Heaters turned off.")

        self._log_msg(
            "\nAxis twist compensation saved.\n"
            "Use SAVE_CONFIG to persist."
        )

    def _run_axis_twist_for_axis(
        self, gcmd: GCodeCommand, axis: str,
        sample_count: int, samples_per_point: int
    ) -> dict:
        """Run axis twist calibration for a single axis (X or Y)."""
        center_x, center_y = self._get_bed_center()

        # Determine start/end and fixed position for this axis
        start = end = fixed_pos = None

        # Try to read from [axis_twist_compensation] config
        try:
            atc = self._printer.lookup_object("axis_twist_compensation", None)
            if atc is not None:
                if axis == "X":
                    if hasattr(atc, 'calibrate_start_x'):
                        start = atc.calibrate_start_x
                    if hasattr(atc, 'calibrate_end_x'):
                        end = atc.calibrate_end_x
                    if hasattr(atc, 'calibrate_y'):
                        fixed_pos = atc.calibrate_y
                else:
                    if hasattr(atc, 'calibrate_start_y'):
                        start = atc.calibrate_start_y
                    if hasattr(atc, 'calibrate_end_y'):
                        end = atc.calibrate_end_y
                    if hasattr(atc, 'calibrate_x'):
                        fixed_pos = atc.calibrate_x
        except Exception:
            pass

        # Fall back to bed_mesh boundaries
        if start is None or end is None:
            try:
                bm = self._printer.lookup_object("bed_mesh")
                bmc = bm.bmc
                if hasattr(bmc, 'mesh_min') and hasattr(bmc, 'mesh_max'):
                    idx = 0 if axis == "X" else 1
                    start = start or bmc.mesh_min[idx]
                    end = end or bmc.mesh_max[idx]
            except Exception:
                pass

        # Fixed position defaults to bed center on the other axis
        if fixed_pos is None:
            fixed_pos = center_y if axis == "X" else center_x

        # Fall back to kinematics range
        if start is None or end is None:
            th = self._printer.lookup_object("toolhead")
            kin = th.get_kinematics()
            rail_idx = 0 if axis == "X" else 1
            rail_range = kin.rails[rail_idx].get_range()
            start = start or (rail_range[0] + 20.0)
            end = end or (rail_range[1] - 20.0)

        # Allow GCode parameter overrides
        if axis == "X":
            start = gcmd.get_float("START_X", start)
            end = gcmd.get_float("END_X", end)
            fixed_pos = gcmd.get_float("Y", fixed_pos)
        else:
            start = gcmd.get_float("START_Y", start)
            end = gcmd.get_float("END_Y", end)
            fixed_pos = gcmd.get_float("X", fixed_pos)

        if end <= start:
            raise self._printer.command_error(
                f"END_{axis} ({end}) must be greater than START_{axis} ({start})"
            )

        # Generate evenly spaced positions
        interval = (end - start) / (sample_count - 1)
        points = [start + i * interval for i in range(sample_count)]

        fixed_label = "Y" if axis == "X" else "X"
        self._log_msg(
            f"\n{'='*50}\n"
            f"Axis twist calibration ({axis}): {sample_count} points from "
            f"{axis}={start:.0f} to {axis}={end:.0f} at "
            f"{fixed_label}={fixed_pos:.0f}"
        )

        toolhead = self._toolhead
        tap_results = []

        # Build tap config once
        tapcfg = ProbeEddy.TapConfig(
            mode=self.params.tap_mode,
            threshold=self.params.tap_threshold,
        )
        if tapcfg.mode == "butter":
            if self.params.is_default_butter_config() and self._sensor._data_rate == 250:
                tapcfg.sos = [
                    [0.046131802093312926, 0.09226360418662585, 0.046131802093312926, 1.0, -1.3297767184682712, 0.5693902189294331],
                    [1.0, -2.0, 1.0, 1.0, -1.845000600983779, 0.8637525213328747],
                ]
            elif self.params.is_default_butter_config() and self._sensor._data_rate == 500:
                tapcfg.sos = [
                    [0.013359200027856505, 0.02671840005571301, 0.013359200027856505, 1.0, -1.686278256753083, 0.753714473246724],
                    [1.0, -2.0, 1.0, 1.0, -1.9250515947328444, 0.9299234737648037],
                ]
            elif scipy:
                tapcfg.sos = scipy.signal.butter(
                    self.params.tap_butter_order,
                    [self.params.tap_butter_lowcut, self.params.tap_butter_highcut],
                    btype="bandpass",
                    fs=self._sensor._data_rate,
                    output="sos",
                ).tolist()

        orig_drive_current = self._sensor.get_drive_current()
        try:
            self._sensor.set_drive_current(self._tap_drive_current)

            for i, pos in enumerate(points):
                nozzle_x = pos if axis == "X" else fixed_pos
                nozzle_y = fixed_pos if axis == "X" else pos

                self._log_msg(
                    f"Point {i+1}/{sample_count}: {axis}={pos:.1f} "
                    f"(nozzle at {nozzle_x:.1f}, {nozzle_y:.1f})"
                )

                toolhead.manual_move(
                    [nozzle_x, nozzle_y, self.params.tap_start_z + 2.0],
                    self.params.move_speed
                )
                toolhead.wait_moves()

                point_results = []
                max_attempts = samples_per_point + 2

                for attempt in range(max_attempts):
                    tap = self.do_one_tap(
                        start_z=self.params.tap_start_z,
                        target_z=self.params.tap_target_z,
                        tap_speed=self.params.tap_speed,
                        lift_speed=self.params.lift_speed,
                        tapcfg=tapcfg,
                    )
                    if tap.error:
                        self._log_msg(
                            f"  Tap attempt {attempt+1} failed: {tap.error}"
                        )
                        continue
                    point_results.append(tap.probe_z)
                    if len(point_results) >= samples_per_point:
                        break

                if len(point_results) < 1:
                    raise self._printer.command_error(
                        f"All tap attempts failed at point {i+1} "
                        f"({axis}={pos:.1f})"
                    )

                median_z = float(np.median(point_results))
                stddev = (float(np.std(point_results))
                          if len(point_results) > 1 else 0.0)
                self._log_msg(
                    f"  Result: Z={median_z:.4f} (stddev={stddev:.4f}, "
                    f"{len(point_results)} samples)"
                )
                tap_results.append(median_z)

        finally:
            self._sensor.set_drive_current(orig_drive_current)
            self._endstop_wrapper.tap_config = None
            toolhead.manual_move(
                [None, None, self.params.tap_start_z + 5.0],
                self.params.lift_speed
            )

        # Compute compensations: normalize to average
        avg_z = float(np.mean(tap_results))
        compensations = [avg_z - z for z in tap_results]

        self._log_msg(f"\nAxis twist compensation results ({axis}):")
        for i, (pos, comp) in enumerate(zip(points, compensations)):
            self._log_msg(f"  {axis}={pos:.0f}: {comp:+.4f} mm")

        total_twist = max(compensations) - min(compensations)
        self._log_msg(f"Total {axis} twist: {total_twist:.4f} mm")

        return {
            "axis": axis,
            "start": start,
            "end": end,
            "fixed_pos": fixed_pos,
            "compensations": compensations,
            "total_twist": total_twist,
        }

    def _wait_for_temperature(self, target: float, direction: str = "heat",
                              timeout: float = 600.0):
        """Wait for coil temperature to reach target."""
        reactor = self._reactor
        start = time.time()
        none_count = 0
        while True:
            reactor.pause(reactor.monotonic() + 2.0)
            temp = self._get_coil_temperature()
            if temp is None:
                none_count += 1
                if none_count > 30:
                    raise self._printer.command_error(
                        "Temperature sensor not available. "
                        "Check [temperature_sensor] configuration."
                    )
                continue

            none_count = 0
            self._log_debug(
                f"_wait_for_temperature: {temp:.1f}C "
                f"(target: {target:.0f}C {direction})"
            )

            if direction == "heat" and temp >= target:
                return
            if direction == "cool" and temp <= target:
                return

            if time.time() - start > timeout:
                raise self._printer.command_error(
                    f"Temperature timeout: wanted {target:.0f}C "
                    f"({direction}), currently {temp:.1f}C"
                )

    def _get_coil_temperature(self) -> Optional[float]:
        """Get current bed temperature for temperature calibration.

        Tries heater_bed first, then falls back to any temperature_sensor
        objects that might provide bed temperature.
        """
        eventtime = self._reactor.monotonic()
        # Try heater_bed
        try:
            heater = self._printer.lookup_object("heater_bed", None)
            if heater is not None:
                temp, _ = heater.get_temp(eventtime)
                if temp is not None and temp > 0:
                    return float(temp)
        except Exception as e:
            logging.debug(f"_get_coil_temperature heater_bed failed: {e}")

        # Try heaters module (covers renamed heaters)
        try:
            pheaters = self._printer.lookup_object("heaters", None)
            if pheaters is not None:
                for name, heater in pheaters.heaters.items():
                    if "bed" in name.lower():
                        temp, _ = heater.get_temp(eventtime)
                        if temp is not None and temp > 0:
                            return float(temp)
        except Exception as e:
            logging.debug(f"_get_coil_temperature heaters failed: {e}")

        return None

    def cmd_MODEL(self, gcmd: GCodeCommand):
        """Manage named calibration models.

        ACTION=SAVE NAME=<name>  - Save current calibration as named model
        ACTION=LOAD NAME=<name>  - Load a named model as active calibration
        ACTION=LIST              - List all saved model names
        ACTION=DELETE NAME=<name> - Delete a named model
        """
        action = gcmd.get("ACTION", "LIST").upper()
        name = gcmd.get("NAME", "")

        fmap = self._dc_to_fmap.get(self._reg_drive_current)

        if action == "LIST":
            if fmap is None:
                self._log_msg("No calibration loaded, no models available")
                return
            models = fmap.get_model_names()
            if not models:
                self._log_msg("No saved models")
            else:
                self._log_msg(f"Saved models: {', '.join(models)}")
            return

        if not name:
            raise self._printer.command_error(
                "NAME parameter required for SAVE/LOAD/DELETE"
            )

        if action == "SAVE":
            if fmap is None or not fmap.calibrated():
                raise self._printer.command_error(
                    "No active calibration to save"
                )
            fmap.save_calibration(model_name=name)
            self._log_msg(
                f"Saved current calibration as model '{name}'. "
                "Use SAVE_CONFIG to persist."
            )

        elif action == "LOAD":
            if fmap is None:
                fmap = ProbeEddyFrequencyMap(self)
            if not fmap.load_named_model(name):
                raise self._printer.command_error(
                    f"Model '{name}' not found"
                )
            self._dc_to_fmap[fmap.drive_current] = fmap
            self._log_msg(f"Loaded model '{name}'")

        elif action == "DELETE":
            if fmap is None:
                raise self._printer.command_error("No calibration loaded")
            if not fmap.delete_named_model(name):
                raise self._printer.command_error(
                    f"Model '{name}' not found"
                )
            self._log_msg(
                f"Deleted model '{name}'. "
                "Use SAVE_CONFIG to persist."
            )

        else:
            raise self._printer.command_error(
                f"Unknown ACTION '{action}'. "
                "Use SAVE, LOAD, LIST, or DELETE."
            )


@final
class ProbeEddyHomingSession:
    """Probe session for G28 Z homing using endstop-based descent.

    The scanning probe requires valid sensor readings to start, but the
    LDC1612 returns ERR_AHE when the gantry is far from the bed (>~2.5mm).
    During G28 Z the gantry may be at any height, so this session uses
    the endstop-based approach which handles ERR_AHE gracefully via
    _probe_to_start_position_unhomed on the MCU side.
    """

    def __init__(self, eddy: ProbeEddy, gcmd: GCodeCommand):
        self.eddy = eddy
        self._printer = eddy._printer
        self._toolhead = self._printer.lookup_object("toolhead")
        self._results = []

    def _start_session(self):
        pass  # Endstop homing handles positioning in _handle_homing_move_begin

    def get_probe_params(self, gcmd):
        return {
            "lift_speed": self.eddy.params.lift_speed,
            "probe_speed": self.eddy.params.probe_speed,
        }

    def run_probe(self, gcmd):
        speed = gcmd.get_float("PROBE_SPEED", self.eddy.params.probe_speed)
        pos = self._toolhead.get_position()
        kin = self._toolhead.get_kinematics()
        z_min = kin.limits[2][0]
        if z_min >= pos[2]:
            # limits not set properly — use rail range as fallback
            z_min = kin.rails[2].get_range()[0]
        pos[2] = z_min

        logging.info(
            "ProbeEddyHomingSession: run_probe speed=%.1f target_z=%.1f",
            speed, pos[2],
        )

        phoming = self._printer.lookup_object("homing")
        epos = phoming.probing_move(
            self.eddy._endstop_wrapper, pos, speed, check_movement=False
        )

        offsets = self.eddy.get_offsets()
        if HAS_PROBE_RESULT_TYPE:
            res = manual_probe.create_probe_result(epos, offsets)
        else:
            res = [
                epos[0] + offsets[0],
                epos[1] + offsets[1],
                epos[2] - offsets[2],
            ]
        self._results.append(res)

        logging.info(
            "ProbeEddyHomingSession: trigger at Z=%.3f, bed_z=%.3f",
            epos[2], res.bed_z if HAS_PROBE_RESULT_TYPE else res[2],
        )

    def pull_probed_results(self):
        res = self._results
        self._results = []
        return res

    def end_probe_session(self):
        pass


# Probe interface that does only scanning, no up/down movement.
# It scans at whatever height the probe is, but returns values
# as if the probing happened (i.e. relative to
# z_offset/home_trigger_height).
@final
class ProbeEddyScanningProbe:
    def __init__(self, eddy: ProbeEddy, gcmd: GCodeCommand):
        self.eddy = eddy
        self._printer = eddy._printer
        self._toolhead = self._printer.lookup_object("toolhead")
        self._toolhead_kin = self._toolhead.get_kinematics()

        # we're going to scan at this height; pull_probed_results
        # also expects to return values based on this height
        self._scan_z = eddy.params.home_trigger_height

        # sensor thinks is _home_trigger_height vs. what it actually is.
        # For example, if we do a tap, adjust, and then we move the toolhead up
        # to 2.0 but the sensor says 1.950, then this would be +0.050.
        self._tap_offset = eddy._tap_offset

        # how much to dwell at each sample position in addition to sample_time
        self._sample_time_delay = self.eddy.params.scan_sample_time_delay
        self._sample_time: float = gcmd.get_float("SAMPLE_TIME", self.eddy.params.scan_sample_time, above=0.0)
        self._is_rapid = gcmd.get("METHOD", "automatic").lower() == "rapid_scan"

        self._sampler: ProbeEddySampler = None

        self._notes = []

    def get_probe_params(self, gcmd):
        # this seems to be all that external users of get_probe_params
        # use (bed_mesh, axis_twist_compensation)
        return {
            "lift_speed": self.eddy.params.lift_speed,
            "probe_speed": self.eddy.params.probe_speed,
        }

    def _start_session(self):
        if not self.eddy._z_homed():
            raise self._printer.command_error("Z axis must be homed before probing")

        self.eddy.probe_to_start_position()
        self._sampler = self.eddy.start_sampler()

        # Wait for the first sample to arrive from the MCU before returning.
        # Without this, QGL may call run_probe() immediately and record a
        # print_time for which no sensor data exists yet, causing
        # "No samples received" errors.
        th = self._printer.lookup_object("toolhead")
        th.dwell(0.100)
        self._sampler.wait_for_sample_at_time(
            th.get_last_move_time(), max_wait_time=2.0)

    def end_probe_session(self):
        self._sampler.finish()
        self._sampler = None

    def _rapid_lookahead_cb(self, time, th_pos):
        # The time passed here is the time when the move finishes;
        # but this is super obnoxious because we don't get any info
        # here about _where_ the move is to. So we explicitly pass
        # in the last position in run_probe
        start_time = time - self._sample_time / 2.0
        self._notes.append([start_time, time, th_pos])

    def run_probe(self, gcmd, *args: Any, **kwargs: Any):
        th = self._toolhead
        th_pos = th.get_position()

        if self._is_rapid:
            # this callback is attached to the last move in the queue, so that
            # we can grab the toolhead position when the toolhead actually hits it

            self._toolhead.register_lookahead_callback(lambda time: self._rapid_lookahead_cb(time, th_pos))
            return

        th.dwell(self._sample_time_delay)
        start_time = th.get_last_move_time()
        self._toolhead.dwell(self._sample_time + self._sample_time_delay)
        self._notes.append((start_time, start_time + self._sample_time / 2.0, th_pos))

    def pull_probed_results(self):
        if self._is_rapid:
            # Flush lookahead (so all lookahead callbacks are invoked)
            self._toolhead.get_last_move_time()

        # make sure we get the sample for the final move
        self._sampler.wait_for_sample_at_time(self._notes[-1][0] + self._sample_time)

        # note: we can't call finish() here! this session can continue to be used
        # to probe additional points and pull them, because that's what QGL does.

        results = []

        logging.info(f"ProbeEddyScanningProbe: pulling {len(self._notes)} results")
        for start_time, sample_time, th_pos in self._notes:
            if th_pos is None:
                th_pos, _ = self.eddy._get_trapq_position(sample_time)
                if th_pos is None:
                    raise self._printer.command_error(f"No trapq history found for {sample_time:.3f} and no position!")

            end_time = start_time + self._sample_time
            height = self._sampler.find_height_at_time(start_time, end_time)

            if not math.isclose(th_pos[2], self._scan_z, rel_tol=1e-3):
                logging.info(
                    f"ProbeEddyScanningProbe warning: toolhead not at home_trigger_height ({self._scan_z:.3f}) during probes (saw {th_pos[2]:.3f})"
                )

            h_orig = height
            tz_orig = th_pos[2]

            # adjust the sensor height value based on the fine-tuned tap offset amount
            height += self._tap_offset

            # the delta between where the toolhead thinks it should be (since it
            # should be homed), and the actual physical offset (height)
            z_deviation = th_pos[2] - height

            # what callers want to know is "what Z would the toolhead be at, if it was at the height
            # the probe would 'trigger'", because this is all done in terms of klicky-type probes
            z = float(self._scan_z + z_deviation)

            if HAS_PROBE_RESULT_TYPE:
                bed_x = th_pos[0] + self.eddy.params.x_offset
                bed_y = th_pos[1] + self.eddy.params.y_offset
                res = manual_probe.ProbeResult(bed_x, bed_y, z_deviation,
                                               th_pos[0], th_pos[1], th_pos[2])
                self._printer.send_event("probe:update_results", [res])
            else:
                res = [th_pos[0], th_pos[1], z]
                self._printer.send_event("probe:update_results", res)

            results.append(res)

        # reset notes so that this session can continue to be used
        self._notes = []

        return results


# This is a ProbeEndstopWrapper-compatible class,
# which also forwards the "mcu_probe" methods.
@final
class ProbeEddyEndstopWrapper:
    REASON_BASE = mcu.MCU_trsync.REASON_COMMS_TIMEOUT + 1
    REASON_ERROR_SENSOR = REASON_BASE + 0
    REASON_ERROR_PROBE_TOO_LOW = REASON_BASE + 1
    REASON_ERROR_TOO_EARLY = REASON_BASE + 2

    def __init__(self, eddy: ProbeEddy):
        self.eddy = eddy
        self._sensor = eddy._sensor
        self._printer = eddy._printer
        self._mcu = eddy._mcu
        self._reactor = eddy._reactor

        # these two are filled in by the outside.
        self.tap_config: Optional[ProbeEddy.TapConfig] = None
        # if not None, after a probe session is finished we'll
        # write all samples here
        self.save_samples_path: Optional[str] = None

        self._multi_probe_in_progress = False

        self._dispatch = mcu.TriggerDispatch(self._mcu)

        # the times of the last successful endstop home_wait
        self.last_trigger_time = 0.0
        self.last_tap_start_time = 0.0

        self._homing_in_progress = False
        self._sampler: ProbeEddySampler = None

        # Register z_virtual_endstop pin (skip if already registered by tool_probe_endstop)
        try:
            self._printer.lookup_object("pins").register_chip("probe_eddy", self)
        except:
            pass
        # Register event handlers
        self._printer.register_event_handler("klippy:mcu_identify", self._handle_mcu_identify)
        self._printer.register_event_handler("homing:homing_move_begin", self._handle_homing_move_begin)
        self._printer.register_event_handler("homing:homing_move_end", self._handle_homing_move_end)
        self._printer.register_event_handler("homing:home_rails_begin", self._handle_home_rails_begin)
        self._printer.register_event_handler("homing:home_rails_end", self._handle_home_rails_end)
        self._printer.register_event_handler("gcode:command_error", self._handle_command_error)

        # copy some things in for convenience
        self._home_trigger_height = self.eddy.params.home_trigger_height
        self._home_trigger_safe_start_offset = self.eddy.params.home_trigger_safe_start_offset
        self._home_start_height = self.eddy._home_start_height  # this is trigger + safe_start + 1.0
        self._probe_speed = self.eddy.params.probe_speed
        self._lift_speed = self.eddy.params.lift_speed

    def _handle_mcu_identify(self):
        kin = self._printer.lookup_object("toolhead").get_kinematics()
        for stepper in kin.get_steppers():
            if stepper.is_active_axis("z"):
                self.add_stepper(stepper)

    def _handle_home_rails_begin(self, homing_state, rails):
        endstops = [es for rail in rails for es, name in rail.get_endstops()]
        if self not in endstops:
            return
        # Nothing to do
        pass

    def _handle_homing_move_begin(self, hmove):
        if self not in hmove.get_mcu_endstops():
            return
        self._sampler = self.eddy.start_sampler()
        self._homing_in_progress = True
        # if we're doing a tap, we're already in the right position;
        # otherwise move there
        if self.tap_config is None:
            self.eddy._probe_to_start_position_unhomed(move_home=True)

    def _handle_homing_move_end(self, hmove):
        if self not in hmove.get_mcu_endstops():
            return
        self._sampler.finish()
        self._homing_in_progress = False

    def _handle_home_rails_end(self, homing_state, rails):
        endstops = [es for rail in rails for es, name in rail.get_endstops()]
        if self not in endstops:
            return
        # Nothing to do
        pass

    def _handle_command_error(self, gcmd=None):
        if self._homing_in_progress:
            self._homing_in_progress = False
        try:
            if self._sampler is not None:
                self._sampler.finish()
        except:
            logging.exception("EDDYng handle_command_error: sampler.finish() failed")

    def setup_pin(self, pin_type, pin_params):
        if pin_type != "endstop" or pin_params["pin"] != "z_virtual_endstop":
            raise pins.error("Probe virtual endstop only useful as endstop pin")
        if pin_params["invert"] or pin_params["pullup"]:
            raise pins.error("Can not pullup/invert probe virtual endstop")
        return self

    # these are the "MCU Probe" methods
    def get_mcu(self):
        return self._mcu

    def add_stepper(self, stepper):
        self._dispatch.add_stepper(stepper)

    def get_steppers(self):
        return self._dispatch.get_steppers()

    def get_position_endstop(self):
        if self.tap_config is None:
            return self._home_trigger_height
        else:
            return 0.0

    def home_start(self, print_time, sample_time, sample_count, rest_time, triggered=True):
        if not self._sampler.active():
            raise self._printer.command_error("home_start called without a sampler active")

        self.last_trigger_time = 0.0
        self.last_tap_start_time = 0.0

        trigger_height = self._home_trigger_height
        safe_height = trigger_height + self._home_trigger_safe_start_offset

        if self.tap_config is None:
            safe_time = print_time + self.eddy.params.home_trigger_safe_time_offset
            trigger_freq = self.eddy.height_to_freq(trigger_height)
            safe_freq = self.eddy.height_to_freq(safe_height)
        else:
            # TODO: the home trigger safe time won't work, because we'll pass
            # the home_trigger_height maybe by default given where tap might
            # start
            safe_time = 0
            # initial freq to pass through
            safe_freq = self.eddy.height_to_freq(self._home_trigger_height)
            # second freq to pass through; toolhead acceleration
            # must be smooth after this point
            trigger_freq = self.eddy.height_to_freq(self.eddy.params.tap_trigger_safe_start_height)

        trigger_completion = self._dispatch.start(print_time)

        if self.tap_config is not None:
            if self.tap_config.mode == "butter":
                sos = self.tap_config.sos
                assert sos
                for i in range(len(sos)):
                    self.eddy._sensor.set_sos_section(i, sos[i])
                mode = "sos"
            elif self.tap_config.mode == "wma":
                mode = "wma"
            else:
                raise self._printer.command_error(f"Invalid tap mode: {self.tap_config.mode}")
            tap_threshold = self.tap_config.threshold
        else:
            mode = "home"
            tap_threshold = None

        self.eddy._log_debug(
            f"EDDYng home_start {mode}: {print_time:.3f} freq: {trigger_freq:.2f} safe-start: {safe_freq:.2f} @ {safe_time:.3f}"
        )
        # setup homing -- will start scanning and trigger when we hit
        # trigger_freq
        self._sensor.setup_home(
            self._dispatch.get_oid(),
            mcu.MCU_trsync.REASON_ENDSTOP_HIT,
            self.REASON_BASE,
            trigger_freq,
            safe_freq,
            safe_time,
            mode=mode,
            tap_threshold=tap_threshold,
            max_errors=self.eddy.params.max_errors,
        )

        return trigger_completion

    def home_wait(self, home_end_time):
        self.eddy._log_debug(f"home_wait until {home_end_time:.3f}")
        # logging.info(f"EDDYng home_wait {home_end_time} cur {curtime} ept {est_print_time} ehe {est_he_time}")
        self._dispatch.wait_end(home_end_time)

        # make sure homing is stopped, and grab the trigger_time from the mcu
        home_result = self._sensor.finish_home()
        trigger_time = home_result.trigger_time
        tap_start_time = home_result.tap_start_time
        error = self._sensor.data_error_to_str(home_result.error) if home_result.error != 0 else ""

        is_tap = self.tap_config is not None

        self._sampler.memo("trigger_time", trigger_time)
        if is_tap:
            self._sampler.memo("tap_start_time", tap_start_time)
            self._sampler.memo("tap_threshold", self.tap_config.threshold)

        self.eddy._log_debug(
            f"trigger_time {trigger_time} (mcu: {self._mcu.print_time_to_clock(trigger_time)}) tap time: {tap_start_time}-{trigger_time} {error}"
        )

        # nb: _dispatch.stop() will treat anything >= REASON_COMMS_TIMEOUT as an error,
        # and will only return those results. Fine for us since we only have one trsync,
        # but annoying in general.
        res = self._dispatch.stop()

        # clean these up, and only update them if successful
        self.last_trigger_time = 0.0
        self.last_tap_start_time = 0.0

        # always reset this; taps are one-shot usages of the endstop wrapper
        self.tap_config = None

        # if we're doing a tap, we wait for samples for the end as well so that we can get
        # beter data for analysis
        self._sampler.wait_for_sample_at_time(trigger_time)

        # success?
        if res == mcu.MCU_trsync.REASON_ENDSTOP_HIT:
            self.last_trigger_time = trigger_time
            self.last_tap_start_time = tap_start_time
            if is_tap:
                return tap_start_time + (trigger_time - tap_start_time) * self.eddy.params.tap_time_position
            return trigger_time

        # various errors
        if res == mcu.MCU_trsync.REASON_COMMS_TIMEOUT:
            raise self._printer.command_error("Communication timeout during homing")
        if res == self.REASON_ERROR_SENSOR:
            raise self._printer.command_error(f"Sensor error ({error})")
        if res == self.REASON_ERROR_PROBE_TOO_LOW:
            raise self._printer.command_error("Probe too low at start of homing, did not clear safe height.")
        if res == self.REASON_ERROR_TOO_EARLY:
            raise self._printer.command_error("Probe cleared safe height too early.")
        if res == mcu.MCU_trsync.REASON_PAST_END_TIME:
            raise self._printer.command_error(
                "Probe completed movement before triggering. If this is a tap, try lowering TARGET_Z or adjusting the THRESHOLD."
            )

        raise self._printer.command_error(f"Unknown homing error: {res}")

    def query_endstop(self, print_time):
        return False

    def _setup_sampler(self):
        self._sampler = self.eddy.start_sampler()

    def _finish_sampler(self):
        self._sampler.finish()
        self._sampler = None


# Helper to gather samples and convert them to probe positions
@final
class ProbeEddySampler:
    def __init__(
        self,
        eddy: ProbeEddy,
        calculate_heights: bool = True,
    ):
        self.eddy = eddy
        self._sensor = eddy._sensor
        self._printer = self.eddy._printer
        self._reactor = self._printer.get_reactor()
        self._mcu = self._sensor.get_mcu()
        self._stopped = False
        self._started = False
        self._errors = 0
        self._fmap = eddy.map_for_drive_current() if calculate_heights else None

        self.times = []
        self.raw_freqs = []
        self.freqs = []
        self.heights = [] if self._fmap is not None else None

        self.memos = dict()

    @property
    def raw_count(self):
        return len(self.times)

    @property
    def height_count(self):
        return len(self.heights) if self.heights else 0

    # this is just a handy way to communicate values between different parts of the system,
    # specifically to record things like trigger times for plotting
    def memo(self, name, value):
        self.memos[name] = value

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self.finish()

    def active(self):
        return self._started and not self._stopped

    # bulk sample callback for when new data arrives
    # from the probe
    def _add_hw_measurement(self, msg):
        if self._stopped:
            return False

        self._errors += msg["errors"]
        data = msg["data"]

        # data is (t, fv)
        if data:
            times, raw_freqs = zip(*data)
        else:
            times, raw_freqs = [], []

        self.times.extend(times)
        self.raw_freqs.extend(raw_freqs)

        return True

    def start(self):
        if self._stopped:
            raise self._printer.command_error("ProbeEddySampler.start() called after finish()")
        if not self._started:
            self._sensor.add_bulk_sensor_data_client(self._add_hw_measurement)
            self._started = True

    def finish(self):
        if self._stopped:
            return
        if not self._started:
            raise self._printer.command_error("ProbeEddySampler.finish() called without start()")
        if self.eddy._sampler is not self:
            raise self._printer.command_error("ProbeEddySampler.finish(): eddy._sampler is not us!")
        self._update_samples()
        self.eddy._sampler_finished(self)
        self._stopped = True

    def _update_samples(self):
        if len(self.freqs) == len(self.raw_freqs):
            return

        conv_ratio = self._sensor.freqval_conversion_value()

        start_idx = len(self.freqs)
        freqs_np = np.asarray(self.raw_freqs[start_idx:]) * conv_ratio
        self.freqs.extend(freqs_np.tolist())

        if self._fmap is not None:
            heights_np = self._fmap.freqs_to_heights_np(freqs_np)
            self.heights.extend(heights_np.tolist())

    @property
    def error_count(self):
        return self._errors

    def get_last_freq(self) -> Optional[float]:
        """Get the last sampled frequency, or None if no samples yet."""
        self._update_samples()
        if len(self.freqs) == 0:
            return None
        return self.freqs[-1]

    # get the last sampled height
    def get_last_height(self) -> float:
        if self.heights is None:
            raise self._printer.command_error("ProbeEddySampler: no height mapping")
        self._update_samples()
        if len(self.heights) == 0:
            raise self._printer.command_error("ProbeEddySampler: no samples")
        return self.heights[-1]

    # wait for a sample for the current time and get a new height
    def get_height_now(self) -> Optional[float]:
        now = self.eddy._print_time_now()
        if not self.wait_for_sample_at_time(now, max_wait_time=1.000, raise_error=False):
            return None
        return self.get_last_height()

    # Wait until a sample for the given time arrives
    def wait_for_sample_at_time(self, sample_print_time, max_wait_time=0.250, raise_error=True) -> bool:
        def report_no_samples():
            if raise_error:
                raise self._printer.command_error(f"No samples received for time {sample_print_time:.3f} (waited for {max_wait_time:.3f})")
            return False

        if self._stopped:
            # if we're not getting any more samples, we can check directly
            if len(self.times) == 0:
                return report_no_samples()
            return self.times[-1] >= sample_print_time

        # quick check
        if self.times and self.times[-1] >= sample_print_time:
            return True

        wait_start_time = self.eddy._print_time_now()

        # if sample_print_time is in the future, make sure to wait max_wait_time
        # past the expected time
        if sample_print_time > wait_start_time:
            max_wait_time = max_wait_time + (sample_print_time - wait_start_time)

        # this is just a sanity check, there shouldn't be any reason to ever wait this long
        if max_wait_time > 30.0:
            traceback.print_stack()
            msg = f"ProbeEddyFrequencySampler: max_wait_time {max_wait_time:.3f} is too far into the future"
            raise self._printer.command_error(msg)

        self.eddy._log_debug(
            f"EDDYng waiting for sample at {sample_print_time:.3f} (now: {wait_start_time:.3f}, max_wait_time: {max_wait_time:.3f})"
        )
        now = self.eddy._print_time_now()
        while len(self.times) == 0 or self.times[-1] < sample_print_time:
            now = self.eddy._print_time_now()
            if now - wait_start_time > max_wait_time:
                return report_no_samples()
            self._reactor.pause(self._reactor.monotonic() + 0.010)

        if now - wait_start_time > 1.0:
            self.eddy._log_info(f"note: waited {now - wait_start_time:.3f}s for sample")

        return True

    # Wait for some samples to be collected, even if errors
    # TODO: there's a minimum wait time -- we need to fill up the buffer before data is sent, and that
    # depends on the data rate
    def wait_for_samples(
        self,
        max_wait_time=0.300,
        count_errors=False,
        min_samples=1,
        new_only=False,
        raise_error=True,
    ):
        # Make sure enough samples have been collected
        wait_start_time = self.eddy._print_time_now()

        start_error_count = self._errors
        start_count = 0
        if new_only:
            start_count = len(self.raw_freqs) + (self._errors if count_errors else 0)

        while (len(self.raw_freqs) + (self._errors if count_errors else 0)) - start_count < min_samples:
            now = self.eddy._print_time_now()
            if now - wait_start_time > max_wait_time:
                if raise_error:
                    raise self._printer.command_error(
                        f"probe_eddy_ng sensor outage: no samples for {max_wait_time:.2f}s (got {self._errors - start_error_count} errors)"
                    )
                return False
            self._reactor.pause(self._reactor.monotonic() + 0.010)

        return True

    def find_heights_at_times(self, intervals):
        self._update_samples()
        times = self.times
        heights = np.asarray(self.heights)
        num_samples = len(times)

        interval_heights = []
        i = 0
        for iv_start, iv_end in intervals:
            while i < num_samples and times[i] < iv_start:
                i += 1
            istart = i

            while i < num_samples and times[i] < iv_end:
                i += 1
            iend = i

            if istart == iend:
                # no samples in this range
                raise self._printer.command_error(f"No samples in time range {iv_start}-{iv_end}")

            median = np.median(heights[istart:iend])
            interval_heights.append(float(median))

        return interval_heights

    def find_height_at_time(self, start_time, end_time):
        if end_time < start_time:
            raise self._printer.command_error("find_height_at_time: end_time is before start_time")

        self._update_samples()

        if len(self.times) == 0:
            raise self._printer.command_error("No samples at all, so none in time range")

        if not self.heights:
            raise self._printer.command_error("Update samples didn't compute heights")

        self.eddy._log_debug(
                f"find_height_at_time: looking between {start_time:.3f}s-{end_time:.3f}s, inside {len(self.times)} samples, time range {self.times[0]:.3f}s to {self.times[-1]:.3f}s"
        )

        # find the first sample that is >= start_time
        start_idx = bisect.bisect_left(self.times, start_time)
        if start_idx >= len(self.times):
            raise self._printer.command_error("Nothing after start_time?")

        # find the last sample that is < end_time
        end_idx = start_idx
        while end_idx < len(self.times) and self.times[end_idx] < end_time:
            end_idx += 1

        # average the heights of the samples in the range
        heights = self.heights[start_idx:end_idx]
        if len(heights) == 0:
            raise self._printer.command_error(f"no samples between time {start_time:.1f} and {end_time:.1f}!")
        hmin, hmax = np.min(heights), np.max(heights)
        mean = np.mean(heights)
        median = np.median(heights)
        self.eddy._log_debug(
            f"find_height_at_time: {len(heights)} samples, median: {median:.3f}, mean: {mean:.3f} (range {hmin:.3f}-{hmax:.3f})"
        )

        return float(median)


@final
class ProbeEddyFrequencyMap:
    calibration_version = 5
    low_z_threshold = 5.0

    def __init__(self, eddy: ProbeEddy):
        self._eddy = eddy
        self._sensor = eddy._sensor

        self.drive_current = 0
        self.height_range = (math.inf, -math.inf)
        self.freq_range = (math.inf, -math.inf)
        self._ftoh: Optional[npp.Polynomial] = None
        self._ftoh_high: Optional[npp.Polynomial] = None
        self._htof: Optional[npp.Polynomial] = None

    def _str_to_exact_floatlist(self, str):
        return [float.fromhex(v) for v in str.split(",")]

    def _exact_floatlist_to_str(self, vals):
        return str.join(", ", [float.hex(v) for v in vals])

    def _coefs_to_str(self, coefs):
        return ", ".join([format(c, ".3f") for c in coefs])

    def freq_spread(self) -> float:
        return ((self.freq_range[1] / self.freq_range[0]) - 1.0) * 100.0

    @staticmethod
    def _poly_to_json(poly):
        """Serialize a numpy Polynomial to a JSON-safe dict."""
        if poly is None:
            return None
        return {
            "coef": [float.hex(float(c)) for c in poly.coef],
            "domain": [float.hex(float(d)) for d in poly.domain],
            "window": [float.hex(float(w)) for w in poly.window],
        }

    @staticmethod
    def _poly_from_json(d):
        """Deserialize a numpy Polynomial from a JSON dict."""
        if d is None:
            return None
        coef = [float.fromhex(c) for c in d["coef"]]
        domain = [float.fromhex(v) for v in d["domain"]]
        window = [float.fromhex(v) for v in d["window"]]
        return npp.Polynomial(coef, domain=domain, window=window)

    def _load_from_pickle(self, calibstr, drive_current):
        """Legacy loader for old pickle-based calibration data."""
        try:
            data = pickle.loads(base64.b64decode(calibstr))
        except Exception:
            return False
        v = data.get("v", None)
        if v is None or v < self.calibration_version:
            self._eddy._log_info(f"Calibration for dc {drive_current} is old ({v}), needs recalibration")
            return False
        self._ftoh = data.get("ftoh", None)
        self._ftoh_high = data.get("ftoh_high", None)
        self._htof = data.get("htof", None)
        self.height_range = data.get("h_range", (math.inf, -math.inf))
        self.freq_range = data.get("f_range", (math.inf, -math.inf))
        self.drive_current = drive_current
        return True

    def _load_from_json(self, calibstr, drive_current):
        """Load calibration from JSON format."""
        try:
            data = json.loads(calibstr)
        except (json.JSONDecodeError, ValueError):
            return False
        v = data.get("v", None)
        if v is None or v < self.calibration_version:
            self._eddy._log_info(f"Calibration for dc {drive_current} is old ({v}), needs recalibration")
            return False
        dc = data.get("dc", None)
        if dc != drive_current:
            raise configerror(f"ProbeEddyFrequencyMap: drive current mismatch: loaded {dc} != requested {drive_current}")
        self._ftoh = self._poly_from_json(data.get("ftoh"))
        self._ftoh_high = self._poly_from_json(data.get("ftoh_high"))
        self._htof = self._poly_from_json(data.get("htof"))
        h_range = data.get("h_range", [math.inf, -math.inf])
        f_range = data.get("f_range", [math.inf, -math.inf])
        self.height_range = (h_range[0], h_range[1])
        self.freq_range = (f_range[0], f_range[1])
        self.drive_current = drive_current
        return True

    def load_from_config(self, config: ConfigWrapper, drive_current: int):
        calibstr = config.get(f"calibration_{drive_current}", None)
        if calibstr is None:
            self.drive_current = 0
            self._ftoh = None
            self._htof = None
            self.height_range = (math.inf, -math.inf)
            self.freq_range = (math.inf, -math.inf)
            return

        # Try JSON first, fall back to legacy pickle
        calibstr_stripped = calibstr.strip()
        if calibstr_stripped.startswith("{"):
            loaded = self._load_from_json(calibstr_stripped, drive_current)
        else:
            loaded = self._load_from_pickle(calibstr_stripped, drive_current)
            if loaded:
                self._eddy._log_info(
                    f"Loaded legacy pickle calibration for dc {drive_current}. "
                    "Run SAVE_CONFIG to convert to new JSON format."
                )

        if not loaded:
            return False

        self._eddy._log_info(f"Loaded calibration for drive current {drive_current}")
        return True

    def save_calibration(self, model_name: Optional[str] = None):
        if self._ftoh is None or self._htof is None:
            return

        configfile = self._eddy._printer.lookup_object("configfile")
        data = {
            "v": self.calibration_version,
            "dc": self.drive_current,
            "h_range": [self.height_range[0], self.height_range[1]],
            "f_range": [self.freq_range[0], self.freq_range[1]],
            "ftoh": self._poly_to_json(self._ftoh),
            "ftoh_high": self._poly_to_json(self._ftoh_high),
            "htof": self._poly_to_json(self._htof),
        }
        calibstr = json.dumps(data, separators=(",", ":"))
        configfile.set(self._eddy._full_name, f"calibration_{self.drive_current}", calibstr)

        # Also save as named model if requested
        if model_name is not None:
            configfile.set(self._eddy._full_name, f"model_{model_name}", calibstr)
            # Update saved model list
            models = self._get_saved_model_names(configfile)
            if model_name not in models:
                models.append(model_name)
                configfile.set(
                    self._eddy._full_name,
                    "saved_models",
                    ",".join(models),
                )

    def _get_saved_model_names(self, configfile=None) -> List[str]:
        """Get list of saved named model names from autosave config."""
        if configfile is None:
            configfile = self._eddy._printer.lookup_object("configfile")
        asfc = configfile.autosave.fileconfig
        models_str = asfc.get(self._eddy._full_name, "saved_models", fallback="")
        if not models_str:
            return []
        return [m.strip() for m in models_str.split(",") if m.strip()]

    def get_model_names(self) -> List[str]:
        """Return list of all saved model names."""
        return self._get_saved_model_names()

    def load_named_model(self, model_name: str) -> bool:
        """Load a named calibration model."""
        configfile = self._eddy._printer.lookup_object("configfile")
        asfc = configfile.autosave.fileconfig
        calibstr = asfc.get(self._eddy._full_name, f"model_{model_name}", fallback=None)
        if calibstr is None:
            return False
        calibstr = calibstr.strip()
        if not calibstr.startswith("{"):
            return False
        try:
            data = json.loads(calibstr)
        except (json.JSONDecodeError, ValueError):
            return False
        dc = data.get("dc", self.drive_current)
        return self._load_from_json(calibstr, dc)

    def delete_named_model(self, model_name: str) -> bool:
        """Delete a named calibration model."""
        configfile = self._eddy._printer.lookup_object("configfile")
        models = self._get_saved_model_names(configfile)
        if model_name not in models:
            return False
        models.remove(model_name)
        configfile.set(
            self._eddy._full_name,
            "saved_models",
            ",".join(models) if models else "",
        )
        # Clear the model data by setting to empty
        configfile.set(self._eddy._full_name, f"model_{model_name}", "")
        return True

    def get_reference_frequency(self) -> float:
        """Return the frequency corresponding to height=0 (bed surface).

        Used as reference point for temperature compensation calibration.
        """
        if self._htof is None:
            raise self._eddy._printer.command_error(
                "Calling get_reference_frequency on uncalibrated map"
            )
        return self.height_to_freq(0.0)

    def calibrate_from_values(
        self,
        drive_current: int,
        raw_times: List[float],
        raw_freqs_list: List[float],
        raw_heights_list: List[float],
        raw_vels_list: List[float],
        report_errors: bool,
        write_debug_files: bool,
    ):
        if len(raw_freqs_list) != len(raw_heights_list):
            raise ValueError("freqs and heights must be the same length")

        if len(raw_freqs_list) == 0:
            self._eddy._log_info("calibrate_from_values: empty list")
            return None, None

        # everything must be a np.array or things get confused below
        times = np.asarray(raw_times)
        freqs = np.asarray(raw_freqs_list)
        heights = np.asarray(raw_heights_list)
        vels = np.asarray(raw_vels_list) if raw_vels_list else None

        if write_debug_files:
            with open("/tmp/eddy-calibration.csv", "w") as data_file:
                data_file.write("time,frequency,avg_freq,z,avg_z,v\n")
                for i in range(len(freqs)):
                    s_t = times[i]
                    s_f = freqs[i]
                    s_z = heights[i]
                    s_v = vels[i] if vels is not None else 0.0
                    data_file.write(f"{s_t},{s_f},{s_z},,{s_v}\n")
                self._eddy._log_info(f"Wrote {len(freqs)} samples to /tmp/eddy-calibration.csv")

        if len(freqs) == 0 or len(heights) == 0:
            if report_errors:
                self._eddy._log_error(
                    f"Drive current {drive_current}: Calibration failed, couldn't compute averages ({len(raw_freqs_list)}, {len(raw_heights_list)}), probably due to no valid samples received."
                )
            return None, None

        max_height = float(heights.max())
        min_height = float(heights.min())
        min_freq = float(freqs.min())
        max_freq = float(freqs.max())
        freq_spread = ((max_freq / min_freq) - 1.0) * 100.0

        # Check if our calibration is good enough
        if report_errors:
            if max_height < 2.5:  # we really can't do anything with this
                self._eddy._log_error(
                    f"Drive current {drive_current} error: max height for valid samples is too low: {max_height:.3f} < 2.5. Possible causes: bad drive current, bad sensor mount height."
                )
                if not self._eddy.params.allow_unsafe:
                    return None, None

            if min_height > 0.65:  # this is a bit arbitrary; but if it's this far off we shouldn't trust it
                self._eddy._log_error(
                    f"Drive current {drive_current} error: min height for valid samples is too high: {min_height:.3f} > 0.65. Possible causes: bad drive current, bad sensor mount height."
                )
                if not self._eddy.params.allow_unsafe:
                    return None, None

            if min_height > 0.025:
                self._eddy._log_msg(
                    f"Drive current {drive_current} warning: min height is {min_height:.3f} (> 0.025) is too high for tap. This calibration will work fine for homing, but may not for tap."
                )

            # somewhat arbitrary spread
            if freq_spread < 0.30:
                extremely = "EXTREMELY " if freq_spread < 0.15 else ""
                self._eddy._log_warning(
                    f"Drive current {drive_current} warning: frequency spread is {extremely}low ({freq_spread:.2f}%, {min_freq:.1f}-{max_freq:.1f}), which will greatly impact accuracy. Your sensor may be too high."
                )

        low_samples = heights <= ProbeEddyFrequencyMap.low_z_threshold
        high_samples = heights >= ProbeEddyFrequencyMap.low_z_threshold - 0.5

        ftoh_low_fn = npp.Polynomial.fit(1.0 / freqs[low_samples], heights[low_samples], deg=9)
        htof_low_fn = npp.Polynomial.fit(heights[low_samples], 1.0 / freqs[low_samples], deg=9)

        if np.count_nonzero(high_samples) > 50:
            ftoh_high_fn = npp.Polynomial.fit(1.0 / freqs[high_samples], heights[high_samples], deg=9)
        else:
            self._eddy._log_debug(f"not computing ftoh_high, not enough high samples")
            ftoh_high_fn = None

        # Calculate rms, only for the low values (where error is most relevant)
        rmse_fth = np_rmse(
            ftoh_low_fn,
            1.0 / freqs[low_samples],
            heights[low_samples],
        )
        rmse_htf = np_rmse(
            htof_low_fn,
            heights[low_samples],
            1.0 / freqs[low_samples],
        )

        if report_errors:
            if rmse_fth > 0.050:
                self._eddy._log_error(
                    f"Drive current {drive_current} error: calibration error margin is too high ({rmse_fth:.3f}). Possible causes: bad drive current, bad sensor mount height."
                )
                if not self._eddy.params.allow_unsafe:
                    return None, None

        self._ftoh = ftoh_low_fn
        self._htof = htof_low_fn
        self._ftoh_high = ftoh_high_fn
        self.drive_current = drive_current
        self.height_range = [min_height, max_height]
        self.freq_range = [min_freq, max_freq]

        self._eddy._log_msg(
            f"Drive current {drive_current}: valid height: {min_height:.3f} to {max_height:.3f}, "
            f"freq spread {freq_spread:.2f}% ({min_freq:.1f} - {max_freq:.1f}), "
            f"Fit {rmse_fth:.4f} ({rmse_htf:.2f})"
        )

        if write_debug_files:
            self._write_calibration_plot(
                times,
                freqs,
                heights,
                rmse_fth,
                rmse_htf,
                vels=vels,
            )

        return rmse_fth, rmse_htf

    def _write_calibration_plot(
        self,
        times,
        freqs,
        heights,
        rmse_fth,
        rmse_htf,
        vels=None,
    ):
        if not plotly:
            return

        if self._ftoh is None or self._htof is None:
            logging.warning(f"write_calibration_plot: null calibration?")
            return

        import plotly.graph_objects as go

        low_samples = heights <= ProbeEddyFrequencyMap.low_z_threshold
        high_samples = heights >= ProbeEddyFrequencyMap.low_z_threshold - 0.5

        f_to_z_low_err = heights[low_samples] - self._ftoh(1.0 / freqs[low_samples])

        if self._ftoh_high is not None:
            f_to_z_high_err = heights[high_samples] - self._ftoh_high(1.0 / freqs[high_samples])
        else:
            f_to_z_high_err = None

        fig = go.Figure()
        fig.add_trace(go.Scatter(x=times, y=heights, mode="lines", name="Z"))

        fig.add_trace(
            go.Scatter(
                x=times[low_samples],
                y=self._ftoh(1.0 / freqs[low_samples]),
                mode="lines",
                name=f"Z {rmse_fth:.4f}",
            )
        )

        if self._ftoh_high is not None:
            fig.add_trace(
                go.Scatter(
                    x=times[high_samples],
                    y=self._ftoh_high(1.0 / freqs[high_samples]),
                    mode="lines",
                    name=f"Z (high)",
                )
            )

        fig.add_trace(go.Scatter(x=times, y=freqs, mode="lines", name="F", yaxis="y2"))

        fig.add_trace(
            go.Scatter(
                x=times[low_samples],
                y=1.0 / self._htof(heights[low_samples]),
                mode="lines",
                name=f"F ({rmse_htf:.2f})",
                yaxis="y2",
            )
        )

        fig.add_trace(
            go.Scatter(
                x=times[low_samples],
                y=f_to_z_low_err,
                mode="lines",
                name="Err",
                yaxis="y3",
            )
        )
        if f_to_z_high_err is not None:
            fig.add_trace(
                go.Scatter(
                    x=times[high_samples],
                    y=f_to_z_high_err,
                    mode="lines",
                    name="Err (high)",
                    yaxis="y3",
                )
            )

        if vels is not None:
            fig.add_trace(go.Scatter(x=times, y=vels, mode="lines", name="V", yaxis="y4"))

        fig.update_layout(
            hovermode="x unified",
            title=f"Calibration for drive current {self.drive_current}",
            yaxis2=dict(title="Freq", overlaying="y", tickformat="d", side="right"),
            yaxis3=dict(overlaying="y", side="right", position=0.1),
            yaxis4=dict(overlaying="y", side="right", position=0.2),
        )
        fig.write_html("/tmp/eddy-calibration.html")

    def freq_to_height(self, freq: float) -> float:
        if self._ftoh is None:
            raise self._eddy._printer.command_error("Calling freq_to_height on uncalibrated map")
        invfreq = 1.0 / freq
        if self._ftoh_high is not None and invfreq < self._ftoh.domain[0]:
            return float(self._ftoh_high(invfreq))
        return float(self._ftoh(invfreq))

    def freqs_to_heights_np(self, freqs: np.array) -> np.array:
        if self._ftoh is None:
            raise self._eddy._printer.command_error("Calling freqs_to_heights on uncalibrated map")
        invfreqs = 1.0 / freqs
        if self._ftoh_high is not None:
            heights = np.zeros(len(invfreqs))
            low_freq_vals = invfreqs > self._ftoh.domain[1]
            heights[low_freq_vals] = np.vectorize(self._ftoh_high, otypes=[float])(invfreqs[low_freq_vals])
            heights[~low_freq_vals] = np.vectorize(self._ftoh, otypes=[float])(invfreqs[~low_freq_vals])
        else:
            heights = self._ftoh(invfreqs)
        return heights

    def height_to_freq(self, height: float) -> float:
        if self._htof is None:
            raise self._eddy._printer.command_error("Calling height_to_freq on uncalibrated map")
        return 1.0 / float(self._htof(height))

    def calibrated(self) -> bool:
        return self._ftoh is not None and self._htof is not None


@final
class BedMeshScanHelper:
    def __init__(self, eddy, config):
        self._eddy = eddy
        self._printer = eddy._printer

        bmc = config.getsection("bed_mesh")
        self._bed_mesh = eddy._printer.load_object(bmc, "bed_mesh")
        self._x_points, self._y_points = bmc.getintlist("probe_count", count=2, note_valid=False)
        self._x_min, self._y_min = bmc.getfloatlist("mesh_min", count=2, note_valid=False)
        self._x_max, self._y_max = bmc.getfloatlist("mesh_max", count=2, note_valid=False)
        self._speed = bmc.getfloat("speed", 100.0, above=0.0, note_valid=False)
        self._scan_z = bmc.getfloat("horizontal_move_z", self._eddy.params.home_trigger_height, above=0.0, note_valid=False)

        self._x_offset = self._eddy.params.x_offset
        self._y_offset = self._eddy.params.y_offset

        self._mesh_points, self._mesh_path = self._generate_path()


    def _generate_path(self):
        x_vals = np.linspace(self._x_min, self._x_max, self._x_points)
        y_vals = np.linspace(self._y_min, self._y_max, self._y_points)
        path = []
        reverse = False

        for y in y_vals:
            row = [(x, y, True) for x in (reversed(x_vals) if reverse else x_vals)]
            path.extend(row)
            reverse = not reverse
        return path, path

    def _scan_path(self):
        th = self._eddy._toolhead
        times = []

        for pt in self._mesh_path:
            # TODO bounds
            th.manual_move([pt[0] - self._x_offset, pt[1] - self._y_offset, None], self._speed)
            th.register_lookahead_callback(lambda t: times.append(t))

        th.wait_moves()

        return times

    def _set_bed_mesh(self, heights):
        # heights is in the order of the _mesh_path points; convert to
        # be ordered min_y..max_y, min_x..max_x, then pull out the heights
        indexed_points = []
        i = 0
        for x, y, include in self._mesh_path:
            if not include:
                continue
            indexed_points.append((x, y, i))
            i += 1

        def sort_points(a, b):
            if a[1] < b[1]: # y first
                return -1
            if a[1] > b[1]:
                return 1
            if a[0] < b[0]: # then x
                return -1
            if a[0] > b[0]:
                return 1
            return 0

        indices = [ki for _, _, ki in sorted(indexed_points, key=cmp_to_key(sort_points))]

        ki = 0
        matrix = []
        for _ in range(self._y_points):
            row = []
            for _ in range(self._x_points):
                v = heights[indices[ki]]
                row.append(self._scan_z - v)
                ki += 1
            matrix.append(row)

        params = self._bed_mesh.bmc.mesh_config.copy()
        params.update({
            "min_x": self._x_min,
            "max_x": self._x_max,
            "min_y": self._y_min,
            "max_y": self._y_max,
            "x_count": self._x_points,
            "y_count": self._y_points,
        })
        mesh = bed_mesh.ZMesh(params, None)
        try:
            mesh.build_mesh(matrix)
        except bed_mesh.BedMeshError as e:
            raise self._printer.command_error(str(e))
        self._bed_mesh.set_mesh(mesh)
        self._eddy._log_msg("Mesh scan complete")

    def scan(self):
        th = self._eddy._toolhead

        # move to the start point
        v = self._mesh_path[0]
        th.manual_move([None, None, 10.0], self._eddy.params.lift_speed)
        th.manual_move([v[0] - self._x_offset, v[1] - self._y_offset, None], self._speed)
        th.manual_move([None, None, self._scan_z], self._eddy.params.probe_speed)
        th.wait_moves()

        heights = []

        sample_time = self._eddy.params.scan_sample_time

        with self._eddy.start_sampler() as sampler:
            path_times = self._scan_path()
            sampler.wait_for_sample_at_time(path_times[-1] + sample_time*2.)
            sampler.finish()

            heights = sampler.find_heights_at_times([(t - sample_time/2., t + sample_time/2.) for t in path_times])
            # Note plus tap_offset here, vs -tap_offset when probing. These are actual
            # heights, the other is "offset from real"
            heights = [h + self._eddy._tap_offset for h in heights]

            with open("/tmp/mesh.csv", "w") as mfile:
                mfile.write("time,x,y,z\n")
                for i in range(len(self._mesh_points)):
                    t = path_times[i]
                    x = self._mesh_points[i][0]
                    y = self._mesh_points[i][1]
                    z = heights[i]
                    mfile.write(f"{t},{x},{y},{z}\n")

            self._set_bed_mesh(heights)


def np_rmse(p, x, y):
    y_hat = p(x)
    return np.sqrt(np.mean((y - y_hat) ** 2))


def bed_mesh_ProbeManager_start_probe_override(self, gcmd):
    method = gcmd.get("METHOD", "automatic").lower()
    can_scan = False
    pprobe = self.printer.lookup_object("probe", None)
    if pprobe is not None:
        probe_name = pprobe.get_status(None).get("name", "")
        can_scan = "eddy" in probe_name
    if method == "rapid_scan" and can_scan:
        self.rapid_scan_helper.perform_rapid_scan(gcmd)
    else:
        self.probe_helper.start_probe(gcmd)


# ─── Backlash estimation ─────────────────────────────────────────────────────

@dataclass
class BacklashResult:
    backlash: float
    mean_up: float
    mean_down: float
    std_up: float
    std_down: float
    t_stat: float
    degrees_of_freedom: float
    significant: bool


def welchs_ttest(a: List[float], b: List[float]) -> Tuple[float, float]:
    """Welch's t-test for two samples with unequal variance.

    Returns (t_statistic, degrees_of_freedom).
    """
    n_a = len(a)
    n_b = len(b)
    if n_a < 2 or n_b < 2:
        return 0.0, 0.0

    mean_a = sum(a) / n_a
    mean_b = sum(b) / n_b

    # Sample variance with Bessel's correction
    var_a = sum((x - mean_a) ** 2 for x in a) / (n_a - 1)
    var_b = sum((x - mean_b) ** 2 for x in b) / (n_b - 1)

    se_a = var_a / n_a
    se_b = var_b / n_b
    se_sum = se_a + se_b

    if se_sum < 1e-15:
        return 0.0, float(n_a + n_b - 2)

    t_stat = (mean_a - mean_b) / math.sqrt(se_sum)

    # Welch-Satterthwaite degrees of freedom
    numerator = se_sum ** 2
    denominator = (se_a ** 2 / (n_a - 1)) + (se_b ** 2 / (n_b - 1))
    if denominator < 1e-15:
        df = float(n_a + n_b - 2)
    else:
        df = numerator / denominator

    return t_stat, df


def estimate_backlash(
    measure_height_func,
    move_func,
    wait_func,
    height: float,
    delta: float = 0.5,
    iterations: int = 10,
    speed: float = 5.0,
) -> BacklashResult:
    """Estimate Z-axis backlash by measuring from both directions.

    Args:
        measure_height_func: Callable that returns current measured height.
        move_func: Callable(z, speed) that moves Z axis.
        wait_func: Callable that waits for moves to complete.
        height: Reference height for measurement.
        delta: Distance to move above/below reference.
        iterations: Number of measurement cycles.
        speed: Movement speed.

    Returns:
        BacklashResult with statistical analysis.
    """
    measurements_up: List[float] = []
    measurements_down: List[float] = []

    # Initial compensating moves to eliminate startup transients
    move_func(height + delta, speed)
    wait_func()
    move_func(height, speed)
    wait_func()
    move_func(height - delta, speed)
    wait_func()
    move_func(height, speed)
    wait_func()

    for _ in range(iterations):
        # Approach from below (moving UP)
        move_func(height - delta, speed)
        wait_func()
        move_func(height, speed)
        wait_func()
        h = measure_height_func()
        measurements_up.append(h)

        # Approach from above (moving DOWN)
        move_func(height + delta, speed)
        wait_func()
        move_func(height, speed)
        wait_func()
        h = measure_height_func()
        measurements_down.append(h)

    # Statistics
    n = len(measurements_up)
    mean_up = sum(measurements_up) / n
    mean_down = sum(measurements_down) / n
    std_up = math.sqrt(sum((x - mean_up) ** 2 for x in measurements_up) / (n - 1)) if n > 1 else 0.0
    std_down = math.sqrt(sum((x - mean_down) ** 2 for x in measurements_down) / (n - 1)) if n > 1 else 0.0

    t_stat, df = welchs_ttest(measurements_down, measurements_up)

    # t >= 2.0 is approximately p <= 0.05 for df > 30
    significant = abs(t_stat) >= 2.0

    if significant:
        backlash = mean_down - mean_up
        if backlash < 0:
            logging.warning("Negative backlash (%.4f mm) is unexpected, "
                           "setting to 0", backlash)
            backlash = 0.0
            significant = False
    else:
        backlash = 0.0

    return BacklashResult(
        backlash=backlash,
        mean_up=mean_up,
        mean_down=mean_down,
        std_up=std_up,
        std_down=std_down,
        t_stat=t_stat,
        degrees_of_freedom=df,
        significant=significant,
    )


# ─── Data streaming ──────────────────────────────────────────────────────────

@dataclass
class StreamSample:
    time: float
    frequency: float
    temperature: float = 0.0
    position_x: float = 0.0
    position_y: float = 0.0
    position_z: float = 0.0
    has_position: bool = False


class StreamSession:
    """A data collection session that accumulates samples."""

    def __init__(self):
        self.samples: List[StreamSample] = []
        self.active: bool = True
        self.start_time: float = time.time()

    def add_sample(self, sample: StreamSample):
        if self.active:
            self.samples.append(sample)

    def stop(self):
        self.active = False

    @property
    def duration(self) -> float:
        return time.time() - self.start_time

    @property
    def count(self) -> int:
        return len(self.samples)


class DataStreamer:
    """Manages data streaming sessions with CSV export.

    Usage:
        streamer = DataStreamer()
        session = streamer.start_session("/tmp/output.csv")
        # ... collect data via add_sample() ...
        streamer.stop_session()  # writes CSV
    """

    def __init__(self):
        self._session: Optional[StreamSession] = None
        self._output_file: Optional[str] = None

    @property
    def is_active(self) -> bool:
        return self._session is not None and self._session.active

    @property
    def session(self) -> Optional[StreamSession]:
        return self._session

    def start_session(self, output_file: Optional[str] = None) -> StreamSession:
        if self.is_active:
            raise RuntimeError(
                "Stream already active. Stop current stream first."
            )

        self._output_file = output_file or _generate_filepath("eddy_ng_stream")
        _validate_output_path(self._output_file)

        self._session = StreamSession()
        logging.info("Started streaming session, will save to: %s",
                    self._output_file)
        return self._session

    def add_sample(self, sample: StreamSample):
        if self._session and self._session.active:
            self._session.add_sample(sample)

    def stop_session(self) -> Optional[str]:
        """Stop session and write CSV. Returns output file path."""
        if self._session is None:
            return None

        self._session.stop()
        output = None

        if self._output_file and self._session.samples:
            _write_csv(self._session.samples, self._output_file)
            output = self._output_file
            logging.info("Stopped streaming. %d samples saved to: %s",
                        len(self._session.samples), self._output_file)
        elif not self._session.samples:
            logging.warning("No samples collected during streaming session")

        self._session = None
        self._output_file = None
        return output

    def cancel_session(self):
        """Cancel session without saving."""
        if self._session:
            self._session.stop()
            logging.info("Cancelled streaming session (%d samples discarded)",
                        len(self._session.samples))
        self._session = None
        self._output_file = None

    def get_status(self) -> str:
        if not self.is_active:
            return "No active streaming session"
        s = self._session
        return (f"Streaming active: {s.count} samples collected "
                f"over {s.duration:.1f}s → {self._output_file}")


def _generate_filepath(label: str) -> str:
    timestamp = time.strftime("%Y%m%d_%H%M%S")
    filename = f"{label}_{timestamp}.csv"
    return os.path.join("/tmp", filename)


def _validate_output_path(path: str):
    parent = os.path.dirname(path)
    if parent and not os.path.exists(parent):
        os.makedirs(parent, exist_ok=True)


def _write_csv(samples: List[StreamSample], output_file: str):
    with open(output_file, "w") as f:
        f.write("time,frequency,temperature,position_x,position_y,position_z\n")
        for s in samples:
            if s.has_position:
                f.write(f"{s.time:.6f},{s.frequency:.3f},{s.temperature:.2f},"
                        f"{s.position_x:.4f},{s.position_y:.4f},"
                        f"{s.position_z:.6f}\n")
            else:
                f.write(f"{s.time:.6f},{s.frequency:.3f},{s.temperature:.2f}"
                        f",,,\n")


# ─── Temperature compensation ────────────────────────────────────────────────

@dataclass
class TempCompCoefficients:
    """Temperature compensation model coefficients.

    The model uses frequency-dependent quadratic interpolation:
      a_interp = a_a * (freq - ref_freq) + a_b
      b_interp = b_a * (freq - ref_freq) + b_b

    Then frequency is modeled as:
      freq = a_interp * temp^2 + b_interp * temp + c
    """
    a_a: float
    a_b: float
    b_a: float
    b_b: float
    ref_frequency: float  # baseline frequency at calibration
    ref_temperature: float  # temperature at calibration


def _param_linear(freq_offset: float, slope: float, intercept: float) -> float:
    return slope * freq_offset + intercept


class TemperatureCompensationModel:
    """Compensates frequency readings for temperature drift.

    Uses a quadratic model fitted across multiple heights and temperatures
    to adjust raw frequency to what it would be at the reference temperature.
    """

    def __init__(self, coefficients: TempCompCoefficients):
        self.coeff = coefficients

    def compensate(self, frequency: float, temp_source: float,
                   temp_target: float) -> float:
        """Adjust frequency from temp_source to temp_target."""
        if abs(temp_source - temp_target) < 0.1:
            return frequency

        c = self.coeff
        freq_offset = frequency - c.ref_frequency

        # Interpolate quadratic parameters for this frequency
        param_a = _param_linear(freq_offset, c.a_a, c.a_b)
        param_b = _param_linear(freq_offset, c.b_a, c.b_b)

        # Try quadratic solution first
        result = self._compensate_quadratic(
            frequency, freq_offset, param_a, param_b,
            temp_source, temp_target
        )
        if result is not None:
            return result

        # Fallback to linear compensation
        return self._compensate_linear(
            frequency, param_a, param_b, temp_source, temp_target
        )

    def _compensate_quadratic(self, frequency: float, freq_offset: float,
                              param_a: float, param_b: float,
                              temp_source: float, temp_target: float
                              ) -> Optional[float]:
        c = self.coeff

        # Build quadratic equation for freq_offset solution
        quad_a = (4 * (temp_source * c.a_a) ** 2
                  + 4 * temp_source * c.a_a * c.b_a
                  + c.b_a ** 2 + 4 * c.a_a)
        quad_b = (8 * temp_source ** 2 * c.a_a * c.a_b
                  + 4 * temp_source * (c.a_a * c.b_b + c.a_b * c.b_a)
                  + 2 * c.b_a * c.b_b + 4 * c.a_b
                  - 4 * freq_offset * c.a_a)
        quad_c = (4 * (temp_source * c.a_b) ** 2
                  + 4 * temp_source * c.a_b * c.b_b
                  + c.b_b ** 2 - 4 * freq_offset * c.a_b)

        discriminant = quad_b ** 2 - 4 * quad_a * quad_c
        if discriminant < 0:
            return None

        if abs(quad_a) < 1e-15:
            return None

        ax = (math.sqrt(discriminant) - quad_b) / (2 * quad_a)

        # Get parameters at solution point
        a_at_ax = _param_linear(ax, c.a_a, c.a_b)
        b_at_ax = _param_linear(ax, c.b_a, c.b_b)

        if abs(a_at_ax) > 1e-12:
            temp_offset = b_at_ax / (2 * a_at_ax)
            return a_at_ax * (temp_target + temp_offset) ** 2 + ax + c.ref_frequency
        else:
            return b_at_ax * temp_target + ax + c.ref_frequency

    def _compensate_linear(self, frequency: float,
                           param_a: float, param_b: float,
                           temp_source: float, temp_target: float) -> float:
        # Extract constant c from: freq = a*temp_src^2 + b*temp_src + c
        param_c = frequency - param_a * temp_source ** 2 - param_b * temp_source
        # Apply at target temperature
        return param_a * temp_target ** 2 + param_b * temp_target + param_c


def fit_temperature_model(
    data_per_height: dict,
    ref_frequency: float,
    ref_temperature: float,
) -> Optional[TempCompCoefficients]:
    """Fit temperature compensation model from calibration data.

    Args:
        data_per_height: Dict mapping height (mm) to list of
            (frequency, temperature) tuples.
        ref_frequency: Baseline frequency from initial calibration.
        ref_temperature: Temperature at initial calibration.

    Returns:
        TempCompCoefficients if fitting succeeds, None otherwise.
    """
    try:
        from scipy.optimize import curve_fit
    except ImportError:
        logging.error("Temperature calibration requires scipy. "
                     "Install with: pip install scipy")
        return None

    if len(data_per_height) < 2:
        logging.error("Need at least 2 heights for temperature calibration, "
                     "got %d", len(data_per_height))
        return None

    coefficients_a = []
    coefficients_b = []
    frequencies_at_vertex = []

    for height, samples in sorted(data_per_height.items()):
        if len(samples) < 15:
            logging.warning("Skipping height %.1f mm: only %d samples "
                           "(need >= 15)", height, len(samples))
            continue

        freqs = np.array([s[0] for s in samples])
        temps = np.array([s[1] for s in samples])

        # Downsample if too many points (>1000 -> 800)
        if len(samples) > 1000:
            freqs, temps = _downsample_by_temp_bins(freqs, temps, 800)

        # Fit: freq = a*temp^2 + b*temp + c
        try:
            def quad_func(t, a, b, c):
                return a * t ** 2 + b * t + c

            popt, _ = curve_fit(
                quad_func, temps, freqs,
                bounds=([0, -np.inf, -np.inf], [np.inf, np.inf, np.inf]),
                maxfev=100000,
            )
            a, b, c = popt
        except Exception as e:
            logging.warning("Quadratic fit failed for height %.1f: %s",
                           height, e)
            continue

        # Check vertex position
        if abs(a) < 1e-15:
            vertex_temp = 60.0  # default
        else:
            vertex_temp = -b / (2 * a)

        # Constrain vertex to reasonable range
        if vertex_temp > 120:
            # Re-fit with vertex at 120
            try:
                def line120(t, a_c, c_c):
                    return a_c * t ** 2 - 240 * a_c * t + c_c
                popt2, _ = curve_fit(line120, temps, freqs, maxfev=100000)
                a, b = popt2[0], -240 * popt2[0]
                freq_at_vertex = quad_func(120, a, b, popt2[1])
            except Exception:
                freq_at_vertex = float(np.mean(freqs))
        elif vertex_temp < 0:
            # Re-fit with vertex at 0
            try:
                def line0(t, a_c, c_c):
                    return a_c * t ** 2 + c_c
                popt2, _ = curve_fit(line0, temps, freqs, maxfev=100000)
                a, b = popt2[0], 0.0
                freq_at_vertex = quad_func(0, a, b, popt2[1])
            except Exception:
                freq_at_vertex = float(np.mean(freqs))
        else:
            freq_at_vertex = quad_func(vertex_temp, a, b, c)

        coefficients_a.append(a)
        coefficients_b.append(b)
        frequencies_at_vertex.append(freq_at_vertex)

    if len(coefficients_a) < 2:
        logging.error("Not enough valid heights for temperature model")
        return None

    # Fit linear relationships: coeff = slope * (freq - ref_freq) + intercept
    freq_array = np.array(frequencies_at_vertex) - ref_frequency

    def linear(x, slope, intercept):
        return slope * x + intercept

    try:
        params_a, _ = curve_fit(linear, freq_array, coefficients_a)
        params_b, _ = curve_fit(linear, freq_array, coefficients_b)
    except Exception as e:
        logging.error("Linear fit failed: %s", e)
        return None

    return TempCompCoefficients(
        a_a=float(params_a[0]),
        a_b=float(params_a[1]),
        b_a=float(params_b[0]),
        b_b=float(params_b[1]),
        ref_frequency=ref_frequency,
        ref_temperature=ref_temperature,
    )


def _downsample_by_temp_bins(freqs, temps, target_count):
    """Downsample by evenly distributing across temperature bins."""
    temp_min, temp_max = temps.min(), temps.max()
    n_bins = target_count
    bin_edges = np.linspace(temp_min, temp_max, n_bins + 1)

    indices = []
    for i in range(n_bins):
        mask = (temps >= bin_edges[i]) & (temps < bin_edges[i + 1])
        bin_indices = np.where(mask)[0]
        if len(bin_indices) > 0:
            indices.append(bin_indices[len(bin_indices) // 2])

    indices = np.array(indices)
    return freqs[indices], temps[indices]


def save_temp_comp_to_config(configfile, section: str,
                             coeff: TempCompCoefficients):
    """Save temperature compensation to printer config."""
    val = ",".join([
        f"{coeff.a_a:.10e}", f"{coeff.a_b:.10e}",
        f"{coeff.b_a:.10e}", f"{coeff.b_b:.10e}",
        f"{coeff.ref_frequency:.6f}", f"{coeff.ref_temperature:.3f}",
    ])
    configfile.set(section, "temperature_compensation", val)


def load_config_prefix(config: ConfigWrapper):
    return ProbeEddy(config)
