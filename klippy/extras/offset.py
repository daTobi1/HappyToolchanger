import os
import re
import time
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

        # Dock calibration defaults - all overridable per run from the UI
        self.dock_start_z    = config.getfloat('dock_start_z', 100.0, above=0.)
        self.dock_new_y      = config.getfloat('dock_new_y', 0.0)
        self.dock_test_depth = config.getfloat('dock_test_depth', 15.0,
                                               above=0.)
        self.dock_test_repeats = config.getint('dock_test_repeats', 1,
                                               minval=1)
        self.dock_test_speed = config.getfloat('dock_test_speed', 5.0,
                                               above=0.)
        self.dock_travel_speed = config.getfloat('dock_travel_speed', 100.0,
                                                 above=0.)

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

        # Per-tool PID tuning. The part fan and the distance to the bed
        # dominate the thermal response, so both belong to the measurement
        # and are recorded with the result.
        self.pid_temp = config.getfloat('pid_temp', 200.0, minval=60.,
                                        maxval=500.)
        self.pid_height = config.getfloat('pid_height', 10.0, minval=0.)
        self.pid_fan_speed = config.getint('pid_fan_speed', 100,
                                           minval=0, maxval=100)
        self.pid_tool = config.getint('pid_tool', None)
        self.pid_results = {}
        self.dock_results = {}
        self.dock_state = None
        self._dock_saved_origin = None
        self._dock_saved_transform = None

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
        for name in ('START', 'MOUNTED', 'TEST', 'ACCEPT', 'ABORT'):
            self.gcode.register_command(
                'DOCK_CALIBRATE_' + name,
                getattr(self, 'cmd_DOCK_CALIBRATE_' + name),
                desc=getattr(self, 'cmd_DOCK_CALIBRATE_' + name + '_help'))
        self.gcode.register_command('CALIBRATE_TOOL_PID',
                                    self.cmd_CALIBRATE_TOOL_PID,
                                    desc=self.cmd_CALIBRATE_TOOL_PID_help)

    PID_STATE_FILE = '.offset_pid_results.json'

    def _get_state_file_path(self, name='.offset_probe_results.json'):
        config_file = self.printer.get_start_args().get('config_file', '')
        config_dir = os.path.dirname(os.path.abspath(config_file))
        return os.path.join(config_dir, name)

    def _save_pid_results(self):
        """A RESTART - which the webapp itself offers a button for - builds
        every Klipper object anew, so anything kept only in memory is gone.
        A finished tune takes minutes per tool and cannot be recovered from
        Klipper afterwards: PID_CALIBRATE stages its values for SAVE_CONFIG,
        and SAVE_CONFIG cannot write pid_Kp because an included T<n>.cfg
        already defines it. Without this file the run is simply lost before
        anyone can apply it."""
        try:
            path = self._get_state_file_path(self.PID_STATE_FILE)
            with open(path, 'w') as f:
                json.dump(self.pid_results, f, indent=2)
        except Exception as e:
            self.gcode.respond_info(
                "Warning: could not save PID results: %s" % e)

    def _load_pid_results(self):
        try:
            path = self._get_state_file_path(self.PID_STATE_FILE)
            if os.path.exists(path):
                with open(path, 'r') as f:
                    self.pid_results = json.load(f)
                if self.pid_results:
                    self.gcode.respond_info(
                        "Loaded PID results for %d tools from %s"
                        % (len(self.pid_results), os.path.basename(path)))
        except Exception as e:
            self.gcode.respond_info(
                "Warning: could not load PID results: %s" % e)

    def _save_probe_results(self):
        try:
            path = self._get_state_file_path()
            data = {}
            for k, v in self.probe_results.items():
                entry = dict(v)
                # transient, not worth persisting; run_id however must survive
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
        self._load_pid_results()
        self._load_dock_results()
        if self.config_file_path:
            self.config_file_path = os.path.expanduser(self.config_file_path)
            if os.path.exists(self.config_file_path):
                self.has_cfg_data = True
                self.gcode.respond_info(f"Offset config file found ({self.config_file_path})")
            else:
                self.gcode.respond_info(f"Offset config file not found ({self.config_file_path})")

    def _require_leveled(self, gcmd):
        """Refuse to measure on an unlevelled gantry.

        Every routine here compares tools against one common Z. A gantry
        that has not been levelled puts a position-dependent error into
        that comparison, and the result looks plausible but is wrong.
        Silently skipped when the printer has no such section."""
        for name in ('quad_gantry_level', 'z_tilt'):
            obj = self.printer.lookup_object(name, None)
            if obj is None:
                continue
            try:
                st = obj.get_status(self.printer.get_reactor().monotonic())
            except Exception:
                continue
            if not st.get('applied', False):
                raise gcmd.error(
                    "%s has not been applied - run it first"
                    % name.upper().replace('_', ' '))

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
            'pid_results': self.pid_results,
            'dock_results': self.dock_results,
            'dock_defaults': self._dock_defaults(),
            'dock_state': (dict(self.dock_state, tool=self._dock_current_tool())
                           if self.dock_state else None),
            'pid_defaults': {
                'temp': self.pid_temp,
                'height': self.pid_height,
                'fan_speed': self.pid_fan_speed,
                'tool': self.pid_tool,
            },
            'tool_pid': self._current_tool_pid(),
        }

    def cmd_MOVE_TO_ZSWITCH(self, gcmd):
        if not self.is_homed():
            raise gcmd.error("Must home first")
        if not self.has_switch_pos():
            raise gcmd.error("Z switch positions invalid")

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
            raise gcmd.error("Must home first")
        self._require_leveled(gcmd)

        self.cmd_OFFSET_START_GCODE(gcmd)

        extruder_temp = gcmd.get_int('EXTRUDER_TEMP', 0, minval=0, maxval=350)

        z_calc = (gcmd.get('Z_CALC', None) or '').strip().lower()
        if z_calc and z_calc not in ('median', 'average', 'avg', 'mean', 'trimmed', 'trim', 'trimmed_mean'):
            raise gcmd.error("Invalid Z_CALC. Use median, average or trimmed")

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
            raise gcmd.error("No tools available")

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
            raise gcmd.error("No valid tools selected")

        # Ensure reference is included and first
        if ref_tool not in ordered_tools:
            ordered_tools.insert(0, ref_tool)
        ordered_tools = [ref_tool] + [t for t in ordered_tools if t != ref_tool]

        self.last_ref_tool = ref_tool

        # Clean run
        self.probe_results = {}
        ref_trigger = None

        # Marks this measurement session. CALIBRATE_PROBE_OFFSETS refuses to
        # mix Z-switch data from a different run: its formula is
        # probe_z_offset = trigger_z - gcode_z_offset, so any drift in the
        # relative nozzle heights since the Z-switch run lands silently
        # inside probe_z_offset instead of being flagged.
        # Wall clock, not reactor.monotonic(): the latter restarts with
        # Klipper, so two runs in different sessions could collide.
        run_id = "%d" % int(time.time())

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
                else:
                    if ref_trigger is None:
                        self.probe_results[key]['z_offset'] = 0.0
                    else:
                        self.probe_results[key]['z_offset'] = z_trig - ref_trigger
                self.probe_results[key]['ref_tool'] = ref_tool
                self.probe_results[key]['run_id'] = run_id
                zs_temp = self._tool_extruder_temp(tool)
                if zs_temp is not None:
                    self.probe_results[key]['zswitch_temp'] = zs_temp

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
        "APPLY=1 (default) sets z_offset at runtime only - persist it with "
        "APPLY PROBE OFFSETS in the webapp, not SAVE_CONFIG. Only tools "
        "measured with their mechanical Tap are applied.")

    def cmd_CALIBRATE_PROBE_OFFSETS(self, gcmd):
        if not self.is_homed():
            raise gcmd.error("Must home first")
        self._require_leveled(gcmd)

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

        # probe_z_offset = trigger_z - gcode_z_offset, and gcode_z_offset comes
        # from the Z-switch run. Any drift in the relative nozzle heights since
        # then lands silently inside probe_z_offset instead of being flagged,
        # so require one common run and say how old it is.
        run_ids = set()
        for t in calibrate_tools:
            rid = self.probe_results[str(t)].get('run_id')
            run_ids.add(rid)
        if len(run_ids) > 1:
            raise gcmd.error(
                "Z-switch data for the selected tools comes from different "
                "runs. Re-run CALIBRATE_ALL_Z_OFFSETS for all of them.")
        run_id = run_ids.pop() if run_ids else None
        if run_id is None:
            self.gcode.respond_info(
                "Warning: Z-switch data predates run tracking - age unknown. "
                "Re-run CALIBRATE_ALL_Z_OFFSETS if the nozzles were cleaned "
                "or changed since.")
        else:
            age_h = max(0.0, (time.time() - float(run_id)) / 3600.0)
            if age_h >= 1.0:
                self.gcode.respond_info(
                    "Warning: Z-switch data is %.1f h old. Anything that "
                    "changed the relative nozzle heights since then will end "
                    "up in probe_z_offset." % age_h)
            else:
                self.gcode.respond_info(
                    "Z-switch data age: %d min" % int(age_h * 60))

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

        # The reference tap defines Z=0 for every other tool, so it has to
        # happen at the same nozzle temperature as those. Two reasons it
        # would otherwise run cold: EXTRUDER_TEMP was only honoured in step 2,
        # and an Eddy-routed reference never executes the tool_probe's
        # activate_gcode (_TAP_PROBE_ACTIVATE), which is what heats the Tap
        # tools as a side effect.
        if extruder_temp > 0:
            self.gcode.run_script_from_command("M109 S%d" % extruder_temp)

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

        if extruder_temp > 0 and ref_tool not in calibrate_tools:
            # Step 2 would otherwise never cool this one down again
            self.gcode.run_script_from_command("M104 S0")

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
                self._record_tap_temp(tool_nr, key)

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
                self._record_tap_temp(tool_nr, key)
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
                        # Runtime only, deliberately no configfile.set():
                        # z_offset lives in the included T<n>.cfg, and staging
                        # it for SAVE_CONFIG makes Klipper refuse with
                        # "conflicts with included value" - which then blocks
                        # SAVE_CONFIG for everything else too until restart.
                        # APPLY PROBE OFFSETS in the webapp writes the file.
                        self.gcode.respond_info(
                            "T%d: z_offset set at runtime (use APPLY PROBE "
                            "OFFSETS to write it into T%d.cfg; do NOT use "
                            "SAVE_CONFIG for this)" % (tool_nr, tool_nr))
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
                "Offsets applied at runtime. Persist them with APPLY PROBE "
                "OFFSETS in the webapp - not with SAVE_CONFIG, which cannot "
                "write options that an included file already defines.")
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

    def _record_tap_temp(self, tool_nr, key):
        """Store the nozzle temperature of this measurement and flag a
        mismatch against the Z-switch run. Both branches need this: the
        expansion enters probe_z_offset either way, and for an Eddy-measured
        tool it enters the reference that every other tool is compared to."""
        tap_temp = self._tool_extruder_temp(tool_nr)
        if tap_temp is None:
            return
        self.probe_results[key]['tap_temp'] = tap_temp
        zs_temp = self.probe_results[key].get('zswitch_temp')
        if zs_temp is not None and abs(tap_temp - zs_temp) > 5.0:
            self.gcode.respond_info(
                "T%d: WARNUNG Z-Switch bei %.0fC, Tap bei %.0fC gemessen - "
                "die Waermeausdehnung der Duese (Groessenordnung 0.1mm) "
                "steckt damit im Ergebnis. Beide Laeufe mit demselben "
                "EXTRUDER_TEMP fahren."
                % (tool_nr, zs_temp, tap_temp))

    # ─── CALIBRATE_TOOL_PID ──────────────────────────────────────────────

    def _tool_extruder_name(self, tool_nr):
        tool = self.printer.lookup_object('tool T%d' % tool_nr, None)
        return getattr(tool, 'extruder_name', None) if tool else None

    def _current_tool_pid(self):
        """Live pid_Kp/Ki/Kd per tool, so the UI can show current vs new.
        Klipper divides the config values by PID_PARAM_BASE (255) when it
        builds ControlPID, so scale them back."""
        out = {}
        for tn in self.toolchanger.tool_numbers:
            try:
                name = self._tool_extruder_name(tn)
                if not name:
                    continue
                heater = self.printer.lookup_object(name).get_heater()
                # Heater exposes set_control() but no getter; the active
                # algorithm sits in the plain attribute.
                control = getattr(heater, 'control', None)
                if control is None and hasattr(heater, 'get_control'):
                    control = heater.get_control()
                kp = getattr(control, 'Kp', None)
                if kp is None:
                    continue  # bang-bang or watermark control
                out[str(tn)] = {
                    'pid_kp': control.Kp * 255.0,
                    'pid_ki': control.Ki * 255.0,
                    'pid_kd': control.Kd * 255.0,
                }
            except Exception:
                pass
        return out

    cmd_CALIBRATE_TOOL_PID_help = (
        "PID tune extruders under realistic conditions: each tool is picked "
        "up, parked at HEIGHT over the bed centre and its part fan runs at "
        "FAN percent during the tune. TOOLS=0,1,2 tunes several in sequence; "
        "TOOL=<n> a single one. TEMP, HEIGHT and FAN default to the "
        "[offset] pid_* settings. Results are kept for the webapp - persist "
        "them with APPLY PID, not SAVE_CONFIG, because pid_Kp lives in the "
        "included T<n>.cfg.")

    def cmd_CALIBRATE_TOOL_PID(self, gcmd):
        if not self.is_homed():
            raise gcmd.error("Must home first")
        self._require_leveled(gcmd)

        available = sorted(self.toolchanger.tool_numbers)
        tools_param = gcmd.get('TOOLS', None)
        if tools_param is not None:
            try:
                tools = [int(x.strip()) for x in tools_param.split(',')
                         if x.strip()]
            except ValueError:
                raise gcmd.error(
                    "TOOLS must be comma-separated integers, e.g. TOOLS=0,1,2")
        else:
            single = gcmd.get_int('TOOL', self.pid_tool)
            tools = [single if single is not None
                     else self._active_tool_number(gcmd)]
        for tn in tools:
            if tn not in available:
                raise gcmd.error("Tool T%d not configured" % tn)
        if not tools:
            raise gcmd.error("No tools selected")

        temp = gcmd.get_float('TEMP', self.pid_temp, minval=60., maxval=500.)
        height = gcmd.get_float('HEIGHT', self.pid_height, minval=0.)
        fan = gcmd.get_int('FAN', self.pid_fan_speed, minval=0, maxval=100)

        toolhead = self.printer.lookup_object('toolhead')
        st = toolhead.get_status(self.printer.get_reactor().monotonic())
        axmin, axmax = st['axis_minimum'], st['axis_maximum']
        cx = gcmd.get_float('X', (max(0., axmin[0]) + axmax[0]) / 2.)
        cy = gcmd.get_float('Y', (max(0., axmin[1]) + axmax[1]) / 2.)

        self.gcode.respond_info(
            "=== PID tuning: %s at %.0fC, %.1fmm over the bed centre, "
            "part fan %d%% ==="
            % (", ".join("T%d" % t for t in tools), temp, height, fan))

        for tool_nr in tools:
            self._pid_tune_tool(tool_nr, temp, height, fan, cx, cy, gcmd)

        self.gcode.respond_info(
            "=== PID tuning complete === Persist with APPLY PID in the "
            "webapp. SAVE_CONFIG cannot write pid_Kp: it lives in the "
            "included T<n>.cfg. A Klipper restart discards the runtime "
            "values, so apply them before restarting.")

    def _pid_tune_tool(self, tool_nr, temp, height, fan, cx, cy, gcmd):
        extruder_name = self._tool_extruder_name(tool_nr)
        if not extruder_name:
            raise gcmd.error("T%d has no extruder" % tool_nr)
        tool = self.printer.lookup_object('tool T%d' % tool_nr, None)
        fan_name = getattr(tool, 'fan_name', None)

        if self._active_tool_number(gcmd) != tool_nr:
            self.gcode.run_script_from_command(
                "SELECT_TOOL T=%d RESTORE_AXIS=XYZ" % tool_nr)
        self.gcode.run_script_from_command("STOP_TOOL_PROBE_CRASH_DETECTION")

        toolhead = self.printer.lookup_object('toolhead')
        self._move_z(max(height, self.probe_offset_z_hop))
        self.gcode_move.cmd_G1(
            self.gcode.create_gcode_command(
                "G0", "G0",
                {'X': cx, 'Y': cy,
                 'F': self.probe_offset_travel_speed * 60}))
        toolhead.wait_moves()
        self._move_z(height)

        self.gcode.respond_info(
            "--- T%d (%s) ---" % (tool_nr, extruder_name))

        if fan_name:
            self.gcode.run_script_from_command(
                "SET_FAN_SPEED FAN='%s' SPEED=%.3f" % (fan_name, fan / 100.0))
        try:
            self.gcode.run_script_from_command(
                "PID_CALIBRATE HEATER=%s TARGET=%.1f" % (extruder_name, temp))
        finally:
            # Leave nothing hot or spinning, even if the tune aborts
            if fan_name:
                self.gcode.run_script_from_command(
                    "SET_FAN_SPEED FAN='%s' SPEED=0" % fan_name)
            self.gcode.run_script_from_command(
                "SET_HEATER_TEMPERATURE HEATER=%s TARGET=0" % extruder_name)

        values = self._read_staged_pid(extruder_name)
        if values is None:
            raise gcmd.error(
                "PID_CALIBRATE produced no pid_Kp/Ki/Kd for %s"
                % extruder_name)

        values.update({'temp': temp, 'height': height, 'fan': fan,
                       'extruder': extruder_name})
        self.pid_results[str(tool_nr)] = values
        # Nach jedem Tool sichern, nicht erst am Ende: ein Lauf ueber sechs
        # Tools dauert ueber zehn Minuten, und ein Abbruch dazwischen soll
        # die bereits gemessenen Tools nicht mitnehmen.
        self._save_pid_results()
        self.gcode.respond_info(
            "T%d PID: pid_Kp=%.3f pid_Ki=%.3f pid_Kd=%.3f"
            % (tool_nr, values['pid_kp'], values['pid_ki'], values['pid_kd']))

    # ------------------------------------------------------------------
    # Dock calibration
    #
    # Finding a tool's dock position is a hand-eye job: the user jogs the
    # toolhead until the tool sits right, then the position is read back.
    # That means the printer has to move, stop, and wait for a human
    # several times per tool - so this is a state machine driven by one
    # command per step, not a single blocking routine.
    #
    # Offsets are the subtle part. Klipper's toolchanger already disables
    # the per-tool offsets during a change (ToolGcodeTransform.tool = None,
    # see _set_toolchange_transform) - that is why params_park_* are stored
    # offset-free. What it does NOT clear is homing_origin, the manual
    # SET_GCODE_OFFSET: that line is commented out upstream. A leftover
    # Z babystep from a print would therefore shift every dock move. Both
    # are cleared here and restored when the run ends or is aborted.
    # ------------------------------------------------------------------

    DOCK_STATE_FILE = '.offset_dock_results.json'

    def _save_dock_results(self):
        try:
            path = self._get_state_file_path(self.DOCK_STATE_FILE)
            with open(path, 'w') as f:
                json.dump(self.dock_results, f, indent=2)
        except Exception as e:
            self.gcode.respond_info(
                "Warning: could not save dock results: %s" % e)

    def _load_dock_results(self):
        try:
            path = self._get_state_file_path(self.DOCK_STATE_FILE)
            if os.path.exists(path):
                with open(path, 'r') as f:
                    self.dock_results = json.load(f)
                if self.dock_results:
                    self.gcode.respond_info(
                        "Loaded dock positions for %d tools from %s"
                        % (len(self.dock_results), os.path.basename(path)))
        except Exception as e:
            self.gcode.respond_info(
                "Warning: could not load dock results: %s" % e)

    def _dock_defaults(self):
        return {
            'start_z': self.dock_start_z,
            'new_y': self.dock_new_y,
            'test_depth': self.dock_test_depth,
            'test_repeats': self.dock_test_repeats,
            'test_speed': self.dock_test_speed,
            'travel_speed': self.dock_travel_speed,
        }

    def _bed_centre(self):
        toolhead = self.printer.lookup_object('toolhead')
        st = toolhead.get_status(self.printer.get_reactor().monotonic())
        lo, hi = st['axis_minimum'], st['axis_maximum']
        return ((max(0., lo[0]) + hi[0]) / 2.0,
                (max(0., lo[1]) + hi[1]) / 2.0)

    def _dock_offsets_off(self):
        """Take the tool offsets out of the picture and remember them.

        Both layers matter: the per-tool transform and homing_origin. The
        dock paths are expressed in raw toolhead coordinates, so anything
        that shifts a G0 would land the tool next to its dock, not in it."""
        gm = self.gcode_move.get_status(
            self.printer.get_reactor().monotonic())
        self._dock_saved_origin = list(gm.get('homing_origin', [0., 0., 0., 0.]))
        tc = self.toolchanger
        self._dock_saved_transform = getattr(
            getattr(tc, 'gcode_transform', None), 'tool', None)
        if getattr(tc, 'gcode_transform', None) is not None:
            tc.gcode_transform.tool = None
            self.gcode_move.reset_last_position()
        self.gcode.run_script_from_command(
            "SET_GCODE_OFFSET X=0.0 Y=0.0 Z=0.0")

    def _dock_offsets_restore(self):
        tc = self.toolchanger
        if getattr(tc, 'gcode_transform', None) is not None:
            tc.gcode_transform.tool = self._dock_saved_transform
            self.gcode_move.reset_last_position()
        o = self._dock_saved_origin or [0., 0., 0., 0.]
        self.gcode.run_script_from_command(
            "SET_GCODE_OFFSET X=%.4f Y=%.4f Z=%.4f" % (o[0], o[1], o[2]))
        self._dock_saved_origin = None
        self._dock_saved_transform = None

    def _dock_tool_park(self, tool_nr):
        """The tool's stored dock position, or None if it has none."""
        tool = self.printer.lookup_object('tool T%d' % tool_nr, None)
        if tool is None:
            return None
        params = getattr(tool, 'params', None) or {}
        try:
            return (float(params['params_park_x']),
                    float(params['params_park_y']),
                    float(params['params_park_z']))
        except (KeyError, TypeError, ValueError):
            return None

    def _dock_move(self, x=None, y=None, z=None, speed=None):
        toolhead = self.printer.lookup_object('toolhead')
        toolhead.wait_moves()
        toolhead.manual_move([x, y, z], speed or self.dock_travel_speed)
        toolhead.wait_moves()
        self.gcode_move.reset_last_position()

    def _dock_require_run(self, gcmd):
        if not self.dock_state:
            raise gcmd.error(
                "No dock calibration running - start with "
                "DOCK_CALIBRATE_START")
        return self.dock_state

    def _dock_current_tool(self):
        st = self.dock_state
        return st['tools'][st['index']]

    def _dock_announce(self, step, msg):
        self.dock_state['step'] = step
        self.gcode.respond_info("DOCK: %s" % msg)

    cmd_DOCK_CALIBRATE_START_help = (
        "Start dock calibration. MODE=NEW|RECAL TOOLS=0,1,2")

    def cmd_DOCK_CALIBRATE_START(self, gcmd):
        if self.dock_state:
            raise gcmd.error(
                "Dock calibration already running - finish it or run "
                "DOCK_CALIBRATE_ABORT")
        # Argumente zuerst: ein Tippfehler in MODE oder TOOLS soll auch am
        # ungehomten Drucker auffallen, nicht erst nach dem Homing.
        mode = (gcmd.get('MODE', 'RECAL') or 'RECAL').strip().upper()
        if mode not in ('NEW', 'RECAL'):
            raise gcmd.error("MODE must be NEW or RECAL")
        tools_raw = gcmd.get('TOOLS', '')
        try:
            tools = [int(t) for t in str(tools_raw).split(',') if t.strip()]
        except ValueError:
            raise gcmd.error("TOOLS must be a comma separated list, e.g. 0,1")
        if not tools:
            raise gcmd.error("No tools selected")
        known = list(self.toolchanger.tool_numbers)
        for t in tools:
            if t not in known:
                raise gcmd.error("Unknown tool T%d" % t)
        if mode == 'RECAL':
            missing = [t for t in tools if self._dock_tool_park(t) is None]
            if missing:
                raise gcmd.error(
                    "No stored dock position for %s - use MODE=NEW"
                    % ", ".join("T%d" % t for t in missing))

        toolhead = self.printer.lookup_object('toolhead')
        if 'xyz' not in toolhead.get_status(
                self.printer.get_reactor().monotonic())['homed_axes']:
            raise gcmd.error("Must home first")

        start_z = gcmd.get_float('START_Z', self.dock_start_z, above=0.)
        self.dock_state = {
            'mode': mode,
            'tools': tools,
            'index': 0,
            'step': 'confirm_mounted',
            'start_z': start_z,
            'new_y': gcmd.get_float('NEW_Y', self.dock_new_y),
        }
        self._dock_offsets_off()

        cx, cy = self._bed_centre()
        self._dock_move(z=start_z)
        self._dock_move(x=cx, y=cy)
        self.gcode.respond_info(
            "=== Dock calibration (%s): %s ==="
            % (mode, ", ".join("T%d" % t for t in tools)))
        self._dock_announce(
            'confirm_mounted',
            "at bed centre, Z=%.1f. Mount T%d, then run "
            "DOCK_CALIBRATE_MOUNTED." % (start_z, tools[0]))

    cmd_DOCK_CALIBRATE_MOUNTED_help = (
        "Confirm the tool is mounted and approach the dock")

    def cmd_DOCK_CALIBRATE_MOUNTED(self, gcmd):
        st = self._dock_require_run(gcmd)
        if st['step'] != 'confirm_mounted':
            raise gcmd.error("Not waiting for a mount confirmation "
                             "(step: %s)" % st['step'])
        tool_nr = self._dock_current_tool()
        cx, cy = self._bed_centre()

        # Same order as the DOCK_MOUNT macro: Y first, then X, then Z.
        self._dock_move(y=cy)
        self._dock_move(x=cx)
        if st['mode'] == 'RECAL':
            park = self._dock_tool_park(tool_nr)
            self._dock_move(z=park[2])
            self._dock_move(x=park[0])
            self._dock_move(y=park[1])
            where = "stored dock position of T%d (%.2f, %.2f, %.2f)" % (
                tool_nr, park[0], park[1], park[2])
        else:
            self._dock_move(z=st['start_z'])
            self._dock_move(y=st['new_y'])
            where = "bed centre, Y forward (%.2f, %.2f, %.2f)" % (
                cx, st['new_y'], st['start_z'])

        self._dock_announce(
            'jog',
            "T%d at %s. Jog the toolhead until the tool sits in its dock, "
            "then DOCK_CALIBRATE_TEST or DOCK_CALIBRATE_ACCEPT."
            % (tool_nr, where))

    cmd_DOCK_CALIBRATE_TEST_help = (
        "Test the current dock position: move down and back up")

    def cmd_DOCK_CALIBRATE_TEST(self, gcmd):
        st = self._dock_require_run(gcmd)
        if st['step'] not in ('jog', 'tested'):
            raise gcmd.error("Nothing to test yet (step: %s)" % st['step'])
        depth = gcmd.get_float('DEPTH', self.dock_test_depth, above=0.)
        repeats = gcmd.get_int('REPEATS', self.dock_test_repeats, minval=1)
        speed = gcmd.get_float('SPEED', self.dock_test_speed, above=0.)

        toolhead = self.printer.lookup_object('toolhead')
        toolhead.wait_moves()
        z0 = toolhead.get_position()[2]
        for i in range(repeats):
            self.gcode.respond_info(
                "DOCK: test %d/%d - Z %.2f -> %.2f -> %.2f"
                % (i + 1, repeats, z0, z0 - depth, z0))
            self._dock_move(z=z0 - depth, speed=speed)
            self._dock_move(z=z0, speed=speed)
        self._dock_announce(
            'tested',
            "test done. Adjust and test again, or DOCK_CALIBRATE_ACCEPT.")

    cmd_DOCK_CALIBRATE_ACCEPT_help = (
        "Store the current position as this tool's dock position")

    def cmd_DOCK_CALIBRATE_ACCEPT(self, gcmd):
        st = self._dock_require_run(gcmd)
        if st['step'] not in ('jog', 'tested'):
            raise gcmd.error("Nothing to accept yet (step: %s)" % st['step'])
        tool_nr = self._dock_current_tool()

        toolhead = self.printer.lookup_object('toolhead')
        toolhead.wait_moves()
        pos = toolhead.get_position()
        self.dock_results[str(tool_nr)] = {
            'params_park_x': round(pos[0], 3),
            'params_park_y': round(pos[1], 3),
            'params_park_z': round(pos[2], 3),
            'mode': st['mode'],
        }
        self._save_dock_results()
        self.gcode.respond_info(
            "T%d dock: params_park_x=%.3f params_park_y=%.3f "
            "params_park_z=%.3f" % (tool_nr, pos[0], pos[1], pos[2]))

        # Leave the tool behind: drop away from it, then back off, so the
        # user can swap tools by hand. Mirrors the tail of the dropoff path.
        self._dock_move(z=pos[2] - self.dock_test_depth,
                        speed=self.dock_test_speed)
        self._dock_move(y=st['new_y'])

        st['index'] += 1
        if st['index'] >= len(st['tools']):
            self._dock_finish()
            return
        cx, cy = self._bed_centre()
        self._dock_move(z=st['start_z'])
        self._dock_move(x=cx, y=cy)
        self._dock_announce(
            'confirm_mounted',
            "T%d done. Mount T%d, then DOCK_CALIBRATE_MOUNTED."
            % (tool_nr, self._dock_current_tool()))

    def _dock_finish(self):
        done = list(self.dock_state['tools'])
        self.dock_state = None
        self._dock_offsets_restore()
        self.gcode.respond_info(
            "=== Dock calibration complete for %s === Persist with APPLY "
            "DOCK in the webapp; params_park_* live in the included "
            "T<n>.cfg, which SAVE_CONFIG cannot write."
            % ", ".join("T%d" % t for t in done))

    cmd_DOCK_CALIBRATE_ABORT_help = "Abort a running dock calibration"

    def cmd_DOCK_CALIBRATE_ABORT(self, gcmd):
        if not self.dock_state:
            self.gcode.respond_info("DOCK: nothing running")
            return
        self.dock_state = None
        self._dock_offsets_restore()
        self.gcode.respond_info(
            "DOCK: aborted, gcode offsets restored. Measured positions are "
            "kept and can still be applied.")

    def _read_staged_pid(self, section):
        """The values PID_CALIBRATE just staged for SAVE_CONFIG."""
        try:
            configfile = self.printer.lookup_object('configfile')
            pending = configfile.get_status(
                self.printer.get_reactor().monotonic()
            )['save_config_pending_items']
            entry = pending.get(section) or {}
            # Klipper stages them as pid_Kp / pid_Ki / pid_Kd - the option
            # name is kept verbatim, so match case-insensitively.
            lower = {str(k).lower(): v for k, v in entry.items()}
            out = {}
            for key in ('pid_kp', 'pid_ki', 'pid_kd'):
                if key in lower:
                    out[key] = float(lower[key])
            return out if len(out) == 3 else None
        except Exception:
            return None

    def _tool_extruder_temp(self, tool_nr):
        """Current nozzle temperature of a tool, or None.

        Thermal expansion of the hot end is not negligible here: ~30mm of
        brass over 175K is about 0.1mm, the same order as the tap overtravel
        being measured. A Z-switch run taken cold and a tap taken hot
        therefore differ by the expansion, and that difference lands in
        probe_z_offset."""
        try:
            tool = self.printer.lookup_object('tool T%d' % tool_nr, None)
            name = getattr(tool, 'extruder_name', None) if tool else None
            if not name:
                return None
            extruder = self.printer.lookup_object(name, None)
            if extruder is None:
                return None
            st = extruder.get_status(self.printer.get_reactor().monotonic())
            temp = st.get('temperature')
            return float(temp) if temp is not None else None
        except Exception:
            return None

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
            # The nozzle zero itself succeeded; only the re-referencing may
            # be impossible (no Z-switch data yet, single-tool setup, ...).
            # Aborting here would take homing down with it, so warn instead.
            try:
                self._apply_tool_z_offsets(tool_nr, gcmd, save=False)
            except Exception as e:
                self.gcode.respond_info(
                    "NOZZLE_ZERO: Z=0 gesetzt, aber die Tool-Offsets konnten "
                    "nicht auf T%d umreferenziert werden: %s" % (tool_nr, e))

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
            # Always put the original tool back, even if the mesh failed.
            # Guarded so a follow-up failure cannot mask the original error.
            try:
                if self._active_tool_number(gcmd) != tool_nr:
                    self.gcode.run_script_from_command(
                        "SELECT_TOOL T=%d RESTORE_AXIS=XYZ" % tool_nr)
                self.gcode.run_script_from_command(
                    "SET_ACTIVE_TOOL_PROBE T=%d" % tool_nr)
                self.gcode.run_script_from_command(
                    "APPLY_TOOL_PROBE_FOR OP=mesh")
            except Exception as e:
                self.gcode.respond_info(
                    "BED_MESH_AUTO: Rueckwechsel auf T%d fehlgeschlagen: %s"
                    % (tool_nr, e))

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
