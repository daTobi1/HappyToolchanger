// Duesenreinigung in der Offset-UI (Z-Switch und Probe-Offsets): Haken,
// gemerkte Auswahl, CLEAN=1 im Kommando. Wie check_xy_offset_ui.js werden
// die Funktionen als Text aus tools.js geschnitten - ohne Browser/DOM.
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

eval(grab('cleanCommandPart') + grab('cleanSummaryText') + grab('cleanOptionHtml'));

// --- Kommando ---
check('verfuegbar + angehakt -> CLEAN=1', cleanCommandPart(true, true) === ' CLEAN=1');
check('verfuegbar, nicht angehakt -> nichts', cleanCommandPart(true, false) === '');
// Der gemerkte Haken stammt evtl. von einer Config, die clean_gcode noch
// hatte. Klipper lehnt CLEAN=1 ohne clean_gcode ab - also nie senden.
check('angehakt, aber nicht verfuegbar -> nichts', cleanCommandPart(false, true) === '');

// --- Zusammenfassung im Bestaetigungsdialog ---
check('Dialog: nicht konfiguriert', cleanSummaryText(false, true) === 'not configured');
check('Dialog: ja', /^yes/.test(cleanSummaryText(true, true)));
check('Dialog: nein', cleanSummaryText(true, false) === 'no');

// --- Haken ---
var html = cleanOptionHtml('x-clean', true, true);
check('verfuegbar + gewaehlt -> checked, nicht disabled',
  / checked/.test(html) && !/ disabled/.test(html), html);
check('id und gemeinsame Klasse gesetzt',
  /id="x-clean"/.test(html) && /offset-clean-cb/.test(html) && /for="x-clean"/.test(html));
html = cleanOptionHtml('x-clean', true, false);
check('verfuegbar, nicht gewaehlt -> kein checked', !/ checked/.test(html));
html = cleanOptionHtml('x-clean', false, true);
check('nicht verfuegbar -> disabled, nicht checked, Hinweis auf clean_gcode',
  / disabled/.test(html) && !/ checked/.test(html) && /Not available/.test(html) &&
  /clean_gcode/.test(html), html);

// --- gemerkte Auswahl, je Drucker ---
function withStorage(storage, fn) {
  global.localStorage = storage;
  global._uiCleanSelection = {};
  global.printerIp = '192.168.1.10';
  eval(grab('cleanStorageKey') + grab('loadCleanSelection') + grab('saveCleanSelection'));
  fn(loadCleanSelection, saveCleanSelection, cleanStorageKey);
}

var mem = {};
withStorage({
  getItem: function (k) { return (k in mem) ? mem[k] : null; },
  setItem: function (k, v) { mem[k] = String(v); }
}, function (load, save, key) {
  check('Default: aus', load() === false);
  save(true);
  check('gespeichert unter dem Drucker-Schluessel', mem[key()] === '1', JSON.stringify(mem));
  check('wieder geladen: an', load() === true);
  global.printerIp = '192.168.1.11';
  check('anderer Drucker: eigene Auswahl (aus)', load() === false);
  global.printerIp = '192.168.1.10';
  save(false);
  check('abgewaehlt: aus', load() === false && mem[key()] === '0');
});

// localStorage wirft (privates Fenster): Auswahl gilt bis zum Reload
withStorage({
  getItem: function () { throw new Error('denied'); },
  setItem: function () { throw new Error('denied'); }
}, function (load, save) {
  check('ohne localStorage: Default aus, wirft nicht', load() === false);
  save(true);
  check('ohne localStorage: Auswahl haelt im Speicher', load() === true);
});

// --- Verdrahtung: beide Kommandos und beide Bloecke benutzen die Bausteine ---
check('Z-Switch-Kommando haengt cleanCommandPart an',
  /CALIBRATE_ALL_Z_OFFSETS TOOLS=[^`]*\$\{cleanCommandPart\(_cleanAvailable, cleanSel\)\}/.test(src));
check('Probe-Offset-Kommando haengt cleanCommandPart an',
  /CALIBRATE_PROBE_OFFSETS TOOLS='[\s\S]{0,200}cleanCommandPart\(_cleanAvailable, cleanSel\)/.test(src));
check('beide Bloecke rendern den Haken',
  /cleanOptionHtml\("calibrate-clean"/.test(src) && /cleanOptionHtml\("probe-cal-clean"/.test(src));
check('clean_available wird aus dem Offset-Status gelesen',
  /_cleanAvailable = !!\(st\?\.clean_available\)/.test(src));

console.log(failed ? ('\n' + failed + ' FAILED') : '\nall ok');
process.exit(failed ? 1 : 0);
