# Per-tool Z-Probe support with optional external Z probe routing
#
# Copyright (C) 2023 Viesturs Zarins <viesturz@gmail.com>
# Z-probe routing extensions (C) 2026
#
# This file may be distributed under the terms of the GNU GPLv3 license.
import logging
from . import probe
from . import manual_probe

# Helper class to provide probe offsets interface for ToolProbeEndstop
class ToolProbeOffsetsHelper:
    def __init__(self, tool_probe_endstop):
        self.tool_probe_endstop = tool_probe_endstop

    def get_offsets(self, gcmd=None):
        return self.tool_probe_endstop.get_offsets(gcmd)

    def create_probe_result(self, test_pos):
        x_offset, y_offset, z_offset = self.tool_probe_endstop.get_offsets()
        return manual_probe.ProbeResult(
            test_pos[0]+x_offset, test_pos[1]+y_offset,
            test_pos[2]-z_offset, test_pos[0], test_pos[1], test_pos[2])

# Virtual endstop, using a tool attached Z probe in a toolchanger setup.
# Tool endstop change may be done either via SET_ACTIVE_TOOL_PROBE TOOL=99
# Or via auto-detection of single open tool probe via DETECT_ACTIVE_TOOL_PROBE
#
# Optional z_probe support: When a tool_probe has z_probe configured (e.g.
# Eddy, Cartographer), probing operations (PROBE, BED_MESH, QGL) are routed
# to that probe automatically. Tool detection and crash detection always use
# the tool_probe (Tap).
class ToolProbeEndstop:
    def __init__(self, config):
        self.printer = config.get_printer()
        self.reactor = self.printer.get_reactor()
        self.name = config.get_name()
        self.tool_probes = {}
        self.last_query = {} # map from tool number to endstop state
        self.active_probe = None
        self.active_tool_number = -1
        self.z_probe_obj = None  # External Z probe (e.g. Eddy, Cartographer)
        self.gcode_macro = self.printer.load_object(config, 'gcode_macro')
        self.crash_detection_active = False
        self.crash_lasttime = 0.
        self.mcu_probe = EndstopRouter(self.printer)
        self.probe_offsets = ToolProbeOffsetsHelper(self)
        self.param_helper = probe.ProbeParameterHelper(config)
        self.homing_helper = probe.HomingViaProbeHelper(
            config, self.mcu_probe, self.probe_offsets, self.param_helper)
        self.probe_session = probe.ProbeSessionHelper(
            config, self.param_helper, self.homing_helper.start_probe_session)
        self.cmd_helper = probe.ProbeCommandHelper(
            config, self, self.mcu_probe.query_endstop)

        # Emulate the probe object, since others rely on this.
        if self.printer.lookup_object('probe', default=None):
            raise self.printer.config_error(
                'Cannot have both [probe] and [tool_probe_endstop].')
        self.printer.add_object('probe', self)

        self.crash_mintime = config.getfloat('crash_mintime', 0.5, above=0.)
        self.crash_gcode = self.gcode_macro.load_template(
            config, 'crash_gcode', '')
        self.printer.register_event_handler("klippy:connect",
                                            self._handle_connect)
        # Register commands
        self.gcode = self.printer.lookup_object('gcode')
        self.gcode.register_command(
            'SET_ACTIVE_TOOL_PROBE',
            self.cmd_SET_ACTIVE_TOOL_PROBE,
            desc=self.cmd_SET_ACTIVE_TOOL_PROBE_help)
        self.gcode.register_command(
            'DETECT_ACTIVE_TOOL_PROBE',
            self.cmd_DETECT_ACTIVE_TOOL_PROBE,
            desc=self.cmd_DETECT_ACTIVE_TOOL_PROBE_help)
        self.gcode.register_command(
            'START_TOOL_PROBE_CRASH_DETECTION',
            self.cmd_START_TOOL_PROBE_CRASH_DETECTION,
            desc=self.cmd_START_TOOL_PROBE_CRASH_DETECTION_help)
        self.gcode.register_command(
            'STOP_TOOL_PROBE_CRASH_DETECTION',
            self.cmd_STOP_TOOL_PROBE_CRASH_DETECTION,
            desc=self.cmd_STOP_TOOL_PROBE_CRASH_DETECTION_help)
        self.gcode.register_command(
            'SET_ACTIVE_Z_PROBE',
            self.cmd_SET_ACTIVE_Z_PROBE,
            desc=self.cmd_SET_ACTIVE_Z_PROBE_help)

    def _handle_connect(self):
        self.toolhead = self.printer.lookup_object('toolhead')
        self._detect_active_tool()

    # --- Probe interface (used by BED_MESH, QGL, PROBE, etc.) ---
    # Routes to z_probe when available, otherwise to tool_probe (Tap).

    def get_offsets(self, gcmd=None):
        if self.z_probe_obj:
            return self.z_probe_obj.get_offsets(gcmd)
        if self.active_probe:
            return self.active_probe.get_offsets(gcmd)
        return 0.0, 0.0, 0.0

    def get_probe_params(self, gcmd=None):
        if self.z_probe_obj:
            return self.z_probe_obj.get_probe_params(gcmd)
        if self.active_probe:
            return self.active_probe.get_probe_params(gcmd)
        return self.param_helper.get_probe_params(gcmd)

    def start_probe_session(self, gcmd):
        if self.z_probe_obj:
            return self.z_probe_obj.start_probe_session(gcmd)
        if self.active_probe:
            return self.active_probe.start_probe_session(gcmd)
        raise self.printer.command_error("No active tool probe")

    def get_status(self, eventtime):
        status = self.cmd_helper.get_status(eventtime)
        status['last_tools_query'] = self.last_query
        status['active_tool_number'] = self.active_tool_number
        z_name = None
        if self.z_probe_obj:
            z_name = getattr(self.z_probe_obj, 'name',
                     getattr(self.z_probe_obj, '_name',
                     getattr(self.z_probe_obj, '_full_name',
                             str(self.z_probe_obj))))
        status['active_z_probe'] = z_name
        if self.active_probe:
            # Always report the TOOL PROBE (Tap) offsets here – these are
            # used by _ADJUST_Z_HOME_FOR_TOOL_OFFSET.  The z_probe (Eddy)
            # offsets are only used internally for probe operations.
            tap_offsets = self.active_probe.get_offsets()
            status['active_tool_probe'] = self.active_probe.name
            status['active_tool_probe_x_offset'] = tap_offsets[0]
            status['active_tool_probe_y_offset'] = tap_offsets[1]
            status['active_tool_probe_z_offset'] = tap_offsets[2]
        else:
            status['active_tool_probe'] = None
            status['active_tool_probe_x_offset'] = 0.0
            status['active_tool_probe_y_offset'] = 0.0
            status['active_tool_probe_z_offset'] = 0.0
        return status

    # --- Probe management ---

    def add_probe(self, config, tool_probe):
        if tool_probe.tool in self.tool_probes:
            raise config.error(
                "Duplicate tool probe nr: %s" % (tool_probe.tool,))
        self.tool_probes[tool_probe.tool] = tool_probe
        self.mcu_probe.add_mcu(tool_probe.mcu_probe)

    def set_active_probe(self, tool_probe):
        if self.active_probe == tool_probe:
            return
        self.active_probe = tool_probe
        if self.active_probe:
            self.mcu_probe.set_active_mcu(tool_probe.mcu_probe)
            self.active_tool_number = self.active_probe.tool
        else:
            self.mcu_probe.set_active_mcu(None)
            self.active_tool_number = -1
        # Auto-activate z_probe if configured on this tool_probe
        z_probe = None
        if tool_probe:
            z_probe = getattr(tool_probe, 'z_probe', None)
            # Lazy resolution: if z_probe_name is set but object not resolved
            if z_probe is None and getattr(tool_probe, 'z_probe_name', None):
                z_probe = self.printer.lookup_object(
                    tool_probe.z_probe_name, None)
                if z_probe:
                    tool_probe.z_probe = z_probe
        self.set_active_z_probe(z_probe)

    def set_active_z_probe(self, z_probe_obj):
        """Set an external Z probe that overrides the tool probe for probing
        operations (PROBE, BED_MESH_CALIBRATE, QUAD_GANTRY_LEVEL, etc.).
        Tool detection and crash detection remain on the tool_probe (Tap).
        Pass None to clear and fall back to tool_probe.
        """
        self.z_probe_obj = z_probe_obj
        z_mcu = None
        if z_probe_obj and hasattr(z_probe_obj, 'mcu_probe'):
            z_mcu = z_probe_obj.mcu_probe
        self.mcu_probe.set_z_mcu(z_mcu)
        if z_probe_obj:
            name = getattr(z_probe_obj, 'name', str(z_probe_obj))
            logging.info("tool_probe_endstop: Z probe set to %s", name)
        else:
            logging.info(
                "tool_probe_endstop: Z probe cleared, using tool probe")

    # --- Tool detection (always uses Tap, never z_probe) ---

    def _query_open_tools(self):
        print_time = self.toolhead.get_last_move_time()
        self.last_query.clear()
        candidates = []
        for tool_probe in self.tool_probes.values():
            triggered = tool_probe.mcu_probe.query_endstop(print_time)
            self.last_query[tool_probe.tool] = triggered
            if not triggered:
                candidates.append(tool_probe)
        return candidates

    def _describe_tool_detection_issue(self, candidates):
        if len(candidates) == 1:
            return 'OK'
        elif len(candidates) == 0:
            return "All probes triggered"
        else:
            names = [p.name for p in candidates]
            return "Multiple probes not triggered: %s" % names

    def _ensure_active_tool_or_fail(self, gcode):
        if self.active_probe:
            return
        active_tools = self._query_open_tools()
        if len(active_tools) != 1:
            raise gcode.error(
                self._describe_tool_detection_issue(active_tools))
        self.set_active_probe(active_tools[0])

    def _detect_active_tool(self):
        active_tools = self._query_open_tools()
        if len(active_tools) == 1:
            self.set_active_probe(active_tools[0])

    # --- GCode commands ---

    cmd_SET_ACTIVE_TOOL_PROBE_help = (
        "Set the tool probe that will act as the Z endstop.")
    def cmd_SET_ACTIVE_TOOL_PROBE(self, gcmd):
        probe_nr = gcmd.get_int("T")
        if probe_nr not in self.tool_probes:
            raise gcmd.error(
                "SET_ACTIVE_TOOL_PROBE no tool probe for tool %d"
                % (probe_nr))
        self.set_active_probe(self.tool_probes[probe_nr])

    cmd_DETECT_ACTIVE_TOOL_PROBE_help = (
        "Detect which tool is active by identifying a probe "
        "that is NOT triggered")
    def cmd_DETECT_ACTIVE_TOOL_PROBE(self, gcmd):
        active_tools = self._query_open_tools()
        if len(active_tools) == 1:
            active = active_tools[0]
            gcmd.respond_info(
                "Found active tool probe: %s" % (active.name))
            self.set_active_probe(active)
        else:
            self.set_active_probe(None)
            gcmd.respond_info(
                self._describe_tool_detection_issue(active_tools))

    cmd_SET_ACTIVE_Z_PROBE_help = (
        "Set or clear the external Z probe for probing operations. "
        "Use PROBE=<name> to set, PROBE=none to clear.")
    def cmd_SET_ACTIVE_Z_PROBE(self, gcmd):
        probe_name = gcmd.get("PROBE", None)
        if (probe_name is None
                or probe_name.lower() == "none"
                or probe_name == ""):
            self.set_active_z_probe(None)
            gcmd.respond_info(
                "Z probe cleared, using tool probe for probing")
            return
        z_probe = self.printer.lookup_object(probe_name, None)
        if z_probe is None:
            raise gcmd.error(
                "SET_ACTIVE_Z_PROBE: object '%s' not found" % probe_name)
        for method in ('get_probe_params', 'get_offsets',
                       'start_probe_session'):
            if not hasattr(z_probe, method):
                raise gcmd.error(
                    "SET_ACTIVE_Z_PROBE: '%s' missing method %s()"
                    % (probe_name, method))
        self.set_active_z_probe(z_probe)
        gcmd.respond_info("Z probe set to '%s'" % probe_name)

    # --- Crash detection (always uses tool_probe/Tap) ---

    cmd_START_TOOL_PROBE_CRASH_DETECTION_help = (
        "Start detecting tool crashes")
    def cmd_START_TOOL_PROBE_CRASH_DETECTION(self, gcmd):
        self.cmd_DETECT_ACTIVE_TOOL_PROBE(gcmd)
        expected_tool_number = gcmd.get_int("T", self.active_tool_number)
        if expected_tool_number is None:
            raise gcmd.error(
                "Cannot start probe crash detection - no active tool")
        if expected_tool_number != self.active_tool_number:
            raise gcmd.error(
                "Cannot start probe crash detection"
                " - expected tool not active")
        self.crash_lasttime = 0.
        self.crash_detection_active = True

    cmd_STOP_TOOL_PROBE_CRASH_DETECTION_help = (
        "Stop detecting tool crashes")
    def cmd_STOP_TOOL_PROBE_CRASH_DETECTION(self, gcmd):
        self.toolhead.register_lookahead_callback(
            lambda _: self.stop_crash_detection())

    def stop_crash_detection(self):
        self.crash_lasttime = 0.
        self.crash_detection_active = False

    def note_probe_triggered(self, probe, eventtime, is_triggered):
        if not self.crash_detection_active:
            return
        if probe != self.active_probe:
            return
        if is_triggered:
            self.crash_lasttime = eventtime
            self.reactor.register_callback(
                lambda _: self._probe_triggered_delayed(eventtime),
                eventtime + self.crash_mintime)
        else:
            self.crash_lasttime = 0.

    def _probe_triggered_delayed(self, expect_eventtime):
        if self.crash_lasttime != expect_eventtime:
            return
        if self.crash_detection_active:
            self.crash_detection_active = False
            self.crash_gcode.run_gcode_from_command()


