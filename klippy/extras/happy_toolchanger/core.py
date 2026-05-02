import logging

from .endless_spool import EndlessSpool, GATE_AVAILABLE, GATE_EMPTY
from .statistics import Statistics

# Persistence variable names (must be lowercase for save_variables)
VARS_TTG_MAP = "htc_tool_to_gate_map"
VARS_GATE_STATUS = "htc_gate_status"
VARS_GATE_COLORS = "htc_gate_colors"
VARS_GATE_MATERIALS = "htc_gate_materials"
VARS_GATE_TEMPERATURES = "htc_gate_temperatures"
VARS_GATE_SPOOL_IDS = "htc_gate_spool_ids"
VARS_GATE_FILAMENT_NAMES = "htc_gate_filament_names"
VARS_ENDLESS_SPOOL_GROUPS = "htc_endless_spool_groups"
VARS_ACTIVE_TOOL = "htc_active_tool"
VARS_STATS = "htc_stats"
VARS_REVISION = "htc__revision"


class HappyToolchanger:
    def __init__(self, config):
        self.config = config
        self.printer = config.get_printer()
        self.reactor = self.printer.get_reactor()
        self.gcode = self.printer.lookup_object('gcode')

        # Config
        self.num_tools = config.getint('num_tools', 4, minval=1, maxval=16)
        self.tool_change_command = config.get('tool_change_command', 'SELECT_TOOL T={tool}')
        self.log_level = config.getint('log_level', 1, minval=0, maxval=2)
        self.spoolman_support = config.get('spoolman_support', 'off')

        # Endless Spool config
        es_enabled = config.getboolean('endless_spool_enabled', False)
        es_groups_str = config.get('endless_spool_groups', '')
        if es_groups_str:
            es_groups = [int(x.strip()) for x in es_groups_str.split(',')]
        else:
            es_groups = list(range(self.num_tools))

        # Gate metadata defaults from config
        self.default_gate_colors = self._parse_list(config.get('gate_colors', ''), self.num_tools, '')
        self.default_gate_materials = self._parse_list(config.get('gate_materials', ''), self.num_tools, '')
        self.default_gate_temperatures = self._parse_int_list(config.get('gate_temperatures', ''), self.num_tools, 0)
        self.default_gate_filament_names = self._parse_list(config.get('gate_filament_names', ''), self.num_tools, '')

        # State
        self.active_tool = -1
        self.ttg_map = list(range(self.num_tools))
        self.gate_status = [GATE_AVAILABLE] * self.num_tools
        self.gate_colors = list(self.default_gate_colors)
        self.gate_materials = list(self.default_gate_materials)
        self.gate_temperatures = list(self.default_gate_temperatures)
        self.gate_spool_ids = [-1] * self.num_tools
        self.gate_filament_names = list(self.default_gate_filament_names)

        # Sub-components
        self.endless_spool = EndlessSpool(self.num_tools, es_groups, es_enabled)
        self.statistics = Statistics(self.num_tools)

        # Persistence
        self.save_variables = None
        self._can_write = True

        # Print state
        self.is_printing = False

        # Sensor manager
        sensor_debounce = config.getfloat('sensor_debounce_time', 1.0, minval=0.1)
        from .sensor_manager import SensorManager
        self.sensor_manager = SensorManager(
            self.printer, self.num_tools, sensor_debounce, self.handle_runout)

        # NOTE: T-macros are defined in happy_toolchanger.cfg as [gcode_macro T0] etc.
        # They call HTC_CHANGE_TOOL TOOL=N. This allows Mainsail to see color/spool_id vars.

        # Register events
        self.printer.register_event_handler('klippy:ready', self._handle_ready)
        self.printer.register_event_handler('klippy:disconnect', self._handle_disconnect)
        self.printer.register_event_handler("idle_timeout:printing", self._handle_printing)
        self.printer.register_event_handler("idle_timeout:ready", self._handle_not_printing)
        self.printer.register_event_handler("idle_timeout:idle", self._handle_not_printing)

        # Register GCode commands
        self.gcode.register_command('HTC_STATUS', self.cmd_HTC_STATUS,
                                    desc="Show HappyToolchanger status")
        self.gcode.register_command('HTC_CHANGE_TOOL', self.cmd_HTC_CHANGE_TOOL,
                                    desc="Change tool with endless spool support")
        self.gcode.register_command('HTC_REMAP', self.cmd_HTC_REMAP,
                                    desc="Remap tool to gate")
        self.gcode.register_command('HTC_RESET_TTG', self.cmd_HTC_RESET_TTG,
                                    desc="Reset tool-to-gate map to defaults")
        self.gcode.register_command('HTC_SET_GATE', self.cmd_HTC_SET_GATE,
                                    desc="Set gate metadata")
        self.gcode.register_command('HTC_ENDLESS_SPOOL', self.cmd_HTC_ENDLESS_SPOOL,
                                    desc="Configure endless spool")
        self.gcode.register_command('HTC_STATS', self.cmd_HTC_STATS,
                                    desc="Show print statistics")
        self.gcode.register_command('HTC_STATS_RESET', self.cmd_HTC_STATS_RESET,
                                    desc="Reset print statistics")
        self.gcode.register_command('HTC_SYNC', self.cmd_HTC_SYNC,
                                    desc="Manually sync state")

        self.log("HappyToolchanger v1.0 initialized (%d tools)" % self.num_tools)

    # --- Helpers ---

    def _parse_list(self, s, count, default):
        if not s.strip():
            return [default] * count
        parts = [x.strip() for x in s.split(',')]
        result = []
        for i in range(count):
            result.append(parts[i] if i < len(parts) else default)
        return result

    def _parse_int_list(self, s, count, default):
        if not s.strip():
            return [default] * count
        parts = [x.strip() for x in s.split(',')]
        result = []
        for i in range(count):
            result.append(int(parts[i]) if i < len(parts) and parts[i] else default)
        return result

    def log(self, msg, level=1):
        if level <= self.log_level:
            self.gcode.respond_info("HTC: " + msg)

    # --- Event Handlers ---

    def _handle_ready(self):
        self.save_variables = self.printer.lookup_object('save_variables', None)
        if self.save_variables is None:
            raise self.config.error(
                "HappyToolchanger requires [save_variables] in your config. "
                "Add [save_variables] with a filename to your printer.cfg.")
        self._load_persisted_state()
        self.log("Ready. Active tool: T%d" % self.active_tool
                 if self.active_tool >= 0 else "Ready. No tool active.")
        self._update_t_macros()

    def _handle_disconnect(self):
        pass

    def _handle_printing(self, eventtime):
        self.is_printing = True
        import time
        self.statistics.set_active_tool(self.active_tool, time.monotonic())

    def _handle_not_printing(self, eventtime):
        if self.is_printing:
            import time
            self.statistics.set_active_tool(-1, time.monotonic())
        self.is_printing = False

    # --- Persistence ---

    def _load_persisted_state(self):
        v = self.save_variables.allVariables
        self.ttg_map = v.get(VARS_TTG_MAP, list(range(self.num_tools)))
        self.gate_status = v.get(VARS_GATE_STATUS, [GATE_AVAILABLE] * self.num_tools)
        self.gate_colors = v.get(VARS_GATE_COLORS, list(self.default_gate_colors))
        self.gate_materials = v.get(VARS_GATE_MATERIALS, list(self.default_gate_materials))
        self.gate_temperatures = v.get(VARS_GATE_TEMPERATURES, list(self.default_gate_temperatures))
        self.gate_spool_ids = v.get(VARS_GATE_SPOOL_IDS, [-1] * self.num_tools)
        self.gate_filament_names = v.get(VARS_GATE_FILAMENT_NAMES, list(self.default_gate_filament_names))
        self.active_tool = v.get(VARS_ACTIVE_TOOL, -1)

        es_groups = v.get(VARS_ENDLESS_SPOOL_GROUPS, None)
        if es_groups:
            self.endless_spool.update_groups(es_groups)

        saved_stats = v.get(VARS_STATS, None)
        if saved_stats:
            self.statistics = Statistics(self.num_tools, saved_data=saved_stats)

    def _save_state(self):
        if not self.save_variables or not self._can_write:
            return
        v = self.save_variables.allVariables
        v[VARS_TTG_MAP] = list(self.ttg_map)
        v[VARS_GATE_STATUS] = list(self.gate_status)
        v[VARS_GATE_COLORS] = list(self.gate_colors)
        v[VARS_GATE_MATERIALS] = list(self.gate_materials)
        v[VARS_GATE_TEMPERATURES] = list(self.gate_temperatures)
        v[VARS_GATE_SPOOL_IDS] = list(self.gate_spool_ids)
        v[VARS_GATE_FILAMENT_NAMES] = list(self.gate_filament_names)
        v[VARS_ENDLESS_SPOOL_GROUPS] = list(self.endless_spool.groups)
        v[VARS_ACTIVE_TOOL] = self.active_tool
        v[VARS_STATS] = self.statistics.get_data()
        rev = v.get(VARS_REVISION, 0) + 1
        self.gcode.run_script_from_command(
            "SAVE_VARIABLE VARIABLE=%s VALUE=%d" % (VARS_REVISION, rev))
        self._update_t_macros()

    # --- Tool Change ---

    def change_tool(self, tool):
        if tool < 0 or tool >= self.num_tools:
            raise self.gcode.error("HTC: Invalid tool T%d (num_tools=%d)" % (tool, self.num_tools))

        gate = self.ttg_map[tool]
        prev_tool = self.active_tool

        if prev_tool == tool:
            self.log("Tool T%d already active" % tool)
            return

        self.log("Changing to T%d (gate %d)" % (tool, gate))

        cmd = self.tool_change_command.replace('{tool}', str(gate))
        self.gcode.run_script_from_command(cmd)

        import time
        now = time.monotonic()
        if prev_tool >= 0:
            self.statistics.record_swap(prev_tool, tool)
        self.statistics.set_active_tool(tool, now)
        self.active_tool = tool
        self._save_state()

        # Tell Moonraker which spool is now active for filament tracking
        gate = self.ttg_map[tool]
        spool_id = self.gate_spool_ids[gate] if gate < len(self.gate_spool_ids) else -1
        self._set_moonraker_spool(spool_id)

        self.log("Tool change complete: T%d" % tool)

    # --- Endless Spool Integration ---

    def handle_runout(self, gate):
        self.gate_status[gate] = GATE_EMPTY

        if not self.is_printing:
            self.log("Gate %d empty (not printing, no action)" % gate)
            self._save_state()
            return

        tool = -1
        for t in range(self.num_tools):
            if self.ttg_map[t] == gate:
                tool = t
                break

        if tool < 0 or tool != self.active_tool:
            self.log("Gate %d empty but not active tool, updating status only" % gate)
            self._save_state()
            return

        next_gate = self.endless_spool.find_next_gate(gate, self.gate_status)

        if next_gate < 0:
            self.gcode.run_script_from_command("PAUSE")
            self.statistics.record_error()
            self._save_state()
            raise self.gcode.error(
                "HTC: Filament runout on T%d (gate %d) - no replacement in group %d!"
                % (tool, gate, self.endless_spool.groups[gate]))

        self.log("Endless Spool: T%d remapping gate %d -> %d" % (tool, gate, next_gate), level=0)
        self.ttg_map[tool] = next_gate
        self.statistics.record_endless_spool_event()

        self.gcode.run_script_from_command("PAUSE")
        cmd = self.tool_change_command.replace('{tool}', str(next_gate))
        self.gcode.run_script_from_command(cmd)
        self._save_state()
        self.gcode.run_script_from_command("RESUME")

    # --- GCode Commands ---

    def cmd_HTC_STATUS(self, gcmd):
        lines = []
        lines.append("HappyToolchanger Status")
        lines.append("Active tool: %s" % ("T%d" % self.active_tool if self.active_tool >= 0 else "None"))
        lines.append("Tool-to-gate map: %s" % self.ttg_map)
        lines.append("Gate status: %s" % ["available" if s else "empty" for s in self.gate_status])
        lines.append("Endless Spool: %s (groups: %s)" % (
            "enabled" if self.endless_spool.enabled else "disabled",
            self.endless_spool.groups))
        for i in range(self.num_tools):
            gate = self.ttg_map[i]
            lines.append("  T%d -> Gate %d [%s] %s %s %s" % (
                i, gate,
                "OK" if self.gate_status[gate] else "EMPTY",
                self.gate_colors[gate] or "-",
                self.gate_materials[gate] or "-",
                self.gate_filament_names[gate] or "-"))
        gcmd.respond_info("\n".join(lines))

    def cmd_HTC_CHANGE_TOOL(self, gcmd):
        tool = gcmd.get_int('TOOL')
        self.change_tool(tool)

    def cmd_HTC_REMAP(self, gcmd):
        tool = gcmd.get_int('TOOL')
        gate = gcmd.get_int('GATE')
        if tool < 0 or tool >= self.num_tools:
            raise gcmd.error("Invalid tool T%d" % tool)
        if gate < 0 or gate >= self.num_tools:
            raise gcmd.error("Invalid gate %d" % gate)
        self.ttg_map[tool] = gate
        self._save_state()
        gcmd.respond_info("HTC: T%d remapped to gate %d" % (tool, gate))

    def cmd_HTC_RESET_TTG(self, gcmd):
        self.ttg_map = list(range(self.num_tools))
        self._save_state()
        gcmd.respond_info("HTC: Tool-to-gate map reset to defaults")

    def cmd_HTC_SET_GATE(self, gcmd):
        gate = gcmd.get_int('GATE')
        if gate < 0 or gate >= self.num_tools:
            raise gcmd.error("Invalid gate %d" % gate)
        color = gcmd.get('COLOR', self.gate_colors[gate])
        material = gcmd.get('MATERIAL', self.gate_materials[gate])
        temp = gcmd.get_int('TEMP', self.gate_temperatures[gate])
        name = gcmd.get('NAME', self.gate_filament_names[gate])
        status = gcmd.get_int('STATUS', self.gate_status[gate])
        spool_id = gcmd.get_int('SPOOL_ID', self.gate_spool_ids[gate])
        self.gate_colors[gate] = color
        self.gate_materials[gate] = material
        self.gate_temperatures[gate] = temp
        self.gate_filament_names[gate] = name
        self.gate_status[gate] = status
        self.gate_spool_ids[gate] = spool_id
        self._save_state()
        # If this gate is the active tool's gate, update Moonraker's active spool
        if self.active_tool >= 0 and self.ttg_map[self.active_tool] == gate:
            self._set_moonraker_spool(spool_id)
        gcmd.respond_info("HTC: Gate %d updated (spool_id=%d)" % (gate, spool_id))

    def cmd_HTC_ENDLESS_SPOOL(self, gcmd):
        enable = gcmd.get_int('ENABLE', int(self.endless_spool.enabled))
        groups_str = gcmd.get('GROUPS', '')
        self.endless_spool.enabled = bool(enable)
        if groups_str:
            groups = [int(x.strip()) for x in groups_str.split(',')]
            if len(groups) != self.num_tools:
                raise gcmd.error("GROUPS must have %d entries" % self.num_tools)
            self.endless_spool.update_groups(groups)
        self._save_state()
        gcmd.respond_info("HTC: Endless Spool %s (groups: %s)" % (
            "enabled" if self.endless_spool.enabled else "disabled",
            self.endless_spool.groups))

    def cmd_HTC_STATS(self, gcmd):
        data = self.statistics.get_data()
        lines = ["HappyToolchanger Statistics"]
        lines.append("Total swaps: %d" % data['total_swaps'])
        lines.append("Total errors: %d" % data['total_errors'])
        lines.append("Endless Spool events: %d" % data['endless_spool_events'])
        lines.append("Last reset: %s" % data['last_reset'])
        for i, pt in enumerate(data['per_tool']):
            lines.append("  T%d: swaps_to=%d swaps_from=%d time=%.0fs" % (
                i, pt['swaps_to'], pt['swaps_from'], pt['time_active_s']))
        gcmd.respond_info("\n".join(lines))

    def cmd_HTC_STATS_RESET(self, gcmd):
        self.statistics.reset()
        self._save_state()
        gcmd.respond_info("HTC: Statistics reset")

    def cmd_HTC_SYNC(self, gcmd):
        tool = gcmd.get_int('TOOL', -1)
        if tool >= 0:
            self.active_tool = tool
            self._save_state()
        gcmd.respond_info("HTC: State synced (active tool: T%d)" % self.active_tool
                          if self.active_tool >= 0 else "HTC: State synced (no tool active)")

    def _update_t_macros(self):
        """Update T-macro variables for Mainsail visibility."""
        for tool in range(self.num_tools):
            gate = self.ttg_map[tool]
            t_macro = self.printer.lookup_object("gcode_macro T%d" % tool, None)
            if t_macro:
                t_macro.variables = dict(t_macro.variables)
                t_macro.variables['color'] = self.gate_colors[gate] if gate < len(self.gate_colors) else ''
                t_macro.variables['spool_id'] = self.gate_spool_ids[gate] if gate < len(self.gate_spool_ids) else -1

    # --- Spoolman Integration ---

    def _set_moonraker_spool(self, spool_id):
        """Notify Moonraker of the active spool for filament tracking."""
        if self.spoolman_support == 'off':
            return
        webhooks = self.printer.lookup_object('webhooks', None)
        if webhooks is None:
            return
        try:
            # Moonraker listens for this and updates the active spool
            sid = spool_id if spool_id > 0 else None
            webhooks.call_remote_method('spoolman_set_active_spool',
                                        spool_id=sid)
            self.log("Spoolman: active spool set to %s" %
                     (str(spool_id) if sid else "None"), level=2)
        except Exception as e:
            self.log("Spoolman: failed to set active spool: %s" % str(e))

    # --- Webhook Status ---

    def get_status(self, eventtime):
        return {
            'num_tools': self.num_tools,
            'active_tool': self.active_tool,
            'ttg_map': list(self.ttg_map),
            'gate_status': list(self.gate_status),
            'gate_colors': list(self.gate_colors),
            'gate_materials': list(self.gate_materials),
            'gate_temperatures': list(self.gate_temperatures),
            'gate_spool_ids': list(self.gate_spool_ids),
            'gate_filament_names': list(self.gate_filament_names),
            'endless_spool': self.endless_spool.get_status(),
            'is_printing': self.is_printing,
            'statistics': self.statistics.get_data(),
        }
