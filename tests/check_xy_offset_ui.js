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

// _cameraOffsetFor liest ueber xyRefTool() das Referenztool und dazu
// _cameraPositions - beides globale `let`-Variablen in tools.js. Frisch
// fuer jeden Testfall setzen, dann die herausgeschnittenen Funktionen neu
// auswerten (eval() bindet an die zu diesem Zeitpunkt sichtbaren globals).
//
// xyRefTool() faellt, wenn Klipper noch kein ref_tool gemessen hat, auf die
// UI-Auswahl zurueck (getSelectedReferenceTool/computeDefaultRef). Beide
// werden echt mitgeschnitten statt gestubbt - der Rueckfallweg ist genau
// das, was Fix-Runde 3 hier geaendert hat. getSelectedReferenceTool()
// braucht dafuer ein $-Stub: uiRef === undefined heisst "keine Checkbox
// angehakt".
function withState(xyResults, cameraPositions, fn, toolGcodeOffsets, uiRef) {
  global._xyResults = xyResults;
  global._cameraPositions = cameraPositions;
  global._toolGcodeOffsets = toolGcodeOffsets || {};
  global.offsetMasterTool = null;
  global.$ = function () {
    return { first: function () {
      return (uiRef === undefined)
        ? { length: 0 }
        : { length: 1, val: function () { return String(uiRef); } };
    } };
  };
  eval(grab('computeDefaultRef') + grab('getSelectedReferenceTool') +
       grab('xyRefTool') + grab('_cameraOffsetFor'));
  return fn(_cameraOffsetFor, xyRefTool);
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

// --- 6a) Fremde Config ohne T0: ohne ref_tool darf NICHT T0 unterstellt
//     werden, sonst wird die Referenzposition unter einer Toolnummer
//     gesucht, die es nicht gibt - jede Zeile "nicht gemessen". Genau der
//     Generality-Bruch aus Fix-Runde 3. ---
withState(
  {},
  { "1": { x: 100, y: 50 }, "3": { x: 100.25, y: 49.9 } },
  function (f, refTool) {
    check('kein T0 in der Config -> kleinstes Tool ist Referenz, nicht T0',
      refTool() === 1, 'ref=' + refTool());
    var off = f(3);
    check('kein T0: Offset wird gegen T1 gerechnet',
      off && Math.abs(off.x - 0.25) < 1e-9 && Math.abs(off.y - (-0.1)) < 1e-9,
      JSON.stringify(off));
  },
  { "1": {}, "3": {} }
);

// --- 6b) UI-Auswahl im Kalibrier-Abschnitt schlaegt die Vorgabe durch ---
withState(
  {},
  { "0": { x: 100, y: 50 }, "2": { x: 99.5, y: 50.4 } },
  function (f, refTool) {
    check('UI-Referenzauswahl T2 wird uebernommen (nicht hart T0)',
      refTool() === 2, 'ref=' + refTool());
    var off = f(0);
    check('UI-Referenz T2: Offset von T0 wird gegen T2 gerechnet',
      off && Math.abs(off.x - 0.5) < 1e-9 && Math.abs(off.y - (-0.4)) < 1e-9,
      JSON.stringify(off));
  },
  { "0": {}, "2": {} },
  2
);

// --- 6c) Hat Klipper gemessen, gilt SEIN ref_tool - nicht die UI ---
withState(
  { ref_tool: 0 },
  { "0": { x: 100, y: 50 }, "2": { x: 99.5, y: 50.4 } },
  function (f, refTool) {
    check('gemessenes ref_tool schlaegt die UI-Auswahl',
      refTool() === 0, 'ref=' + refTool());
  },
  { "0": {}, "2": {} },
  2
);

// --------------------------------------------------------------------
// Teil 1b: xyMeasured() (Fix-Runde 3) - entscheidet, ob ein Eintrag als
// Messwert zaehlt. Eine blosse Wahrheitspruefung reichte nicht: das noch
// zu schreibende Klipper-Modul legt fuer ein Tool auch dann einen Eintrag
// an, wenn der Fit fehlschlaegt. Dann fehlt x/y - und res.x.toFixed(3)
// warf mitten in der ungefangenen Poll-Kette, die Dock-, PID- und
// Probe-Tabelle gleich mit einfriert.
// --------------------------------------------------------------------
eval(grab('xyMeasured'));

check('vollstaendiger Messwert -> true',
  xyMeasured({ x: 0.5, y: -0.2 }) === true);
check('Eintrag ohne x -> false (nicht gemessen statt Absturz)',
  xyMeasured({ y: -0.2 }) === false);
check('Eintrag ohne y -> false',
  xyMeasured({ x: 0.5 }) === false);
check('x als String -> false (toFixed gibt es dort nicht)',
  xyMeasured({ x: "0.5", y: "-0.2" }) === false);
check('x = 0 zaehlt als Messwert (0 ist ein gueltiger Offset)',
  xyMeasured({ x: 0, y: 0 }) === true);
check('null/undefined -> false', xyMeasured(null) === false &&
  xyMeasured(undefined) === false);

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
// Teil 2b: xyStepOk() (Task 8, Fix-Runde 1; Bedeutung geaendert in
// Fix-Runde 3) - entscheidet nach jedem sendGcodeWithRecovery()-Aufruf im
// XY-Assistenten, ob es weitergehen darf.
//
// Bis Fix-Runde 2 galt {transport:true} hier als Erfolg. Das war falsch,
// und diese Testzeile hat den Fehler mit beglaubigt:
// /printer/gcode/script bleibt offen, BIS das Skript fertig ist - ein
// Verbindungsabbruch heisst also "der Drucker arbeitet noch", nicht
// "fertig". Der PID-Lauf und der Z-Lauf melden darauf "Verbindung zum Lauf
// verloren - er laeuft weiter" und HALTEN AN; nur der Assistent hat daran
// einen maschinenbewegenden Folgeschritt gekettet (Halterung aufs Bett
// stellen lassen, waehrend G28 noch faehrt; den absenkenden Messlauf
// direkt hinter den Trockenlauf; FIRMWARE_RESTART mitten in der Fahrt).
// Seit Fix-Runde 3 ist nur {ok:true} ein Erfolg; wo transport der
// Regelfall ist (Messlauf), wartet der Assistent stattdessen auf
// idle_timeout.
// --------------------------------------------------------------------
eval(grab('xyStepOk'));

check('{ok:true} -> true', xyStepOk({ ok: true }) === true);
check('{transport:true} -> false (Lauf laeuft weiter - kein Erfolg)',
  xyStepOk({ transport: true }) === false);
check('{handled:true} -> false (Fehlerdialog schon gezeigt, nicht weiter)',
  xyStepOk({ handled: true }) === false);
check('null -> false', xyStepOk(null) === false);

// --------------------------------------------------------------------
// Teil 2c: xyIsHomed() (Fix-Runde 3) - die Pruefung, die
// ensureHomedAfterActivate() nach dem G28 WIEDERHOLT, statt dem
// Sendeergebnis zu glauben.
// --------------------------------------------------------------------
eval(grab('xyIsHomed'));

check('"xyz" -> gehomt', xyIsHomed("xyz") === true);
check('"xy" -> nicht gehomt (Z fehlt)', xyIsHomed("xy") === false);
check('"" -> nicht gehomt (nach FIRMWARE_RESTART der Normalfall)',
  xyIsHomed("") === false);
check('undefined -> nicht gehomt (kein Crash)',
  xyIsHomed(undefined) === false);

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
  global.ensureLeveledBeforeSetup = function () { return Promise.resolve(true); };
  global.sendGcodeWithRecovery = function () { return Promise.resolve({ ok: true }); };
  global.xyProbeDeactivate = function () { deactivateCalls++; return Promise.resolve(); };
  global.updateAllProbeResults = function () {};
  // Schritt 5a (Anfahren) auf Funktionsebene gestubbt: xyParkDialog geht
  // hier NICHT ueber confirmDialog, damit die Antwortfolge unten die
  // uebrigen Dialoge weiterhin in ihrer Reihenfolge trifft. Der Schritt
  // selbst wird in Teil 6 geprueft.
  global.xyParkDefaults = function () { return Promise.resolve({ x: 125, y: 130, z: 60 }); };
  global.xyParkDialog = function (d) { return Promise.resolve(d); };
  global.xyWriteParkConfig = function () { return Promise.resolve(); };
  global.xyParkMove = function () { return Promise.resolve(true); };

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
// Teil 3b: ensureHomedAfterActivate() (Fix-Runde 3) - der Schritt direkt
// VOR der Aufforderung "Halterung auf das Bett stellen". Vorher galt hier
// das Ergebnis von sendGcodeWithRecovery() als Beweis; bei
// {transport:true} meldete die Funktion "gehomt", waehrend G28 - oder der
// Recovery-Weg G28 -> QGL -> G28 Z, der Minuten dauert - noch fuhr. Der
// Nutzer wurde also aufgefordert, in eine fahrende Maschine zu greifen.
// Jetzt wird homed_axes NACH dem Lauf erneut gefragt.
// --------------------------------------------------------------------
function runEnsureHomedTest() {
  function withHoming(sendResult, axesSequence, idleResult) {
    var queries = 0;
    var sends = 0;
    global.showToast = function () {};
    global.printerUrl = function (ip, path) { return 'http://' + ip + ':7125' + path; };
    global.printerIp = '1.2.3.4';
    global.$ = { get: function () {
      var axes = axesSequence[Math.min(queries, axesSequence.length - 1)];
      queries++;
      return Promise.resolve(
        { result: { status: { toolhead: { homed_axes: axes } } } });
    } };
    global.sendGcodeWithRecovery = function () {
      sends++;
      return Promise.resolve(sendResult);
    };
    global.waitForPrinterIdle = function () { return Promise.resolve(idleResult); };

    eval(grab('xyHomedAxes') + grab('xyIsHomed') +
         grab('ensureHomedAfterActivate'));
    return ensureHomedAfterActivate().then(
      function () { return { ok: true, queries: queries, sends: sends }; },
      function (e) {
        return { ok: false, msg: String(e && e.message),
                 queries: queries, sends: sends };
      });
  }

  var seq = Promise.resolve();

  // --- schon gehomt -> gar kein G28, eine einzige Abfrage ---
  seq = seq.then(function () {
    return withHoming({ ok: true }, ["xyz"], true).then(function (r) {
      check('bereits x/y/z gehomt -> kein G28 noetig',
        r.ok === true && r.sends === 0 && r.queries === 1, JSON.stringify(r));
    });
  });

  // --- transport, danach meldet der Drucker xyz -> erst DAS ist Erfolg ---
  seq = seq.then(function () {
    return withHoming({ transport: true }, ["", "xyz"], true).then(function (r) {
      check('transport + homed_axes danach "xyz" -> gehomt',
        r.ok === true, JSON.stringify(r));
      check('homed_axes wird nach dem Lauf ERNEUT abgefragt',
        r.queries === 2, 'queries=' + r.queries);
    });
  });

  // --- transport, aber der Drucker meldet weiterhin nichts gehomt ---
  seq = seq.then(function () {
    return withHoming({ transport: true }, ["", ""], true).then(function (r) {
      check('transport allein ist KEIN Beweis fuers Homing -> Abbruch',
        r.ok === false, JSON.stringify(r));
    });
  });

  // --- selbst {ok:true} wird nachgeprueft ---
  seq = seq.then(function () {
    return withHoming({ ok: true }, ["", ""], true).then(function (r) {
      check('auch {ok:true} wird gegen homed_axes geprueft -> Abbruch',
        r.ok === false, JSON.stringify(r));
    });
  });

  // --- der Drucker arbeitet noch (idle-Wartezeit abgelaufen) ---
  seq = seq.then(function () {
    return withHoming({ transport: true }, ["", "xyz"], false).then(function (r) {
      check('Drucker arbeitet noch -> Abbruch, statt zum Aufsetzen zu bitten',
        r.ok === false, JSON.stringify(r));
    });
  });

  return seq;
}

// --------------------------------------------------------------------
// Teil 3c: xyWizard()-Tore um Trocken- und Messlauf (Fix-Runde 3).
// Vorher hingen Trockenlauf, Messlauf und Deaktivieren ohne irgendetwas
// dazwischen aneinander - ein Verbindungsabbruch ({transport:true}) beim
// Trockenlauf schob den absenkenden Messlauf sofort hinterher, und ein
// Abbruch beim Messlauf fuehrte zu FIRMWARE_RESTART mitten in der Fahrt.
// Alle drei Faelle laufen hier mit mountedResult {transport:true}, weil
// genau das bei einem Lauf ueber alle Tools der Regelfall ist.
// --------------------------------------------------------------------
function runXyWizardGateTest() {
  function runWizard(answers, opts) {
    opts = opts || {};
    var confirms = [];
    var sent = [];
    var deactivateCalls = 0;
    var queue = answers.slice();

    global.confirmDialog = function (o) {
      confirms.push(o.title);
      return Promise.resolve(queue.shift());
    };
    global.showToast = function () {};
    global.xyProbeCheckPresent = function () { return Promise.resolve(true); };
    global.xyProbeActivate = function () { return Promise.resolve(); };
    global.ensureHomedAfterActivate = function () { return Promise.resolve(); };
    global.ensureLeveledBeforeSetup = function () { return Promise.resolve(true); };
    global.sendGcodeWithRecovery = function () { return Promise.resolve({ ok: true }); };
    // Schritt 5a (Anfahren) gestubbt, siehe runXyWizardAbortTest; opts.park
    // erlaubt, den Schritt scheitern oder abbrechen zu lassen.
    global.xyParkDefaults = function () { return Promise.resolve({ x: 125, y: 130, z: 60 }); };
    global.xyParkDialog = function (d) {
      return Promise.resolve(opts.parkCancel ? null : d);
    };
    global.xyWriteParkConfig = function () { return Promise.resolve(); };
    global.xyParkMove = function () {
      if (opts.parkFail) return Promise.reject(new Error("Kopf steht nicht auf der Anfahrposition"));
      return Promise.resolve(true);
    };
    global.xySendMounted = function (script) {
      sent.push(script);
      return Promise.resolve({ transport: true });
    };
    // Fortschrittsdialog (2026-09-04): geht vor dem Senden auf und loest
    // auf, wenn Klipper den Lauf beendet meldet. Hier: sofort, aber der
    // Aufruf und seine Reihenfolge werden festgehalten.
    var progressCalls = [];
    global.xyRunProgressDialog = function (title) {
      progressCalls.push({ title: title, sentBefore: sent.length });
      return Promise.resolve(true);
    };
    global.waitForPrinterIdle = function () {
      return Promise.resolve(opts.idle !== false);
    };
    global.updateAllProbeResults = function () { return Promise.resolve(); };
    global.xyProbeDeactivate = function () {
      deactivateCalls++;
      return Promise.resolve();
    };

    eval(grab('gcodeErrorMessage') + grab('xyStepOk') + grab('xyWizard'));
    return xyWizard().then(function () {
      return { confirms: confirms, sent: sent, deactivate: deactivateCalls, progress: progressCalls };
    });
  }

  var seq = Promise.resolve();

  // --- Kein Trockenlauf mehr im Ablauf (Tobi, 2026-09-04): nach dem
  // Aufsetzen startet direkt der Messlauf, DRY_RUN wird nie gesendet ---
  seq = seq.then(function () {
    return runWizard([true, true, null], { idle: false }).then(function (r) {
      check('kein Trockenlauf-Dialog mehr',
        r.confirms.indexOf('Trockenlauf') === -1 &&
        r.confirms.indexOf('Trockenlauf beendet?') === -1,
        JSON.stringify(r.confirms));
      check('DRY_RUN wird nicht gesendet',
        r.sent.every(function (s) { return s.indexOf('DRY_RUN') === -1; }),
        JSON.stringify(r.sent));
      check('Fortschrittsdialog geht VOR dem Senden des Messlaufs auf',
        r.progress.length === 1 && r.progress[0].sentBefore === 0,
        JSON.stringify(r.progress));
    });
  });

  // --- "Abschliessen": Sonde aktiv lassen (Tobi, 2026-09-04: der Nutzer
  // soll sich aussuchen koennen, ob er abstecken und deaktivieren muss) ---
  seq = seq.then(function () {
    return runWizard([true, true, 'extra', true], { idle: true }).then(function (r) {
      check('Sonde aktiv lassen: kein Deaktivieren, kein FIRMWARE_RESTART',
        r.deactivate === 0, 'deactivate=' + r.deactivate);
      check('Sonde aktiv lassen: Abschluss-Dialog sagt, dass sie aktiv bleibt',
        r.confirms.indexOf('Abschließen') !== -1 &&
        r.confirms.indexOf('Sonde bleibt aktiv') !== -1,
        JSON.stringify(r.confirms));
    });
  });

  // --- Messlauf laeuft noch (nie idle) -> kein FIRMWARE_RESTART ---
  seq = seq.then(function () {
    return runWizard([true, true, null], { idle: false })
      .then(function (r) {
        check('Messlauf wird direkt nach dem Aufsetzen gestartet',
          r.sent.length === 1 && r.sent[0] === 'CALIBRATE_XY_OFFSETS',
          JSON.stringify(r.sent));
        check('Drucker noch nicht idle -> "Abschliessen" wird NICHT angeboten',
          r.confirms.indexOf('Abschließen') === -1,
          JSON.stringify(r.confirms));
        check('Drucker noch nicht idle -> kein xyProbeDeactivate (kein Restart)',
          r.deactivate === 0, 'calls=' + r.deactivate);
      });
  });

  // --- Regelfall: transport, aber der Drucker meldet sich wieder idle ---
  seq = seq.then(function () {
    return runWizard([true, true, true, true]).then(function (r) {
      check('idle nach dem Messlauf -> "Abschliessen" und Deaktivieren',
        r.confirms.indexOf('Abschließen') !== -1 && r.deactivate === 1,
        JSON.stringify(r.confirms) + ' calls=' + r.deactivate);
      check('vollstaendige Dialogfolge des Regelfalls',
        JSON.stringify(r.confirms) === JSON.stringify([
          'XY-Sonde: Anstecken', 'XY-Sonde: Aufsetzen', 'Abschließen', 'Fertig'
        ]), JSON.stringify(r.confirms));
    });
  });


  // --- Schritt 5a: Anfahren abgebrochen -> Abbruch-Dialog, KEIN Aufsetzen ---
  // Das Bett ist hier noch leer, aber die Sonde ist schon aktiviert: der
  // Nutzer muss deaktivieren koennen, bevor er abzieht.
  seq = seq.then(function () {
    return runWizard([true, 'extra', true], { parkCancel: true }).then(function (r) {
      check('Anfahren abgebrochen -> Aufsetzen wird NICHT angeboten',
        r.confirms.indexOf('XY-Sonde: Aufsetzen') === -1, JSON.stringify(r.confirms));
      check('Anfahren abgebrochen -> Abbruch-Dialog mit Deaktivieren',
        r.confirms.indexOf('XY-Assistent abgebrochen') !== -1 && r.deactivate === 1,
        JSON.stringify(r.confirms) + ' calls=' + r.deactivate);
      check('Anfahren abgebrochen -> kein Trockenlauf, kein Messlauf',
        r.sent.length === 0, JSON.stringify(r.sent));
    });
  });

  // --- Schritt 5a: Position nicht nachgewiesen -> ebenfalls kein Aufsetzen ---
  seq = seq.then(function () {
    return runWizard([true, 'extra', true], { parkFail: true }).then(function (r) {
      check('Positionsnachweis gescheitert -> Aufsetzen wird NICHT angeboten',
        r.confirms.indexOf('XY-Sonde: Aufsetzen') === -1, JSON.stringify(r.confirms));
      check('Positionsnachweis gescheitert -> Abbruch-Dialog',
        r.confirms.indexOf('XY-Assistent abgebrochen') !== -1, JSON.stringify(r.confirms));
    });
  });
  return seq;
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
  // Teil 3c teilt sich den confirmDialog-Stub-Stil mit Teil 3, setzt ihn
  // aber je Szenario neu - deshalb direkt danach.
  return runXyWizardGateTest();
}).then(function () {
  // Teil 3b stubbt $ als Objekt mit .get - danach nutzt Teil 4 dasselbe
  // Muster weiter.
  return runEnsureHomedTest();
}).then(function () {
  // Teil 4 stubbt $ und printerUrl/printerIp neu - erst NACHDEM Teil 2/3
  // ihre eigenen fetch/confirmDialog-Stubs nicht mehr brauchen.
  return runCaptureMountedToolTest();
}).then(function () {
  // Teil 5 stubbt global.fetch neu - erst NACHDEM Teil 4 fertig ist.
  return runCaptureCameraPositionCatchTest();
}).then(function () {
  return runParkTest();
}).then(function () {
  console.log(failed ? '\n' + failed + ' TESTS FEHLGESCHLAGEN' : '\nALLE TESTS OK');
  process.exit(failed ? 1 : 0);
}).catch(function (e) {
  console.log('EXCEPTION', e);
  process.exit(1);
});

