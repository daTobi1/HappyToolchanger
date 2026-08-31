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

// --------------------------------------------------------------------
// Teil 3: xyWizard() (Task 8, Fix-Runde 2) - bricht der Nutzer den
// "Aufsetzen"-Dialog ab, ist die Sonde zu diesem Zeitpunkt schon
// aktiviert (Fix-Runde 1 hat die Reihenfolge geaendert: aktivieren, dann
// homen, dann ERST zum Aufsetzen auffordern). Ein stiller Abbruch wuerde
// die Sonde unbemerkt aktiv zuruecklassen. Alle Nachbarfunktionen werden
// gestubbt, damit nur dieser eine Kontrollfluss-Zweig geprueft wird;
// confirmDialog/alertDialog/escapeHtml sind schon aus Teil 2 vorhanden und
// werden hier per global.confirmDialog auf eine eigene Antwortfolge
// umgebogen. Nur definiert, noch nicht aufgerufen - laeuft als letzter
// Schritt der Teil-2-Kette unten, NACHDEM Teil 2 seinen eigenen
// confirmDialog-Stub nicht mehr braucht.
// --------------------------------------------------------------------
function runXyWizardAbortTest() {
  var wizConfirms = [];
  var wizQueue = [];
  var deactivateCalls = 0;

  global.confirmDialog = function (o) {
    wizConfirms.push(o.title);
    return Promise.resolve(wizQueue.shift());
  };
  global.showToast = function () {};
  global.xyProbeCheckPresent = function () { return Promise.resolve(true); };
  global.xyProbeActivate = function () { return Promise.resolve(); };
  global.ensureHomedAfterActivate = function () { return Promise.resolve(); };
  global.sendGcodeWithRecovery = function () { return Promise.resolve({ ok: true }); };
  global.xyProbeDeactivate = function () { deactivateCalls++; return Promise.resolve(); };
  global.updateAllProbeResults = function () {};

  eval(grab('gcodeErrorMessage') + grab('xyStepOk') + grab('xyWizard'));

  // Reihenfolge: "Anstecken" bestaetigen (true), "Aufsetzen" abbrechen
  // (false), dann im Abbruch-Dialog "Sonde deaktivieren" waehlen (extra) -
  // die vierte Antwort ist fuer die abschliessende Erfolgsmeldung
  // ("Sonde deaktiviert", selbst ueber alertDialog->confirmDialog).
  wizQueue = [true, false, 'extra', true];
  return xyWizard().then(function () {
    check('Abbruch bei "Aufsetzen" ruft xyProbeDeactivate() auf statt still zu enden',
      deactivateCalls === 1, 'calls=' + deactivateCalls);
    check('Dialogreihenfolge: Anstecken, Aufsetzen, Abbruch-Dialog, Erfolgsmeldung',
      JSON.stringify(wizConfirms) === JSON.stringify([
        'XY-Sonde: Anstecken', 'XY-Sonde: Aufsetzen',
        'XY-Assistent abgebrochen', 'Sonde deaktiviert'
      ]), JSON.stringify(wizConfirms));
    check('Trockenlauf wird nicht erreicht (Abbruch vor dem Aufsetzen-OK)',
      wizConfirms.indexOf('Trockenlauf') === -1, JSON.stringify(wizConfirms));
  });
}

