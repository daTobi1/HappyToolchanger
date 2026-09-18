#!/usr/bin/env python3
"""Filament entladen (UNLOAD=1) und Duesenreinigung (CLEAN=1) in
CALIBRATE_ALL_Z_OFFSETS / CALIBRATE_PROBE_OFFSETS.

Braucht weder Klipper noch Drucker: die Helfer werden per ast aus
klippy/extras/offset.py geschnitten und gegen Attrappen gefahren, die
Reihenfolge an den Aufrufstellen wird am Quelltext geprueft.

    scp tests/check_offset_prep.py klippy/extras/offset.py biqu@<IP>:/tmp/
    ssh biqu@<IP> 'cd /tmp && python3 check_offset_prep.py'
"""
import ast
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
for cand in (os.path.join(HERE, 'offset.py'),
             os.path.join(HERE, '..', 'klippy', 'extras', 'offset.py')):
    if os.path.exists(cand):
        SRC_PATH = cand
        break
else:
    sys.exit("offset.py nicht gefunden")

with open(SRC_PATH) as f:
    SRC = f.read()
TREE = ast.parse(SRC)

failed = 0


def check(name, cond, extra=''):
    global failed
    if cond:
        print('  ok  ' + name)
    else:
        failed += 1
        print('FAIL: ' + name + ('  ' + str(extra) if extra else ''))


def method_source(name):
    for node in ast.walk(TREE):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return ast.get_source_segment(SRC, node)
    raise SystemExit("Methode %s fehlt in offset.py" % name)


# --- Attrappen -------------------------------------------------------------

class GcmdError(Exception):
    pass


class FakeGcmd:
    error = GcmdError

    def __init__(self, params):
        self.params = params

    def get(self, name, default=None):
        return self.params.get(name, default)

    def get_int(self, name, default=None, minval=None, maxval=None):
        v = int(self.params.get(name, default))
        if minval is not None and v < minval:
            raise GcmdError("%s below minimum" % name)
        if maxval is not None and v > maxval:
            raise GcmdError("%s above maximum" % name)
        return v


class FakeGcode:
    def __init__(self, log):
        self.log = log

    def respond_info(self, msg):
        self.log.append(('info', msg))

    def run_script_from_command(self, script):
        self.log.append(('script', script))


class FakeTemplate:
    def __init__(self, log, fail=False):
        self.log = log
        self.fail = fail

    def create_template_context(self):
        # Klippers Standardkontext; ein uebergebener Kontext ERSETZT ihn.
        return {'printer': 'PRINTER', 'params': {}}

    def run_gcode_from_command(self, ctx):
        self.log.append(('clean', dict(ctx)))
        if self.fail:
            raise GcmdError("macro failed")


class FakeToolhead:
    def __init__(self, z):
        self.z = z

    def wait_moves(self):
        pass

    def get_position(self):
        return [0., 0., self.z, 0.]


class FakePrinter:
    def __init__(self, toolhead):
        self.toolhead = toolhead

    def lookup_object(self, name, default=None):
        return self.toolhead


class FakeGcodeMove:
    def __init__(self, log):
        self.log = log

    def reset_last_position(self):
        self.log.append(('resync',))


# ast.get_source_segment liefert die erste Zeile ohne Einrueckung, den Rest
# mit der originalen (4 Leerzeichen) - ein Praefix genuegt also.
ns = {}
body = []
for name in ('_template_context', '_run_prep_gcode', '_parse_tool_temps', '_unload_requested', '_unload_tools',
             '_clean_requested', '_clean_nozzle'):
    body.append("    " + method_source(name))
exec("class Offset:\n" + "\n\n".join(body), ns)
Offset = ns['Offset']


def make(has_clean=True, has_unload=True, unload_temp=0, clean_temp=0, z_after=2.0, fail=False, clean_safe_z=10.0):
    log = []
    o = Offset()
    o.has_clean_gcode = has_clean
    o.has_unload_gcode = has_unload
    o.unload_temp = unload_temp
    o.clean_temp = clean_temp
    o.unload_gcode = FakeTemplate(log, fail)
    o.clean_safe_z = clean_safe_z
    o.z_move_speed = 5.0
    o.gcode = FakeGcode(log)
    o.clean_gcode = FakeTemplate(log, fail)
    o.printer = FakePrinter(FakeToolhead(z_after))
    o.gcode_move = FakeGcodeMove(log)
    o._move_z = lambda z, speed=10.: log.append(('move_z', z, speed))
    return o, log


# --- _clean_requested ---------------------------------------------------------

o, log = make()
check('ohne CLEAN -> aus (bestehende Aufrufe aendern sich nicht)',
      o._clean_requested(FakeGcmd({}), 200) is False)
