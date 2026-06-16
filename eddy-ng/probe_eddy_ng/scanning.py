# EDDY-ng scanning probe
# This file may be distributed under the terms of the GNU GPLv3 license.
from __future__ import annotations

import logging
import math

from typing import Any, TYPE_CHECKING, final

from ._compat import GCodeCommand, manual_probe, HAS_PROBE_RESULT_TYPE

if TYPE_CHECKING:
    from .probe import ProbeEddy


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

        self._sampler = None

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

        th = self._printer.lookup_object("toolhead")
        th_pos = th.get_position()
        logging.info(
            f"ProbeEddyScanningProbe: _start_session at Z={th_pos[2]:.3f}, "
            f"scan_z={self._scan_z:.3f}, is_rapid={self._is_rapid}"
        )

        self.eddy.probe_to_start_position()

        th_pos_after = th.get_position()
        logging.info(
            f"ProbeEddyScanningProbe: after probe_to_start_position Z={th_pos_after[2]:.3f}"
        )

        self._sampler = self.eddy.start_sampler()

        # Wait for the first sample to arrive from the MCU before returning.
        # Without this, QGL may call run_probe() immediately and record a
        # print_time for which no sensor data exists yet, causing
        # "No samples received" errors.
        th.dwell(0.100)
        target_time = th.get_last_move_time()
        now_time = self.eddy._print_time_now()
        logging.info(
            f"ProbeEddyScanningProbe: waiting for first sample, "
            f"target_time={target_time:.3f}, now={now_time:.3f}, "
            f"sampler.raw_count={self._sampler.raw_count}"
        )
        self._sampler.wait_for_sample_at_time(target_time, max_wait_time=2.0)
        logging.info(
            f"ProbeEddyScanningProbe: _start_session OK, "
            f"sampler.raw_count={self._sampler.raw_count}, "
            f"sampler.error_count={self._sampler.error_count}, "
            f"time_range=[{self._sampler.times[0]:.3f}, {self._sampler.times[-1]:.3f}]"
        )

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

        logging.info(
            f"ProbeEddyScanningProbe: run_probe #{len(self._notes)} at "
            f"Z={th_pos[2]:.3f}, sampler.raw_count={self._sampler.raw_count}, "
            f"errors={self._sampler.error_count}"
        )

        if self._is_rapid:
            # this callback is attached to the last move in the queue, so that
            # we can grab the toolhead position when the toolhead actually hits it

            self._toolhead.register_lookahead_callback(lambda time: self._rapid_lookahead_cb(time, th_pos))
            return

        # For point-by-point probing (e.g. QGL), lower to scan height
        # so the sensor is within reading range, then return to travel height.
        travel_z = th_pos[2]
        if travel_z > self._scan_z + 0.5:
            pos = list(th_pos)
            pos[2] = self._scan_z
            th.manual_move(pos, self.eddy.params.probe_speed)

        th.dwell(self._sample_time_delay)
        start_time = th.get_last_move_time()
        self._toolhead.dwell(self._sample_time + self._sample_time_delay)

        # Record position at scan height for correct height calculation
        scan_pos = list(th_pos)
        scan_pos[2] = self._scan_z
        self._notes.append((start_time, start_time + self._sample_time / 2.0, scan_pos))

        # Return to travel height
        if travel_z > self._scan_z + 0.5:
            pos = list(th_pos)
            pos[2] = travel_z
            th.manual_move(pos, self.eddy.params.lift_speed)

    def pull_probed_results(self):
        if self._is_rapid:
            # Flush lookahead (so all lookahead callbacks are invoked)
            self._toolhead.get_last_move_time()

        now_time = self.eddy._print_time_now()
        target_time = self._notes[-1][0] + self._sample_time
        time_range_str = "EMPTY"
        if self._sampler.times:
            time_range_str = f"[{self._sampler.times[0]:.3f}, {self._sampler.times[-1]:.3f}]"
        logging.info(
            f"ProbeEddyScanningProbe: pull_probed_results, "
            f"{len(self._notes)} notes, target_time={target_time:.3f}, "
            f"now={now_time:.3f}, sampler.raw_count={self._sampler.raw_count}, "
            f"errors={self._sampler.error_count}, "
            f"times={time_range_str}"
        )

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
        # Target well below trigger height so the endstop fires during descent.
        # The endstop wrapper's _handle_homing_move_begin will adjust the
        # kinematic position for maximum travel if needed (ERR_AHE case).
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