// --------------------------------------------------------------------
// Teil 6: Anfahrposition (Spec R-B'). xyPatchParkLines ist rein; die
// Reihenfolge im Assistenten wird am Quelltext geprueft: Anfahren liegt
// zwischen Sondenpruefung und Aufsetzen, und der Aufsetzen-Dialog kommt
// erst NACH dem positiven Positionsnachweis.
// --------------------------------------------------------------------
function runParkTest() {
  console.log('\n-- Teil 6: Anfahrposition --');
  eval(grab('xyPatchParkLines'));
  var park = { x: 125, y: 130.25, z: 60 };

  var tpl = "[mcu xyprobe]\nserial: /dev/x\n\n[nozzle_locator]\ni2c_mcu: xyprobe\n" +
            "# park_x/park_y weglassen = Bettmitte\n#park_x: 1\n#park_y: 2\npark_z: 15\n" +
            "search_span: 30\n";
  var out = xyPatchParkLines(tpl, park);
  check('auskommentierte park_x-Zeile wird zur echten', /^park_x: 125\.0$/m.test(out));
  check('park_y auf eine Nachkommastelle gerundet', /^park_y: 130\.3$/m.test(out));
  check('vorhandenes park_z ersetzt', /^park_z: 60\.0$/m.test(out) && !/park_z: 15/.test(out));
  check('Kommentarzeile mit park_x/park_y im Text bleibt', out.indexOf('# park_x/park_y weglassen') >= 0);
  check('uebrige Zeilen unveraendert', out.indexOf('search_span: 30') >= 0 && out.indexOf('serial: /dev/x') >= 0);
  check('kein Duplikat', out.split('\n').filter(function (l) { return /^park_x:/.test(l); }).length === 1);

  var bare = "[nozzle_locator]\ni2c_mcu: xyprobe\n";
  var out2 = xyPatchParkLines(bare, park);
  var lines2 = out2.split('\n');
  check('fehlende Schluessel direkt hinter [nozzle_locator] eingefuegt',
    lines2[0] === '[nozzle_locator]' && lines2[1] === 'park_x: 125.0' &&
    lines2[2] === 'park_y: 130.3' && lines2[3] === 'park_z: 60.0');

  var threw = false;
  try { xyPatchParkLines("[mcu xyprobe]\n", park); } catch (e) { threw = true; }
  check('ohne [nozzle_locator]-Sektion wird geworfen', threw);

  var wiz = grab('xyWizard');
  var iRead = wiz.indexOf('NOZZLE_LOCATOR_READ');
  var iPark = wiz.indexOf('xyParkDefaults()');
  var iMount = wiz.indexOf('XY-Sonde: Aufsetzen');
  check('Assistent: Anfahren liegt zwischen Sondenpruefung und Aufsetzen',
    iRead >= 0 && iPark > iRead && iMount > iPark);
  check('Aufsetzen-Text nennt das Unterstellen unter die Duese',
    /unter die Duese/.test(wiz.slice(iMount, iMount + 400)));

  var mv = grab('xyParkMove');
  check('xyParkMove wartet bei transport auf Stillstand',
    /transport/.test(mv) && /waitForPrinterIdle/.test(mv));
  check('xyParkMove weist die Position ueber die Kopfposition nach',
    /xyToolheadPosition/.test(mv) && /0\.5/.test(mv));
  return Promise.resolve();
}

