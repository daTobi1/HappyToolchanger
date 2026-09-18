// Vorbereitung vor dem Messen in der Offset-UI (Z-Switch und Probe-Offsets):
// Filament entladen (UNLOAD=1) und Duesen reinigen (CLEAN=1) -- Haken,
// gemerkte Auswahl, Parameter im Kommando. Wie check_xy_offset_ui.js werden
// die Funktionen als Text aus tools.js geschnitten - ohne Browser/DOM.
// (eval wertet dabei nur unseren eigenen Quelltext aus.)
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

eval(grab('prepOption') + grab('prepKinds') + grab('prepTempValue') + grab('prepCommandPart') +
     grab('prepDefaultText') + grab('prepSummaryRows') + grab('prepTempsTableHtml') +
     grab('prepOptionsHtml'));

var BOTH = { unload: true, clean: true };
var NONE = { unload: false, clean: false };

// --- Kommando ---
check('beides verfuegbar + angehakt -> UNLOAD=1 CLEAN=1 (Reihenfolge wie in Klipper)',
  prepCommandPart(BOTH, BOTH) === ' UNLOAD=1 CLEAN=1', prepCommandPart(BOTH, BOTH));
check('nur reinigen', prepCommandPart(BOTH, { unload: false, clean: true }) === ' CLEAN=1');
check('nur entladen', prepCommandPart(BOTH, { unload: true, clean: false }) === ' UNLOAD=1');
check('nichts angehakt -> nichts', prepCommandPart(BOTH, NONE) === '');
// Der gemerkte Haken stammt evtl. von einer Config, die das Template noch
// hatte. Klipper lehnt den Parameter ohne Template ab - also nie senden.
check('angehakt, aber nicht verfuegbar -> nichts', prepCommandPart(NONE, BOTH) === '');
check('je Art getrennt: entladen fehlt, reinigen geht',
  prepCommandPart({ unload: false, clean: true }, BOTH) === ' CLEAN=1');

// --- Temperatur je Tool ---
var TEMPS = { unload: { '0': '240', '2': '225.4' }, clean: { '1': '250', '7': '199' } };
check('Temperaturen je Tool: nur Tools des Laufs, nur gefuellte Felder, gerundet',
  prepCommandPart(BOTH, BOTH, TEMPS, [0, 1, 2]) ===
    ' UNLOAD=1 UNLOAD_TEMPS=0:240,2:225 CLEAN=1 CLEAN_TEMPS=1:250',
  prepCommandPart(BOTH, BOTH, TEMPS, [0, 1, 2]));
check('alle Felder leer -> kein *_TEMPS (Klipper nimmt den Default)',
  prepCommandPart(BOTH, BOTH, { unload: {}, clean: {} }, [0, 1]) === ' UNLOAD=1 CLEAN=1');
check('Art nicht angehakt -> ihre Temperaturen gehen nicht raus',
  prepCommandPart(BOTH, { unload: false, clean: true }, TEMPS, [0, 1, 2]) === ' CLEAN=1 CLEAN_TEMPS=1:250');
check('Komma als Dezimaltrenner', prepTempValue('230,6', 'clean', 0) === 231);
check('leer / null -> null (Default)', prepTempValue('', 'clean', 0) === null && prepTempValue(null, 'clean', 0) === null);
['abc', '0', '-5', '351', '1e9'].forEach(function (bad) {
  var threw = false, msg = '';
  try { prepCommandPart(BOTH, BOTH, { unload: { '1': bad }, clean: {} }, [0, 1]); }
  catch (e) { threw = true; msg = e.message; }
  check('Unsinn "' + bad + '" wirft und nennt Art und Tool', threw && /Unload filament T1/.test(msg), msg);
});
var threwOther = false;
try { prepCommandPart(BOTH, BOTH, { unload: { '5': 'abc' }, clean: {} }, [0, 1]); } catch (e) { threwOther = true; }
check('Unsinn bei einem Tool, das nicht mitlaeuft, stoert nicht', !threwOther);

// --- Zusammenfassung im Bestaetigungsdialog ---
var DEF = { unload_temp: 240, clean_temp: 0 };
var trows = prepSummaryRows(BOTH, BOTH, TEMPS, [0, 1], DEF);
check('Dialog: je Tool die Temperatur, sonst der Default aus [offset]',
  /T0 240 &deg;C, T1 240 &deg;C/.test(trows), trows);
