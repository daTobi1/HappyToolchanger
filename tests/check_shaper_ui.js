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

console.log(failed ? failed + ' Befund(e)' : 'sauber');
process.exit(failed ? 1 : 0);
