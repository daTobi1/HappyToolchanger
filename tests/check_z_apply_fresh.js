// APPLY Z OFFSETS muss die Ergebnisse des letzten Laufs uebernehmen, nicht
// den Stand vom Seitenaufbau. Der 2s-Poller hat die "New Z"-Anzeige
// aktualisiert, _zSwitchResults aber nicht - der Dialog bot dann nur die
// Tools (und Werte) an, die beim Laden der Seite schon gemessen waren.
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

// --- Stubs ---
let snapshot = {};
let toggles = [];
global.getOffsetSnapshot = () => Promise.resolve(snapshot);
global.updatePidResults = global.updateDockResults = global.updateXyResults = () => {};
global.pollXySparkline = () => {};
global.updateProbeResults = () => {};
global.probeCalResultsTable = () => '';
global.OffsetDebug = { error: (m, e) => { throw e; } };
global.$ = (sel) => ({
  length: 0,
  each() {},
  html() {},
  toggleClass(cls, on) { toggles.push({ sel, cls, on }); }
});

var _probeCalResults = {}, _eddyTapDeviations = {}, _toolProbeOffsets = {};
// Stand vom Seitenaufbau: ein alter Lauf mit T0 und T2
var _zSwitchResults = {
  '0': { z_offset: 0.0, z_trigger: 1.04 },
  '2': { z_offset: 0.21125, z_trigger: 1.25 }
};

eval(grab('zSwitchResultsFrom') + grab('updateAllProbeResults'));

// --- 1) reine Funktion ---
const picked = zSwitchResultsFrom({
  '0': { z_offset: 0.0, z_trigger: 1.04 },
  '1': { probe_z_offset: -0.6 },            // nur Probe-Kalibrierung, kein Z-Switch
  '2': null,
  '3': { z_offset: -0.225, z_trigger: 0.815 }
});
check('nur Tools mit z_offset', JSON.stringify(Object.keys(picked)) === '["0","3"]');
check('leere Eingabe -> leer', JSON.stringify(zSwitchResultsFrom(undefined)) === '{}');

// --- 2) Poller uebernimmt den neuen Lauf mit allen vier Tools ---
snapshot = { probe_results: {
  '0': { z_offset: 0.0, z_trigger: 1.04 },
  '1': { z_offset: -0.150417, z_trigger: 0.8896 },
  '2': { z_offset: 0.1475, z_trigger: 1.1875 },
  '3': { z_offset: -0.225, z_trigger: 0.815 }
}};
updateAllProbeResults().then(() => {
  check('alle vier Tools nach dem Poll',
    JSON.stringify(Object.keys(_zSwitchResults).sort()) === '["0","1","2","3"]',
    JSON.stringify(Object.keys(_zSwitchResults)));
  check('T2 traegt den neuen Wert, nicht den vom Seitenaufbau',
    _zSwitchResults['2'].z_offset === 0.1475, String(_zSwitchResults['2'].z_offset));
  const t = toggles.filter(x => x.sel === '#apply-z-wrap').pop();
  check('Apply-Button wird eingeblendet', !!t && t.cls === 'd-none' && t.on === false);

  // --- 3) fehlgeschlagener Poll ({}): Stand bleibt, Button flackert nicht ---
  snapshot = {};
  toggles = [];
  return updateAllProbeResults();
}).then(() => {
  check('fehlgeschlagener Poll loescht nichts', Object.keys(_zSwitchResults).length === 4);
  check('fehlgeschlagener Poll fasst den Button nicht an',
    toggles.filter(x => x.sel === '#apply-z-wrap').length === 0);
  console.log(failed ? failed + ' FAILED' : 'all ok');
  process.exit(failed ? 1 : 0);
}).catch(e => { console.log('FAIL: exception', e); process.exit(1); });
