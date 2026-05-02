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
import traceback
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
    final,
)

from ._compat import (
    mcu, pins, chelper, Printer, ConfigWrapper, configerror,
    GCodeCommand, ToolHead, probe, manual_probe, bed_mesh,
    HomingMove, IS_KALICO, HAS_PROBE_RESULT_TYPE,
    ldc1612_ng, plotly, scipy,
)
from .params import ProbeEddyParams, ProbeEddyProbeResult
from .frequency_map import ProbeEddyFrequencyMap
from .sampler import ProbeEddySampler
from .endstop import ProbeEddyEndstopWrapper
from .scanning import ProbeEddyScanningProbe
from .bed_mesh_helper import BedMeshScanHelper, bed_mesh_ProbeManager_start_probe_override
from .alpha_beta_filter import AlphaBetaFilter
from .temperature_compensation import (
    TemperatureCompensationModel, TempCompCoefficients,
    fit_temperature_model, load_temp_comp_from_config,
    save_temp_comp_to_config,
)
from .backlash import estimate_backlash, BacklashResult
from .streaming import DataStreamer, StreamSample


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

        # runtime configurable
        self._tap_adjust_z = self.params.tap_adjust_z

        # Temperature compensation
        self._temp_comp: Optional[TemperatureCompensationModel] = None
        tc = load_temp_comp_from_config(config)
        if tc is not None:
            self._temp_comp = TemperatureCompensationModel(tc)
            logging.info("EDDYng: Temperature compensation loaded")

        # Alpha-beta filter for measurement smoothing
        self._ab_filter = AlphaBetaFilter(
            alpha=self.params.filter_alpha,
            beta=self.params.filter_beta,
        )

        # Data streamer
        self._streamer = DataStreamer()

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

        # New features
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

    def _get_bed_center(self) -> Tuple[float, float]:
        """Get bed center coordinates.

        Uses bed_mesh zero_reference_position if available, falls back to
        mesh_min/mesh_max midpoint, then kinematics range midpoint.
        """
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
        kin = th.get_kinematics()
        zrange = kin.rails[2].get_range()
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

        # Smart default: scan around the current drive current instead of the full 1-31 range.
        # This saves ~60% of scan time since most DCs outside this range produce no signal.
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

        # Requirements for homing: height range must cover 0.5 to 5.0mm
        homing_req_min = 0.5
        homing_req_max = 5.0
        # Requirements for tap: height range must cover 0.025 to 3.0mm
        tap_req_min = 0.025
        tap_req_max = 3.0
        # Minimum frequency spread to be usable
        min_freq_spread = 0.30
        # Maximum RMSE to be usable
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
            # Tap verification results
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
                0.0,  # z_target
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

            # Check homing suitability
            if h_min <= homing_req_min and h_max >= homing_req_max and spread >= min_freq_spread and fth_rms <= max_rmse:
                r.ok_for_homing = True
                r.homing_score = (1.0 / (1.0 + fth_rms * 100.0)) + (spread / 100.0)

            # Check tap suitability (mapping-based)
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

                # Temporarily install this DC's calibration and switch to it
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

                    # Boost tap_score with verification quality
                    # Lower range and stddev = better
                    r.tap_score += (1.0 / (1.0 + r.tap_range * 100.0)) + (1.0 / (1.0 + r.tap_stddev * 100.0)) + r.tap_success_rate
                else:
                    self._log_msg(
                        f"  DC {r.dc}: only {len(probe_zs)}/{tap_verify_count} OK "
                        f"({errors} errors) -- tap verification FAILED"
                    )
                    # Penalize heavily: this DC can't reliably tap
                    r.tap_score *= 0.1

            self._sensor.set_drive_current(old_dc)

        # === Phase 3: Select best and report ===
        homing_candidates = [r for r in results if r.ok_for_homing]
        # Re-sort tap candidates by updated scores (after verification)
        tap_candidates = sorted(
            [r for r in results if r.ok_for_tap],
            key=lambda r: r.tap_score,
            reverse=True,
        )

        best_homing = max(homing_candidates, key=lambda r: r.homing_score) if homing_candidates else None
        best_tap = tap_candidates[0] if tap_candidates else None

        # Build results summary
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

        # Apply results
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
        session = ProbeEddyScanningProbe(self, gcmd)
        session._start_session()
        return session
        # method = gcmd.get('METHOD', 'automatic').lower()
        # if method in ('scan', 'rapid_scan'):
        #    session = ProbeEddyScanningProbe(self, gcmd)
        #    session._start_session()
        #    return session
        #
        # return self._probe_session.start_probe_session(gcmd)

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

        # If we can't get a value at all for right now, for safety, just abort.
        if now_height is None:
            raise self._printer.command_error("Couldn't get any valid samples from sensor.")

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
        filtered = scipy.signal.sosfilt(sos, s_f - s_f[0])

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
        verification_samples = gcmd.get_int("VERIFICATION_SAMPLES", 5, minval=3, maxval=20)
        req_range = gcmd.get_float("SAMPLE_RANGE", 0.050, above=0.0)
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
        # Only save tap_mode if it differs from the user's config value.
        # Writing it unconditionally causes SAVE_CONFIG conflicts when
        # tap_mode is already defined in an included config file.
        if mode != self.params.tap_mode:
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
                gcode.run_script_from_command("M104 S0")  # hotend off → heater_fan off
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
                    reactor.pause(reactor.monotonic() + 1.0)
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
