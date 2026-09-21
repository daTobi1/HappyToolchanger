#!/usr/bin/env python3
"""Prueft den Endless-Spool-Wechsel und die M104/M109-Umleitung in
klippy/extras/happy_toolchanger/core.py gegen Klipper-Attrappen.

Braucht kein Klipper und keinen Drucker -- nur Python 3:

    python3 tests/check_htc_failover.py

Exit-Code 0 = sauber, 1 = Befunde.
"""
import contextlib
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
for extras in (os.path.join(HERE, "..", "klippy", "extras"), HERE):
    if os.path.isdir(os.path.join(extras, "happy_toolchanger")):
        sys.path.insert(0, extras)
        break
else:
    raise SystemExit("Paket happy_toolchanger nicht gefunden")

from happy_toolchanger.core import HappyToolchanger  # noqa: E402

FINDINGS = []
CHECKS = [0]
MISSING = object()


def ok(cond, what, detail=""):
    CHECKS[0] += 1
    if not cond:
        FINDINGS.append("%s%s" % (what, (" -- " + detail) if detail else ""))


class GcodeError(Exception):
    pass


class FakeGcmd:
    def __init__(self, name, line, params):
        self.name, self.line, self.params = name, line, dict(params)

    def get_int(self, key, default=None):
        return int(self.params[key]) if key in self.params else default

    def get_command_parameters(self):
        return self.params


class FakeGcode:
    error = GcodeError

    def __init__(self, world):
        self.world = world
        self.commands = {}
        self.scripts = []
        self.raw = []

    def register_command(self, name, func, desc=None):
        prev = self.commands.pop(name, None)
        if func is not None:
            self.commands[name] = func
        return prev

    def run_script_from_command(self, script):
        self.scripts.append(script)
        self.world.on_script(script)

    def respond_info(self, msg):
        pass

    def respond_raw(self, msg):
        self.raw.append(msg)

    def get_mutex(self):
        return contextlib.nullcontext()

    def create_gcode_command(self, name, line, params):
        return FakeGcmd(name, line, params)


class FakeReactor:
    NEVER = 9e99

    def monotonic(self):
        return 100.

    def register_callback(self, callback):
        callback(100.)

    def register_timer(self, callback, waketime):
        return object()

    def unregister_timer(self, timer):
        pass


class FakeTemplate:
    def __init__(self):
        self.runs = []

    def create_template_context(self):
        return {'printer': 'PRINTER'}

    def run_gcode_from_command(self, context):
        self.runs.append(context)


class FakeGcodeMacro:
    def __init__(self, template):
        self.template = template

    def load_template(self, config, option, default=None):
        return self.template


class FakeHeater:
    def __init__(self, target):
        self.target = target

    def get_status(self, eventtime):
        return {'target': self.target}


class FakeExtruder:
    def __init__(self, target):
        self.heater = FakeHeater(target)

    def get_heater(self):
        return self.heater


class FakeKtcTool:
    def __init__(self, number, extruder_name):
        self.tool_number, self.extruder_name = number, extruder_name


class FakeToolchanger:
    def __init__(self, extruder_names):
        self.tools = {i: FakeKtcTool(i, n) for i, n in enumerate(extruder_names)}
        self.status = 'ready'
        self.tool_number = 0

    def lookup_tool(self, number):
        return self.tools.get(number)

    def get_status(self, eventtime):
        return {'status': self.status, 'tool_number': self.tool_number}


class FakePauseResume:
    def __init__(self):
        self.is_paused = False
        self.pause_commands = 0

    def send_pause_command(self):
        self.pause_commands += 1


class FakePrintStats:
    def __init__(self):
        self.state = 'printing'

    def get_status(self, eventtime):
        return {'state': self.state}


class FakeSaveVariables:
    def __init__(self):
        self.allVariables = {}


class FakeWebhooks:
    def __init__(self):
        self.calls = []

    def call_remote_method(self, method, **kwargs):
        self.calls.append((method, kwargs))