// --------------------------------------------------------------------
// Teil 4: captureMountedToolPosition() (Task 9) - reine Entscheidungslogik:
// welches Tool wird erfasst? Das MONTIERTE (toolchanger.tool_number), NICHT
// das in der UI angehakte Referenztool. captureCameraPosition() selbst wird
// gestubbt, damit nur diese Entscheidung geprueft wird, nicht das Erfassen.
// --------------------------------------------------------------------
function runCaptureMountedToolTest() {
  // toolchangerStatus === undefined simuliert eine Antwort ganz ohne
  // toolchanger-Objekt (z.B. Drucker ohne Toolchanger-Modul konfiguriert -
  // Generality-Requirement: fremde Configs duerfen kein toolchanger
  // voraussetzen).
  function withToolNumber(toolchangerStatus) {
    var captured = [];
    var alerts = [];
    global.captureCameraPosition = function (t) {
      captured.push(t);
      return Promise.resolve();
    };
    global.$ = { get: function () {
      return Promise.resolve(
        { result: { status: { toolchanger: toolchangerStatus } } });
    } };
    global.printerUrl = function (ip, path) { return 'http://' + ip + ':7125' + path; };
    global.printerIp = '1.2.3.4';

    // alertDialog() ist bereits als "var" auf Modulebene aus dem
    // writeXyConfigs-Abschnitt oben gebunden (eval(grab('alertDialog') +
    // ...) laeuft dort auf Top-Level) - ein "global.alertDialog = ..." hier
    // wuerde diese vorhandene Bindung NICHT erreichen. Deshalb per "var"
    // im selben eval neu binden, naeher an dieser Funktion als die
    // Modul-Bindung.
    eval('var alertDialog = function (title) { alerts.push(title); ' +
         'return Promise.resolve(); };' +
         grab('captureMountedToolPosition'));
    return captureMountedToolPosition().then(function () {
      return { captured: captured, alerts: alerts };
    });
  }

  var seq = Promise.resolve();

  // --- montiertes Tool 2 -> wird unter seiner eigenen Nummer erfasst ---
  seq = seq.then(function () {
    return withToolNumber({ tool_number: 2 }).then(function (r) {
      check('montiertes Tool 2 -> captureCameraPosition(2), kein Alert',
        r.captured.length === 1 && r.captured[0] === 2 && r.alerts.length === 0,
        JSON.stringify(r));
    });
  });

  // --- montiertes Tool 0 -> ebenfalls erfasst (0 ist ein gueltiges Tool,
  //     nicht mit "kein Tool" verwechseln) ---
  seq = seq.then(function () {
    return withToolNumber({ tool_number: 0 }).then(function (r) {
      check('montiertes Tool 0 -> captureCameraPosition(0), nicht als "keins" behandelt',
        r.captured.length === 1 && r.captured[0] === 0 && r.alerts.length === 0,
        JSON.stringify(r));
    });
  });

  // --- tool_number < 0 -> kein Tool montiert: kein Capture, Dialog statt ---
  seq = seq.then(function () {
    return withToolNumber({ tool_number: -1 }).then(function (r) {
      check('tool_number < 0 -> kein Capture',
        r.captured.length === 0, JSON.stringify(r));
      check('tool_number < 0 -> Dialog "Kein Tool montiert"',
        r.alerts.length === 1 && r.alerts[0] === 'Kein Tool montiert',
        JSON.stringify(r));
    });
  });

  // --- toolchanger-Objekt fehlt komplett -> wie "kein Tool", kein Crash ---
  seq = seq.then(function () {
    return withToolNumber(undefined).then(function (r) {
      check('kein toolchanger-Objekt -> kein Capture (kein Crash)',
        r.captured.length === 0, JSON.stringify(r));
      check('kein toolchanger-Objekt -> Dialog "Kein Tool montiert"',
        r.alerts.length === 1 && r.alerts[0] === 'Kein Tool montiert',
        JSON.stringify(r));
    });
  });

  // --- Befund 1 (Fix-Runde 1): $.get(toolchanger) schlaegt fehl -> Dialog
  //     statt stillem Reject. captureCameraPosition() darf dabei nicht
  //     erreicht werden. ---
  function withRejectedToolchangerGet() {
    var captured = [];
    var alerts = [];
    global.captureCameraPosition = function (t) {
      captured.push(t);
      return Promise.resolve();
    };
    global.$ = { get: function () {
      return Promise.reject(new Error('toolchanger-Abfrage fehlgeschlagen'));
    } };
    global.printerUrl = function (ip, path) { return 'http://' + ip + ':7125' + path; };
    global.printerIp = '1.2.3.4';

    eval('var alertDialog = function (title) { alerts.push(title); ' +
         'return Promise.resolve(); };' +
         grab('captureMountedToolPosition'));
    return captureMountedToolPosition().then(function () {
      return { captured: captured, alerts: alerts };
    });
  }

  seq = seq.then(function () {
    return withRejectedToolchangerGet().then(function (r) {
      check('$.get(toolchanger) abgelehnt -> Dialog statt Stille',
        r.alerts.length === 1 &&
        r.alerts[0] === 'Montiertes Tool konnte nicht ermittelt werden',
        JSON.stringify(r));
      check('$.get(toolchanger) abgelehnt -> kein Capture',
        r.captured.length === 0, JSON.stringify(r));
    }, function (err) {
      check('$.get(toolchanger) abgelehnt -> Promise trotzdem NICHT abgelehnt (catch faengt)',
        false, String(err));
    });
  });

  return seq;
}

