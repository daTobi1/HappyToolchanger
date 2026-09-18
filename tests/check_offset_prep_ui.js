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

eval(grab('prepOption') + grab('prepKinds') + grab('prepCommandPart') +
     grab('prepSummaryRows') + grab('prepOptionsHtml'));

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

// --- Zusammenfassung im Bestaetigungsdialog ---
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

// --- gemerkte Auswahl, je Drucker und je Art ---
function withStorage(storage, fn) {
  global.localStorage = storage;
  global._uiPrepSelection = {};
  global.printerIp = '192.168.1.10';
  eval(grab('prepStorageKey') + grab('loadPrepSelection') + grab('savePrepSelection') +
       grab('currentPrepSelection'));
  fn(loadPrepSelection, savePrepSelection, prepStorageKey, currentPrepSelection);
}

var mem = {};
withStorage({
  getItem: function (k) { return (k in mem) ? mem[k] : null; },
  setItem: function (k, v) { mem[k] = String(v); }
}, function (load, save, key, current) {
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
});

// localStorage wirft (privates Fenster): Auswahl gilt bis zum Reload
withStorage({
  getItem: function () { throw new Error('denied'); },
  setItem: function () { throw new Error('denied'); }
}, function (load, save) {
  check('ohne localStorage: Default aus, wirft nicht', load('unload') === false);
  save('unload', true);
  check('ohne localStorage: Auswahl haelt im Speicher', load('unload') === true);
});

// --- Verdrahtung: beide Kommandos und beide Bloecke benutzen die Bausteine ---
check('Z-Switch-Kommando haengt prepCommandPart an',
  /CALIBRATE_ALL_Z_OFFSETS TOOLS=[^`]*\$\{prepCommandPart\(_prepAvailable, prepSel\)\}/.test(src));
check('Probe-Offset-Kommando haengt prepCommandPart an',
  /CALIBRATE_PROBE_OFFSETS TOOLS='[\s\S]{0,200}prepCommandPart\(_prepAvailable, prepSel\)/.test(src));
check('beide Bloecke rendern die Haken',
  /prepOptionsHtml\("calibrate-prep"/.test(src) && /prepOptionsHtml\("probe-cal-prep"/.test(src));
check('beide Dialoge zeigen die Zusammenfassung',
  (src.match(/prepSummaryRows\(_prepAvailable, prepSel\)/g) || []).length === 2);
check('Verfuegbarkeit wird aus dem Offset-Status gelesen',
  /unload: !!\(st\?\.unload_available\), clean: !!\(st\?\.clean_available\)/.test(src));

console.log(failed ? ('\n' + failed + ' FAILED') : '\nall ok');
process.exit(failed ? 1 : 0);