class World:
    """Printer + Config in einem; on_script spielt die Maschine nach."""

    def __init__(self, options=None, tool_change_fails=False,
                 resume_refused=False, with_ktc=True):
        self.options = {'num_tools': '4', 'spoolman_support': 'sync',
                        'endless_spool_enabled': '1',
                        'endless_spool_groups': '0, 1, 0, 1'}
        self.options.update(options or {})
        self.tool_change_fails = tool_change_fails
        self.resume_refused = resume_refused
        self.reactor = FakeReactor()
        self.gcode = FakeGcode(self)
        self.template = FakeTemplate()
        self.objects = {
            'gcode': self.gcode,
            'gcode_macro': FakeGcodeMacro(self.template),
            'pause_resume': FakePauseResume(),
            'print_stats': FakePrintStats(),
            'save_variables': FakeSaveVariables(),
            'webhooks': FakeWebhooks(),
            'extruder': FakeExtruder(215.),
            'extruder1': FakeExtruder(0.),
            'hotend_c': FakeExtruder(0.),
            'extruder3': FakeExtruder(0.),
        }
        if with_ktc:
            self.objects['toolchanger'] = FakeToolchanger(
                ['extruder', 'extruder1', 'hotend_c', 'extruder3'])

    # --- Config ---
    def get_printer(self):
        return self

    def get(self, key, default=MISSING):
        if key in self.options:
            return self.options[key]
        if default is MISSING:
            raise KeyError(key)
        return default

    def getint(self, key, default=MISSING, **kw):
        return int(self.get(key, default))

    def getfloat(self, key, default=MISSING, **kw):
        return float(self.get(key, default))

    def getboolean(self, key, default=MISSING):
        return str(self.get(key, default)).lower() in ('1', 'true', 'yes')

    # --- Printer ---
    def get_reactor(self):
        return self.reactor

    def lookup_object(self, name, default=MISSING):
        if name in self.objects:
            return self.objects[name]
        if default is MISSING:
            raise KeyError(name)
        return default

    def load_object(self, config, name):
        return self.objects[name]

    def add_object(self, name, obj):
        self.objects[name] = obj

    def register_event_handler(self, event, handler):
        pass

    def is_shutdown(self):
        return False

    # --- Maschine ---
    def on_script(self, script):
        pr = self.objects['pause_resume']
        if script == 'PAUSE':
            pr.is_paused = True
        elif script == 'RESUME' and not self.resume_refused:
            pr.is_paused = False
        elif script.startswith('SELECT_TOOL'):
            tc = self.objects.get('toolchanger')
            if tc is None:
                return
            if self.tool_change_fails:
                tc.status = 'error'
            else:
                tc.tool_number = int(script.split('T=')[1])


def make(**kw):
    world = World(**kw)
    htc = HappyToolchanger(world)
    htc._handle_ready()
    htc.active_tool = 0
    htc.gate_spool_ids = [11, 12, 13, 14]
    world.gcode.scripts[:] = []
    return world, htc


def tool_scripts(world):
    # SAVE_VARIABLE gehoert zur Persistenz, nicht zum Ablauf
    return [s for s in world.gcode.scripts if not s.startswith('SAVE_VARIABLE')]


