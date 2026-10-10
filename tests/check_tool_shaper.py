#!/usr/bin/env python3
"""Input-Shaper-Kalibrierung je Tool (CALIBRATE_TOOL_SHAPER) und die
Anwendung der params_input_shaper_* beim Toolwechsel.

Braucht weder Klipper noch Drucker: die reinen Helfer werden per ast aus
klippy/extras/offset.py und klippy/extras/toolchanger.py geschnitten und
gegen Attrappen gefahren.

    scp tests/check_tool_shaper.py klippy/extras/offset.py \
        klippy/extras/toolchanger.py biqu@<IP>:/tmp/
    ssh biqu@<IP> 'cd /tmp && python3 check_tool_shaper.py'

Exit-Code 0 = sauber, 1 = Befunde.
"""
import ast
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))


def load_source(basename):
    for cand in (os.path.join(HERE, basename),
                 os.path.join(HERE, '..', 'klippy', 'extras', basename)):
        if os.path.exists(cand):
            with open(cand) as f:
                return f.read()
    sys.exit("%s nicht gefunden" % basename)


OFFSET_SRC = load_source('offset.py')
TC_SRC = load_source('toolchanger.py')

failed = 0


def check(name, cond, extra=''):
    global failed
    if cond:
        print('  ok  ' + name)
    else:
        failed += 1
        print('FAIL: ' + name + ('  ' + str(extra) if extra else ''))


def function_source(src, name):
    tree = ast.parse(src)
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return ast.get_source_segment(src, node)
    return None


def function_source_method(src, name):
    """Quelltext einer Methode, egal in welcher Klasse."""
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return ast.get_source_segment(src, node)
    return None


def load_function(src, name, namespace):
    code = function_source(src, name)
    check('%s existiert als Modulfunktion' % name, code is not None)
    if code is None:
        return None
    exec(code, namespace)
    return namespace[name]


def load_constants(src, namespace):
    """Modulkonstanten (SHAPER_AXES usw.), von denen die Helfer leben."""
    for node in ast.parse(src).body:
        if isinstance(node, ast.Assign) and isinstance(
                node.value, (ast.Constant, ast.Tuple, ast.List, ast.Dict)):
            exec(ast.get_source_segment(src, node), namespace)


ns = {'re': __import__('re')}
load_constants(OFFSET_SRC, ns)
shaper_params_from_tool_params = load_function(
    OFFSET_SRC, 'shaper_params_from_tool_params', ns)
pick_accel_chip = load_function(OFFSET_SRC, 'pick_accel_chip', ns)
input_shaper_command_params = load_function(
    TC_SRC, 'input_shaper_command_params', ns)

# --- shaper_params_from_tool_params ----------------------------------------
if shaper_params_from_tool_params:
    p = shaper_params_from_tool_params({
        'params_input_shaper_type_x': '2hump_ei',
        'params_input_shaper_freq_x': 77.2,
        'params_input_shaper_damping_ratio_x': 0.01,
        'params_input_shaper_type_y': 'mzv',
        'params_input_shaper_freq_y': 41.2,
        'params_park_x': 12.0,
    })
    check('liest Typ/Frequenz/Daempfung je Achse',
          p == {'type_x': '2hump_ei', 'freq_x': 77.2,
                'damping_ratio_x': 0.01, 'type_y': 'mzv', 'freq_y': 41.2},
          p)
    check('ohne Shaper-Params leer', shaper_params_from_tool_params(
        {'params_park_x': 1.0}) == {})
    check('Frequenz als Text wird Zahl', shaper_params_from_tool_params(
        {'params_input_shaper_freq_x': '50.5'}) == {'freq_x': 50.5})

# --- pick_accel_chip --------------------------------------------------------
if pick_accel_chip:
    chips = [('adxl345 adxl1345_T1', 'EBBT1'),
             ('adxl345 adxl1345_T2', 'EBBT2'),
             ('lis2dw bed_chip', 'mcu')]
    check('params_accel_chip gewinnt (voller Objektname)',
          pick_accel_chip(2, {'params_accel_chip': 'lis2dw bed_chip'},
                          'EBBT2', chips) == 'lis2dw bed_chip')
    check('params_accel_chip gewinnt (nur Chipname)',
          pick_accel_chip(2, {'params_accel_chip': 'bed_chip'},
                          'EBBT2', chips) == 'lis2dw bed_chip')
    check('params_accel_chip unbekannt -> None, kein stiller Ersatz',
          pick_accel_chip(2, {'params_accel_chip': 'gibtsnicht'},
                          'EBBT2', chips) is None)
    check('MCU des Tools entscheidet',
          pick_accel_chip(2, {}, 'EBBT2', chips) == 'adxl345 adxl1345_T2')
    check('ohne MCU-Treffer: Name mit T<n>',
          pick_accel_chip(1, {}, None, chips) == 'adxl345 adxl1345_T1')
    check('T1 trifft nicht T10',
          pick_accel_chip(1, {}, None,
                          [('adxl345 adxl_T10', 'EBBT10')]) is None)
    check('Tool ohne Chip -> None',
          pick_accel_chip(0, {}, 'EBBT0', chips) is None)
    check('MCU-Treffer vor Namenstreffer',
          pick_accel_chip(1, {}, 'EBBT2', chips) == 'adxl345 adxl1345_T2')

