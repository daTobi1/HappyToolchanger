import logging
import json
import threading

try:
    from urllib.request import urlopen, Request
    from urllib.error import URLError, HTTPError
except ImportError:
    from urllib2 import urlopen, Request, URLError, HTTPError

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

# Wie oft das nach dem Ausloesen verdruckte Filament nachgezaehlt wird (s)
COUNTDOWN_INTERVAL = 0.5

# Ausgang des Wartens auf das Ersatz-Hotend
HEAT_READY = "ready"
HEAT_RESUMED = "resumed"
HEAT_ABORTED = "aborted"


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

        # Spoolman location tracking
        self.printer_name = config.get('printer_name', '')
        self.spoolman_server = config.get('spoolman_server', '')
        self.default_location = config.get('default_location', '')

        # Endless Spool config
        es_enabled = config.getboolean('endless_spool_enabled', False)
        es_groups_str = config.get('endless_spool_groups', '')
        if es_groups_str:
            es_groups = [int(x.strip()) for x in es_groups_str.split(',')]
        else:
            es_groups = list(range(self.num_tools))

        # Endless-Spool-Wechsel: wie weit das Ersatz-Hotend unter dem Sollwert
        # liegen darf, bevor weitergedruckt wird, und optionales GCode
        # (Vorfoerdern/Reinigen) zwischen Werkzeugwechsel und RESUME.
        self.failover_temp_tolerance = config.getfloat(
            'endless_spool_temp_tolerance', 4.0, minval=0.5)
        # Filament zwischen Sensor und Extruder, das nach dem Ausloesen noch
        # verdruckt wird, bevor pausiert bzw. gewechselt wird (mm, 0 = sofort).
        self.runout_distance = config.getfloat(
            'sensor_runout_distance', 0., minval=0.)
        self._countdowns = {}
        gcode_macro = self.printer.load_object(config, 'gcode_macro')
        self.failover_template = gcode_macro.load_template(
            config, 'endless_spool_gcode', '')

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
            self.printer, self.num_tools, sensor_debounce,
            self.handle_runout, self.handle_insert)
        # Mainsail liest die Sensorzustaende aus diesem Objekt (HtcMixin.ts)
        self.printer.add_object('htc_sensor_manager', self.sensor_manager)
        self._state_loaded = False

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
        self._state_loaded = True
        self.sync_gates_from_sensors()
        self._wrap_temperature_commands()
        self.log("Ready. Active tool: T%d" % self.active_tool
                 if self.active_tool >= 0 else "Ready. No tool active.")
        self._update_t_macros()
        self._resolve_spoolman_server()
        self._sync_spoolman_locations()

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

    def _is_paused(self):
        """Check if the printer is in a paused state."""
        pause_resume = self.printer.lookup_object('pause_resume', None)
        if pause_resume is None:
            return False
        return pause_resume.is_paused

    def _get_ktc_tool_number(self):
        """Get the actual active tool number from KTC (toolchanger module)."""
        tc = self.printer.lookup_object('toolchanger', None)
        if tc and tc.active_tool and hasattr(tc.active_tool, 'tool_number'):
            return tc.active_tool.tool_number
        return -1

    def change_tool(self, tool):
        if tool < 0 or tool >= self.num_tools:
            raise self.gcode.error("HTC: Invalid tool T%d (num_tools=%d)" % (tool, self.num_tools))

        gate = self.ttg_map[tool]
        prev_tool = self.active_tool

        if prev_tool == tool:
            ktc_tool = self._get_ktc_tool_number()
            if ktc_tool >= 0 and ktc_tool != gate:
                self.log("State mismatch: HTC=T%d but KTC=T%d, forcing change"
                         % (tool, ktc_tool), level=0)
            else:
                self.log("Tool T%d already active" % tool)
                return

        self.log("Changing to T%d (gate %d)" % (tool, gate))

        cmd = self.tool_change_command.replace('{tool}', str(gate))
        self.gcode.run_script_from_command(cmd)

        # Klipper's _process_commands catches internal errors, calls
        # invoke_shutdown(), and returns normally. Without this check
        # we'd update active_tool even though the change never completed.
        if self.printer.is_shutdown():
            self.log("Tool change to T%d aborted (printer shutdown)" % tool,
                     level=0)
            self.statistics.record_error()
            return

        # Check if tool change failed (triggered PAUSE via error_gcode)
        if self._is_paused():
            self.log("Tool change to T%d failed (printer paused)" % tool,
                     level=0)
            self.statistics.record_error()
            self._save_state()
            return

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

    def sync_gates_from_sensors(self):
        """Gates mit Sensor folgen dem Sensor, nicht dem gespeicherten Stand.

        Laeuft beim Start -- aus unserem ready-Handler und aus dem von
        htc_sensors, weil deren Reihenfolge von der Config abhaengt. Vor dem
        Laden des gespeicherten Stands passiert nichts, sonst wuerde der
        gleich wieder ueberschrieben.
        """
        if not self._state_loaded:
            return
        for gate in self.sensor_manager.sensors:
            self.gate_status[gate] = (
                GATE_AVAILABLE if self.sensor_manager.is_present(gate)
                else GATE_EMPTY)

    def _is_print_running(self):
        # is_printing folgt idle_timeout und ist auch bei Handbetrieb wahr
        # (jedes GCode zaehlt). Ein Runout darf nur einen echten Druck anhalten.
        print_stats = self.printer.lookup_object('print_stats', None)
        if print_stats is None:
            return self.is_printing
        return print_stats.get_status(
            self.reactor.monotonic())['state'] == 'printing'

    def _tool_for_gate(self, gate):
        for t in range(self.num_tools):
            if self.ttg_map[t] == gate:
                return t
        return -1

    def _run_sensor_event(self, handler, gate, *args):
        # Sensor-Events kommen aus Reactor-Timern und Button-Callbacks. Dort
        # darf kein GCode laufen, und eine Exception wuerde Klipper
        # abschalten. Wie Klippers filament_switch_sensor: eigener Callback,
        # GCode unter dem Mutex, Fehler melden statt werfen.
        def run(eventtime):
            try:
                with self.gcode.get_mutex():
                    handler(gate, *args)
            except Exception as e:
                logging.exception("HTC: sensor event for gate %d failed", gate)
                self.gcode.respond_raw("!! HTC: %s" % (e,))
        self.reactor.register_callback(run)

    def handle_insert(self, gate):
        # Filament ist wieder da: der Rest muss nicht mehr abgezaehlt werden
        self._cancel_countdown(gate)
        self._run_sensor_event(self._process_insert, gate)

    def _process_insert(self, gate):
        if not self._state_loaded:
            return
        if self.gate_status[gate] != GATE_AVAILABLE:
            self.gate_status[gate] = GATE_AVAILABLE
            self.log("Gate %d: filament detected" % gate)
            self._save_state()

    def _note_gate_empty(self, gate):
        if self.gate_status[gate] != GATE_EMPTY:
            self.gate_status[gate] = GATE_EMPTY
            self._save_state()

    def handle_runout(self, gate):
        printing = self._is_print_running()
        tool = self._tool_for_gate(gate)
        active = printing and tool >= 0 and tool == self.active_tool
        if (active and self.runout_distance > 0.
                and self._filament_used() is not None):
            # Der Sensor sitzt vor dem Extruder: zwischen beiden steckt noch
            # Filament. Erst das verdrucken, dann handeln.
            self._run_sensor_event(self._note_gate_empty, gate)
            self._start_countdown(gate, tool)
            return
        self._trigger_runout(gate, printing, tool)

    # --- Restfilament nach dem Ausloesen verdrucken ---

    def _filament_used(self):
        print_stats = self.printer.lookup_object('print_stats', None)
        if print_stats is None:
            return None
        return print_stats.get_status(
            self.reactor.monotonic()).get('filament_used')

    def _print_state(self):
        print_stats = self.printer.lookup_object('print_stats', None)
        if print_stats is None:
            return 'printing' if self.is_printing else 'standby'
        return print_stats.get_status(self.reactor.monotonic())['state']

    def _start_countdown(self, gate, tool):
        self._cancel_countdown(gate)
        countdown = {'tool': tool, 'remaining': self.runout_distance,
                     'last_used': self._filament_used()}
        countdown['timer'] = self.reactor.register_timer(
            lambda et, g=gate: self._countdown_tick(g, et),
            self.reactor.monotonic() + COUNTDOWN_INTERVAL)
        self._countdowns[gate] = countdown
        self.log("Gate %d empty - printing %.0f mm more before acting"
                 % (gate, self.runout_distance), level=0)

    def _cancel_countdown(self, gate):
        countdown = self._countdowns.pop(gate, None)
        if countdown is not None:
            self.reactor.unregister_timer(countdown['timer'])

    def _countdown_tick(self, gate, eventtime):
        # Reactor-Timer: kein GCode, keine Exception nach draussen.
        try:
            return self._countdown_step(gate, eventtime)
        except Exception:
            logging.exception("HTC: runout countdown for gate %d failed", gate)
            self._countdowns.pop(gate, None)
            return self.reactor.NEVER

    def _countdown_step(self, gate, eventtime):
        countdown = self._countdowns.get(gate)
        if countdown is None:
            return self.reactor.NEVER
        state = self._print_state()
        if state not in ('printing', 'paused'):
            # Druck beendet oder abgebrochen: nichts mehr zu tun
            self._countdowns.pop(gate, None)
            return self.reactor.NEVER
        used = self._filament_used()
        delta = used - countdown['last_used']
        countdown['last_used'] = used
        # Mit Vorzeichen: Retract und Un-Retract heben sich auf. Nur zaehlen,
        # solange dieses Tool druckt -- der Slicer kann zwischendurch wechseln.
        if state == 'printing' and self.active_tool == countdown['tool']:
            countdown['remaining'] -= delta
            if countdown['remaining'] <= 0.:
                self._countdowns.pop(gate, None)
                self._trigger_runout(gate, True, countdown['tool'])
                return self.reactor.NEVER
        return eventtime + COUNTDOWN_INTERVAL

    # --- Runout ausfuehren ---

    def _trigger_runout(self, gate, printing, tool):
        # Hier entscheiden, nicht erst unter dem Mutex: nach
        # send_pause_command steht print_stats schon auf "paused".
        if printing and tool >= 0 and tool == self.active_tool:
            # Den Druck sofort anhalten -- auf den Mutex warten wir unter
            # Umstaenden noch eine ganze GCode-Zeile lang.
            pause_resume = self.printer.lookup_object('pause_resume', None)
            if pause_resume is not None:
                pause_resume.send_pause_command()

        # Sensor-Events kommen aus Reactor-Timern (siehe _run_sensor_event).
        # Der Wechsel laeuft in drei Abschnitten: GCode nur unter dem Mutex,
        # das Warten aufs Heizen ohne -- sonst nimmt Klipper minutenlang
        # keinen Befehl an, auch kein CANCEL_PRINT.
        def run(eventtime):
            try:
                with self.gcode.get_mutex():
                    plan = self._process_runout(gate, printing, tool)
                if plan is None:
                    return
                outcome = self._wait_for_heater(plan)
                with self.gcode.get_mutex():
                    self._finish_failover(plan, outcome)
            except Exception as e:
                logging.exception("HTC: runout on gate %d failed", gate)
                self.gcode.respond_raw("!! HTC: %s" % (e,))
        self.reactor.register_callback(run)

    def _process_runout(self, gate, printing, tool):
        self.gate_status[gate] = GATE_EMPTY

        if not printing:
            self.log("Gate %d empty (not printing, no action)" % gate)
            self._save_state()
            return None

        if tool < 0 or tool != self.active_tool:
            self.log("Gate %d empty but not active tool, updating status only" % gate)
            self._save_state()
            return None

        next_gate = self.endless_spool.find_next_gate(gate, self.gate_status)

        if next_gate < 0:
            self.gcode.run_script_from_command("PAUSE")
            self.statistics.record_error()
            self._save_state()
            self.gcode.respond_raw(
                "!! HTC: Filament runout on T%d (gate %d) - no replacement in group %d!"
                % (tool, gate, self.endless_spool.groups[gate]))
            return None

        return self._begin_failover(tool, gate, next_gate)

    def _extruder_name_for_gate(self, gate):
        # KTC kennt den Extruder je Tool; ohne KTC gilt Klippers Namensschema.
        tc = self.printer.lookup_object('toolchanger', None)
        ktc_tool = tc.lookup_tool(gate) if tc is not None else None
        name = getattr(ktc_tool, 'extruder_name', None)
        if name:
            return name
        return 'extruder' if gate == 0 else 'extruder%d' % gate

    def _heater_target(self, extruder_name):
        extruder = self.printer.lookup_object(extruder_name, None)
        if extruder is None:
            return 0.
        return extruder.get_heater().get_status(
            self.reactor.monotonic())['target']

    def _failover_tool_mounted(self, gate):
        # KTC faengt Wechselfehler selbst ab (error_gcode) und kehrt ohne
        # Exception zurueck -- ob das Ersatz-Tool haengt, sagt nur der Status.
        if self.printer.is_shutdown():
            return False
        tc = self.printer.lookup_object('toolchanger', None)
        if tc is None:
            return True
        status = tc.get_status(self.reactor.monotonic())
        return (status.get('status') == 'ready'
                and status.get('tool_number') == gate)

    def _begin_failover(self, tool, gate, next_gate):
        """Abschnitt 1 (unter dem Mutex): anhalten, Ersatz heizen, wechseln.

        Das Ersatz-Hotend heizt schon vor dem Wechsel auf den Sollwert des
        alten. Die Zuordnung wird erst uebernommen, wenn das Tool haengt;
        sonst bleibt der Druck pausiert und T<tool> zeigt auf das alte Gate.
        Liefert den Plan fuer die naechsten Abschnitte oder None.
        """
        run = self.gcode.run_script_from_command
        old_extruder = self._extruder_name_for_gate(gate)
        new_extruder = self._extruder_name_for_gate(next_gate)
        target = self._heater_target(old_extruder)
        plan = {'tool': tool, 'old_gate': gate, 'new_gate': next_gate,
                'old_extruder': old_extruder, 'new_extruder': new_extruder,
                'temp': target,
                'hand_over': target > 0. and new_extruder != old_extruder}

        self.log("Endless Spool: T%d gate %d -> %d" % (tool, gate, next_gate),
                 level=0)
        run("PAUSE")
        if plan['hand_over']:
            run("SET_HEATER_TEMPERATURE HEATER=%s TARGET=%.1f"
                % (new_extruder, target))
        run(self.tool_change_command.replace('{tool}', str(next_gate)))

        if not self._failover_tool_mounted(next_gate):
            self.statistics.record_error()
            self._save_state()
            self.gcode.respond_raw(
                "!! HTC: Endless Spool: tool change to gate %d failed - "
                "print stays paused, T%d still mapped to gate %d"
                % (next_gate, tool, gate))
            return None

        self.ttg_map[tool] = next_gate
        self.statistics.record_endless_spool_event()
        spool_id = (self.gate_spool_ids[next_gate]
                    if next_gate < len(self.gate_spool_ids) else -1)
        self._set_moonraker_spool(spool_id)
        self._save_state()
        return plan

    def _wait_for_heater(self, plan):
        """Abschnitt 2 (OHNE Mutex): warten, bis das Ersatz-Hotend heiss ist.

        Klipper bleibt bedienbar. Deshalb kann sich waehrenddessen alles
        aendern -- jede Runde neu pruefen. Ergebnis: HEAT_READY, HEAT_RESUMED
        (jemand hat selbst fortgesetzt) oder HEAT_ABORTED.
        """
        if not plan['hand_over']:
            return HEAT_READY
        extruder = self.printer.lookup_object(plan['new_extruder'], None)
        if extruder is None:
            return HEAT_READY
        heater = extruder.get_heater()
        while True:
            if self.printer.is_shutdown():
                return HEAT_ABORTED
            if not self._is_paused():
                if self._print_state() == 'printing':
                    return HEAT_RESUMED
                return HEAT_ABORTED
            now = self.reactor.monotonic()
            temp, target = heater.get_temp(now)
            if target <= 0.:
                return HEAT_ABORTED
            if temp >= target - self.failover_temp_tolerance:
                return HEAT_READY
            self.reactor.pause(now + 1.)

    def _finish_failover(self, plan, outcome):
        """Abschnitt 3 (unter dem Mutex): altes Hotend aus, Hook, RESUME."""
        run = self.gcode.run_script_from_command
        tool, next_gate = plan['tool'], plan['new_gate']
        if outcome == HEAT_ABORTED:
            self.gcode.respond_raw(
                "!! HTC: Endless Spool: heating %s was aborted - T%d is on "
                "gate %d, print is not resumed"
                % (plan['new_extruder'], tool, next_gate))
            return
        if plan['hand_over']:
            run("SET_HEATER_TEMPERATURE HEATER=%s TARGET=0"
                % plan['old_extruder'])
        if outcome == HEAT_RESUMED:
            self.log("Endless Spool: print was resumed manually", level=0)
            return

        # Eigener Kontext auf Basis des Standardkontexts: render(context)
        # ersetzt ihn sonst, und `printer` fehlt im Template.
        context = self.failover_template.create_template_context()
        context.update({k: plan[k] for k in (
            'tool', 'old_gate', 'new_gate', 'old_extruder', 'new_extruder',
            'temp')})
        self.failover_template.run_gcode_from_command(context)

        run("RESUME")
        if self._is_paused():
            self.gcode.respond_raw(
                "!! HTC: Endless Spool: RESUME refused after switching T%d "
                "to gate %d - print stays paused" % (tool, next_gate))

    # --- M104/M109 folgen der Tool-Gate-Zuordnung ---

    def _wrap_temperature_commands(self):
        # Erst bei klippy:ready: gcode_macro benennt M109 (rename_existing)
        # in klippy:connect um, danach steht fest, wer der Vorgaenger ist.
        for name in ('M104', 'M109'):
            prev = self.gcode.register_command(name, None)
            if prev is None:
                continue
            self.gcode.register_command(
                name,
                lambda gcmd, n=name, p=prev: self._cmd_temp_redirect(gcmd, n, p))

    def _cmd_temp_redirect(self, gcmd, name, prev):
        # Der Slicer adressiert das logische Tool ("M104 T0 S210"). Zeigt T0
        # nach einem Endless-Spool-Wechsel auf ein anderes Gate, muss das
        # Hotend dieses Gates heizen -- sonst heizt das leere weiter.
        tool = gcmd.get_int('T', None)
        if (tool is None or tool < 0 or tool >= self.num_tools
                or self.ttg_map[tool] == tool):
            return prev(gcmd)
        gate = self.ttg_map[tool]
        params = dict(gcmd.get_command_parameters())
        params['T'] = str(gate)
        line = "%s %s" % (name, " ".join(
            "%s%s" % (k, v) for k, v in params.items()))
        self.log("%s T%d -> gate %d" % (name, tool, gate), level=2)
        return prev(self.gcode.create_gcode_command(name, line, params))

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
        old_spool_id = self.gate_spool_ids[gate]
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
        # Update Spoolman location tracking
        if spool_id != old_spool_id:
            if old_spool_id > 0:
                self._set_spoolman_location(old_spool_id, self.default_location)
            if spool_id > 0:
                self._set_spoolman_location(
                    spool_id, self._make_location_string(gate))
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
        if tool < 0:
            tool = self._get_ktc_tool_number()
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

    # --- Spoolman Location Tracking ---

    def _resolve_spoolman_server(self):
        """Resolve the Spoolman server URL from Moonraker or config."""
        if self.spoolman_support == 'off':
            self._spoolman_url = None
            return
        # Try to get URL from Moonraker's spoolman config
        if not self.spoolman_server:
            try:
                webhooks = self.printer.lookup_object('webhooks', None)
                if webhooks:
                    webhooks.call_remote_method(
                        'spoolman_get_server_url',
                        callback=self._on_spoolman_url)
            except Exception:
                pass
        self._spoolman_url = self.spoolman_server or None
        if self._spoolman_url:
            # Strip trailing slash
            self._spoolman_url = self._spoolman_url.rstrip('/')
            self.log("Spoolman location tracking: %s" % self._spoolman_url,
                     level=2)

    def _on_spoolman_url(self, url):
        """Callback if Moonraker provides the Spoolman URL."""
        if url and not self._spoolman_url:
            self._spoolman_url = url.rstrip('/')

    def _make_location_string(self, gate):
        """Build location string for a gate, e.g. 'Voron 350 - T0'."""
        if self.printer_name:
            return "%s - T%d" % (self.printer_name, gate)
        return "T%d" % gate

    def _sync_spoolman_locations(self):
        """On startup, set location for all loaded spools."""
        if not self._spoolman_url or not self.printer_name:
            return
        for gate in range(self.num_tools):
            spool_id = self.gate_spool_ids[gate]
            if spool_id > 0:
                self._set_spoolman_location(
                    spool_id, self._make_location_string(gate))

    def _set_spoolman_location(self, spool_id, location):
        """Update a spool's location in Spoolman (non-blocking)."""
        if not self._spoolman_url:
            return
        url = "%s/api/v1/spool/%d" % (self._spoolman_url, spool_id)
        data = json.dumps({"location": location}).encode('utf-8')
        reactor = self.printer.get_reactor()

        def report(msg, level=1):
            """Log from the worker thread.

            self.log() ends in gcode.respond_info(), and that belongs to the
            reactor thread - it appends to Klipper's output while the reactor
            may be writing there too. register_async_callback is the only
            thread-safe way back in, so the message is handed over instead of
            written directly. Matters most when Spoolman is unreachable: that
            path logs on every failed update.
            """
            reactor.register_async_callback(
                lambda eventtime: self.log(msg, level=level))

        def do_request():
            try:
                req = Request(url, data=data, method='PATCH')
                req.add_header('Content-Type', 'application/json')
                resp = urlopen(req, timeout=5)
                resp.read()
                resp.close()
                report("Spoolman: spool %d location -> '%s'" %
                       (spool_id, location), level=2)
            except HTTPError as e:
                if e.code == 404:
                    report("Spoolman: spool %d not found (404)" % spool_id)
                else:
                    report("Spoolman: HTTP %d updating spool %d" %
                           (e.code, spool_id))
            except Exception as e:
                report("Spoolman: failed to update spool %d: %s" %
                       (spool_id, str(e)))

        # Run in background thread to avoid blocking the reactor
        thread = threading.Thread(target=do_request, daemon=True)
        thread.start()

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
            'runout_remaining': [
                round(self._countdowns[g]['remaining'], 1)
                if g in self._countdowns else -1
                for g in range(self.num_tools)],
            'statistics': self.statistics.get_data(),
        }
