import os
import re
import ast
from statistics import median, mean

from . import tools_calibrate
from . import toolchanger


class Offset:
    def __init__(self, config):
        self.printer       = config.get_printer()
        self.gcode         = self.printer.lookup_object('gcode')
        self.gcode_move    = self.printer.load_object(config, 'gcode_move')

        self.x_pos         = config.getfloat('zswitch_x_pos', None)
        self.y_pos         = config.getfloat('zswitch_y_pos', None)
        self.z_pos         = config.getfloat('zswitch_z_pos', None)

        self.lift_z        = config.getfloat('lift_z', 1.0)
        self.safe_start_z  = config.getfloat('safe_start_z', 6.0, minval=0.)

        self.move_speed    = config.getint('move_speed', 60)
        self.z_move_speed  = config.getint('z_move_speed', 10)

        # Samples are defined via config
        self.samples               = config.getint('samples', 10)
        self.samples_tolerance     = config.getfloat('samples_tolerance', 0.02, minval=0.)
        self.samples_max_count     = config.getint('samples_max_count', self.samples, minval=self.samples)

        # Z trigger calc method (median|average|trimmed)
        self.z_calc_method = (config.get('z_calc_method', 'median') or 'median').strip().lower()
        if self.z_calc_method not in ('median', 'average', 'avg', 'mean', 'trimmed', 'trim', 'trimmed_mean'):
            raise config.error("offset: z_calc_method must be one of: median, average, trimmed")

        # how many values to trim on each side for trimmed mean
        self.z_trim_count = config.getint('z_trim_count', 1, minval=0)

        self.pin              = config.get('pin', None)
        self.config_file_path = config.get('config_file_path', None)

        # Recovery against "Probe triggered prior to movement"
        self.recover_lift_mm      = config.getfloat('recover_lift_mm', 2.0, minval=0.)
        self.recover_pause_ms     = config.getint('recover_pause_ms', 150, minval=0)
        self.recover_max_attempts = config.getint('recover_max_attempts', 4, minval=1)

        # Default reference tool for Z (UI default should be T0 if exists)
        self.default_ref_tool = config.getint('default_ref_tool', 0, minval=0)
        self.last_ref_tool = self.default_ref_tool

        # Probe offset calibration settings
        self.probe_offset_x = config.getfloat('probe_offset_x', 125.0)
        self.probe_offset_y = config.getfloat('probe_offset_y', 115.0)
        self.probe_offset_samples = config.getint('probe_offset_samples', 3,
                                                   minval=1)
        self.probe_offset_z_hop = config.getfloat('probe_offset_z_hop', 10.0,
                                                    above=0.)
        self.probe_offset_travel_speed = config.getfloat(
            'probe_offset_travel_speed', 80.0, above=0.)

        self.gcode_macro = self.printer.load_object(config, 'gcode_macro')
        self.start_gcode = self.gcode_macro.load_template(config, 'start_gcode', '')
        self.before_pickup_gcode = self.gcode_macro.load_template(config, 'before_pickup_gcode', '')
        self.after_pickup_gcode  = self.gcode_macro.load_template(config, 'after_pickup_gcode', '')
        self.finish_gcode        = self.gcode_macro.load_template(config, 'finish_gcode', '')

        self.has_cfg_data = False
        self.probe_results = {}
        self.probe_cal_map = {}

        if self.pin is not None:
            self.probe_multi_axis = tools_calibrate.PrinterProbeMultiAxis(
                config,
                tools_calibrate.ProbeEndstopWrapper(config, 'x'),
                tools_calibrate.ProbeEndstopWrapper(config, 'y'),
                tools_calibrate.ProbeEndstopWrapper(config, 'z')
            )
            query_endstops = self.printer.load_object(config, 'query_endstops')
            query_endstops.register_endstop(
                self.probe_multi_axis.mcu_probe[-1].mcu_endstop,
                "Offset"
            )
        else:
            self.probe_multi_axis = None

        self.toolchanger = self.printer.load_object(config, 'toolchanger')
        self.printer.register_event_handler("klippy:connect", self.handle_connect)

        self.gcode.register_command('MOVE_TO_ZSWITCH', self.cmd_MOVE_TO_ZSWITCH)
        self.gcode.register_command('PROBE_ZSWITCH', self.cmd_PROBE_ZSWITCH)
        self.gcode.register_command('CALIBRATE_ALL_Z_OFFSETS', self.cmd_CALIBRATE_ALL_Z_OFFSETS)
        self.gcode.register_command('CALIBRATE_PROBE_OFFSETS',
                                    self.cmd_CALIBRATE_PROBE_OFFSETS,
                                    desc=self.cmd_CALIBRATE_PROBE_OFFSETS_help)

        self.gcode.register_command('OFFSET_START_GCODE', self.cmd_OFFSET_START_GCODE)
        self.gcode.register_command('OFFSET_BEFORE_PICKUP_GCODE', self.cmd_OFFSET_BEFORE_PICKUP_GCODE)
        self.gcode.register_command('OFFSET_AFTER_PICKUP_GCODE', self.cmd_OFFSET_AFTER_PICKUP_GCODE)
        self.gcode.register_command('OFFSET_FINISH_GCODE', self.cmd_OFFSET_FINISH_GCODE)

        self.gcode.register_command('SET_PROBE_CAL_MAP',
                                    self.cmd_SET_PROBE_CAL_MAP,
                                    desc="Set probe assignment for a tool (used by CALIBRATE_PROBE_OFFSETS)")
        self.gcode.register_command('SET_TOOL_GCODE_OFFSET',
                                    self.cmd_SET_TOOL_GCODE_OFFSET,
                                    desc="Set gcode_x/y/z_offset on a tool and stage for SAVE_CONFIG")

    def handle_connect(self):
        if self.config_file_path:
            self.config_file_path = os.path.expanduser(self.config_file_path)
            if os.path.exists(self.config_file_path):
                self.has_cfg_data = True
                self.gcode.respond_info(f"Offset config file found ({self.config_file_path})")
            else:
                self.gcode.respond_info(f"Offset config file not found ({self.config_file_path})")

    def is_homed(self):
        toolhead = self.printer.lookup_object('toolhead')
        homed = toolhead.get_kinematics().get_status(
            self.printer.get_reactor().monotonic()
        )['homed_axes']
        return all(a in homed for a in 'xyz')

    def has_switch_pos(self):
        return all(v is not None for v in (self.x_pos, self.y_pos, self.z_pos))

    def get_status(self, eventtime):
        tp_offsets = {}
        for tn in self.toolchanger.tool_numbers:
            try:
                tp = self.printer.lookup_object('tool_probe T%d' % tn)
                tp_offsets[str(tn)] = tp.probe_offsets.z_offset
            except Exception:
                pass
        # Current gcode offsets per tool
        tool_gcode_offsets = {}
        for tn in self.toolchanger.tool_numbers:
            try:
                tool_obj = self.printer.lookup_object('tool T%d' % tn)
                tool_gcode_offsets[str(tn)] = {
                    'x': tool_obj.gcode_x_offset,
                    'y': tool_obj.gcode_y_offset,
                    'z': tool_obj.gcode_z_offset,
                }
            except Exception:
                pass
        # Discover available probe objects
        available_probes = []
        for obj_name, obj in self.printer.lookup_objects('probe'):
            if obj_name and 'tool_probe_endstop' not in obj_name:
                available_probes.append(obj_name)
        for obj_name, obj in self.printer.lookup_objects('probe_eddy_ng'):
            if obj_name:
                available_probes.append(obj_name)
        # Current probe_cal_map as string keys for JSON
        pcm = {}
        for k, v in self.probe_cal_map.items():
            pcm[str(k)] = v
        return {
            'probe_results': self.probe_results,
            'tool_probe_offsets': tp_offsets,
            'has_cfg_data': self.has_cfg_data,
            'has_switch_pos': self.has_switch_pos(),
            'z_calc_method': self.z_calc_method,
            'z_trim_count': self.z_trim_count,
            'ref_tool': self.last_ref_tool,
            'available_probes': available_probes,
            'probe_cal_map': pcm,
            'tool_gcode_offsets': tool_gcode_offsets,
        }

    def cmd_MOVE_TO_ZSWITCH(self, gcmd):
        if not self.is_homed():
            gcmd.respond_error("Must home first")
            return
        if not self.has_switch_pos():
            gcmd.respond_error("Z switch positions invalid")
            return

        toolhead = self.printer.lookup_object('toolhead')
        toolhead.wait_moves()
        cur = toolhead.get_position()

        self.gcode_move.cmd_G1(
            self.gcode.create_gcode_command(
                "G0", "G0",
                {'X': self.x_pos, 'Y': self.y_pos, 'Z': cur[2], 'F': self.move_speed * 60}
            )
        )

        target_z = max(self.z_pos + self.lift_z, self.safe_start_z)
        toolhead.manual_move([None, None, target_z], self.z_move_speed)
        toolhead.wait_moves()

    def _run_probe_with_recovery(self, gcmd):
        toolhead = self.printer.lookup_object('toolhead')
        last_err = None

        probe_gcmd = self.gcode.create_gcode_command(
            "PROBE_ZSWITCH", "PROBE_ZSWITCH",
            {'SAMPLES': 1, 'SAMPLES_TOLERANCE': 0.0, 'SAMPLES_MAX_COUNT': 1}
        )

        for _ in range(self.recover_max_attempts):
            try:
                return self.probe_multi_axis.run_probe(
                    "z-", probe_gcmd, speed_ratio=0.5, max_distance=10.0, samples=1
                )[2]
            except Exception as e:
                last_err = e
                if "triggered prior to movement" not in str(e).lower():
                    raise
                toolhead.wait_moves()
                cur = toolhead.get_position()
                toolhead.manual_move(
                    [None, None, cur[2] + self.recover_lift_mm],
                    self.z_move_speed
                )
                toolhead.wait_moves()
                if self.recover_pause_ms:
                    self.gcode.run_script_from_command(f"G4 P{self.recover_pause_ms}")

        raise gcmd.error(f"Offset: Probe still triggered after recovery. {last_err}")

    def _effective_calc_method(self, gcmd):
        method = (gcmd.get('Z_CALC', self.z_calc_method) or self.z_calc_method).strip().lower()
        if method in ('avg', 'mean'):
            return 'average'
        if method in ('trim', 'trimmed_mean'):
            return 'trimmed'
        if method in ('median', 'average', 'trimmed'):
            return method
        return 'median'

    def _calc_value(self, samples, method):
        if method == 'average':
            return mean(samples)
        if method == 'trimmed':
            trim = int(self.z_trim_count)
            n = len(samples)
            if trim <= 0:
                return mean(samples)
            if n <= 2 * trim:
                return median(samples)
            s = sorted(samples)
            s2 = s[trim:n-trim]
            return mean(s2)
        return median(samples)

    def _probe_zswitch(self, gcmd):
        requested = gcmd.get_int('SAMPLES', self.samples, minval=1)
        max_count = gcmd.get_int('SAMPLES_MAX_COUNT', self.samples_max_count, minval=requested)
        tolerance = gcmd.get_float('SAMPLES_TOLERANCE', self.samples_tolerance, minval=0.)

        toolhead = self.printer.lookup_object('toolhead')
        total_taken = 0
        last_spread = None

        while total_taken + requested <= max_count:
            batch_samples = []

            for _ in range(requested):
                z = self._run_probe_with_recovery(gcmd)
                batch_samples.append(z)
                total_taken += 1

                toolhead.wait_moves()
                cur = toolhead.get_position()
                target_z = max(cur[2] + self.recover_lift_mm, self.safe_start_z)
                toolhead.manual_move([None, None, target_z], self.z_move_speed)
                toolhead.wait_moves()

            spread = max(batch_samples) - min(batch_samples)
            last_spread = spread
            if spread <= tolerance:
                method = self._effective_calc_method(gcmd)
                return self._calc_value(batch_samples, method)

        attempted_batches = max_count // requested
        raise gcmd.error(
            f"Probe spread {last_spread:.5f} exceeds tolerance {tolerance:.5f} "
            f"after {attempted_batches} batch(es) of {requested} samples"
        )

    def cmd_PROBE_ZSWITCH(self, gcmd):
        toolhead = self.printer.lookup_object('toolhead')
        tool_no = str(self.toolchanger.active_tool.tool_number)
        start_pos = toolhead.get_position()

        z = self._probe_zswitch(gcmd)
        t = self.printer.get_reactor().monotonic()

        # Neutral: only store trigger; offset is set by CALIBRATE_ALL_Z_OFFSETS referencing logic.
        if tool_no not in self.probe_results:
            self.probe_results[tool_no] = {}
        self.probe_results[tool_no].update({'z_trigger': z, 'z_offset': 0.0, 'last_run': t})

        toolhead.move(start_pos, self.z_move_speed)
        toolhead.set_position(start_pos)
        toolhead.wait_moves()

    def cmd_CALIBRATE_ALL_Z_OFFSETS(self, gcmd):
        if not self.is_homed():
            gcmd.respond_error("Must home first")
            return

        self.cmd_OFFSET_START_GCODE(gcmd)

        z_calc = (gcmd.get('Z_CALC', None) or '').strip().lower()
        if z_calc and z_calc not in ('median', 'average', 'avg', 'mean', 'trimmed', 'trim', 'trimmed_mean'):
            gcmd.respond_error("Invalid Z_CALC. Use median, average or trimmed")
            return

        effective_method = self._effective_calc_method(gcmd)
        origin = "override" if z_calc else "config default"

        self.gcode.respond_info(f"Offset: Z calculation method = {effective_method} ({origin})")
        self.gcode.run_script_from_command(f"M118 Offset: Z calc = {effective_method} ({origin})")

        selected_tools = gcmd.get('TOOLS', None)
        if selected_tools:
            requested = []
            for token in selected_tools.split(','):
                token = token.strip()
                if token.isdigit():
                    requested.append(int(token))
        else:
            requested = list(self.toolchanger.tool_numbers)

        # Sorted list for stable fallback behavior
        available_tools = sorted(self.toolchanger.tool_numbers)
        if not available_tools:
            gcmd.respond_error("No tools available")
            return

        # Reference tool with fallback:
        # - prefer gcmd REF
        # - else prefer config default_ref_tool
        # - if that doesn't exist -> fallback to smallest tool number
        ref_tool = gcmd.get_int('REF', self.default_ref_tool, minval=0)
        if ref_tool not in available_tools:
            ref_tool = available_tools[0]

        # Build ordered tool list
        available_set = set(available_tools)
        ordered_tools = []
        seen = set()
        for tool in requested:
            if tool in available_set and tool not in seen:
                seen.add(tool)
                ordered_tools.append(tool)

        if not ordered_tools:
            gcmd.respond_error("No valid tools selected")
            return

        # Ensure reference is included and first
        if ref_tool not in ordered_tools:
            ordered_tools.insert(0, ref_tool)
        ordered_tools = [ref_tool] + [t for t in ordered_tools if t != ref_tool]

        self.last_ref_tool = ref_tool

        # Clean run
        self.probe_results = {}
        ref_trigger = None

        for tool in ordered_tools:
            self.cmd_OFFSET_BEFORE_PICKUP_GCODE(gcmd)
            self.gcode.run_script_from_command(f"T{tool}")
            self.cmd_OFFSET_AFTER_PICKUP_GCODE(gcmd)

            self.gcode.run_script_from_command("MOVE_TO_ZSWITCH")

            z_calc_arg = f" Z_CALC={z_calc}" if z_calc else ""
            self.gcode.run_script_from_command(
                f"PROBE_ZSWITCH SAMPLES={self.samples} "
                f"SAMPLES_TOLERANCE={self.samples_tolerance} "
                f"SAMPLES_MAX_COUNT={self.samples_max_count}" + z_calc_arg
            )

            # Re-reference offsets to REF tool
            key = str(tool)
            if key in self.probe_results:
                z_trig = self.probe_results[key]['z_trigger']

                if tool == ref_tool:
                    ref_trigger = z_trig
                    self.probe_results[key]['z_offset'] = 0.0
                    self.probe_results[key]['ref_tool'] = ref_tool
                else:
                    if ref_trigger is None:
                        self.probe_results[key]['z_offset'] = 0.0
                    else:
                        self.probe_results[key]['z_offset'] = z_trig - ref_trigger
                    self.probe_results[key]['ref_tool'] = ref_tool

        self.cmd_OFFSET_FINISH_GCODE(gcmd)

    # ─── Probe offset calibration helpers ───────────────────────────────

    def _do_tap_probe(self, probe_obj, samples):
        """Run a single probe cycle via the standard probe interface."""
        from . import probe as probe_mod
        dummy_gcmd = self.gcode.create_gcode_command("", "", {
            "SAMPLES": str(samples),
            "SAMPLES_RESULT": "median",
        })
        result = probe_mod.run_single_probe(probe_obj, dummy_gcmd)
        return result.bed_z

    def _is_eddy_probe(self, probe_name):
        """Check if a probe name refers to an Eddy-NG probe."""
        return 'eddy' in probe_name.lower()

    def _get_probe_for_tool(self, tool_nr, ref_tool):
        """Get probe name for a tool from probe_cal_map with fallback."""
        if tool_nr in self.probe_cal_map:
            return self.probe_cal_map[tool_nr]
        # Fallback: ref_tool gets first eddy probe if available, others get 'probe'
        if tool_nr == ref_tool:
            for obj_name, obj in self.printer.lookup_objects('probe_eddy_ng'):
                if obj_name:
                    return obj_name
        return 'probe'

    # ─── CALIBRATE_PROBE_OFFSETS ─────────────────────────────────────────

    cmd_CALIBRATE_PROBE_OFFSETS_help = (
        "Calibrate tool_probe z_offset for each tool. "
        "Uses probe_cal_map to determine probe per tool (Eddy Tap or "
        "mechanical Tap). REF_TOOL selects the bed reference tool. "
        "Requires CALIBRATE_ALL_Z_OFFSETS to have been "
        "run first (needs z_offset / gcode_z_offset data). "
        "TOOLS=0,1,2,3 to select tools (default: all with z_offset data). "
        "APPLY=1 (default) sets z_offset at runtime and stages config save.")

    def cmd_CALIBRATE_PROBE_OFFSETS(self, gcmd):
        if not self.is_homed():
            raise gcmd.error("Must home first")

        # Check that z_offset data exists from Z-switch calibration
        if not self.probe_results:
            raise gcmd.error(
                "No Z-switch data. Run CALIBRATE_ALL_Z_OFFSETS first")

        apply_offsets = gcmd.get_int('APPLY', 1)
        samples = gcmd.get_int('SAMPLES', self.probe_offset_samples, minval=1)
        probe_x = gcmd.get_float('PROBE_X', self.probe_offset_x)
        probe_y = gcmd.get_float('PROBE_Y', self.probe_offset_y)
        z_hop = gcmd.get_float('Z_HOP', self.probe_offset_z_hop, above=0.)
        travel_speed = gcmd.get_float('TRAVEL_SPEED',
                                       self.probe_offset_travel_speed, above=0.)

        # REF_TOOL parameter (was hardcoded to 0)
        ref_tool = gcmd.get_int('REF_TOOL', self.default_ref_tool, minval=0)

        # Parse TOOLS parameter
        tools_param = gcmd.get('TOOLS', None)
        available_tools = sorted(self.toolchanger.tool_numbers)

        if tools_param is not None:
            try:
                requested = [int(t.strip())
                             for t in tools_param.split(',') if t.strip()]
            except ValueError:
                raise gcmd.error(
                    "TOOLS must be comma-separated integers, e.g. TOOLS=0,1,2")
            for t in requested:
                if t not in available_tools:
                    raise gcmd.error(f"Tool T{t} not configured")
            calibrate_tools = requested
        else:
            # Default: all tools that have z_offset data
            calibrate_tools = [t for t in available_tools
                               if str(t) in self.probe_results]

        if not calibrate_tools:
            raise gcmd.error("No tools to calibrate")

        # Ensure ref_tool is valid
        if ref_tool not in available_tools:
            ref_tool = available_tools[0]

        # Verify all requested tools have z_offset data
        missing = [t for t in calibrate_tools
                   if str(t) not in self.probe_results]
        if missing:
            raise gcmd.error(
                "Missing Z-switch data for T%s. "
                "Run CALIBRATE_ALL_Z_OFFSETS first"
                % ",".join(str(t) for t in missing))

        toolhead = self.printer.lookup_object('toolhead')
        probe_obj = self.printer.lookup_object('probe')

        # Get ref probe from map
        ref_probe_name = self._get_probe_for_tool(ref_tool, ref_tool)
        ref_is_eddy = self._is_eddy_probe(ref_probe_name)

        # ── Step 1: Reference probe on ref_tool → Z=0 at true nozzle contact ──
        self.gcode.respond_info("=== Probe Offset Calibration ===")
        self.gcode.respond_info(
            "Tools: %s  Ref: T%d (%s)"
            % (", ".join("T%d" % t for t in calibrate_tools),
               ref_tool, ref_probe_name))

        self.gcode.respond_info(
            "Step 1: %s on T%d (bed reference)"
            % ("Eddy Tap" if ref_is_eddy else "Tap", ref_tool))

        self.gcode.run_script_from_command(
            "SELECT_TOOL T=%d RESTORE_AXIS=XYZ" % ref_tool)
        self.gcode.run_script_from_command("STOP_TOOL_PROBE_CRASH_DETECTION")
        self.gcode.run_script_from_command(
            "SET_ACTIVE_TOOL_PROBE T=%d" % ref_tool)

        # Position nozzle at probe point
        self.gcode_move.cmd_G1(
            self.gcode.create_gcode_command(
                "G0", "G0",
                {'X': probe_x, 'Y': probe_y, 'F': travel_speed * 60}
            )
        )
        toolhead.wait_moves()

        # Use ref probe (Eddy or Tap) based on map
        if ref_is_eddy:
            self.gcode.run_script_from_command(
                'SET_ACTIVE_Z_PROBE PROBE="%s"' % ref_probe_name)
            self.gcode.run_script_from_command(
                "PROBE_EDDY_NG_TAP HOME_Z=1 SAMPLES=%d" % samples)
            self.gcode.respond_info(
                "T%d Eddy Tap: Z=0 set at nozzle contact" % ref_tool)
        else:
            self.gcode.run_script_from_command(
                "SET_ACTIVE_Z_PROBE PROBE=none")
            # Tap reference: probe and set Z=0 at contact
            toolhead.manual_move([None, None, 5.0], 10.)
            toolhead.wait_moves()
            bed_z = self._do_tap_probe(probe_obj, samples)
            self.gcode.respond_info(
                "T%d Tap: bed_z=%.4f (reference)" % (ref_tool, bed_z))

        toolhead.manual_move([None, None, z_hop], 10.)
        toolhead.wait_moves()

        # ── Step 2: Probe on each selected tool ──
        self.gcode.respond_info("Step 2: Probe per tool")

        for tool_nr in calibrate_tools:
            key = str(tool_nr)
            gcode_z_off = self.probe_results[key]['z_offset']

            # Get probe for this tool from map
            tool_probe_name = self._get_probe_for_tool(tool_nr, ref_tool)
            tool_is_eddy = self._is_eddy_probe(tool_probe_name)

            self.gcode.respond_info(
                "--- T%d (gcode_z_offset=%.4f, probe=%s) ---"
                % (tool_nr, gcode_z_off, tool_probe_name))

            if tool_nr != ref_tool or self.toolchanger.active_tool.tool_number != ref_tool:
                self.gcode.run_script_from_command(
                    "SELECT_TOOL T=%d RESTORE_AXIS=Z" % tool_nr)
            self.gcode.run_script_from_command("STOP_TOOL_PROBE_CRASH_DETECTION")
            self.gcode.run_script_from_command(
                "SET_ACTIVE_TOOL_PROBE T=%d" % tool_nr)

            # Activate probe from map
            if tool_is_eddy:
                self.gcode.run_script_from_command(
                    'SET_ACTIVE_Z_PROBE PROBE="%s"' % tool_probe_name)
            else:
                self.gcode.run_script_from_command(
                    "SET_ACTIVE_Z_PROBE PROBE=none")

            # Position nozzle at probe point
            self.gcode_move.cmd_G1(
                self.gcode.create_gcode_command(
                    "G0", "G0",
                    {'X': probe_x, 'Y': probe_y, 'F': travel_speed * 60}
                )
            )
            toolhead.manual_move([None, None, 5.0], 10.)
            toolhead.wait_moves()

            # Get current tool_probe z_offset (subtracted inside run_single_probe)
            current_pz = probe_obj.get_offsets()[2]

            # Probe via z_probe router (SET_ACTIVE_Z_PROBE already
            # routes to the correct Eddy or Tap probe for this tool)
            bed_z = self._do_tap_probe(probe_obj, samples)

            # After HOME_Z=1: Z=0 at ref_tool nozzle contact.
            # Tn contacts bed at kinematic Z = gcode_z_off (ToolGcodeTransform
            # adds offset: kinematic = gcode + offset).
            # Tap triggers at kinematic Z = gcode_z_off + true_pz.
            # run_single_probe: bed_z = trigger_z - current_pz.
            # → true_pz = bed_z + current_pz - gcode_z_off
            probe_z_offset = bed_z + current_pz - gcode_z_off

            self.probe_results[key]['probe_z_offset'] = probe_z_offset
            self.probe_results[key]['tap_bed_z'] = bed_z

            self.gcode.respond_info(
                "T%d: Tap bed_z=%.4f  probe_z_offset=%.4f"
                % (tool_nr, bed_z, probe_z_offset))

            if apply_offsets:
                try:
                    tp = self.printer.lookup_object(
                        'tool_probe T%d' % tool_nr)
                    tp.probe_offsets.z_offset = probe_z_offset
                    configfile = self.printer.lookup_object('configfile')
                    configfile.set('tool_probe T%d' % tool_nr,
                                   'z_offset', '%.3f' % probe_z_offset)
                    self.gcode.respond_info(
                        "T%d: z_offset applied (SAVE_CONFIG to persist)"
                        % tool_nr)
                except Exception as e:
                    self.gcode.respond_info(
                        "T%d: could not apply z_offset: %s"
                        % (tool_nr, str(e)))

            toolhead.manual_move([None, None, z_hop], 10.)
            toolhead.wait_moves()

        # ── Restore ref_tool with its probe routing ──
        if self.toolchanger.active_tool.tool_number != ref_tool:
            self.gcode.run_script_from_command(
                "SELECT_TOOL T=%d RESTORE_AXIS=XZ" % ref_tool)
        self.gcode.run_script_from_command(
            "SET_ACTIVE_TOOL_PROBE T=%d" % ref_tool)

        # Restore ref probe routing from map (not hardcoded eddy)
        ref_restore = self._get_probe_for_tool(ref_tool, ref_tool)
        if self._is_eddy_probe(ref_restore):
            self.gcode.run_script_from_command(
                'SET_ACTIVE_Z_PROBE PROBE="%s"' % ref_restore)
        else:
            self.gcode.run_script_from_command(
                "SET_ACTIVE_Z_PROBE PROBE=none")

        # ── Summary ──
        self.gcode.respond_info("=== Probe Offset Calibration Complete ===")
        for tool_nr in calibrate_tools:
            key = str(tool_nr)
            data = self.probe_results[key]
            pzo = data.get('probe_z_offset', 0.0)
            zo = data.get('z_offset', 0.0)
            saved = " [APPLIED]" if apply_offsets else ""
            self.gcode.respond_info(
                "T%d: gcode_z_offset=%.4f  probe_z_offset=%.4f%s"
                % (tool_nr, zo, pzo, saved))
        if apply_offsets:
            self.gcode.respond_info(
                "Offsets applied at runtime. Use SAVE_CONFIG to persist.")

    # ─── Gcode macro hooks ───────────────────────────────────────────────

    def cmd_OFFSET_START_GCODE(self, gcmd):
        if self.start_gcode:
            self.start_gcode.run_gcode_from_command({})

    def cmd_OFFSET_BEFORE_PICKUP_GCODE(self, gcmd):
        if self.before_pickup_gcode:
            self.before_pickup_gcode.run_gcode_from_command({})

    def cmd_OFFSET_AFTER_PICKUP_GCODE(self, gcmd):
        if self.after_pickup_gcode:
            self.after_pickup_gcode.run_gcode_from_command({})

    def cmd_OFFSET_FINISH_GCODE(self, gcmd):
        if self.finish_gcode:
            self.finish_gcode.run_gcode_from_command({})

    def cmd_SET_PROBE_CAL_MAP(self, gcmd):
        tool = gcmd.get_int('TOOL', None)
        if tool is None:
            raise gcmd.error("SET_PROBE_CAL_MAP requires TOOL parameter")
        raw = gcmd.get_commandline()
        probe_match = None
        m = re.search(r'PROBE="([^"]+)"', raw, re.IGNORECASE)
        if m:
            probe_match = m.group(1)
        else:
            m = re.search(r'PROBE=(\S+)', raw, re.IGNORECASE)
            if m:
                probe_match = m.group(1)
        if not probe_match:
            raise gcmd.error("SET_PROBE_CAL_MAP requires PROBE parameter")
        self.probe_cal_map[tool] = probe_match
        self.gcode.respond_info(
            "Probe cal map: T%d -> %s" % (tool, probe_match))

    # ─── SET_TOOL_GCODE_OFFSET ───────────────────────────────────────────

    def cmd_SET_TOOL_GCODE_OFFSET(self, gcmd):
        tool_nr = gcmd.get_int('T', None)
        if tool_nr is None:
            raise gcmd.error("SET_TOOL_GCODE_OFFSET requires T parameter")
        try:
            tool_obj = self.printer.lookup_object('tool T%d' % tool_nr)
        except Exception:
            raise gcmd.error("Tool T%d not found" % tool_nr)

        changed = []
        for axis in ('x', 'y', 'z'):
            param = axis.upper()
            val = gcmd.get_float(param, None)
            if val is not None:
                name = 'gcode_%s_offset' % axis
                tool_obj.set_parameter(name, '%.6f' % val)
                tool_obj.save_parameter(name)
                changed.append('%s=%.4f' % (param, val))

        if changed:
            self.gcode.respond_info(
                "T%d offsets set: %s (SAVE_CONFIG to persist)"
                % (tool_nr, ', '.join(changed)))
        else:
            self.gcode.respond_info(
                "T%d: no offset parameters provided" % tool_nr)


def load_config(config):
    return Offset(config)
