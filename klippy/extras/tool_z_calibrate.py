# Tool Z Offset Calibration — Nozzle height differences
#
# Uses Eddy-NG Tap (PROBE_EDDY_NG_TAP) on T0 as reference: detects
# exact nozzle contact with the bed via Eddy sensor. Then probes each
# other tool with its mechanical Tap. The difference is the pure nozzle
# height offset (gcode_z_offset).
#
# Config:  [tool_z_calibrate]
#          probe_x: 175       # X position for probing (bed center)
#          probe_y: 150       # Y position for probing (bed center)
#          samples: 5         # Probe samples per measurement
#          z_hop: 10          # Safe Z travel height
#          travel_speed: 100  # XY travel speed mm/s
#
# Usage:   CALIBRATE_TOOL_Z_OFFSETS [TOOLS=1,2,3] [SAMPLES=5] [APPLY=1]
#          TOOLS: comma-separated tool numbers to calibrate (default: all)
#          T0 Eddy Tap is always the reference (gcode_z_offset = 0).
#
# Copyright (C) 2026
# This file may be distributed under the terms of the GNU GPLv3 license.
import logging

class ToolZCalibrate:
    def __init__(self, config):
        self.printer = config.get_printer()
        self.gcode = self.printer.lookup_object('gcode')
        self.probe_x = config.getfloat('probe_x', 175.0)
        self.probe_y = config.getfloat('probe_y', 150.0)
        self.samples = config.getint('samples', 5, minval=1)
        self.z_hop = config.getfloat('z_hop', 10.0, above=0.)
        self.travel_speed = config.getfloat('travel_speed', 100., above=0.)
        self.last_results = {}
        self.gcode.register_command(
            'CALIBRATE_TOOL_Z_OFFSETS',
            self.cmd_CALIBRATE_TOOL_Z_OFFSETS,
            desc=self.cmd_CALIBRATE_TOOL_Z_OFFSETS_help)

    cmd_CALIBRATE_TOOL_Z_OFFSETS_help = (
        "Calibrate Z offsets for tools. Uses Eddy Tap on T0 as reference "
        "(exact nozzle contact), then mechanical Tap on other tools. "
        "TOOLS=1,2,3 to select tools (default: all). "
        "APPLY=1 (default) saves offsets immediately.")

    def cmd_CALIBRATE_TOOL_Z_OFFSETS(self, gcmd):
        samples = gcmd.get_int('SAMPLES', self.samples, minval=1)
        apply_offsets = gcmd.get_int('APPLY', 1)
        probe_x = gcmd.get_float('PROBE_X', self.probe_x)
        probe_y = gcmd.get_float('PROBE_Y', self.probe_y)
        tools_param = gcmd.get('TOOLS', None)

        toolhead = self.printer.lookup_object('toolhead')
        probe_obj = self.printer.lookup_object('probe')
        toolchanger = self.printer.lookup_object('toolchanger')

        # Check homing
        curtime = self.printer.get_reactor().monotonic()
        homed = toolhead.get_status(curtime)['homed_axes']
        if 'z' not in homed:
            raise gcmd.error("Must home all axes first (G28)")

        # Parse TOOLS parameter
        all_tool_numbers = toolchanger.tool_numbers
        if tools_param is not None:
            try:
                requested = [int(t.strip())
                             for t in tools_param.split(',') if t.strip()]
            except ValueError:
                raise gcmd.error(
                    "TOOLS parameter must be comma-separated integers, "
                    "e.g. TOOLS=1,2,3")
            for t in requested:
                if t not in all_tool_numbers:
                    raise gcmd.error("Tool T%d not configured" % t)
            # T0 is always reference, never a calibration target
            calibrate_tools = [t for t in requested if t != 0]
            if not calibrate_tools:
                raise gcmd.error(
                    "No tools to calibrate (T0 is the reference)")
        else:
            calibrate_tools = [t for t in all_tool_numbers if t != 0]

        # ── Step 1: Reference via Eddy Tap on T0 ──
        # PROBE_EDDY_NG_TAP HOME_Z=1 sets Z=0 at exact nozzle contact.
        gcmd.respond_info("=== Tool Z Offset Calibration ===")
        gcmd.respond_info("Tools to calibrate: %s"
                          % ", ".join("T%d" % t for t in calibrate_tools))
        gcmd.respond_info("Reference: T0 with Eddy Tap (nozzle contact)")

        self.gcode.run_script_from_command("SELECT_TOOL T=0 RESTORE_AXIS=XYZ")
        self.gcode.run_script_from_command("STOP_TOOL_PROBE_CRASH_DETECTION")
        self.gcode.run_script_from_command("SET_ACTIVE_TOOL_PROBE T=0")

        # Position T0 nozzle at probe point
        toolhead.manual_move([probe_x, probe_y], self.travel_speed)
        toolhead.wait_moves()

        # Eddy Tap: HOME_Z=1 resets Z=0 at nozzle contact point
        self.gcode.run_script_from_command(
            "PROBE_EDDY_NG_TAP HOME_Z=1 SAMPLES=%d" % samples)

        gcmd.respond_info("T0 reference: Z=0 set at nozzle contact (Eddy Tap)")

        # Move to safe height (Z=0 is now at T0 nozzle contact)
        toolhead.manual_move([None, None, self.z_hop], 10.)
        toolhead.wait_moves()

        # ── Step 2: Probe selected tools with mechanical Tap ──
        # After HOME_Z=1, Z=0 = T0 nozzle at bed.
        # Probing Tn with Tap returns bed_z in this coordinate system.
        # If Tn nozzle is higher → Tap triggers at Z<0 → offset = -bed_z > 0.
        results = {}
        for tool_nr in calibrate_tools:
            gcmd.respond_info("--- Calibrating T%d ---" % tool_nr)
            self.gcode.run_script_from_command(
                "SELECT_TOOL T=%d RESTORE_AXIS=Z" % tool_nr)
            self.gcode.run_script_from_command(
                "STOP_TOOL_PROBE_CRASH_DETECTION")
            self.gcode.run_script_from_command(
                "SET_ACTIVE_TOOL_PROBE T=%d" % tool_nr)
            # Force mechanical Tap (disable Eddy routing)
            self.gcode.run_script_from_command(
                "SET_ACTIVE_Z_PROBE PROBE=none")

            # Position nozzle at probe point
            toolhead.manual_move([probe_x, probe_y], self.travel_speed)
            toolhead.manual_move([None, None, 5.0], 10.)
            toolhead.wait_moves()

            # Probe with mechanical Tap
            z_tool = self._do_probe(probe_obj, samples)
            offset = -z_tool  # Z=0 is T0 reference
            results[tool_nr] = offset

            gcmd.respond_info(
                "T%d Z (Tap): %.4f  ->  gcode_z_offset: %.4f"
                % (tool_nr, z_tool, offset))

            if apply_offsets:
                self.gcode.run_script_from_command(
                    'SET_TOOL_PARAMETER T=%d PARAMETER=gcode_z_offset '
                    'VALUE="%.4f"' % (tool_nr, offset))
                self.gcode.run_script_from_command(
                    'SAVE_TOOL_PARAMETER T=%d PARAMETER=gcode_z_offset'
                    % tool_nr)

            toolhead.manual_move([None, None, self.z_hop], 10.)
            toolhead.wait_moves()

        # ── Return to T0, restore Eddy routing ──
        self.gcode.run_script_from_command("SELECT_TOOL T=0 RESTORE_AXIS=XZ")
        self.gcode.run_script_from_command("SET_ACTIVE_TOOL_PROBE T=0")
        self.gcode.run_script_from_command(
            'SET_ACTIVE_Z_PROBE PROBE="probe_eddy_ng my_eddy"')

        # ── Summary ──
        self.last_results = results
        gcmd.respond_info("=== Calibration Complete ===")
        gcmd.respond_info("T0: gcode_z_offset = 0 (Eddy Tap reference)")
        for tool_nr in sorted(results):
            saved = " [SAVED]" if apply_offsets else ""
            gcmd.respond_info(
                "T%d: gcode_z_offset = %.4f%s"
                % (tool_nr, results[tool_nr], saved))

    def _do_probe(self, probe_obj, samples):
        """Run a single probe cycle and return bed_z."""
        from . import probe as probe_mod
        dummy_gcmd = self.gcode.create_gcode_command("", "", {
            "SAMPLES": str(samples),
            "SAMPLES_RESULT": "median",
        })
        result = probe_mod.run_single_probe(probe_obj, dummy_gcmd)
        return result.bed_z

    def get_status(self, eventtime):
        return {'last_results': self.last_results}

def load_config(config):
    return ToolZCalibrate(config)
