// Test-Harness: schneidet _cameraOffsetFor() als Text aus tools.js heraus
// und laesst es gegen gestubbte globale Zustandsvariablen laufen - ohne
// Browser/DOM, wie check_webapp_recovery.js.
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

console.log(failed ? '\n' + failed + ' TESTS FEHLGESCHLAGEN' : '\nALLE TESTS OK');
process.exit(failed ? 1 : 0);
