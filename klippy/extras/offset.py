import os
import re
import ast
import json
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

        # Bed mesh: tool to borrow when the mounted tool has no scanning
        # probe. Per-tool override lives in [tool_probe Tn] mesh_tool.
        self.mesh_tool = config.getint('mesh_tool', None)

        self.gcode_macro = self.printer.load_object(config, 'gcode_macro')
        self.start_gcode = self.gcode_macro.load_template(config, 'start_gcode', '')
        self.before_pickup_gcode = self.gcode_macro.load_template(config, 'before_pickup_gcode', '')
        self.after_pickup_gcode  = self.gcode_macro.load_template(config, 'after_pickup_gcode', '')
        self.finish_gcode        = self.gcode_macro.load_template(config, 'finish_gcode', '')
        # Runs after the mesh tool is picked up, before its nozzle tap
        # (e.g. CLEAN_NOZZLE — a dirty nozzle ruins the tap).
        self.mesh_tool_gcode     = self.gcode_macro.load_template(config, 'mesh_tool_gcode', '')

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

        self.gcode.register_command('NOZZLE_ZERO',
                                    self.cmd_NOZZLE_ZERO,
                                    desc=self.cmd_NOZZLE_ZERO_help)
        self.gcode.register_command('APPLY_TOOL_Z_OFFSETS',
                                    self.cmd_APPLY_TOOL_Z_OFFSETS,
                                    desc=self.cmd_APPLY_TOOL_Z_OFFSETS_help)
        self.gcode.register_command('BED_MESH_AUTO',
                                    self.cmd_BED_MESH_AUTO,
                                    desc=self.cmd_BED_MESH_AUTO_help)

    def _get_state_file_path(self):
        config_file = self.printer.get_start_args().get('config_file', '')
        config_dir = os.path.dirname(os.path.abspath(config_file))
        return os.path.join(config_dir, '.offset_probe_results.json')

    def _save_probe_results(self):
        try:
            path = self._get_state_file_path()
            data = {}
            for k, v in self.probe_results.items():
                entry = dict(v)
                entry.pop('last_run', None)
                data[k] = entry
            with open(path, 'w') as f:
                json.dump(data, f, indent=2)
        except Exception as e:
            self.gcode.respond_info("Warning: could not save probe results: %s" % e)

    def _load_probe_results(self):
        try:
            path = self._get_state_file_path()
            if os.path.exists(path):
                with open(path, 'r') as f:
                    self.probe_results = json.load(f)
                self.gcode.respond_info(
                    "Loaded Z-switch data for %d tools from %s"
                    % (len(self.probe_results), os.path.basename(path)))
        except Exception as e:
            self.gcode.respond_info("Warning: could not load probe results: %s" % e)

    def handle_connect(self):
        self._load_probe_results()
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
        # set_position bypasses gcode_move's cached position
        self.gcode_move.reset_last_position()

    def cmd_CALIBRATE_ALL_Z_OFFSETS(self, gcmd):
        if not self.is_homed():
            gcmd.respond_error("Must home first")
            return

        self.cmd_OFFSET_START_GCODE(gcmd)

        extruder_temp = gcmd.get_int('EXTRUDER_TEMP', 0, minval=0, maxval=350)

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

            self.gcode.run_script_from_command(
                f"SET_TOOL_PARAMETER T={tool} PARAMETER=gcode_z_offset "
                f'VALUE="0.0"')

            if extruder_temp > 0:
                self.gcode.run_script_from_command(
                    f"M109 S{extruder_temp}")

            self.gcode.run_script_from_command("MOVE_TO_ZSWITCH")

            z_calc_arg = f" Z_CALC={z_calc}" if z_calc else ""
            self.gcode.run_script_from_command(
                f"PROBE_ZSWITCH SAMPLES={self.samples} "
                f"SAMPLES_TOLERANCE={self.samples_tolerance} "
                f"SAMPLES_MAX_COUNT={self.samples_max_count}" + z_calc_arg
            )

            if extruder_temp > 0:
                self.gcode.run_script_from_command("M104 S0")

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

        self._save_probe_results()
        self.cmd_OFFSET_FINISH_GCODE(gcmd)

    # ─── Probe offset calibration helpers ───────────────────────────────

    def _do_tap_probe(self, probe_obj, samples):
        """Run a single probe cycle via the standard probe interface.
        Includes recovery against 'Probe triggered prior to movement'."""
        from . import probe as probe_mod
        toolhead = self.printer.lookup_object('toolhead')
        dummy_gcmd = self.gcode.create_gcode_command("", "", {
            "SAMPLES": str(samples),
            "SAMPLES_RESULT": "median",
        })
        last_err = None
        for _ in range(self.recover_max_attempts):
            try:
                result = probe_mod.run_single_probe(probe_obj, dummy_gcmd)
                return result.bed_z
            except Exception as e:
                last_err = e
                if "triggered prior to movement" not in str(e).lower():
                    raise
                self.gcode.respond_info(
                    "Probe triggered prior to movement — lifting and retrying")
                toolhead.wait_moves()
                cur = toolhead.get_position()
                toolhead.manual_move(
                    [None, None, cur[2] + self.recover_lift_mm],
                    self.z_move_speed)
                toolhead.wait_moves()
                if self.recover_pause_ms:
                    self.gcode.run_script_from_command(
                        "G4 P%d" % self.recover_pause_ms)
        raise self.gcode.error(
            "Probe still triggered after %d recovery attempts: %s"
            % (self.recover_max_attempts, last_err))

    def _is_eddy_probe(self, probe_name):
        """Check if a probe name refers to an Eddy-NG probe.
        Identified by the Eddy-NG API rather than by a substring match, so
        that a tool_probe with 'eddy' in its name is not misdetected and a
        renamed Eddy still works.  Probes with their own tap that are not
        Eddy-NG (Cartographer, Beacon) are treated as Tap here."""
        if not probe_name:
            return False
        obj = self.printer.lookup_object(probe_name, None)
        if obj is not None:
            return hasattr(obj, 'probe_static_height')
        return probe_name.lower().startswith('probe_eddy_ng')

    def _eddy_computed_tap_z(self, probe_name):
        """Toolhead Z at which the Eddy's nozzle tap found the bed,
        in the current Z reference frame (tap_z + tap_adjust_z,
        see probe_eddy_ng: computed_tap_z)."""
        obj = self.printer.lookup_object(probe_name, None)
        if obj is None:
            raise self.gcode.error(
                "Eddy probe '%s' not found" % probe_name)
        st = obj.get_status(self.printer.get_reactor().monotonic())
        return float(st['last_tap_z']) + float(st['tap_adjust_z'])

    def _gcode_z_offset(self):
        """Current gcode Z offset (SET_GCODE_OFFSET Z / homing_origin.z)."""
        try:
            return float(self.gcode_move.homing_position[2])
        except Exception:
            st = self.gcode_move.get_status(
                self.printer.get_reactor().monotonic())
            return float(st['homing_origin'].z)

    def _move_z(self, z, speed=10.):
        """Move to an absolute kinematic Z and resync gcode_move.
        Direct toolhead moves bypass gcode_move's cached last_position;
        without the resync the next G0/G1 would command that stale Z."""
        toolhead = self.printer.lookup_object('toolhead')
        toolhead.wait_moves()
        toolhead.manual_move([None, None, z], speed)
        toolhead.wait_moves()
        self.gcode_move.reset_last_position()

    def _set_z_reference(self, bed_z):
        """Re-reference Z so the nozzle contacts the bed at kinematic Z=0.
        bed_z is the kinematic Z of bed contact as reported by the probe."""
        toolhead = self.printer.lookup_object('toolhead')
        toolhead.wait_moves()
        pos = toolhead.get_position()
        pos[2] = pos[2] - bed_z
        toolhead.set_position(pos)
        # Match what PROBE_EDDY_NG_TAP HOME_Z=1 leaves behind
        self.gcode.run_script_from_command("SET_GCODE_OFFSET Z=0")
        self.gcode_move.reset_last_position()

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
        "run first (needs z_offset / gcode_z_offset data), measured against "
        "the same reference tool. "
        "TOOLS=0,1,2,3 to select tools (default: all with z_offset data). "
        "REF_PROBE=\"<name>\" overrides the probe used for the reference "
        "zero (default: the tool's probe_cal_map entry). "
        "APPLY=1 (default) sets z_offset at runtime and stages config save; "
        "only tools measured with their mechanical Tap are applied.")

    def cmd_CALIBRATE_PROBE_OFFSETS(self, gcmd):
        if not self.is_homed():
            raise gcmd.error("Must home first")

        # Check that z_offset data exists from Z-switch calibration
        if not self.probe_results:
            raise gcmd.error(
                "No Z-switch data. Run CALIBRATE_ALL_Z_OFFSETS first")

        apply_offsets = gcmd.get_int('APPLY', 1)
        extruder_temp = gcmd.get_int('EXTRUDER_TEMP', 0, minval=0, maxval=350)
        samples = gcmd.get_int('SAMPLES', self.probe_offset_samples, minval=1)
        probe_x = gcmd.get_float('PROBE_X', self.probe_offset_x)
        probe_y = gcmd.get_float('PROBE_Y', self.probe_offset_y)
        z_hop = gcmd.get_float('Z_HOP', self.probe_offset_z_hop, above=0.)
        travel_speed = gcmd.get_float('TRAVEL_SPEED',
                                       self.probe_offset_travel_speed, above=0.)

        # REF_TOOL parameter (was hardcoded to 0)
        ref_tool = gcmd.get_int('REF_TOOL', self.default_ref_tool, minval=0)

        # REF_PROBE overrides the probe used for the step 1 reference zero.
        # Parsed from the raw command line because probe names contain a
        # space ("probe_eddy_ng my_eddy").
        ref_probe_override = None
        raw_cmdline = gcmd.get_commandline()
        m = re.search(r'REF_PROBE="([^"]+)"', raw_cmdline, re.IGNORECASE)
        if not m:
            m = re.search(r'REF_PROBE=(\S+)', raw_cmdline, re.IGNORECASE)
        if m:
            ref_probe_override = m.group(1)

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

        # The gcode_z_offset values below are relative to the tool that
        # CALIBRATE_ALL_Z_OFFSETS used as its reference. Step 1 sets Z=0 at
        # REF_TOOL's nozzle. If those two differ, every result is shifted by
        # a constant without any sign of it, so refuse instead.
        data_refs = set()
        for t in calibrate_tools:
            r = self.probe_results[str(t)].get('ref_tool')
            if r is not None:
                data_refs.add(int(r))
        if len(data_refs) > 1:
            raise gcmd.error(
                "Z-switch data was measured against different reference "
                "tools (%s). Re-run CALIBRATE_ALL_Z_OFFSETS for all tools."
                % ", ".join("T%d" % r for r in sorted(data_refs)))
        if data_refs and ref_tool not in data_refs:
            data_ref = data_refs.pop()
            raise gcmd.error(
                "REF_TOOL=T%d does not match the reference tool of the "
                "Z-switch data (T%d). Either use REF_TOOL=%d or re-run "
                "CALIBRATE_ALL_Z_OFFSETS with REF=%d."
                % (ref_tool, data_ref, data_ref, ref_tool))

        toolhead = self.printer.lookup_object('toolhead')
        probe_obj = self.printer.lookup_object('probe')

        # Apply Z-switch offsets to tools so the formula works
        # regardless of whether the user clicked "APPLY" in the webapp
        for tool_nr in calibrate_tools:
            key = str(tool_nr)
            z_off = self.probe_results[key]['z_offset']
            self.gcode.run_script_from_command(
                f'SET_TOOL_PARAMETER T={tool_nr} '
                f'PARAMETER=gcode_z_offset VALUE="{z_off:.6f}"')
        self.gcode.respond_info(
            "Applied Z-switch offsets to %d tools" % len(calibrate_tools))

        # Reference probe: explicit REF_PROBE wins over the per-tool map
        ref_probe_name = (ref_probe_override
                          or self._get_probe_for_tool(ref_tool, ref_tool))
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

        # Lift before travelling, then position the nozzle at the probe point
        self._move_z(z_hop)
        self.gcode_move.cmd_G1(
            self.gcode.create_gcode_command(
                "G0", "G0",
                {'X': probe_x, 'Y': probe_y, 'F': travel_speed * 60}
            )
        )
        toolhead.wait_moves()

        # Use ref probe (Eddy or Tap) based on map — both are nozzle taps
        if ref_is_eddy:
            self.gcode.run_script_from_command(
                'SET_ACTIVE_Z_PROBE PROBE="%s"' % ref_probe_name)
            # No SAMPLES here: tap sampling is governed by the Eddy's own
            # tap_samples / tap_max_samples / tap_samples_stddev, and a
            # SAMPLES above tap_max_samples would be rejected outright.
            self.gcode.run_script_from_command("PROBE_EDDY_NG_TAP HOME_Z=1")
            # PROBE_EDDY_NG_TAP moves the toolhead and rewrites gcode_move's
            # Z frame without touching its cached position.
            self.gcode_move.reset_last_position()
            self.gcode.respond_info(
                "T%d Eddy Tap: Z=0 set at nozzle contact" % ref_tool)
        else:
            self.gcode.run_script_from_command(
                "SET_ACTIVE_Z_PROBE PROBE=none")
            # Tap reference: probe, then actually set Z=0 at contact.
            # Without this the whole step 2 formula loses its origin.
            self._move_z(5.0)
            ref_pz = probe_obj.get_offsets()[2]
            bed_z = self._do_tap_probe(probe_obj, samples)
            self._set_z_reference(bed_z)
            self.gcode.respond_info(
                "T%d Tap: bed_z=%.4f -> Z=0 set at nozzle contact"
                % (ref_tool, bed_z))
            # A mechanical Tap has no independent ground truth: the zero is
            # only as good as that tool's own z_offset. An Eddy tap zeroes
            # itself, so prefer it as the reference when one exists.
            self.gcode.respond_info(
                "Note: reference zero is based on T%d's tool_probe "
                "z_offset=%.4f%s"
                % (ref_tool, ref_pz,
                   " (uncalibrated)" if abs(ref_pz) < 1e-9 else ""))
            if ref_tool in calibrate_tools:
                self.gcode.respond_info(
                    "Note: T%d is both reference and measured with its own "
                    "Tap — its result is circular and will be ~0"
                    % ref_tool)

        self._move_z(z_hop)

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

            if extruder_temp > 0:
                self.gcode.run_script_from_command(
                    "M109 S%d" % extruder_temp)

            # Lift BEFORE the XY travel, then position the nozzle
            self._move_z(z_hop)
            self.gcode_move.cmd_G1(
                self.gcode.create_gcode_command(
                    "G0", "G0",
                    {'X': probe_x, 'Y': probe_y, 'F': travel_speed * 60}
                )
            )
            toolhead.wait_moves()

            # Z=0 is ref_tool's nozzle contact (step 1). Tn's nozzle contacts
            # the bed at kinematic Z = gcode_z_off, because ToolGcodeTransform
            # maps kinematic = gcode + gcode_z_offset.
            if tool_is_eddy:
                # Nozzle tap with the Eddy. HOME_Z=0 keeps step 1's reference
                # zero; the tap result lands in the gcode Z offset, which we
                # read back and then restore.
                self.gcode.run_script_from_command(
                    'SET_ACTIVE_Z_PROBE PROBE="%s"' % tool_probe_name)
                saved_gcode_z = self._gcode_z_offset()
                self.gcode.run_script_from_command("PROBE_EDDY_NG_TAP HOME_Z=0")
                computed_tap_z = self._eddy_computed_tap_z(tool_probe_name)
                self.gcode.run_script_from_command(
                    "SET_GCODE_OFFSET Z=%.6f" % saved_gcode_z)
                self.gcode_move.reset_last_position()

                if extruder_temp > 0:
                    self.gcode.run_script_from_command("M104 S0")

                # How far the Eddy's tap zero sits from where the Z-switch
                # chain says this nozzle is. This is NOT the mechanical
                # Tap's trigger height, so it must never be written into
                # [tool_probe Tn] z_offset — correct it via tap_adjust_z.
                deviation = computed_tap_z - gcode_z_off
                self.probe_results[key]['eddy_tap_z'] = computed_tap_z
                self.probe_results[key]['eddy_tap_deviation'] = deviation
                self.probe_results[key]['eddy_probe'] = tool_probe_name
                self.probe_results[key].pop('probe_z_offset', None)
                self.probe_results[key].pop('tap_bed_z', None)

                self.gcode.respond_info(
                    "T%d Eddy Tap: tap_z=%.4f  deviation=%+.4f "
                    "(not applied — adjust tap_adjust_z on '%s' if needed)"
                    % (tool_nr, computed_tap_z, deviation, tool_probe_name))
            else:
                self.gcode.run_script_from_command(
                    "SET_ACTIVE_Z_PROBE PROBE=none")
                self._move_z(5.0)

                # tool_probe z_offset, subtracted inside run_single_probe
                current_pz = probe_obj.get_offsets()[2]
                bed_z = self._do_tap_probe(probe_obj, samples)

                # Tap triggers at kinematic Z = gcode_z_off + true_pz.
                # run_single_probe: bed_z = trigger_z - current_pz.
                # → true_pz = bed_z + current_pz - gcode_z_off
                probe_z_offset = bed_z + current_pz - gcode_z_off

                if extruder_temp > 0:
                    self.gcode.run_script_from_command("M104 S0")

                self.probe_results[key]['probe_z_offset'] = probe_z_offset
                self.probe_results[key]['tap_bed_z'] = bed_z
                self.probe_results[key].pop('eddy_tap_deviation', None)
                self.probe_results[key].pop('eddy_tap_z', None)
                self.probe_results[key].pop('eddy_probe', None)

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

            self._move_z(z_hop)

        # ── Restore ref_tool with its probe routing ──
        if self.toolchanger.active_tool.tool_number != ref_tool:
            self.gcode.run_script_from_command(
                "SELECT_TOOL T=%d RESTORE_AXIS=XZ" % ref_tool)
        self.gcode.run_script_from_command(
            "SET_ACTIVE_TOOL_PROBE T=%d" % ref_tool)

        # Restore ref probe routing (same source as step 1)
        if ref_is_eddy:
            self.gcode.run_script_from_command(
                'SET_ACTIVE_Z_PROBE PROBE="%s"' % ref_probe_name)
        else:
            self.gcode.run_script_from_command(
                "SET_ACTIVE_Z_PROBE PROBE=none")

        # ── Summary ──
        self.gcode.respond_info("=== Probe Offset Calibration Complete ===")
        any_applied = False
        for tool_nr in calibrate_tools:
            key = str(tool_nr)
            data = self.probe_results[key]
            zo = data.get('z_offset', 0.0)
            if 'eddy_tap_deviation' in data:
                self.gcode.respond_info(
                    "T%d: gcode_z_offset=%.4f  eddy tap deviation=%+.4f "
                    "[not applied]" % (tool_nr, zo,
                                       data['eddy_tap_deviation']))
            else:
                pzo = data.get('probe_z_offset', 0.0)
                saved = " [APPLIED]" if apply_offsets else ""
                any_applied = any_applied or bool(apply_offsets)
                self.gcode.respond_info(
                    "T%d: gcode_z_offset=%.4f  probe_z_offset=%.4f%s"
                    % (tool_nr, zo, pzo, saved))
        if any_applied:
            self.gcode.respond_info(
                "Offsets applied at runtime. Use SAVE_CONFIG to persist.")
        self._save_probe_results()

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

    # ─── NOZZLE_ZERO / APPLY_TOOL_Z_OFFSETS ──────────────────────────────

    def _active_tool_number(self, gcmd):
        at = getattr(self.toolchanger, 'active_tool', None)
        if at is None:
            raise gcmd.error(
                "No active tool — mount a tool or pass TOOL=/REF=")
        return at.tool_number

    def _tool_home_probe(self, tool_nr):
        """The probe this tool is configured to home with, or None for Tap."""
        tp = self.printer.lookup_object('tool_probe T%d' % tool_nr, None)
        if tp is None or not hasattr(tp, 'get_z_probe_for'):
            return None
        return tp.get_z_probe_for('home')

    @staticmethod
    def _obj_name(obj):
        return getattr(obj, '_full_name',
               getattr(obj, 'name',
               getattr(obj, '_name', str(obj))))

    def _move_to_tap_point(self, gcmd):
        """Position the nozzle over the tap point (bed centre by default).
        A nozzle touch needs the nozzle there, not the sensor, so no probe
        x/y offset is applied."""
        toolhead = self.printer.lookup_object('toolhead')
        st = toolhead.get_status(self.printer.get_reactor().monotonic())
        axmin, axmax = st['axis_minimum'], st['axis_maximum']
        cx = gcmd.get_float('X', (max(0., axmin[0]) + axmax[0]) / 2.)
        cy = gcmd.get_float('Y', (max(0., axmin[1]) + axmax[1]) / 2.)
        self._move_z(gcmd.get_float('Z_HOP', self.probe_offset_z_hop,
                                    above=0.))
        self.gcode_move.cmd_G1(
            self.gcode.create_gcode_command(
                "G0", "G0",
                {'X': cx, 'Y': cy,
                 'F': self.probe_offset_travel_speed * 60}))
        toolhead.wait_moves()

    @staticmethod
    def _is_scanning_probe(obj):
        """Can this probe do a rapid_scan bed mesh? (Eddy-NG API marker)"""
        return obj is not None and hasattr(obj, 'probe_static_height')

    def _nozzle_zero(self, tool_nr, gcmd):
        """Set kinematic Z=0 at this tool's nozzle contact, using whatever
        probe it is configured with. Both paths are nozzle touches."""
        probe_obj = self.printer.lookup_object('probe')
        z_probe = self._tool_home_probe(tool_nr)
        self.gcode.run_script_from_command(
            "SET_ACTIVE_TOOL_PROBE T=%d" % tool_nr)
        self.gcode.run_script_from_command("STOP_TOOL_PROBE_CRASH_DETECTION")

        if z_probe is not None and hasattr(z_probe, 'probe_static_height'):
            name = self._obj_name(z_probe)
            self.gcode.run_script_from_command(
                'SET_ACTIVE_Z_PROBE PROBE="%s"' % name)
            self.gcode.run_script_from_command("PROBE_EDDY_NG_TAP HOME_Z=1")
            self.gcode_move.reset_last_position()
            self.gcode.respond_info(
                "NOZZLE_ZERO: T%d Z=0 set by Eddy tap (%s)" % (tool_nr, name))
            return

        # Mechanical Tap: it triggers at the nozzle, so probing it and
        # zeroing on bed_z puts Z=0 at nozzle contact. Accuracy depends on
        # this tool's [tool_probe] z_offset (CALIBRATE_PROBE_OFFSETS).
        self.gcode.run_script_from_command("SET_ACTIVE_Z_PROBE PROBE=none")
        self._move_z(5.0)
        bed_z = self._do_tap_probe(probe_obj, self.probe_offset_samples)
        self._set_z_reference(bed_z)
        self.gcode.respond_info(
            "NOZZLE_ZERO: T%d Z=0 set by Tap (bed_z=%.4f)"
            % (tool_nr, bed_z))

    cmd_NOZZLE_ZERO_help = (
        "Set Z=0 at the active tool's nozzle contact, using whatever probe "
        "that tool has (Eddy tap or mechanical Tap). TOOL=<n> picks up that "
        "tool first. X/Y select the tap point (default: bed centre), Z_HOP "
        "the approach height. APPLY_OFFSETS=1 (default) then re-references "
        "every tool's gcode_z_offset to it, so all nozzles print at one "
        "height no matter which tool homed.")

    def cmd_NOZZLE_ZERO(self, gcmd):
        if not self.is_homed():
            raise gcmd.error("Must home first")
        tool_nr = gcmd.get_int('TOOL', None)
        if tool_nr is not None:
            if tool_nr not in self.toolchanger.tool_numbers:
                raise gcmd.error("Tool T%d not configured" % tool_nr)
            if self._active_tool_number(gcmd) != tool_nr:
                self.gcode.run_script_from_command(
                    "SELECT_TOOL T=%d RESTORE_AXIS=XYZ" % tool_nr)
        tool_nr = self._active_tool_number(gcmd)

        self._move_to_tap_point(gcmd)
        self._nozzle_zero(tool_nr, gcmd)

        if gcmd.get_int('APPLY_OFFSETS', 1):
            self._apply_tool_z_offsets(tool_nr, gcmd, save=False)

    cmd_APPLY_TOOL_Z_OFFSETS_help = (
        "Re-reference every tool's gcode_z_offset to REF (default: active "
        "tool) from the Z-switch data, so all nozzles sit at the same height "
        "no matter which tool established Z=0. SAVE=1 also stages the values "
        "for SAVE_CONFIG.")

    def cmd_APPLY_TOOL_Z_OFFSETS(self, gcmd):
        # OPTIONAL=1: never abort the caller (used by the G28 wrapper, which
        # must still work before any calibration data exists).
        optional = bool(gcmd.get_int('OPTIONAL', 0))
        try:
            if optional and not self.is_homed():
                return
            ref = gcmd.get_int('REF', None)
            if ref is None:
                ref = self._active_tool_number(gcmd)
            self._apply_tool_z_offsets(ref, gcmd,
                                       save=bool(gcmd.get_int('SAVE', 0)))
        except Exception as e:
            if not optional:
                raise
            self.gcode.respond_info(
                "APPLY_TOOL_Z_OFFSETS skipped: %s" % e)

    def _apply_tool_z_offsets(self, ref, gcmd, save=False):
        """gcode_z_offset(n) = z_trigger(n) - z_trigger(ref).

        The Z-switch triggers are absolute within one calibration run, so
        switching the reference tool is pure arithmetic — no re-measurement.
        Relative nozzle heights come from the Z-switch (probe independent),
        the absolute zero comes from the homing tool's nozzle touch."""
        if not self.probe_results:
            raise gcmd.error(
                "No Z-switch data. Run CALIBRATE_ALL_Z_OFFSETS first")

        tools = sorted(self.toolchanger.tool_numbers)
        triggers = {}
        missing = []
        data_refs = set()
        for tn in tools:
            entry = self.probe_results.get(str(tn))
            trig = entry.get('z_trigger') if entry else None
            if not isinstance(trig, (int, float)):
                missing.append(tn)
                continue
            triggers[tn] = float(trig)
            if entry.get('ref_tool') is not None:
                data_refs.add(int(entry['ref_tool']))
        if missing:
            raise gcmd.error(
                "Missing Z-switch data for T%s. Run CALIBRATE_ALL_Z_OFFSETS "
                "for all tools first"
                % ",".join(str(t) for t in missing))
        if len(data_refs) > 1:
            raise gcmd.error(
                "Z-switch data comes from different runs (references %s). "
                "Re-run CALIBRATE_ALL_Z_OFFSETS for all tools."
                % ", ".join("T%d" % r for r in sorted(data_refs)))
        if ref not in triggers:
            raise gcmd.error("No Z-switch data for reference tool T%d" % ref)

        base = triggers[ref]
        lines = []
        for tn in tools:
            val = triggers[tn] - base
            # Set the attribute directly instead of via SET_TOOL_PARAMETER:
            # this also has to work from contexts where running gcode would
            # be re-entrant (e.g. straight after G28).
            tool_obj = self.printer.lookup_object('tool T%d' % tn, None)
            if tool_obj is None:
                raise gcmd.error("Tool T%d not found" % tn)
            tool_obj.set_parameter('gcode_z_offset', '%.6f' % val)
            if save:
                tool_obj.save_parameter('gcode_z_offset')
            # Keep the stored data consistent with what is applied
            self.probe_results[str(tn)]['z_offset'] = val
            self.probe_results[str(tn)]['ref_tool'] = ref
            lines.append("T%d=%+.4f" % (tn, val))

        # The transform changed under gcode_move's cached position
        self.gcode_move.reset_last_position()
        self._save_probe_results()

        self.gcode.respond_info(
            "Tool Z offsets referenced to T%d: %s%s"
            % (ref, "  ".join(lines),
               " (staged for SAVE_CONFIG)" if save else ""))
        self.last_ref_tool = ref

    # ─── BED_MESH_AUTO ───────────────────────────────────────────────────

    def _resolve_mesh_tool(self, tool_nr, gcmd):
        """Which tool to borrow for meshing. Per-tool mesh_tool wins over
        [offset] mesh_tool; -1 forces the tapped mesh."""
        override = gcmd.get_int('MESH_TOOL', None)
        if override is not None:
            return override
        tp = self.printer.lookup_object('tool_probe T%d' % tool_nr, None)
        per_tool = getattr(tp, 'mesh_tool', None) if tp else None
        if per_tool is not None:
            return per_tool
        return self.mesh_tool

    cmd_BED_MESH_AUTO_help = (
        "Bed mesh with the best probe available. The mounted tool's own "
        "scanning probe is used if it has one. Otherwise MESH_TOOL / "
        "[tool_probe] mesh_tool / [offset] mesh_tool names a tool that does: "
        "it is picked up, re-zeroes Z on its nozzle (which becomes the "
        "reference for all tools), scans, and the original tool is put back. "
        "Falls back to a tapped mesh when no scanning probe is reachable.")

    def cmd_BED_MESH_AUTO(self, gcmd):
        if not self.is_homed():
            raise gcmd.error("Must home first")
        tool_nr = self._active_tool_number(gcmd)
        profile = gcmd.get('PROFILE', None)
        prof = (' PROFILE=%s' % profile) if profile else ''

        own = self._tool_mesh_probe(tool_nr)
        self.gcode.run_script_from_command("BED_MESH_CLEAR")

        if self._is_scanning_probe(own):
            self.gcode.respond_info(
                "Bed mesh: rapid_scan with T%d's own %s"
                % (tool_nr, self._obj_name(own)))
            self.gcode.run_script_from_command("APPLY_TOOL_PROBE_FOR OP=mesh")
            self.gcode.run_script_from_command(
                "BED_MESH_CALIBRATE METHOD=rapid_scan" + prof)
            return

        mesh_tool = self._resolve_mesh_tool(tool_nr, gcmd)
        helper = None
        if (mesh_tool is not None and mesh_tool >= 0
                and mesh_tool != tool_nr
                and mesh_tool in self.toolchanger.tool_numbers):
            helper = self._tool_mesh_probe(mesh_tool)
            if not self._is_scanning_probe(helper):
                self.gcode.respond_info(
                    "Bed mesh: T%d has no scanning probe either — "
                    "falling back to a tapped mesh" % mesh_tool)
                helper = None

        if helper is None:
            self.gcode.respond_info(
                "Bed mesh: probed with T%d's Tap (slow — set mesh_tool to a "
                "tool with a scanning probe to speed this up)" % tool_nr)
            self.gcode.run_script_from_command("SET_ACTIVE_Z_PROBE PROBE=none")
            self.gcode.run_script_from_command("BED_MESH_CALIBRATE" + prof)
            return

        # Borrow the mesh tool: swap, re-zero on its nozzle, scan, swap back.
        # The nozzle zero re-references every tool to the mesh tool, so the
        # print height no longer depends on the Tap tools' z_offset at all.
        self.gcode.respond_info(
            "Bed mesh: borrowing T%d (%s) for rapid_scan, then back to T%d"
            % (mesh_tool, self._obj_name(helper), tool_nr))
        try:
            self.gcode.run_script_from_command(
                "SELECT_TOOL T=%d RESTORE_AXIS=XYZ" % mesh_tool)
            if self.mesh_tool_gcode:
                self.mesh_tool_gcode.run_gcode_from_command(
                    {'MESH_TOOL': mesh_tool, 'PREVIOUS_TOOL': tool_nr})
            self._move_to_tap_point(gcmd)
            self._nozzle_zero(mesh_tool, gcmd)
            self._apply_tool_z_offsets(mesh_tool, gcmd, save=False)
            self.gcode.run_script_from_command("APPLY_TOOL_PROBE_FOR OP=mesh")
            self.gcode.run_script_from_command(
                "BED_MESH_CALIBRATE METHOD=rapid_scan" + prof)
        finally:
            # Always put the original tool back, even if the mesh failed
            if self._active_tool_number(gcmd) != tool_nr:
                self.gcode.run_script_from_command(
                    "SELECT_TOOL T=%d RESTORE_AXIS=XYZ" % tool_nr)
            self.gcode.run_script_from_command(
                "SET_ACTIVE_TOOL_PROBE T=%d" % tool_nr)
            self.gcode.run_script_from_command("APPLY_TOOL_PROBE_FOR OP=mesh")

    def _tool_mesh_probe(self, tool_nr):
        tp = self.printer.lookup_object('tool_probe T%d' % tool_nr, None)
        if tp is None or not hasattr(tp, 'get_z_probe_for'):
            return None
        return tp.get_z_probe_for('mesh')

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