check('Dialog: ohne [offset]-Default steht "macro default"',
  /T0 macro default, T1 250 &deg;C/.test(trows), trows);

var rows = prepSummaryRows(BOTH, { unload: true, clean: false });
check('Dialog: zwei Zeilen, Entladen vor Reinigung',
  (rows.match(/<tr>/g) || []).length === 2 &&
  rows.indexOf('Unload filament') < rows.indexOf('Nozzle cleaning'), rows);
check('Dialog: entladen ja, reinigen nein',
  /Unload filament<\/td><td[^>]*>yes/.test(rows) && /Nozzle cleaning<\/td><td[^>]*>no</.test(rows), rows);
check('Dialog: nicht konfiguriert',
  (prepSummaryRows(NONE, BOTH).match(/not configured/g) || []).length === 2);

// --- Haken ---
var html = prepOptionsHtml('x-prep', BOTH, { unload: false, clean: true });
check('zwei Haken mit gemeinsamer Klasse und data-prep',
  (html.match(/offset-prep-cb/g) || []).length === 2 &&
  /data-prep="unload" id="x-prep-unload"/.test(html) &&
  /data-prep="clean" id="x-prep-clean"/.test(html), html);
check('Entladen steht vor Reinigen', html.indexOf('x-prep-unload') < html.indexOf('x-prep-clean'));
check('reinigen gewaehlt -> checked; entladen nicht',
  /id="x-prep-clean" checked/.test(html) && !/id="x-prep-unload" checked/.test(html), html);
check('verfuegbar -> nichts disabled', !/ disabled/.test(html));
html = prepOptionsHtml('x-prep', { unload: false, clean: true }, BOTH);
check('entladen nicht verfuegbar -> disabled, nicht checked, Hinweis auf unload_gcode',
  /id="x-prep-unload" disabled/.test(html) && /Not available[^<]*<code>unload_gcode/.test(html) &&
  /id="x-prep-clean" checked/.test(html), html);

// --- Temperaturtabelle ---
html = prepOptionsHtml('x-prep', { unload: true, clean: false }, BOTH, [2, 0], TEMPS, DEF);
check('Tabelle: ein Feld je Tool und Art, Tools sortiert',
  (html.match(/offset-prep-temp/g) || []).length === 4 &&
  html.indexOf('x-prep-unload-temp-0') < html.indexOf('x-prep-unload-temp-2'), html);
check('Tabelle: gemerkter Wert steht im Feld, Default als Platzhalter',
  /id="x-prep-unload-temp-0" value="240" placeholder="240"/.test(html) &&
  /id="x-prep-clean-temp-0" value="" placeholder="macro"/.test(html), html);
check('Tabelle: Art nicht verfuegbar -> ihre Felder disabled',
  /id="x-prep-clean-temp-0"[^>]* disabled/.test(html) && !/id="x-prep-unload-temp-0"[^>]* disabled/.test(html));
check('Tabelle: data-prep/data-tool fuer den Gleichlauf der beiden Bloecke',
  /data-prep="unload" data-tool="2"/.test(html));
check('ohne Tools keine Tabelle', !/offset-prep-temp/.test(prepOptionsHtml('x', BOTH, BOTH, [], TEMPS, DEF)));
check('Feldwert wird entschaerft (kein Attribut-Ausbruch)',
  !/onx/.test(prepOptionsHtml('x', BOTH, BOTH, [0], { unload: { '0': '1" onx="' }, clean: {} }, DEF)));

// --- gemerkte Auswahl, je Drucker und je Art ---
function withStorage(storage, fn) {
  global.localStorage = storage;
  global._uiPrepSelection = {};
  global._uiPrepTemps = {};
  global.printerIp = '192.168.1.10';
  eval(grab('prepStorageKey') + grab('loadPrepSelection') + grab('savePrepSelection') +
       grab('currentPrepSelection') +
       grab('prepTempsStorageKey') + grab('loadPrepTemps') + grab('savePrepTemp'));
  fn(loadPrepSelection, savePrepSelection, prepStorageKey, currentPrepSelection, loadPrepTemps, savePrepTemp);
}

