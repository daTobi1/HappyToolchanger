// Input-Shaper-Abschnitt der Offset-UI: der Config-Schreiber muss fehlende
// params_input_shaper_* in [tool Tn] anlegen statt abzubrechen (fremde
// Configs haben sie nicht), und kopierte Werte (Tool ohne Accelerometer)
// duerfen gemessene nie ueberschreiben.
//
// Test-Harness: eval() laedt die zu testenden Funktionen aus der eigenen
// Projektdatei (keine Fremdeingabe) - ohne Browser/DOM sonst nicht pruefbar.
const fs = require('fs');
const src = fs.readFileSync(require('path').join(__dirname, '..', 'webapp', 'js', 'tools.js'), 'utf8');

function grab(name) {
  const i = src.indexOf('function ' + name + '(');
  if (i < 0) throw new Error('missing ' + name);
  let d = 0;
  for (let k = src.indexOf('{', i); k < src.length; k++) {
    if (src[k] === '{') d++;
    else if (src[k] === '}') { d--; if (!d) return src.slice(i, k + 1); }
  }
}

let failed = 0;
function check(name, cond, extra) {
  if (!cond) { failed++; console.log('FAIL: ' + name + (extra ? '  ' + extra : '')); }
  else console.log('  ok  ' + name);
}

eval(grab('replaceInConfigSection') + grab('setInConfigSection') +
     grab('shaperApplyValues') + grab('shaperConfigChanges'));

// --- setInConfigSection ---------------------------------------------------
const cfg = [
  '[tool T1]',
  'tool_number: 1',
  'params_input_shaper_type_x: \'2hump_ei\'',
  'params_input_shaper_freq_x: 79.6',
  '',
  '# detection_pin entfernt',
  '',
  '[tool_probe T1]',
  'pin: ^EBBT1:PC15',
  ''
].join('\n');

let out = setInConfigSection(cfg, 'tool T1', 'params_input_shaper_freq_x', '51.6');
check('vorhandener Schluessel wird ersetzt',
      out.split('\n')[3] === 'params_input_shaper_freq_x: 51.6', out);

out = setInConfigSection(cfg, 'tool T1', 'params_input_shaper_type_y', "'mzv'");
let lines = out.split('\n');
check('fehlender Schluessel wird direkt hinter dem letzten Eintrag der Sektion angelegt',
      lines[4] === "params_input_shaper_type_y: 'mzv'" && lines[5] === '' &&
      lines[8] === '[tool_probe T1]', out);
check('andere Sektion unberuehrt',
      out.indexOf("[tool_probe T1]\npin: ^EBBT1:PC15") >= 0);

out = setInConfigSection(cfg, 'tool T1', 'PARAMS_INPUT_SHAPER_FREQ_X', '40');
check('Schluessel case-insensitiv wie der PID-Schreiber',
      out.split('\n')[3] === 'params_input_shaper_freq_x: 40');

check('fehlende Sektion -> null, nichts anhaengen',
      setInConfigSection(cfg, 'tool T7', 'params_input_shaper_freq_x', '40') === null);

const tail = '[tool T2]\ntool_number: 2\nparams_park_x: 1';
out = setInConfigSection(tail, 'tool T2', 'params_input_shaper_freq_y', '38.8');
check('Sektion am Dateiende ohne Leerzeile: anhaengen',
      out === tail + '\nparams_input_shaper_freq_y: 38.8', out);

// --- shaperApplyValues ------------------------------------------------------
const measured = {
  '1': { type_x: 'mzv', freq_x: 51.6, type_y: 'ei', freq_y: 38.8, chip: 'adxl345 a_T1' },
  '2': { type_x: 'zv', freq_x: 55.0, chip: 'adxl345 a_T2' }   // nur X gemessen
};
const copied = {
  '0': { from: '1', type_x: 'mzv', freq_x: 51.6, type_y: 'ei', freq_y: 38.8 },
  '1': { from: '2', type_x: 'zv', freq_x: 55.0 }               // darf nicht gewinnen
};
const merged = shaperApplyValues(measured, copied);
check('gemessenes Tool bleibt gemessen', merged['1'].freq_x === 51.6 && !merged['1'].from);
check('Tool ohne Messung bekommt die Kopie samt Quelle',
      merged['0'] && merged['0'].from === '1' && merged['0'].freq_y === 38.8);
