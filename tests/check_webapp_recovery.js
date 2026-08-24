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

// --- Stubs ---
let sent = [];            // alle abgesetzten Skripte
let dialogs = [];         // alle gezeigten Dialoge
let nextChoice = 'ok';    // was der User klickt
let responses = [];       // Antworten fuer die Sends, der Reihe nach

global.printerIp = '1.2.3.4';
global.printerUrl = (ip, path) => 'http://' + ip + ':7125' + path;
global.$ = { get: (url) => {
  const script = decodeURIComponent(url.split('script=')[1]);
  sent.push(script);
  const r = responses.shift();
  return r instanceof Error ? Promise.reject(r.payload) : Promise.resolve({});
}};
global.confirmDialog = (o) => {
  dialogs.push(o);
  return Promise.resolve(nextChoice === 'extra' ? 'extra' : true);
};

eval(grab('gcodeErrorMessage') + grab('escapeHtml') +
     'var _alertQueue = Promise.resolve();' +
     grab('alertDialog') + grab('_showAlert') +
     grab('recoveryFor') + grab('sendGcodeWithRecovery'));

function reject(msg) {
  const e = new Error(); e.payload = { responseJSON: { error: { code: 400, message: msg } } };
  return e;
}
function transportFail() {
  const e = new Error(); e.payload = { status: 0, readyState: 0 };
  return e;
}
function reset(resp, choice) { sent = []; dialogs = []; responses = resp; nextChoice = choice; }

let failed = 0;
function check(name, cond, extra) {
  if (!cond) { failed++; console.log('FAIL: ' + name + (extra ? '  ' + extra : '')); }
  else console.log('  ok  ' + name);
}

// --- 1) recoveryFor erkennt die drei Faelle ---
check('nicht gehomed -> G28+QGL+G28 Z',
  JSON.stringify(recoveryFor('Must home first').steps) ===
  JSON.stringify(['G28', 'QUAD_GANTRY_LEVEL', 'G28 Z']));
check('kein QGL -> QGL+G28 Z',
  JSON.stringify(recoveryFor('QUAD GANTRY LEVEL has not been applied - run it first').steps) ===
  JSON.stringify(['QUAD_GANTRY_LEVEL', 'G28 Z']));
check('kein Z-Tilt -> Z_TILT_ADJUST+G28 Z',
  JSON.stringify(recoveryFor('Z TILT has not been applied - run it first').steps) ===
  JSON.stringify(['Z_TILT_ADJUST', 'G28 Z']));
check('anderer Fehler -> keine Recovery',
  recoveryFor('Probe triggered prior to movement') === null);

const RUN = 'CALIBRATE_ALL_Z_OFFSETS TOOLS=0';

// --- 2) Erfolg: ein Send, kein Dialog ---
reset([null], 'ok');
sendGcodeWithRecovery(RUN, 'X').then(r => {
  check('Erfolg -> ok, ein Send, kein Dialog',
    r.ok === true && sent.length === 1 && dialogs.length === 0);

  // --- 3) Nicht gehomed, User klickt OK: kein zweiter Send ---
  reset([reject('Must home first')], 'ok');
  return sendGcodeWithRecovery(RUN, 'Calibration failed');
}).then(r => {
  check('OK geklickt -> handled, kein Neustart',
    r.handled === true && sent.length === 1, 'sent=' + sent.length);
  check('Dialog bietet Recovery-Button an',
    /Home \+ QGL, dann weiter/.test(dialogs[0].extraLabel || ''));
  check('Dialog nennt die Schritte',
    /G28.*QUAD_GANTRY_LEVEL.*G28 Z/s.test(dialogs[0].body));
  check('Dialog hat kein Cancel', dialogs[0].hideCancel === true);

  // --- 4) Nicht gehomed, User klickt Recovery: G28+QGL+G28 Z+Lauf in EINEM Send ---
  reset([reject('Must home first'), null], 'extra');
  return sendGcodeWithRecovery(RUN, 'Calibration failed');
}).then(r => {
  check('Recovery geklickt -> ok', r.ok === true);
  check('genau zwei Sends', sent.length === 2, 'sent=' + sent.length);
  check('zweiter Send = G28 -> QGL -> G28 Z -> Lauf',
    sent[1] === 'G28\nQUAD_GANTRY_LEVEL\nG28 Z\n' + RUN,
    JSON.stringify(sent[1]));

  // --- 5) Nur QGL fehlt: kein unnoetiges G28 vorweg ---
  reset([reject('QUAD GANTRY LEVEL has not been applied - run it first'), null], 'extra');
  return sendGcodeWithRecovery(RUN, 'X');
}).then(r => {
  check('QGL-Recovery homed nicht unnoetig neu',
    sent[1] === 'QUAD_GANTRY_LEVEL\nG28 Z\n' + RUN, JSON.stringify(sent[1]));

  // --- 6) Recovery scheitert selbst: melden, nicht endlos anbieten ---
  reset([reject('Must home first'), reject('Probe triggered prior to movement')], 'extra');
  return sendGcodeWithRecovery(RUN, 'X');
}).then(r => {
  check('zweiter Fehlschlag -> handled', r.handled === true);
  check('genau zwei Sends, keine Schleife', sent.length === 2, 'sent=' + sent.length);
  check('zweiter Dialog ohne Recovery-Button', !dialogs[1].extraLabel);

  // --- 7) Verbindungsabbruch: kein Dialog, Toast-Pfad ---
  reset([transportFail()], 'ok');
  return sendGcodeWithRecovery(RUN, 'X');
}).then(r => {
  check('Transportfehler -> transport, kein Dialog',
    r.transport === true && dialogs.length === 0);

  // --- 8) Nicht behebbarer Fehler: Dialog ohne Recovery-Button ---
  reset([reject('Probe triggered prior to movement')], 'ok');
  return sendGcodeWithRecovery(RUN, 'X');
}).then(r => {
  check('unbehebbar -> Dialog ohne Extra-Button',
    r.handled === true && dialogs.length === 1 && !dialogs[0].extraLabel);
  check('Fehlertext steht im Dialog',
    /Probe triggered prior to movement/.test(dialogs[0].body));

  console.log(failed ? '\n' + failed + ' TESTS FEHLGESCHLAGEN' : '\nALLE TESTS OK');
  process.exit(failed ? 1 : 0);
}).catch(e => { console.log('EXCEPTION', e); process.exit(1); });