# --- input_shaper_command_params (Toolwechsel) -----------------------------
if input_shaper_command_params:
    full = input_shaper_command_params({
        'params_input_shaper_type_x': '2hump_ei',
        'params_input_shaper_freq_x': 77.2,
        'params_input_shaper_damping_ratio_x': 0.01,
        'params_input_shaper_type_y': 'mzv',
        'params_input_shaper_freq_y': 41.2,
        'params_input_shaper_damping_ratio_y': 0.02,
    })
    check('alle sechs Werte als SET_INPUT_SHAPER-Parameter',
          full == {'SHAPER_TYPE_X': '2hump_ei', 'SHAPER_FREQ_X': '77.2',
                   'DAMPING_RATIO_X': '0.01', 'SHAPER_TYPE_Y': 'mzv',
                   'SHAPER_FREQ_Y': '41.2', 'DAMPING_RATIO_Y': '0.02'},
          full)
    check('ohne Frequenz keine Anwendung (leer)',
          input_shaper_command_params(
              {'params_input_shaper_type_x': 'mzv'}) == {})
    only_y = input_shaper_command_params({
        'params_input_shaper_type_y': 'ei',
        'params_input_shaper_freq_y': 40.0})
    check('nur Y gesetzt -> X bleibt unberuehrt',
          only_y == {'SHAPER_TYPE_Y': 'ei', 'SHAPER_FREQ_Y': '40.0'}, only_y)
    check('Frequenz 0 -> Achse wird abgeschaltet, nicht weggelassen',
          input_shaper_command_params(
              {'params_input_shaper_freq_x': 0}) == {'SHAPER_FREQ_X': '0'})

# --- max_accel je Tool (params_max_accel) -----------------------------------
tool_accel_cap = load_function(TC_SRC, 'tool_accel_cap', ns)
effective_accel = load_function(TC_SRC, 'effective_accel', ns)
if tool_accel_cap:
    check('params_max_accel wird Zahl', tool_accel_cap({'params_max_accel': 4600}) == 4600.0)
    check('params_max_accel als Text', tool_accel_cap({'params_max_accel': '4600'}) == 4600.0)
    check('ohne params_max_accel -> None', tool_accel_cap({'params_park_x': 1}) is None)
    check('0 oder negativ -> None (kein Deckel)', tool_accel_cap({'params_max_accel': 0}) is None)
if effective_accel:
    check('Slicer will mehr als der Kopf darf -> Deckel', effective_accel(8000, 4600) == 4600)
    check('Slicer will weniger -> Slicer', effective_accel(3000, 4600) == 3000)
    check('noch kein M204 -> Deckel', effective_accel(None, 4600) == 4600)
    check('Tool ohne Deckel -> Slicer-Wunsch', effective_accel(8000, None) == 8000)
    check('weder noch -> None (nichts setzen)', effective_accel(None, None) is None)
if shaper_params_from_tool_params:
    check('tool_shaper liefert max_accel mit',
          shaper_params_from_tool_params({'params_max_accel': 4600}) == {'max_accel': 4600.0})

# --- Aufrufstellen am Quelltext --------------------------------------------
configure_src = function_source_method(TC_SRC, '_configure_toolhead_for_tool')
check('toolchanger wendet den Shaper in _configure_toolhead_for_tool an',
      configure_src is not None and
      '_apply_tool_input_shaper' in configure_src)
check('CALIBRATE_TOOL_SHAPER ist registriert',
      "register_command('CALIBRATE_TOOL_SHAPER'" in OFFSET_SRC)
check('toolchanger setzt den Deckel in _configure_toolhead_for_tool',
      configure_src is not None and '_apply_tool_limits' in configure_src)
check('M204 wird bei klippy:ready uebernommen (Vorgaenger = toolhead)',
      "register_command('M204', None)" in TC_SRC)
m204_src = function_source_method(TC_SRC, 'cmd_M204') or ''
check('M204-Ueberlagerung merkt sich den Slicer-Wunsch und ruft den Vorgaenger',
      '_requested_accel' in m204_src and 'effective_accel' in m204_src)
tune_src = function_source_method(OFFSET_SRC, '_shaper_tune_tool') or ''
check('Ergebnisse werden je Tool gesichert (nicht erst am Ende)',
      '_save_shaper_results()' in tune_src)
check('Status liefert shaper_results und tool_accel_chips',
      "'shaper_results'" in OFFSET_SRC and "'tool_accel_chips'" in OFFSET_SRC)

print()
print('%d Befund(e)' % failed if failed else 'sauber')
sys.exit(1 if failed else 0)