// Leveling liegt zwischen Homen und Sondenpruefung -- also vor dem
// Aufsetzen, solange das Bett leer ist. Reine Quelltextpruefung.
(function () {
  var wiz = grab('xyWizard');
  var iHome = wiz.indexOf('ensureHomedAfterActivate()');
  var iLvl = wiz.indexOf('ensureLeveledBeforeSetup()');
  var iRead = wiz.indexOf('NOZZLE_LOCATOR_READ');
  check('Assistent: Leveling zwischen Homen und Sondenpruefung',
    iHome >= 0 && iLvl > iHome && iRead > iLvl);
  var lv = grab('ensureLeveledBeforeSetup');
  check('Leveling referenziert Z neu und weist applied nach',
    /G28 Z/.test(lv) && /waitForPrinterIdle/.test(lv) && /applied/.test(lv));
})();

// --------------------------------------------------------------------
// Teil N: xyMapCommand() - baut das NOZZLE_LOCATOR_MAP-Kommando aus den
// Feldern des Raster-Panels. Reine Funktion; Validierung gehoert hierher,
// damit ein Tippfehler nicht als Maschinenbefehl rausgeht.
// --------------------------------------------------------------------
{
  eval(grab('xyMapCommand'));
  var c = xyMapCommand({ width: 20, height: 20, pitch: 1, label: 'T0' });
  check('xyMapCommand: Standardfelder',
        c === 'NOZZLE_LOCATOR_MAP WIDTH=20 HEIGHT=20 PITCH=1 LABEL=T0', c);
  c = xyMapCommand({ width: '12.5', height: '8', pitch: '0.5', label: '' });
  check('xyMapCommand: Zahlen als Text, leeres Label weggelassen',
        c === 'NOZZLE_LOCATOR_MAP WIDTH=12.5 HEIGHT=8 PITCH=0.5', c);
  var threw = false;
  try { xyMapCommand({ width: 0, height: 20, pitch: 1 }); } catch (e) { threw = true; }
  check('xyMapCommand: Breite 0 wird abgelehnt', threw);
  threw = false;
  try { xyMapCommand({ width: 20, height: 20, pitch: 11 }); } catch (e) { threw = true; }
  check('xyMapCommand: Raster groesser als halbe Hoehe wird abgelehnt', threw);
  threw = false;
  try { xyMapCommand({ width: 20, height: 20, pitch: 1, label: 'T0 X=5' }); } catch (e) { threw = true; }
  check('xyMapCommand: Label mit Leerzeichen/Gleichheitszeichen wird abgelehnt', threw);
  threw = false;
  try { xyMapCommand({ width: 'abc', height: 20, pitch: 1 }); } catch (e) { threw = true; }
  check('xyMapCommand: Unsinn statt Zahl wird abgelehnt', threw);
  threw = false;
  try { xyMapCommand({ width: 200, height: 20, pitch: 1 }); } catch (e) { threw = true; }
  check('xyMapCommand: mehr als 100 mm wird abgelehnt', threw);
}