check('nur gemessene Achse wird geschrieben',
      JSON.stringify(shaperConfigChanges(merged['2'])) ===
      JSON.stringify([{ key: 'params_input_shaper_type_x', to: "'zv'" },
                      { key: 'params_input_shaper_freq_x', to: '55.0' }]),
      JSON.stringify(shaperConfigChanges(merged['2'])));
check('beide Achsen: vier Schluessel, Typ in Anfuehrungszeichen',
      shaperConfigChanges(merged['1']).length === 4 &&
      shaperConfigChanges(merged['1'])[2].to === "'ei'");
check('ohne Werte nichts zu schreiben',
      shaperConfigChanges({ chip: 'x' }).length === 0 &&
      Object.keys(shaperApplyValues({}, {})).length === 0);

// --- shaperState: neu / in Datei geschrieben / aktiv ----------------------
// Nach APPLY stehen die Werte in der T<n>.cfg, der laufende Klipper kennt sie
// aber erst nach RESTART. Die Tabelle muss das unterscheiden, sonst sieht
// ein gelungener APPLY aus wie ein gescheiterter (350er, T5, 2026-10-10).
eval(grab('shaperState'));
const neu = { type_x: '3hump_ei', freq_x: 103.0, type_y: 'mzv', freq_y: 39.6 };
const curOld = { type_x: 'ei', freq_x: 50.2, type_y: 'zv', freq_y: 38.8 };
const fileNew = { params_input_shaper_type_x: "'3hump_ei'", params_input_shaper_freq_x: '103.0',
                  params_input_shaper_type_y: "'mzv'", params_input_shaper_freq_y: '39.6' };
const fileOld = { params_input_shaper_type_x: "'ei'", params_input_shaper_freq_x: '50.2',
                  params_input_shaper_type_y: "'zv'", params_input_shaper_freq_y: '38.8' };
check('Klipper laeuft schon mit den Werten -> aktiv',
      shaperState(neu, { type_x: '3hump_ei', freq_x: 103, type_y: 'mzv', freq_y: 39.6 }, fileNew) === 'active');
check('Datei hat die Werte, Klipper noch nicht -> written',
      shaperState(neu, curOld, fileNew) === 'written');
check('weder Datei noch Klipper -> new', shaperState(neu, curOld, fileOld) === 'new');
check('Dateistand unbekannt -> new', shaperState(neu, curOld, null) === 'new');
check('Dateischluessel fehlen -> new', shaperState(neu, curOld, {}) === 'new');
check('nur X gemessen: Y wird nicht verglichen',
      shaperState({ type_x: 'zv', freq_x: 55.0 }, curOld,
                  { params_input_shaper_type_x: "'zv'", params_input_shaper_freq_x: '55.0' }) === 'written');
check('ohne Messung -> none', shaperState(null, curOld, fileNew) === 'none');

// --- Tabelle zeigt den Zustand ------------------------------------------
global.escapeHtml = (x) => String(x);
var _toolShaper = { '5': curOld }, _shaperResults = { '5': neu }, _shaperCopied = {};
var _toolAccelChips = { '5': 'lis2dw lis2dw_T5' }, _shaperFileValues = { '5': fileNew };
var _shaperAccelChoice = {}, _shaperAccelMode = 'per_tool', _shaperAccelManual = null;
eval(grab('shaperAccelDefault') + grab('shaperAccelChosen') + grab('shaperEffective') + grab('shaperEffectiveAll'));
eval(grab('shaperToolHasChip') + grab('shaperChipShort') + grab('shaperAxisText') + grab('shaperResultsTable'));
let html = shaperResultsTable([5]);
check('geschrieben: Hinweis auf Neustart statt APPLY-Button',
      /Neustart|RESTART/.test(html) && html.indexOf('apply-shaper-btn') < 0, html.slice(0, 300));
_shaperFileValues = { '5': fileOld };
html = shaperResultsTable([5]);
check('noch nicht geschrieben: APPLY-Button da', html.indexOf('apply-shaper-btn') >= 0);
_shaperFileValues = {};
html = shaperResultsTable([5]);
check('Dateistand unbekannt: APPLY-Button da', html.indexOf('apply-shaper-btn') >= 0);

// --- max_accel: Vorschlag, Modus, Config-Schluessel, Zustand -----------------
check('Vorschlag = Minimum von X und Y', shaperAccelDefault({ max_accel_x: 7000, max_accel_y: 4600 }) === 4600);
check('nur eine Achse gemessen -> deren Wert', shaperAccelDefault({ max_accel_x: 7000 }) === 7000);
check('ohne max_accel -> null', shaperAccelDefault({ type_x: 'mzv' }) === null);

