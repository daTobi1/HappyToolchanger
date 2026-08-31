// Test-Harness: schneidet Funktionen als Text aus tools.js heraus und
// laesst sie gegen gestubbte globale Zustandsvariablen/fetch/confirmDialog
// laufen - ohne Browser/DOM, wie check_webapp_recovery.js.
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

// --------------------------------------------------------------------
// Teil 1: _cameraOffsetFor() - reine Funktion, kein Netzwerk noetig.
// --------------------------------------------------------------------

// _cameraOffsetFor liest _xyResults.ref_tool und _cameraPositions - beides
// globale `let`-Variablen in tools.js. Frisch fuer jeden Testfall setzen,
// dann die herausgeschnittene Funktion neu auswerten (eval() bindet an die
// zu diesem Zeitpunkt sichtbaren globals).
function withState(xyResults, cameraPositions, fn) {
  global._xyResults = xyResults;
  global._cameraPositions = cameraPositions;
  eval(grab('_cameraOffsetFor'));
  return fn(_cameraOffsetFor);
}

// --- 1) Positionen fuer Referenz und ein anderes Tool vorhanden ---
withState(
  { ref_tool: 0 },
  { "0": { x: 100, y: 50 }, "1": { x: 100.53, y: 49.98 } },
  function (f) {
    var off = f(1);
    check('Offset = Differenz zum Referenztool',
      off && Math.abs(off.x - 0.53) < 1e-9 && Math.abs(off.y - (-0.02)) < 1e-9,
      JSON.stringify(off));
    check('Referenztool selbst hat Offset 0/0',
      (function () { var r = f(0); return r.x === 0 && r.y === 0; })());
  }
);

// --- 2) Fehlende Position fuer das angefragte Tool -> null ---
withState(
  { ref_tool: 0 },
  { "0": { x: 100, y: 50 } },
  function (f) {
    check('kein Wert fuer das Tool -> null (nicht NaN)', f(1) === null);
  }
);

// --- 3) Fehlende Position fuer das Referenztool -> null ---
withState(
  { ref_tool: 0 },
  { "1": { x: 100.53, y: 49.98 } },
  function (f) {
    check('kein Wert fuer das Referenztool -> null (nicht NaN)', f(1) === null);
  }
);

// --- 4) Gar keine Positionen erfasst -> null fuer jedes Tool ---
withState(
  { ref_tool: 0 },
  {},
  function (f) {
    check('leere _cameraPositions -> null', f(0) === null && f(1) === null);
  }
);

// --- 5) ref_tool fehlt in _xyResults -> Tool 0 gilt als Referenz ---
withState(
  {},
  { "0": { x: 100, y: 50 }, "2": { x: 99.5, y: 50.4 } },
  function (f) {
    var off = f(2);
    check('kein ref_tool gesetzt -> T0 ist Referenz',
      off && Math.abs(off.x - (-0.5)) < 1e-9 && Math.abs(off.y - 0.4) < 1e-9,
      JSON.stringify(off));
  }
);

// --- 6) ref_tool explizit auf ein anderes Tool als 0 gesetzt ---
withState(
  { ref_tool: 2 },
  { "0": { x: 100, y: 50 }, "1": { x: 100.53, y: 49.98 }, "2": { x: 99.5, y: 50.4 } },
  function (f) {
    var off = f(1);
    check('ref_tool != 0 wird respektiert',
      off && Math.abs(off.x - 1.03) < 1e-9 && Math.abs(off.y - (-0.42)) < 1e-9,
      JSON.stringify(off));
  }
);

// --------------------------------------------------------------------
// Teil 2a: parseXyProbeSerial() (Task 8) - reine Textverarbeitung der
// serial-Zeile aus der xy_probe.cfg.disabled-Vorlage. Kein Netzwerk-Stub
// noetig, die Funktion wirft oder liefert rein synchron.
// --------------------------------------------------------------------
eval(grab('parseXyProbeSerial'));

// --- 10) Wohlgeformte Vorlage -> liefert den serial-Pfad ---
(function () {
  var text = '[mcu xyprobe]\n' +
             'serial: /dev/serial/by-id/usb-Klipper_rp2040_XY-if00\n' +
             'canbus_uuid: deadbeef0001\n';
  var out;
  try { out = parseXyProbeSerial(text); } catch (e) { out = e; }
  check('wohlgeformte Vorlage -> serial-Pfad',
    out === '/dev/serial/by-id/usb-Klipper_rp2040_XY-if00', String(out));
})();

// --- 11) Platzhalter HIER_EINTRAGEN noch drin -> wird abgelehnt ---
(function () {
  var text = '[mcu xyprobe]\nserial: /dev/serial/by-id/HIER_EINTRAGEN\n';
  var threw = false;
  try { parseXyProbeSerial(text); }
  catch (e) { threw = /HIER_EINTRAGEN/.test(e.message); }
  check('Platzhalter HIER_EINTRAGEN -> Fehler statt falschem Pfad', threw);
})();

// --- 12) Keine serial-Zeile in der Vorlage -> wird abgelehnt ---
(function () {
  var text = '[mcu xyprobe]\ncanbus_uuid: deadbeef0001\n';
  var threw = false;
  try { parseXyProbeSerial(text); }
  catch (e) { threw = /serial-Zeile/.test(e.message); }
  check('keine serial-Zeile -> Fehler statt undefined-Pfad', threw);
})();