check('CLEAN=0 -> aus', o._clean_requested(FakeGcmd({'CLEAN': '0'}), 200) is False)
check('CLEAN=1 mit clean_gcode -> an',
      o._clean_requested(FakeGcmd({'CLEAN': '1'}), 200) is True)
check('CLEAN=1 mit Temperatur -> keine Warnung', not log, log)

o, log = make()
o._clean_requested(FakeGcmd({'CLEAN': '1'}), 0)
check('CLEAN=1 ohne EXTRUDER_TEMP -> Warnung (Duese kuehlt waehrend der Messung)',
      any(e[0] == 'info' and 'EXTRUDER_TEMP' in e[1] for e in log), log)

o, log = make(has_clean=False)
try:
    o._clean_requested(FakeGcmd({'CLEAN': '1'}), 200)
    check('CLEAN=1 ohne clean_gcode -> Fehler', False, 'kein Fehler')
except GcmdError as e:
    check('CLEAN=1 ohne clean_gcode -> Fehler, nennt clean_gcode',
          'clean_gcode' in str(e), e)
check('CLEAN=0 ohne clean_gcode -> kein Fehler',
      o._clean_requested(FakeGcmd({'CLEAN': '0'}), 200) is False)

try:
    o._clean_requested(FakeGcmd({'CLEAN': '2'}), 200)
    check('CLEAN=2 -> Fehler', False)
except GcmdError:
    check('CLEAN=2 -> Fehler', True)

# --- _clean_nozzle ------------------------------------------------------------

o, log = make(z_after=2.0)
o._clean_nozzle(3, 200, 7.0)
kinds = [e[0] for e in log]
ctx = [e[1] for e in log if e[0] == 'clean'][0]
check('Template bekommt TOOL und TEMP', ctx.get('TOOL') == 3 and ctx.get('TEMP') == 200, ctx)
check('... und behaelt den Standardkontext (printer) - render() ersetzt ihn sonst',
      ctx.get('printer') == 'PRINTER', ctx)
scripts = [e[1] for e in log if e[0] == 'script']
check('Gcode-Zustand wird um die Reinigung gesichert (G91-Falle)',
      scripts == ['SAVE_GCODE_STATE NAME=_offset_prep',
                  'RESTORE_GCODE_STATE NAME=_offset_prep'], scripts)
check('Reihenfolge: SAVE, Reinigung, RESTORE, dann anheben',
      [k for k in kinds if k != 'info'] == ['script', 'clean', 'script', 'move_z'], kinds)
check('endet die Reinigung tief -> anheben auf max(min_z, clean_safe_z)',
      ('move_z', 10.0, 5.0) in log, log)

o, log = make(z_after=2.0, clean_safe_z=4.0)
o._clean_nozzle(0, 0, 7.0)
check('min_z des Aufrufers gewinnt, wenn hoeher', ('move_z', 7.0, 5.0) in log, log)

o, log = make(z_after=25.0)
o._clean_nozzle(0, 0, 7.0)
check('steht die Duese schon hoch -> NICHT absenken, nur resync',
      not any(e[0] == 'move_z' for e in log) and ('resync',) in log, log)

o, log = make(fail=True)
try:
    o._clean_nozzle(1, 200, 7.0)
    check('Fehler im Makro fliegt weiter', False)
except GcmdError:
    scripts = [e[1] for e in log if e[0] == 'script']
    check('Fehler im Makro fliegt weiter, Zustand trotzdem zurueckgeholt',
          scripts[-1] == 'RESTORE_GCODE_STATE NAME=_offset_prep', scripts)
    check('nach einem Fehler keine Fahrt mehr',
          not any(e[0] == 'move_z' for e in log), log)

# --- Temperatur je Tool: UNLOAD_TEMPS= / CLEAN_TEMPS= ------------------------------

o, log = make()
check('ohne Parameter -> leer (Default aus [offset] gilt)',
      o._parse_tool_temps(FakeGcmd({}), 'CLEAN_TEMPS') == {})
check('0:240,2:225 -> {0: 240, 2: 225}',
      o._parse_tool_temps(FakeGcmd({'CLEAN_TEMPS': '0:240, 2:225'}), 'CLEAN_TEMPS')
      == {0: 240, 2: 225})
check('Nachkommastellen werden gerundet',
      o._parse_tool_temps(FakeGcmd({'X': '1:229.6'}), 'X') == {1: 230})