_shaperResults = { '1': { type_x: 'mzv', freq_x: 50, max_accel_x: 7000, max_accel_y: 4600 },
                   '2': { type_x: 'zv', freq_x: 60, max_accel_x: 9000, max_accel_y: 8000 } };
_shaperCopied = {};
_shaperAccelChoice = {}; _shaperAccelMode = 'per_tool';
check('je Tool: eigener Vorschlag', shaperEffective('2').max_accel === 8000);
_shaperAccelChoice = { '2': 6500 };
check('je Tool: Eingabe des Nutzers gewinnt', shaperEffective('2').max_accel === 6500);
_shaperAccelMode = 'uniform';
check('einheitlich: Minimum ueber alle Tools', shaperEffective('2').max_accel === 4600 && shaperEffective('1').max_accel === 4600);
_shaperAccelMode = 'none';
check('nicht uebernehmen: kein max_accel', shaperEffective('2').max_accel === undefined);
_shaperAccelMode = 'manual'; _shaperAccelManual = 6000;
check('eigener Wert: fuer alle Tools derselbe, Vorschlaege egal',
      shaperEffective('1').max_accel === 6000 && shaperEffective('2').max_accel === 6000);
_shaperAccelManual = null;
check('eigener Wert leer: kein max_accel', shaperEffective('2').max_accel === undefined);
_shaperAccelMode = 'manual'; _shaperAccelManual = 6000;
_toolShaper = {}; _toolAccelChips = { '1': 'a', '2': 'b' }; _shaperFileValues = {};
html = shaperResultsTable([1, 2]);
check('eigener Wert: Eingabefeld fuer alle, keine Felder je Tool',
      html.indexOf('shaper-accel-manual') >= 0 && html.indexOf('shaper-accel-input') < 0 &&
      html.indexOf('value="6000"') >= 0, html.slice(-900));
_shaperAccelMode = 'per_tool'; _shaperAccelManual = null;
check('Shaper-Werte bleiben erhalten', shaperEffective('2').freq_x === 60);

check('Config-Schluessel params_max_accel ohne Nachkommastellen',
      JSON.stringify(shaperConfigChanges({ type_x: 'zv', freq_x: 55.0, max_accel: 6500 })[2]) ===
      JSON.stringify({ key: 'params_max_accel', to: '6500' }));
check('ohne max_accel kein Schluessel', shaperConfigChanges({ type_x: 'zv', freq_x: 55.0 }).length === 2);

const neuA = { type_x: '3hump_ei', freq_x: 103.0, max_accel: 4600 };
const fileA = { params_input_shaper_type_x: "'3hump_ei'", params_input_shaper_freq_x: '103.0', params_max_accel: '4600' };
check('max_accel in Datei -> written', shaperState(neuA, curOld, fileA) === 'written');
check('max_accel fehlt in Datei -> new',
      shaperState(neuA, curOld, { params_input_shaper_type_x: "'3hump_ei'", params_input_shaper_freq_x: '103.0' }) === 'new');
check('max_accel in Klipper aktiv -> active',
      shaperState(neuA, { type_x: '3hump_ei', freq_x: 103, max_accel: 4600 }, fileA) === 'active');

// Shaper schon aktiv, nur max_accel neu: kein "94.0 Hz -> 94.0 Hz"
_toolShaper = { '0': { type_x: '3hump_ei', freq_x: 94.0, type_y: 'mzv', freq_y: 41.6 } };
_shaperResults = { '0': { type_x: '3hump_ei', freq_x: 94.0, type_y: 'mzv', freq_y: 41.6, max_accel_x: 5900, max_accel_y: 5100 } };
_shaperCopied = {}; _shaperAccelChoice = {}; _shaperAccelMode = 'per_tool';
_toolAccelChips = { '0': 'lis2dw lis2dw_T0' }; _shaperFileValues = {};
html = shaperResultsTable([0]);
check('Shaper-Zellen ohne Pfeil, wenn Klipper die Werte schon faehrt',
      (html.match(/&rarr;/g) || []).length === 1 && html.indexOf('shaper-accel-input') >= 0, html.slice(0, 400));

console.log(failed ? failed + ' Befund(e)' : 'sauber');
process.exit(failed ? 1 : 0);