def main():
    # --- Wechsel gelingt: Reihenfolge und Temperaturuebergabe ---
    world, htc = make()
    htc.handle_runout(0)
    ok(world.objects['pause_resume'].pause_commands == 1,
       "der Druck muss sofort angehalten werden (send_pause_command)")
    ok(tool_scripts(world) == [
        'PAUSE',
        'SET_HEATER_TEMPERATURE HEATER=hotend_c TARGET=215.0',
        'SELECT_TOOL T=2',
        'TEMPERATURE_WAIT SENSOR=hotend_c MINIMUM=211.0',
        'SET_HEATER_TEMPERATURE HEATER=extruder TARGET=0',
        'RESUME'],
       "Ablauf: PAUSE, Ersatz heizen, wechseln, warten, altes aus, RESUME",
       str(tool_scripts(world)))
    ok(htc.ttg_map == [2, 1, 2, 3], "T0 zeigt danach auf Gate 2", str(htc.ttg_map))
    ok(htc.gate_status[0] == 0, "das leere Gate ist als leer markiert")
    ok(('spoolman_set_active_spool', {'spool_id': 13})
       in world.objects['webhooks'].calls,
       "Spoolman zaehlt auf der Spule des Ersatz-Gates weiter",
       str(world.objects['webhooks'].calls))
    ok(world.gcode.raw == [], "kein Fehler gemeldet", str(world.gcode.raw))
    ok(len(world.template.runs) == 1
       and world.template.runs[0].get('printer') == 'PRINTER'
       and world.template.runs[0].get('new_gate') == 2
       and world.template.runs[0].get('new_extruder') == 'hotend_c'
       and world.template.runs[0].get('temp') == 215.,
       "endless_spool_gcode laeuft mit Standardkontext plus Wechseldaten",
       str(world.template.runs))

    # --- Werkzeugwechsel scheitert: pausiert bleiben, nichts uebernehmen ---
    world, htc = make(tool_change_fails=True)
    htc.handle_runout(0)
    ok('RESUME' not in world.gcode.scripts,
       "nach gescheitertem Wechsel darf kein RESUME kommen")
    ok(htc.ttg_map == [0, 1, 2, 3],
       "gescheiterter Wechsel darf die Zuordnung nicht aendern", str(htc.ttg_map))
    ok(not any(s.startswith('TEMPERATURE_WAIT') for s in world.gcode.scripts),
       "nach gescheitertem Wechsel wird nicht aufs Heizen gewartet")
    ok(len(world.gcode.raw) == 1 and world.gcode.raw[0].startswith('!!'),
       "gescheiterter Wechsel wird als Fehler gemeldet", str(world.gcode.raw))
    ok(world.objects['pause_resume'].is_paused, "der Druck bleibt pausiert")

    # --- RESUME verweigert (z.B. Hotend zu kalt): melden ---
    world, htc = make(resume_refused=True)
    htc.handle_runout(0)
    ok(len(world.gcode.raw) == 1 and 'RESUME refused' in world.gcode.raw[0],
       "verweigertes RESUME wird gemeldet", str(world.gcode.raw))
    ok(htc.ttg_map[0] == 2, "das Tool haengt trotzdem -- Zuordnung bleibt")

    # --- kein Ersatz: nur pausieren ---
    world, htc = make(options={'endless_spool_groups': '0, 1, 2, 3'})
    htc.handle_runout(0)
    ok(tool_scripts(world) == ['PAUSE'], "ohne Ersatz nur PAUSE",
       str(tool_scripts(world)))
    ok(len(world.gcode.raw) == 1, "und eine Fehlermeldung")

    # --- Ersatz-Gate laut Status leer: nicht nehmen ---
    world, htc = make()
    htc.gate_status[2] = 0
    htc.handle_runout(0)
    ok(tool_scripts(world) == ['PAUSE'], "leeres Ersatz-Gate wird uebersprungen",
       str(tool_scripts(world)))

    # --- nicht das aktive Tool / kein Druck: nur Status ---
    world, htc = make()
    htc.handle_runout(1)
    ok(tool_scripts(world) == [] and htc.gate_status[1] == 0
       and world.objects['pause_resume'].pause_commands == 0,
       "Runout am inaktiven Tool: nur Status", str(tool_scripts(world)))
    world, htc = make()
    world.objects['print_stats'].state = 'standby'
    htc.handle_runout(0)
    ok(tool_scripts(world) == [] and htc.gate_status[0] == 0
       and world.objects['pause_resume'].pause_commands == 0,
       "Runout ohne laufenden Druck: nur Status", str(tool_scripts(world)))

    # --- ohne KTC: Klippers Extruder-Namensschema, keine Statuspruefung ---
    world, htc = make(with_ktc=False)
    htc.handle_runout(0)
    ok('SET_HEATER_TEMPERATURE HEATER=extruder2 TARGET=215.0' in world.gcode.scripts
       and world.gcode.scripts[-1] == 'RESUME',
       "ohne [toolchanger] gilt extruder/extruderN", str(tool_scripts(world)))

    # --- M104/M109 folgen der Zuordnung ---
    world = World()
    seen = []
    world.gcode.register_command('M104', lambda g: seen.append(('M104', g)))
    world.gcode.register_command('M109', lambda g: seen.append(('M109', g)))
    htc = HappyToolchanger(world)
    htc._handle_ready()
    original = FakeGcmd('M104', 'M104 T0 S210', {'T': '0', 'S': '210'})
    world.gcode.commands['M104'](original)
    ok(seen[-1][1] is original,
       "ohne Umleitung geht das Kommando unveraendert durch")
    htc.ttg_map = [2, 1, 2, 3]
    world.gcode.commands['M104'](FakeGcmd('M104', 'M104 T0 S210',
                                          {'T': '0', 'S': '210'}))
    g = seen[-1][1]
    ok(seen[-1][0] == 'M104' and g.params == {'T': '2', 'S': '210'}
       and g.line == 'M104 T2 S210',
       "M104 T0 muss nach dem Wechsel Gate 2 heizen", "%s / %s" % (g.params, g.line))
    world.gcode.commands['M109'](FakeGcmd('M109', 'M109 S200 T0',
                                          {'S': '200', 'T': '0'}))
    g = seen[-1][1]
    ok(seen[-1][0] == 'M109' and g.params == {'S': '200', 'T': '2'},
       "M109 T0 ebenso", str(g.params))
    noT = FakeGcmd('M109', 'M109 S200', {'S': '200'})
    world.gcode.commands['M109'](noT)
    ok(seen[-1][1] is noT, "ohne T bleibt das Kommando unveraendert")
    other = FakeGcmd('M104', 'M104 T1 S0', {'T': '1', 'S': '0'})
    world.gcode.commands['M104'](other)
    ok(seen[-1][1] is other, "nicht umgeleitete Tools bleiben unveraendert")

    print("%d Pruefungen, %d Befunde" % (CHECKS[0], len(FINDINGS)))
    for f in FINDINGS:
        print("  BEFUND: " + f)
    return 1 if FINDINGS else 0


if __name__ == "__main__":
    sys.exit(main())