// --------------------------------------------------------------------
// Teil N+1: Messbild je Tool (Klick auf den Toolnamen). xyImageEntries()
// zieht aus einem Ergebnis-Eintrag die anzeigbaren Bilder je Spalt,
// xyImageBodyHtml() baut den Dialogrumpf mit einem Platzhalter je Bild.
// --------------------------------------------------------------------
{
  eval(grab('escapeHtml') + grab('xyImageEntries') + grab('xyImageBodyHtml'));
  var raster = { kind: 'raster', xs: [1, 2], ys: [1, 2], values: [[1, 2], [3, 4]],
                 baseline: 100, x: 1.5, y: 1.5, pitch: 1 };
  var prof = { kind: 'profiles', x: [[1, 5], [2, 9]], y: [[1, 4], [2, 8]], baseline: 0,
               cx: 1.5, cy: 1.5 };
  var entry = { images: [Object.assign({ gap: 0.8 }, raster),
                         Object.assign({ gap: 1.2 }, raster),
                         Object.assign({ gap: 1.6 }, prof)],
                tip_slope_x: 0.01, tip_slope_y: 0.29, tip_method: 'quadratic',
                x: 0.5, y: -5.0, amplitude: 11000 };
  var es = xyImageEntries(entry);
  check('xyImageEntries: drei Bilder', es.length === 3, String(es.length));
  check('xyImageEntries: Spalt im Label', /0\.80/.test(es[0].label) && /1\.60/.test(es[2].label),
        es.map(function (e) { return e.label; }).join('|'));
  check('xyImageEntries: Art bleibt', es[0].kind === 'raster' && es[2].kind === 'profiles');
  check('xyImageEntries: ohne Bilder leer', xyImageEntries({ x: 1 }).length === 0);
  check('xyImageEntries: altes Einzelbild wird aufgenommen',
        xyImageEntries({ image: raster }).length === 1);
  var html = xyImageBodyHtml('3', entry, es);
  check('xyImageBodyHtml: ein Platzhalter je Bild',
        (html.match(/id="xy-img-\d+"/g) || []).length === 3, html.slice(0, 200));
  check('xyImageBodyHtml: Steigung und Methode genannt',
        /0\.29|290/.test(html) && /quadrat/i.test(html));
  check('xyImageBodyHtml: Toolname drin', /T3/.test(html));
}