for bad in ('240', '0:abc', 'a:240', '0:240:1', '0:351', '0:-1', '-1:200'):
    try:
        o._parse_tool_temps(FakeGcmd({'UNLOAD_TEMPS': bad}), 'UNLOAD_TEMPS')
        check("Unsinn '%s' -> Fehler" % bad, False, 'kein Fehler')
    except GcmdError as e:
        check("Unsinn '%s' -> Fehler, nennt den Parameter" % bad,
              'UNLOAD_TEMPS' in str(e), e)

o, log = make(unload_temp=240)
o._unload_tools([0, 1, 2], {1: 225})
ctxs = [e[1] for e in log if e[0] == 'clean']
check('entladen: UNLOAD_TEMP je Tool, sonst der Default aus [offset]',
      [c.get('UNLOAD_TEMP') for c in ctxs] == [240, 225, 240], ctxs)
check('entladen: die Temperatur steht in der Konsole',
      any(e[0] == 'info' and 'T1 @ 225 C' in e[1] for e in log), log)
o, log = make()
o._unload_tools([0])
ctx = [e[1] for e in log if e[0] == 'clean'][0]
check('entladen: ohne Angabe und ohne Default -> UNLOAD_TEMP 0 (Makro entscheidet)',
      ctx.get('UNLOAD_TEMP') == 0, ctx)

o, log = make(clean_temp=260)
o._clean_nozzle(1, 150, 7.0, {1: 245})
ctx = [e[1] for e in log if e[0] == 'clean'][0]
check('reinigen: CLEAN_TEMP aus dem Lauf, TEMP bleibt die Messtemperatur',
      ctx.get('CLEAN_TEMP') == 245 and ctx.get('TEMP') == 150, ctx)
o, log = make(clean_temp=260)
o._clean_nozzle(2, 150, 7.0, {1: 245})
ctx = [e[1] for e in log if e[0] == 'clean'][0]
check('reinigen: Tool ohne Angabe -> Default aus [offset]',
      ctx.get('CLEAN_TEMP') == 260, ctx)

# --- _unload_requested / _unload_tools ------------------------------------------

o, log = make()
check('ohne UNLOAD -> aus', o._unload_requested(FakeGcmd({})) is False)
check('UNLOAD=1 mit unload_gcode -> an',
      o._unload_requested(FakeGcmd({'UNLOAD': '1'})) is True)
o, log = make(has_unload=False)
try:
    o._unload_requested(FakeGcmd({'UNLOAD': '1'}))
    check('UNLOAD=1 ohne unload_gcode -> Fehler', False, 'kein Fehler')
except GcmdError as e:
    check('UNLOAD=1 ohne unload_gcode -> Fehler, nennt unload_gcode',
          'unload_gcode' in str(e), e)

o, log = make()
o._unload_tools([0, 2, 3])
ctxs = [e[1] for e in log if e[0] == 'clean']
check('entladen: Template einmal je Tool, in der Reihenfolge der Liste',
      [c.get('TOOL') for c in ctxs] == [0, 2, 3], ctxs)
check('entladen: Standardkontext bleibt erhalten',
      all(c.get('printer') == 'PRINTER' for c in ctxs), ctxs)
scripts = [e[1] for e in log if e[0] == 'script']
check('entladen: Gcode-Zustand je Tool gesichert (M83/G91-Falle)',
      scripts == ['SAVE_GCODE_STATE NAME=_offset_prep',
                  'RESTORE_GCODE_STATE NAME=_offset_prep'] * 3, scripts)
check('entladen: keine Z-Fahrt, am Ende resync',
      not any(e[0] == 'move_z' for e in log) and log[-1] == ('resync',), log)

o, log = make(fail=True)
try:
    o._unload_tools([0, 1])
    check('entladen: Fehler im Makro bricht den Lauf ab', False)
except GcmdError:
    ctxs = [e for e in log if e[0] == 'clean']
    check('entladen: Fehler im Makro bricht den Lauf ab - kein zweites Tool',
          len(ctxs) == 1, log)

# --- Aufrufstellen (Quelltext-Reihenfolge) -----------------------------------

zs = method_source('cmd_CALIBRATE_ALL_Z_OFFSETS')
check('Z-Switch: CLEAN wird geprueft, BEVOR start_gcode (G28/QGL) laeuft',
      0 < zs.find('_clean_requested(') < zs.find('cmd_OFFSET_START_GCODE('))
check('Z-Switch: UNLOAD wird geprueft, BEVOR start_gcode laeuft',
      0 < zs.find('_unload_requested(') < zs.find('cmd_OFFSET_START_GCODE('))
