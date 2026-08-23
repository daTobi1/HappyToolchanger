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
        self._op_z_probes = {}  # Per-operation z_probe map
        self.gcode_macro = self.printer.load_object(config, 'gcode_macro')
        self.crash_detection_active = False
        self.crash_lasttime = 0.
        self.mcu_probe = EndstopRouter(self.printer)
        self.probe_offsets = ToolProbeOffsetsHelper(self)
        self.param_helper = probe.ProbeParameterHelper(config)
        probe.HomingViaProbeHelper(config, 0.)
        self.cmd_helper = probe.ProbeCommandHelper(config, self)

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
        self.gcode.register_command(
            'APPLY_TOOL_PROBE_FOR',
            self.cmd_APPLY_TOOL_PROBE_FOR,
            desc=self.cmd_APPLY_TOOL_PROBE_FOR_help)

    def _handle_connect(self):
        self.toolhead = self.printer.lookup_object('toolhead')
        # Ensure Z steppers are registered with tool probes.
        # When stepper_z uses a different endstop (e.g. Eddy via
        # probe_eddy:z_virtual_endstop), the EndstopRouter doesn't
        # receive steppers from config.  Add them here so that Tap
        # probing (QGL coarse pass, etc.) still works.
        kin = self.toolhead.get_kinematics()
        for stepper in kin.get_steppers():
            if stepper.is_active_axis('z'):
                if stepper not in self.mcu_probe.get_steppers():
                    self.mcu_probe.add_stepper(stepper)
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

    @staticmethod
    def _probe_name(obj):
        """Full object name, usable with lookup_object / SET_ACTIVE_Z_PROBE.
        _full_name comes first: Eddy-NG's _name is only the short suffix
        ('my_eddy'), which cannot be looked up."""
        if obj is None:
            return None
        return getattr(obj, '_full_name',
               getattr(obj, 'name',
               getattr(obj, '_name', str(obj))))

    def get_status(self, eventtime):
        status = self.cmd_helper.get_status(eventtime)
        status['last_tools_query'] = self.last_query
        status['active_tool_number'] = self.active_tool_number
        # Current z_probe for probe sessions
        status['active_z_probe'] = self._probe_name(self.z_probe_obj)
        # Per-operation z_probe names
        for op in ('home', 'qgl', 'mesh'):
            status['z_probe_%s' % op] = self._probe_name(
                self._op_z_probes.get(op))
        # Tool probe (Tap) info
        if self.active_probe:
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
        # Set per-operation z_probes from tool config
        if tool_probe and hasattr(tool_probe, 'get_z_probe_for'):
            # Lazy resolution if needed
            if (tool_probe.z_probe is None
                    and getattr(tool_probe, 'z_probe_name', None)):
                tool_probe._resolve_z_probes()
            z_home = tool_probe.get_z_probe_for('home')
            z_default = tool_probe.get_z_probe_for('default')
            z_qgl = tool_probe.get_z_probe_for('qgl')
            z_mesh = tool_probe.get_z_probe_for('mesh')
        else:
            # Legacy fallback
            z_probe = None
            if tool_probe:
                z_probe = getattr(tool_probe, 'z_probe', None)
                if z_probe is None and getattr(
                        tool_probe, 'z_probe_name', None):
                    z_probe = self.printer.lookup_object(
                        tool_probe.z_probe_name, None)
                    if z_probe:
                        tool_probe.z_probe = z_probe
            z_home = z_default = z_qgl = z_mesh = z_probe
        self._set_z_home_endstop(z_home)
        self._set_z_probe_obj(z_default)
        self._op_z_probes = {
            'home': z_home, 'qgl': z_qgl,
            'mesh': z_mesh, 'default': z_default,
        }
        if tool_probe:
            names = {op: self._probe_name(p) or 'tap'
                     for op, p in self._op_z_probes.items()}
            logging.info("tool_probe_endstop: T%d z_probes: %s",
                         tool_probe.tool, names)

    def _set_z_probe_obj(self, z_probe_obj):
        """Set the external Z probe for probe sessions."""
        self.z_probe_obj = z_probe_obj

    def _set_z_home_endstop(self, z_probe_obj):
        """Set the Z homing endstop from a probe object."""
        z_endstop = None
        if z_probe_obj:
            z_endstop = getattr(z_probe_obj, '_endstop_wrapper',
                        getattr(z_probe_obj, 'mcu_probe', None))
        self.mcu_probe.set_z_mcu(z_endstop)

    def set_active_z_probe(self, z_probe_obj):
        """Set an external Z probe for probe sessions (QGL, MESH, PROBE).
        Used by SET_ACTIVE_Z_PROBE gcode command.
        Does NOT change the Z homing endstop (z_mcu).
        Pass None to clear and fall back to tool_probe.
        """
        self._set_z_probe_obj(z_probe_obj)
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
        elif len(active_tools) > 1:
            # Multiple probes open - try saved state as hint
            save_vars = self.printer.lookup_object('save_variables', None)
            if save_vars:
                saved_tool = save_vars.allVariables.get(
                    'htc_active_tool', -1)
                if saved_tool >= 0:
                    for tp in active_tools:
                        if tp.tool == saved_tool:
                            self.set_active_probe(tp)
                            logging.info(
                                "tool_probe_endstop: multiple probes"
                                " open (%s), using saved T%d",
                                [p.tool for p in active_tools],
                                saved_tool)
                            break

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

    cmd_APPLY_TOOL_PROBE_FOR_help = (
        "Apply the active tool's configured Z probe for a specific operation. "
        "OP=home|qgl|mesh|default")
    def cmd_APPLY_TOOL_PROBE_FOR(self, gcmd):
        op = gcmd.get("OP", "default").lower()
        if op not in ('home', 'qgl', 'mesh', 'default'):
            raise gcmd.error(
                "APPLY_TOOL_PROBE_FOR: OP must be home, qgl, mesh, or default")
        if not self._op_z_probes:
            raise gcmd.error(
                "APPLY_TOOL_PROBE_FOR: no active tool probe")
        z_probe = self._op_z_probes.get(op)
        name = self._probe_name(z_probe) or 'tap'
        if op == 'home':
            self._set_z_home_endstop(z_probe)
        else:
            self._set_z_probe_obj(z_probe)
        gcmd.respond_info("Z probe for %s: %s" % (op, name))

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
# Supports an optional z_mcu (e.g. Eddy) that takes priority over the
# active_mcu (Tap) for homing.
class EndstopRouter:
    def __init__(self, printer):
        self.active_mcu = None
        self.z_mcu = None  # External Z probe endstop (Eddy)
        self._mcus = []
        self._steppers = []
        self.printer = printer
        self._update_effective()

    @staticmethod
    def _get_endstop(obj):
        """Get the raw MCU endstop from a probe object."""
        return getattr(obj, 'mcu_endstop', obj)

    def add_mcu(self, mcu_probe):
        self._mcus.append(mcu_probe)
        endstop = self._get_endstop(mcu_probe)
        for s in self._steppers:
            endstop.add_stepper(s)

    def set_active_mcu(self, mcu_probe):
        self.active_mcu = mcu_probe
        self._update_effective()

    def set_z_mcu(self, z_mcu):
        """Set an external endstop (e.g. Eddy) for Z homing."""
        self.z_mcu = z_mcu
        if z_mcu:
            endstop = self._get_endstop(z_mcu)
            for s in self._steppers:
                if s not in endstop.get_steppers():
                    endstop.add_stepper(s)
        self._update_effective()
        self._update_rail_position_endstop()

    def _update_rail_position_endstop(self):
        """Update the Z rail's position_endstop to match the current z_mcu.
        position_endstop is read once at config time, but z_mcu is set later
        at runtime (when a tool is detected). Without this update, the rail
        keeps position_endstop=0.0, causing Z=0 to be off by the probe's
        home_trigger_height after G28 Z."""
        pos_endstop = self.get_position_endstop()
        try:
            toolhead = self.printer.lookup_object('toolhead')
            kin = toolhead.get_kinematics()
            for rail in kin.rails:
                for es, name in rail.get_endstops():
                    if es is self:
                        rail.position_endstop = pos_endstop
                        logging.info(
                            "tool_probe_endstop: updated Z rail "
                            "position_endstop to %.3f", pos_endstop)
                        return
        except Exception:
            pass  # Toolhead may not be ready yet during early init

    @staticmethod
    def _noop(*args, **kwargs):
        pass

    def _update_effective(self):
        # z_mcu (Eddy) takes priority for homing; fall back to active_mcu (Tap)
        effective = self.z_mcu or self.active_mcu
        if effective:
            endstop = self._get_endstop(effective)
            self.get_mcu = endstop.get_mcu
            self.home_start = endstop.home_start
            self.home_wait = endstop.home_wait
        else:
            self.get_mcu = self.on_error
            self.home_start = self.on_error
            self.home_wait = self.on_error

    def add_stepper(self, stepper):
        self._steppers.append(stepper)
        for m in self._mcus:
            self._get_endstop(m).add_stepper(stepper)

    def get_steppers(self):
        return list(self._steppers)

    def on_error(self, *args, **kwargs):
        raise self.printer.command_error(
            "Cannot interact with probe - no active tool probe.")

    def query_endstop(self, print_time):
        if not self.active_mcu:
            raise self.printer.command_error(
                "Cannot query endstop - no active tool probe.")
        endstop = self._get_endstop(self.active_mcu)
        return endstop.query_endstop(print_time)

    def get_position_endstop(self):
        effective = self.z_mcu or self.active_mcu
        if not effective:
            return 0.0
        if hasattr(effective, 'get_position_endstop'):
            return effective.get_position_endstop()
        return 0.0


def load_config(config):
    return ToolProbeEndstop(config)