// --------------------------------------------------------------------
// Teil N+2: Fortschrittsdialog des Messlaufs (Tobi, 2026-09-04: der
// Dialog soll offen bleiben und zeigen, was gerade passiert).
// xyProgressHtml() rendert printer.offset.xy_progress plus die letzten
// Konsolenzeilen, xyProgressDone() entscheidet, wann der Lauf vorbei ist.
// --------------------------------------------------------------------
{
  eval(grab('escapeHtml') + grab('xyProgressHtml') + grab('xyProgressDone'));
  var t0 = 1700000000000;
  var p = { running: true, tools: [0, 1, 2], done: [0], tool: 1, step: 'Grobsuche',
            started: t0 / 1000, ref_tool: 0 };
  var html = xyProgressHtml(p, ['// T0: Scheitel X120.7 Y111.0', '// T1: <b>Vorhersage</b>'], t0 + 125000);
  check('xyProgressHtml: alle Tools genannt', /T0/.test(html) && /T1/.test(html) && /T2/.test(html), html);
  check('xyProgressHtml: Schritt und aktuelles Tool', /Grobsuche/.test(html) && /xy-prog-current/.test(html));
  check('xyProgressHtml: erledigtes Tool markiert', /xy-prog-done[^>]*>[^<]*T0/.test(html), html);
  check('xyProgressHtml: Laufzeit in Minuten', /2:05/.test(html), html);
  check('xyProgressHtml: Konsolenzeilen escaped', /&lt;b&gt;Vorhersage/.test(html) && !/<b>Vorhersage/.test(html));
  check('xyProgressHtml: Platz fuer das Live-Raster', /id="xy-prog-map3d"/.test(html));
  check('xyProgressHtml: ohne Status wartet', /wartet|startet/i.test(xyProgressHtml(null, [], t0)));
  check('xyProgressHtml: leeres Objekt (nie gelaufen) zaehlt als kein Status',
        /startet/i.test(xyProgressHtml({}, [], t0)) && !/Fertig/.test(xyProgressHtml({}, [], t0)));
  check('xyProgressHtml: Ende ohne Status -> Hinweis', /steht wieder/.test(xyProgressHtml(null, [], t0, 'x')));
  var err = xyProgressHtml({ running: false, error: 'T2: nur 900 Hz', tools: [0, 1, 2], done: [0, 1] }, [], t0);
  check('xyProgressHtml: Fehler sichtbar', /nur 900 Hz/.test(err) && /abgebrochen|Fehler/i.test(err), err);
  var fin = xyProgressHtml({ running: false, tools: [0, 1], done: [0, 1], step: 'fertig' }, [], t0);
  check('xyProgressHtml: fertig gemeldet', /fertig/i.test(fin));

  check('xyProgressDone: laeuft -> nicht fertig', xyProgressDone({ running: true }, true, true, 99999) === false);
  check('xyProgressDone: lief und ist jetzt aus -> fertig', xyProgressDone({ running: false }, true, false, 5000) === true);
  check('xyProgressDone: nie laufen gesehen, Drucker steht erst kurz -> warten',
        xyProgressDone({ running: false }, false, true, 5000) === false);
  check('xyProgressDone: nie laufen gesehen (altes Klipper), Drucker steht lange -> fertig',
        xyProgressDone(null, false, true, 30000) === true);
  check('xyProgressDone: nie laufen gesehen, Drucker arbeitet -> warten',
        xyProgressDone(null, false, false, 30000) === false);
}