i_unload = zs.find('_unload_tools(ordered_tools')
i_pick = zs.find('run_script_from_command(f"T{tool}")')
i_clean = zs.find('_clean_nozzle(')
i_heat = zs.find('M109 S{extruder_temp}')
i_move = zs.find('MOVE_TO_ZSWITCH')
check('Z-Switch: entladen -> aufnehmen -> reinigen -> Messtemperatur -> Schalter',
      0 < i_unload < i_pick < i_clean < i_heat < i_move,
      (i_unload, i_pick, i_clean, i_heat, i_move))
check('Z-Switch: reinigen VOR dem Nullen von gcode_z_offset',
      i_clean < zs.find('PARAMETER=gcode_z_offset'))

po = method_source('cmd_CALIBRATE_PROBE_OFFSETS')
check('Probe-Offsets: CLEAN wird geprueft, bevor irgendetwas laeuft',
      0 < po.find('_clean_requested(') < po.find('run_script_from_command('))
i_unload = po.find('_unload_tools(')
check('Probe-Offsets: UNLOAD wird geprueft, bevor irgendetwas laeuft',
      0 < po.find('_unload_requested(') < po.find('run_script_from_command('))
check('Probe-Offsets: entladen vor dem ersten SELECT_TOOL, Referenztool dabei',
      0 < i_unload < po.find('SELECT_TOOL') and '[ref_tool] +' in po[i_unload:i_unload + 120])
cleans = [i for i in range(len(po)) if po.startswith('_clean_nozzle(', i)]
heats = [i for i in range(len(po)) if po.startswith('"M109 S%d"', i)]
check('Probe-Offsets: Referenz (Schritt 1) und jedes Tool (Schritt 2) reinigen',
      len(cleans) == 2 and len(heats) == 2, (cleans, heats))
check('Probe-Offsets: jeweils reinigen VOR der Messtemperatur',
      len(cleans) == 2 and len(heats) == 2 and
      cleans[0] < heats[0] < cleans[1] < heats[1], (cleans, heats))
check('Probe-Offsets: jedes Tool nur einmal je Lauf',
      'tool_nr not in cleaned' in po and 'cleaned.add(ref_tool)' in po)

# --- Template-Kontext: keine Aufrufstelle darf den Standardkontext verlieren ----
# render(context) nimmt einen uebergebenen Kontext STATT des Standardkontexts,
# und nur None zaehlt als "nicht uebergeben" - auch {} laesst `printer` weg.

o, log = make()
tpl = FakeTemplate(log)
check('_template_context ohne extra: Standardkontext',
      o._template_context(tpl).get('printer') == 'PRINTER')
ctx = o._template_context(tpl, {'MESH_TOOL': 0, 'PREVIOUS_TOOL': 2})
check('_template_context mit extra: beides drin',
      ctx.get('printer') == 'PRINTER' and ctx.get('MESH_TOOL') == 0 and
      ctx.get('PREVIOUS_TOOL') == 2, ctx)

calls = []
for node in ast.walk(TREE):
    if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
            and node.func.attr == 'run_gcode_from_command'):
        calls.append(node)
check('alle Template-Aufrufe in offset.py gefunden (4 Hooks, mesh_tool, prep)',
      len(calls) == 6, len(calls))
bad = []
for node in calls:
    arg = node.args[0] if node.args else None
    ok = (isinstance(arg, ast.Name) and arg.id == 'context') or (
        isinstance(arg, ast.Call) and isinstance(arg.func, ast.Attribute)
        and arg.func.attr == '_template_context')
    if not ok:
        bad.append(node.lineno)
check('jeder Aufruf bekommt seinen Kontext aus _template_context (kein {} / nacktes dict)',
      not bad, 'Zeilen: %s' % bad)
check('_run_prep_gcode baut `context` ueber _template_context',
      'context = self._template_context(template, extra)' in method_source('_run_prep_gcode'))

for cmd_name, src_text in (('Z-Switch', zs), ('Probe-Offsets', po)):
    check('%s: *_TEMPS werden geprueft, bevor irgendetwas laeuft' % cmd_name,
          0 < src_text.find("_parse_tool_temps(gcmd, 'UNLOAD_TEMPS')")
          < src_text.find("_parse_tool_temps(gcmd, 'CLEAN_TEMPS')")
          < src_text.find('run_script_from_command('))
    check('%s: die Temperaturen erreichen die Helfer' % cmd_name,
          'unload_temps)' in src_text and 'clean_temps)' in src_text)

st = method_source('get_status')
check("get_status meldet 'clean_available' und 'unload_available'",
      "'clean_available'" in st and "'unload_available'" in st)
check("get_status meldet die Defaults ('prep_defaults')",
      "'prep_defaults'" in st and "'unload_temp'" in st and "'clean_temp'" in st)

print('\n%d FAILED' % failed if failed else '\nall ok')
sys.exit(1 if failed else 0)