// --------------------------------------------------------------------
// Teil 5: captureCameraPosition() eigener Fang (Befund 1, Fix-Runde 1) -
// schlaegt der gcode_move-fetch fehl (z.B. Moonraker startet gerade neu),
// darf der Klick nicht stillschweigend verpuffen. Prueft nur den
// Fehlerpfad; der Erfolgspfad haengt an DOM/renderXyBlock und ist ohne
// Browser nicht sinnvoll pruefbar (siehe Report).
// --------------------------------------------------------------------
function runCaptureCameraPositionCatchTest() {
  var alerts = [];
  global.fetch = function () { return Promise.reject(new Error('fetch failed')); };
  global.printerUrl = function (ip, path) { return 'http://' + ip + ':7125' + path; };
  global.printerIp = '1.2.3.4';

  // Gleicher Trick wie bei captureMountedToolPosition oben: alertDialog ist
  // bereits als Modul-Var aus dem writeXyConfigs-Abschnitt gebunden, also
  // hier im selben eval per "var" naeher binden. NO_CACHE/escapeHtml sind
  // ebenfalls schon als Modul-Var vorhanden (aus demselben Abschnitt) -
  // das ist fuer diesen Test unschaedlich, escapeHtml() wird real
  // ausgefuehrt, ihr Ergebnis aber nicht geprueft.
  eval('var alertDialog = function (title) { alerts.push(title); ' +
       'return Promise.resolve(); };' +
       grab('captureCameraPosition'));

  return captureCameraPosition(3).then(function () {
    check('fetch(gcode_move) abgelehnt -> Dialog statt Stille',
      alerts.length === 1 &&
      alerts[0] === 'T3: Position konnte nicht festgehalten werden',
      JSON.stringify(alerts));
  }, function (err) {
    check('fetch(gcode_move) abgelehnt -> Promise trotzdem NICHT abgelehnt (catch faengt)',
      false, String(err));
  });
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

  // Als letzter Schritt: braucht eigene confirmDialog-Antwortfolge, deshalb
  // erst NACHDEM Teil 2 mit seinem eigenen confirmDialog-Stub fertig ist.
  return runXyWizardAbortTest();
}).then(function () {
  // Teil 4 stubbt $ und printerUrl/printerIp neu - erst NACHDEM Teil 2/3
  // ihre eigenen fetch/confirmDialog-Stubs nicht mehr brauchen.
  return runCaptureMountedToolTest();
}).then(function () {
  // Teil 5 stubbt global.fetch neu - erst NACHDEM Teil 4 fertig ist.
  return runCaptureCameraPositionCatchTest();
}).then(function () {
  console.log(failed ? '\n' + failed + ' TESTS FEHLGESCHLAGEN' : '\nALLE TESTS OK');
  process.exit(failed ? 1 : 0);
}).catch(function (e) {
  console.log('EXCEPTION', e);
  process.exit(1);
});