// --------------------------------------------------------------------
// Teil N+3: Messbild-Dialog mit Log-Umschalter, 2D-Link und Ueberlagern
// --------------------------------------------------------------------
{
  eval(grab('escapeHtml') + grab('xyImageEntries') + grab('xyImageBodyHtml'));
  var raster2 = { kind: 'raster', xs: [1, 2], ys: [1, 2], values: [[1, 2], [3, 4]],
                  baseline: 100, x: 1.5, y: 1.5, pitch: 1 };
  var entry2 = { images: [Object.assign({ gap: 0.8 }, raster2)], x: 0.5, y: -5.0 };
  var es2 = xyImageEntries(entry2);
  var html2 = xyImageBodyHtml('1', entry2, es2, { ip: '192.168.178.60' });
  check('xyImageBodyHtml: Log-Umschalter', /id="xy-img-log"/.test(html2) && /checked/.test(html2), html2);
  check('xyImageBodyHtml: 2D-Link je Raster',
        /map\.html\?ip=192\.168\.178\.60&src=xy&t=1&i=0/.test(html2), html2);
  var entryTip = Object.assign({ x_peak: 120.7028, y_peak: 110.9841 }, entry2);
  var htmlTip = xyImageBodyHtml('1', entryTip, xyImageEntries(entryTip), { ip: '1.2.3.4' });
  check('xyImageBodyHtml: Spitzenpunkt in der Kopfzeile', /Spitze/.test(htmlTip) && /120.703/.test(htmlTip) && /110.984/.test(htmlTip), htmlTip.slice(0, 300));
  check('xyImageBodyHtml: Ueberlagern-Knopf schliesst erst das Messbild', /xyOverlayFromImage\(/.test(html2));
}

// --------------------------------------------------------------------
// Teil N+4: Ueberlagerungs-Editor -- xyOverlayState() liest die Felder
// und berechnet die Verschiebung von B (Scheitel + Handverschiebung)
// --------------------------------------------------------------------
{
  global.NozzleOverlay = require(require('path').join(__dirname, '..', 'webapp', 'js', 'overlay.js'));
  eval(grab('xyOverlayLayers'));
  var rA = { kind: 'raster', xs: [0, 1, 2], ys: [0, 1, 2], values: [[0, 1, 0], [1, 5, 1], [0, 1, 0]],
             baseline: 0, x: 1, y: 1, gap: 0.8 };
  var rB = { kind: 'raster', xs: [10, 11, 12], ys: [5, 6, 7], values: [[0, 2, 0], [2, 8, 2], [0, 2, 0]],
             baseline: 0, x: 11, y: 6, gap: 0.8 };
  var results = { ref_tool: 0, '0': { images: [rA] }, '1': { images: [rB] } };
  var L = xyOverlayLayers(results, { a: '0:0', b: '1:0', align: true, dx: 100, dy: -50, normalize: true });
  check('xyOverlayLayers: A aus Tool 0', L.a && L.a.vx === 1 && /T0/.test(L.a.label), JSON.stringify(L.a && L.a.label));
  check('xyOverlayLayers: B auf A geschoben plus Hand-dx/dy in um',
        L.b && Math.abs(L.b.vx - 1.1) < 1e-9 && Math.abs(L.b.vy - 0.95) < 1e-9, JSON.stringify(L.b && [L.b.vx, L.b.vy]));
  check('xyOverlayLayers: normiert', L.a.values[1][1] === 1 && L.b.values[1][1] === 1);
  check('xyOverlayLayers: Verschiebung gemeldet', Math.abs(L.shift.dx - (-9.9)) < 1e-9 && Math.abs(L.shift.dy - (-5.05)) < 1e-9,
        JSON.stringify(L.shift));
  var L2 = xyOverlayLayers(results, { a: '0:0', b: '1:0', align: false, dx: 0, dy: 0, normalize: false });
  check('xyOverlayLayers: ohne Ausrichten bleibt B, wo es gemessen wurde', L2.b.vx === 11 && L2.b.values[1][1] === 8);
  var L3 = xyOverlayLayers(results, { a: '0:0', b: '', align: true, dx: 0, dy: 0, normalize: true });
  check('xyOverlayLayers: ohne B nur A', L3.a && L3.b === null);
  // Spitzenpunkt (x_peak/y_peak, Extrapolation auf Spalt 0) kommt mit und
  // wandert bei B mit der Verschiebung
  var resultsT = { ref_tool: 0, '0': { images: [rA], x_peak: 1.2, y_peak: 0.9 },
                   '1': { images: [rB], x_peak: 11.3, y_peak: 6.1 } };
  var LT = xyOverlayLayers(resultsT, { a: '0:0', b: '1:0', align: true, dx: 0, dy: 0, normalize: true });
  check('xyOverlayLayers: Spitze von A', LT.a.tx === 1.2 && LT.a.ty === 0.9);
  check('xyOverlayLayers: Spitze von B mitverschoben (Scheiteldifferenz -10/-5)',
        Math.abs(LT.b.tx - 1.3) < 1e-9 && Math.abs(LT.b.ty - 1.1) < 1e-9, JSON.stringify([LT.b.tx, LT.b.ty]));
  var L4 = xyOverlayLayers(results, { a: '7:0', b: '', align: true, dx: 0, dy: 0 });
  check('xyOverlayLayers: unbekanntes Tool -> a null', L4.a === null);
}


// --------------------------------------------------------------------
// Teil N+5: Tool-Auswahl ueber allen Abschnitten (Tobi, 2026-09-04: "uns
// ist diese Sektion verloren gegangen, bei der Kamera- wie bei der
// Eddy-Vermessung"). toolSelectionPanel() baut die beiden Kaesten
// (Tools, Referenz), calibrateButton() enthaelt sie NICHT mehr, und der
// Eddy-Lauf bekommt Auswahl und Referenz als REF_TOOL/TOOLS mit.
// --------------------------------------------------------------------
{
  global.offsetMasterTool = null;
  global._offsetZCalcDefault = 'median';
  global._uiZCalcSelection = 'config';
  global.tapMinTempHint = function () { return ''; };
  global.tapMinTempDefault = function () { return 0; };
  eval(grab('computeDefaultRef') + grab('toolSelectionPanel') + grab('calibrateButton') +
       grab('xyCalibrateCommand'));
  var panel = toolSelectionPanel([2, 0, 1]);
  check('toolSelectionPanel: Tools und Referenz drin',
        /Tools to calibrate/.test(panel) && /Reference \(Master\) tool/.test(panel), panel.slice(0, 200));
  check('toolSelectionPanel: eine Checkbox je Tool, sortiert',
        (panel.match(/id="calibrate-tool-\d"/g) || []).join() === 'id="calibrate-tool-0",id="calibrate-tool-1",id="calibrate-tool-2"');
  check('toolSelectionPanel: Referenz T0 vorausgewaehlt',
        /id="calibrate-ref-0" value="0" checked/.test(panel));
  check('toolSelectionPanel: sagt, dass die Auswahl fuer alle Verfahren gilt',
        /Kamera/.test(panel) && /Eddy/.test(panel) && /Z-Switch/.test(panel));
  var zbtn = calibrateButton([0, 1], true);
  check('calibrateButton: Tool-Auswahl nicht mehr im Z-Switch-Abschnitt',
        !/Tools to calibrate/.test(zbtn) && !/calibrate-ref-/.test(zbtn) && /CALIBRATE Z-OFFSETS/.test(zbtn));

  check('xyCalibrateCommand: Referenz und Auswahl',
        xyCalibrateCommand([1, 2, 3], 0) === 'CALIBRATE_XY_OFFSETS REF_TOOL=0 TOOLS=0,1,2,3');
  check('xyCalibrateCommand: Referenz nicht doppelt, sortiert',
        xyCalibrateCommand([3, 1, 0], 1) === 'CALIBRATE_XY_OFFSETS REF_TOOL=1 TOOLS=0,1,3');
  check('xyCalibrateCommand: leere Auswahl -> nur Referenz',
        xyCalibrateCommand([], 2) === 'CALIBRATE_XY_OFFSETS REF_TOOL=2 TOOLS=2');
  check('xyCalibrateCommand: ohne Angaben das nackte Kommando',
        xyCalibrateCommand(null, null) === 'CALIBRATE_XY_OFFSETS');
  // Feinspalt (Tobi, 2026-09-04, nach Lauf 11): Feld im XY-Block, geht als
  // FINE_GAP mit -- leer heisst Klipper-Default, Unsinn wirft.
  check('xyCalibrateCommand: Feinspalt wird angehaengt',
        xyCalibrateCommand([1], 0, { fineGap: 0.4 }) === 'CALIBRATE_XY_OFFSETS REF_TOOL=0 TOOLS=0,1 FINE_GAP=0.4');
  check('xyCalibrateCommand: Feinspalt auch ohne Auswahl',
        xyCalibrateCommand(null, null, { fineGap: '0.55' }) === 'CALIBRATE_XY_OFFSETS FINE_GAP=0.55');
  check('xyCalibrateCommand: leerer Feinspalt wird weggelassen',
        xyCalibrateCommand([1], 0, { fineGap: '' }) === 'CALIBRATE_XY_OFFSETS REF_TOOL=0 TOOLS=0,1');
  var threwGap = false;
  try { xyCalibrateCommand([1], 0, { fineGap: 0.1 }); } catch (e) { threwGap = /Feinspalt/.test(e.message); }
  check('xyCalibrateCommand: Feinspalt unter 0,2 mm wirft', threwGap);
  threwGap = false;
  try { xyCalibrateCommand([1], 0, { fineGap: 'abc' }); } catch (e) { threwGap = true; }
  check('xyCalibrateCommand: Feinspalt als Unsinn wirft', threwGap);
  check('XY-Block: Feld fuer den Feinspalt', /id="xy-fine-gap"/.test(grab('xyOffsetSection')));
  // Fit-Radius (Tobi, 2026-09-05): Feld, geht als FIT_RADIUS mit; Default 2,0.
  check('xyCalibrateCommand: Fit-Radius wird angehaengt',
        xyCalibrateCommand([1], 0, { fitRadius: 1.5 }) === 'CALIBRATE_XY_OFFSETS REF_TOOL=0 TOOLS=0,1 FIT_RADIUS=1.5');
  check('xyCalibrateCommand: Feinspalt und Radius zusammen',
        xyCalibrateCommand([1], 0, { fineGap: 0.4, fitRadius: '2' }) === 'CALIBRATE_XY_OFFSETS REF_TOOL=0 TOOLS=0,1 FINE_GAP=0.4 FIT_RADIUS=2');
  var threwR = false;
  try { xyCalibrateCommand([1], 0, { fitRadius: 0.5 }); } catch (e) { threwR = /Fit-Radius/.test(e.message); }
  check('xyCalibrateCommand: Fit-Radius unter 0,75 wirft', threwR);
  check('XY-Block: Feld fuer den Fit-Radius', /id="xy-fit-radius"/.test(grab('xyOffsetSection')));
}

// --------------------------------------------------------------------
// Teil N+6: Sonde aktiv lassen -- Knoepfe "Messlauf" und "Sonde
// deaktivieren" im XY-Block (nur bei geladenem nozzle_locator sichtbar)
// und der direkte Lauf xyRunDirect(): Fortschrittsdialog vor dem Senden,
// Kommando aus Auswahl und Referenz, kein Homen.
// --------------------------------------------------------------------
{
  var sec = grab('xyOffsetSection');
  check('XY-Block: Knopf Messlauf (versteckt bis die Sonde aktiv ist)',
        /id="xy-run-btn"/.test(sec) && /d-none" id="xy-run-btn"/.test(sec));
  check('XY-Block: Knopf Sonde deaktivieren', /id="xy-deactivate-btn"/.test(sec));
  check('Poll blendet die Knoepfe nach nozzle_locator ein/aus',
        /xy-run-btn, #xy-deactivate-btn/.test(grab('pollXySparkline')));

  // Stubs unter eigenem Namen (Suffix RD), damit sie nicht in die spaeter
  // laufenden Assistenten-Tests hineinwirken -- die teilen sich die globals.
  var sent = [], progressCalls = [], confirms = [];
  global.confirmDialogRD = function (o) { confirms.push(o.title); return Promise.resolve(true); };
  global.alertDialogRD = function (t) { confirms.push(t); return Promise.resolve(true); };
  global.xyRunProgressDialogRD = function () { progressCalls.push(sent.length); return Promise.resolve(true); };
  global.xySendMountedRD = function (s) { sent.push(s); return Promise.resolve({ transport: true }); };
  global.waitForPrinterIdleRD = function () { return Promise.resolve(true); };
  global.updateAllProbeResultsRD = function () { return Promise.resolve(); };
  global.xySelectedToolsRD = function () { return [0, 1]; };
  global.getSelectedReferenceToolRD = function () { return 0; };
  global.xyFineGapValueRD = function () { return ''; };
  global.xyFitRadiusValueRD = function () { return ''; };
  var rd = (grab('xyBuildRunCommand') + grab('xyRunDirect')).replace(
    /\b(confirmDialog|alertDialog|xyRunProgressDialog|xySendMounted|waitForPrinterIdle|updateAllProbeResults|xySelectedTools|getSelectedReferenceTool|xyFineGapValue|xyFitRadiusValue|xyBuildRunCommand)\(/g,
    '$1RD(');
  eval(grab('escapeHtml') + grab('gcodeErrorMessage') + grab('xyCalibrateCommand') + rd);
  xyRunDirect().then(function () {
    check('xyRunDirect: Kommando aus Auswahl und Referenz, ohne G28',
          sent.length === 1 && sent[0] === 'CALIBRATE_XY_OFFSETS REF_TOOL=0 TOOLS=0,1', JSON.stringify(sent));
    check('xyRunDirect: Fortschrittsdialog vor dem Senden', progressCalls.length === 1 && progressCalls[0] === 0);
    check('xyRunDirect: nur ein Bestaetigungsdialog, kein Abbruch-Dialog',
          JSON.stringify(confirms) === JSON.stringify(['XY-Messlauf starten']), JSON.stringify(confirms));
  });
}

// --------------------------------------------------------------------
// Teil N+7: Fit-Radius ermitteln (Tobi, 2026-09-05) -- Knopf am Feld,
// Tabelle aus dem Vorschlag, Vorschlag als Default uebernehmbar.
// --------------------------------------------------------------------
{
  eval(grab('escapeHtml') + grab('xyFitRadiusTableHtml'));
  check('XY-Block: Knopf "ermitteln" am Fit-Radius', /id="xy-fit-radius-suggest"/.test(grab('xyOffsetSection')));
  var sugOk = { radius: 2.0, tolUm: 25, reason: 'Bis 2.00 mm bleibt der Scheitel',
    rows: [{ radius: 1.0, nMin: 13, devUm: 12, ok: true, why: '' }, { radius: 2.0, nMin: 49, devUm: 8, ok: true, why: '' },
           { radius: 3.0, nMin: 0, devUm: null, ok: false, why: 'T0: kein Hochpunkt' }],
    tools: [{ tool: '0', sweep: [{ x: 124.1, y: 126.2 }, { x: 124.11, y: 126.19 }, { error: 'x' }] }] };
  var h = xyFitRadiusTableHtml(sugOk);
  check('xyFitRadiusTableHtml: Vorschlag genannt', /Vorschlag: 2.00 mm/.test(h), h.slice(0, 120));
  check('xyFitRadiusTableHtml: gewaehlte Zeile hervorgehoben', /table-primary[^]*2.00 mm/.test(h));
  check('xyFitRadiusTableHtml: Grund fuer unpassenden Radius', /kein Hochpunkt/.test(h));
  check('xyFitRadiusTableHtml: Scheitel je Tool', /124\.100 \/ 126\.200/.test(h));
  var hNone = xyFitRadiusTableHtml({ radius: null, rows: [], tools: [], reason: 'Keine Raster-Messbilder' });
  check('xyFitRadiusTableHtml: ohne Raster nur der Grund', /Keine Raster/.test(hNone) && !/<table/.test(hNone));
  check('xyShowFitRadius: Vorschlag wird als Default ins Feld geschrieben',
        /xy-fit-radius'\)\.val\(String\(sug\.radius\)\)/.test(grab('xyShowFitRadius')) && /extraLabel/.test(grab('xyShowFitRadius')));
}

// --------------------------------------------------------------------
// Teil N+8: Z-Offsets aus der Sonde (Tobi, 2026-09-05, separate Option).
// gcode_z_offset(t) = gcode_z_offset(ref) + z_compare(t), dieselbe
// Konvention wie der Z-Switch-Lauf (z_trigger(n) - z_trigger(ref)).
// --------------------------------------------------------------------
{
  eval(grab('xyProbeZValues'));
  var resZ = { ref_tool: 0,
    '0': { x: 0, y: 0, z_compare: 0, z_mode: 'switch' },
    '1': { x: 0.5, y: -5, z_compare: 0.31, z_mode: 'switch' },
    '2': { x: 0.6, y: -4, z_compare: -0.215, z_mode: 'switch' },
    '3': { x: 0.1, y: -6 } };
  var off = { '0': { x: 0, y: 0, z: 0 }, '1': { x: 0.5, y: -5, z: -0.055 }, '2': { x: 0.6, y: -4, z: 0.103 }, '3': { x: 0, y: 0, z: 0 } };
  var zv = xyProbeZValues(resZ, 0, off);
  check('xyProbeZValues: Referenz ausgelassen', zv.values['0'] === undefined);
  check('xyProbeZValues: Z = Referenz-Z + z_compare', zv.values['1'].z === '0.310000' && zv.values['2'].z === '-0.215000', JSON.stringify(zv.values));
  check('xyProbeZValues: ohne z_compare kein Wert', zv.values['3'] === undefined);
  check('xyProbeZValues: Modus erkannt', zv.mode === 'switch');
  var off2 = Object.assign({}, off, { '0': { x: 0, y: 0, z: 0.1 } });
  check('xyProbeZValues: Referenz-Z aus der Config geht ein', xyProbeZValues(resZ, 0, off2).values['1'].z === '0.410000');
  var zvA = xyProbeZValues({ ref_tool: 1, '1': { z_compare: 0, z_mode: 'amplitude' }, '2': { z_compare: 0.2, z_mode: 'amplitude' } }, 1, off);
  check('xyProbeZValues: Amplitudenmodus wird gemeldet, Bezug ist das Referenztool', zvA.mode === 'amplitude' && zvA.values['2'].z === '0.145000', JSON.stringify(zvA));
  check('XY-Block: separater Knopf fuer Z aus der Sonde', /applyProbeZOffsets\(/.test(grab('renderXyBlock')));
  check('Z aus der Sonde: immer mit Warnung, Z-Switch als Quelle genannt',
        /Option mit Vorbehalt/.test(grab('applyProbeZOffsets')) && /Trotzdem/.test(grab('applyProbeZOffsets')) &&
        /btn-outline-warning/.test(grab('renderXyBlock')));
}