// --------------------------------------------------------------------
// Teil 2b: xyStepOk() (Task 8, Fix-Runde 1) - entscheidet nach jedem
// sendGcodeWithRecovery()-Aufruf im XY-Assistenten, ob es weitergehen darf.
// Reiner Einzeiler, aber genau diese Entscheidung haette im Fix-Runde-1-
// Befund (Recovery-Knopf mit Halterung auf dem Bett) den Fehlschlag
// durchwinken koennen, waere sie falsch verdrahtet.
// --------------------------------------------------------------------
eval(grab('xyStepOk'));

check('{ok:true} -> true', xyStepOk({ ok: true }) === true);
check('{transport:true} -> true (Verbindung weg, Lauf laeuft weiter)',
  xyStepOk({ transport: true }) === true);
check('{handled:true} -> false (Fehlerdialog schon gezeigt, nicht weiter)',
  xyStepOk({ handled: true }) === false);
check('null -> false', xyStepOk(null) === false);

// --------------------------------------------------------------------
// Teil 2: writeXyConfigs() - Fix-Runde 1, Befund 1 ("Erfolgsmeldung, obwohl
// nichts geschrieben wurde"). Braucht gestubbtes fetch()/confirmDialog(),
// deshalb eigener Abschnitt mit eigenem Netzwerk-Stub.
// --------------------------------------------------------------------

var uploads = [];   // Pfade, fuer die tatsaechlich hochgeladen wurde
var dialogs = [];   // von reportMissingKeys() ausgeloeste Alerts
var files = {};     // simulierter Config-Dateibestand: { path: content }

global.printerIp = '1.2.3.4';
global.printerUrl = (ip, path) => 'http://' + ip + ':7125' + path;
global.OffsetDebug = { error: function () {}, log: function () {} };
global.confirmDialog = function (o) { dialogs.push(o); return Promise.resolve(true); };
global.fetch = function (url) {
  if (url.indexOf('/server/files/config/') !== -1) {
    var path = url.split('/server/files/config/')[1];
    var content = files[path];
    return Promise.resolve({
      ok: content !== undefined,
      text: function () { return Promise.resolve(content === undefined ? '' : content); }
    });
  }
  if (url.indexOf('/server/files/upload') !== -1) {
    uploads.push(url);
    return Promise.resolve({ ok: true });
  }
  return Promise.reject(new Error('unerwarteter fetch: ' + url));
};

eval(
  'var NO_CACHE = { cache: "no-store" };' +
  grab('escapeHtml') +
  'var _alertQueue = Promise.resolve();' +
  grab('alertDialog') + grab('_showAlert') +
  grab('replaceInConfigSection') + grab('reportMissingKeys') +
  grab('updateConfigFile') + grab('writeXyConfigs')
);

function reset(fileMap) {
  uploads = []; dialogs = []; files = fileMap;
}

// --- 7) Beide Schluessel vorhanden -> true, ein Upload, kein Alert ---
reset({ 'toolchanger/tools/T0.cfg':
  '[tool T0]\ngcode_x_offset: 0.000\ngcode_y_offset: 0.000\n' });
writeXyConfigs({ "0": { x: "0.5300", y: "-0.0200" } }).then(function (ok) {
  check('vollstaendige Config -> true', ok === true);
  check('genau ein Upload', uploads.length === 1, 'uploads=' + uploads.length);
  check('kein Alert', dialogs.length === 0, 'dialogs=' + dialogs.length);

  // --- 8) Ein Schluessel fehlt in der Config -> false, KEIN Upload ---
  // Das ist der Fehler aus Fix-Runde-1-Befund 1: vorher lieferte der
  // Aufrufer unbedingt true, obwohl reportMissingKeys() bereits "nicht
  // geschrieben" meldete - Erfolg und Fehlschlag fuer dieselbe Aktion.
  reset({ 'toolchanger/tools/T1.cfg': '[tool T1]\ngcode_x_offset: 0.000\n' });
  return writeXyConfigs({ "1": { x: "1.2000", y: "0.4000" } });
}).then(function (ok) {
  check('gcode_y_offset fehlt -> false (nicht faelschlich true)', ok === false);
  check('kein Upload, wenn ein Schluessel fehlt',
    uploads.length === 0, 'uploads=' + uploads.length);
  check('reportMissingKeys zeigt genau einen Alert', dialogs.length === 1);

  // --- 9) Mehrere Tools, nur eines unvollstaendig -> Gesamtergebnis false,
  //        aber das vollstaendige Tool wird trotzdem geschrieben ---
  reset({
    'toolchanger/tools/T0.cfg':
      '[tool T0]\ngcode_x_offset: 0.000\ngcode_y_offset: 0.000\n',
    'toolchanger/tools/T2.cfg': '[tool T2]\ngcode_x_offset: 0.000\n'
  });
  return writeXyConfigs({
    "0": { x: "0.1000", y: "0.2000" },
    "2": { x: "0.3000", y: "0.4000" }
  });
}).then(function (ok) {
  check('ein unvollstaendiges Tool unter mehreren -> Gesamtergebnis false',
    ok === false);
  check('das vollstaendige Tool wird trotzdem geschrieben',
    uploads.length === 1, 'uploads=' + uploads.length);

  console.log(failed ? '\n' + failed + ' TESTS FEHLGESCHLAGEN' : '\nALLE TESTS OK');
  process.exit(failed ? 1 : 0);
}).catch(function (e) {
  console.log('EXCEPTION', e);
  process.exit(1);
});