# Routes MCU endstop commands to the selected tool probe endstop.
# Supports an optional z_mcu override for external Z probes (Eddy etc).
class EndstopRouter:
    def __init__(self, printer):
        self.active_mcu = None
        self.z_mcu = None
        self._mcus = []
        self._steppers = []
        self.printer = printer
        self._update_effective()

    def add_mcu(self, mcu_probe):
        self._mcus.append(mcu_probe)
        for s in self._steppers:
            mcu_probe.add_stepper(s)

    def set_active_mcu(self, mcu_probe):
        self.active_mcu = mcu_probe
        self._update_effective()

    def set_z_mcu(self, z_mcu):
        """Set an external Z probe MCU that takes priority over the active
        tool probe MCU for homing and probing operations."""
        self.z_mcu = z_mcu
        if z_mcu:
            existing = set()
            if hasattr(z_mcu, 'get_steppers'):
                existing = set(z_mcu.get_steppers())
            for s in self._steppers:
                if s not in existing:
                    z_mcu.add_stepper(s)
        self._update_effective()

    def _get_effective_mcu(self):
        return self.z_mcu if self.z_mcu else self.active_mcu

    def _update_effective(self):
        effective = self._get_effective_mcu()
        if effective:
            self.get_mcu = effective.get_mcu
            self.home_start = effective.home_start
            self.home_wait = effective.home_wait
            self.multi_probe_begin = effective.multi_probe_begin
            self.multi_probe_end = effective.multi_probe_end
            self.probe_prepare = effective.probe_prepare
            self.probe_finish = effective.probe_finish
        else:
            self.get_mcu = self.on_error
            self.home_start = self.on_error
            self.home_wait = self.on_error
            self.multi_probe_begin = self.on_error
            self.multi_probe_end = self.on_error
            self.probe_prepare = self.on_error
            self.probe_finish = self.on_error

    def add_stepper(self, stepper):
        self._steppers.append(stepper)
        for m in self._mcus:
            m.add_stepper(stepper)
        if self.z_mcu:
            existing = set()
            if hasattr(self.z_mcu, 'get_steppers'):
                existing = set(self.z_mcu.get_steppers())
            if stepper not in existing:
                self.z_mcu.add_stepper(stepper)

    def get_steppers(self):
        return list(self._steppers)

    def on_error(self, *args, **kwargs):
        raise self.printer.command_error(
            "Cannot interact with probe - no active tool probe.")

    def query_endstop(self, print_time):
        effective = self._get_effective_mcu()
        if not effective:
            raise self.printer.command_error(
                "Cannot query endstop - no active tool probe.")
        return effective.query_endstop(print_time)

    def get_position_endstop(self):
        effective = self._get_effective_mcu()
        if not effective:
            return 0.0
        return effective.get_position_endstop()


def load_config(config):
    return ToolProbeEndstop(config)