var mem = {};
withStorage({
  getItem: function (k) { return (k in mem) ? mem[k] : null; },
  setItem: function (k, v) { mem[k] = String(v); }
}, function (load, save, key, current, loadTemps, saveTemp) {
  check('Default: beides aus', load('clean') === false && load('unload') === false);
  save('clean', true);
  check('gespeichert unter dem Drucker-Schluessel', mem[key('clean')] === '1', JSON.stringify(mem));
  check('Reinigen-Schluessel unveraendert zur ersten Fassung',
    key('clean') === 'offset_clean_192_168_1_10', key('clean'));
  check('Arten sind getrennt: reinigen an, entladen aus',
    load('clean') === true && load('unload') === false);
  check('currentPrepSelection liefert beide Arten',
    JSON.stringify(current()) === '{"unload":false,"clean":true}', JSON.stringify(current()));
  global.printerIp = '192.168.1.11';
  check('anderer Drucker: eigene Auswahl (aus)', load('clean') === false);
  global.printerIp = '192.168.1.10';
  save('clean', false);
  check('abgewaehlt: aus', load('clean') === false && mem[key('clean')] === '0');
  check('Temperaturen: Default leer', JSON.stringify(loadTemps()) === '{"unload":{},"clean":{}}');
  saveTemp('unload', 1, '235');
  saveTemp('clean', 0, '250');
  check('Temperaturen: je Art und Tool gemerkt',
    JSON.stringify(loadTemps()) === '{"unload":{"1":"235"},"clean":{"0":"250"}}', JSON.stringify(loadTemps()));
  saveTemp('unload', 1, '');
  check('Temperaturen: Feld geleert -> Eintrag weg (Default gilt wieder)',
    JSON.stringify(loadTemps().unload) === '{}');
  global.printerIp = '192.168.1.11';
  check('Temperaturen: anderer Drucker hat eigene', JSON.stringify(loadTemps().clean) === '{}');
  global.printerIp = '192.168.1.10';
  mem['offset_prep_temps_192_168_1_10'] = '{kaputt';
  check('Temperaturen: kaputter Eintrag wirft nicht', !!loadTemps());
});

// localStorage wirft (privates Fenster): Auswahl gilt bis zum Reload
withStorage({
  getItem: function () { throw new Error('denied'); },
  setItem: function () { throw new Error('denied'); }
}, function (load, save, key, current, loadTemps, saveTemp) {
  saveTemp('clean', 3, '245');
  check('ohne localStorage: Temperatur haelt im Speicher', loadTemps().clean['3'] === '245');
  check('ohne localStorage: Default aus, wirft nicht', load('unload') === false);
  save('unload', true);
  check('ohne localStorage: Auswahl haelt im Speicher', load('unload') === true);
});

// --- Verdrahtung: beide Kommandos und beide Bloecke benutzen die Bausteine ---
check('Z-Switch-Kommando haengt prepCommandPart an',
  /prepPart = prepCommandPart\(_prepAvailable, prepSel, prepTemps, selectedTools\)/.test(src) &&
  /CALIBRATE_ALL_Z_OFFSETS TOOLS=[^`]*\$\{prepPart\}/.test(src));
check('Probe-Offset-Kommando haengt prepCommandPart an',
  /prepPart = prepCommandPart\(_prepAvailable, prepSel, prepTemps, prepTools\)/.test(src) &&
  /CALIBRATE_PROBE_OFFSETS TOOLS='[\s\S]{0,200}tempPart \+ prepPart\)/.test(src));
check('Probe-Offsets: das Referenztool gehoert zu den Tools der Vorbereitung',
  /var prepTools = \[parseInt\(config\.ref_tool, 10\)\]\.concat\(selectedTools\)/.test(src));
check('ungueltige Temperatur -> Dialog statt Kommando (beide Laeufe)',
  (src.match(/catch \(e\) \{[^}]*alertDialog\("(Z-switch|Probe) calibration", e\.message\);\s*return;/g) || []).length === 2);
check('beide Bloecke rendern die Haken',
  /prepOptionsHtml\("calibrate-prep"/.test(src) && /prepOptionsHtml\("probe-cal-prep"/.test(src));
check('beide Dialoge zeigen die Zusammenfassung',
  (src.match(/prepSummaryRows\(_prepAvailable, prepSel, prepTemps, (selectedTools|prepTools), _prepDefaults\)/g) || []).length === 2);
check('Defaults werden aus dem Offset-Status gelesen', /_prepDefaults = \(st\?\.prep_defaults/.test(src));
check('Verfuegbarkeit wird aus dem Offset-Status gelesen',
  /unload: !!\(st\?\.unload_available\), clean: !!\(st\?\.clean_available\)/.test(src));

console.log(failed ? ('\n' + failed + ' FAILED') : '\nall ok');
process.exit(failed ? 1 : 0);
