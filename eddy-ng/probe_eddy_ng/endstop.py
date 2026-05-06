# EDDY-ng endstop wrapper
# This file may be distributed under the terms of the GNU GPLv3 license.
from __future__ import annotations

import logging

from typing import TYPE_CHECKING, Optional, final

from ._compat import mcu, pins

if TYPE_CHECKING:
    from .probe import ProbeEddy


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
        self._sampler = None
        # Register z_virtual_endstop pin (probe_eddy to avoid conflict with tool_probe_endstop)
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

    def _is_in_endstops(self, endstops):
        """Check if this endstop is active, either directly or via a router."""
        if self in endstops:
            return True
        # Check if an EndstopRouter is delegating to us
        for es in endstops:
            if getattr(es, 'z_mcu', None) is self:
                return True
        return False

    def _handle_home_rails_begin(self, homing_state, rails):
        endstops = [es for rail in rails for es, name in rail.get_endstops()]
        if not self._is_in_endstops(endstops):
            return
        # Nothing to do
        pass

    def _handle_homing_move_begin(self, hmove):
        if not self._is_in_endstops(hmove.get_mcu_endstops()):
            return
        self._sampler = self.eddy.start_sampler()
        self._homing_in_progress = True
        # if we're doing a tap, we're already in the right position;
        # otherwise move there
        if self.tap_config is None:
            self.eddy._probe_to_start_position_unhomed(move_home=True)

    def _handle_homing_move_end(self, hmove):
        if not self._is_in_endstops(hmove.get_mcu_endstops()):
            return
        self._sampler.finish()
        self._homing_in_progress = False

    def _handle_home_rails_end(self, homing_state, rails):
        endstops = [es for rail in rails for es, name in rail.get_endstops()]
        if not self._is_in_endstops(endstops):
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
