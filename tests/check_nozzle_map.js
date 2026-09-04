#!/usr/bin/env node
// Prueft die reinen Funktionen des Raster-Viewers (webapp/js/map.js):
// Basislinien-Abzug, Differenzbild auf gleichem Gitter, Gitterpruefung,
// Farbrampe und Wertebereich. Kein DOM, kein Drucker.
//
//   node tests/check_nozzle_map.js
'use strict';
const path = require('path');
const NozzleMap = require(path.join(__dirname, '..', 'webapp', 'js', 'map.js'));

let checks = 0;
const findings = [];
function ok(cond, what, detail) {
  checks++;
  if (!cond) findings.push(what + (detail ? ' -- ' + detail : ''));
}

function map(label, baseline, values, xs, ys) {
  return { kind: 'nozzle_locator_map', label, baseline, pitch: 1,
           x: 124, y: 130, grid: { xs, ys, values } };
}

const xs = [122.5, 123.5, 124.5];
const ys = [129.5, 130.5];
const A = map('T0', 1000, [[1100, 1600, 1100], [1050, 1200, 1050]], xs, ys);
const B = map('T1', 1010, [[1110, 1610, 1110], [1060, 1210, null]], xs, ys);

// 1: Basislinie wird abgezogen, null bleibt null
const va = NozzleMap.rasterValues(A);
ok(va[0][1] === 600 && va[1][0] === 50, 'rasterValues zieht die Basislinie nicht ab', JSON.stringify(va));
const vb = NozzleMap.rasterValues(B);
ok(vb[1][2] === null, 'rasterValues macht aus null einen Wert');
ok(NozzleMap.rasterValues(map('x', undefined, [[5]], [1], [1]))[0][0] === 5,
   'rasterValues ohne Basislinie veraendert die Werte');

// 2: Differenz auf gleichem Gitter, relativ zur jeweiligen Basislinie
const d = NozzleMap.rasterDiff(A, B);
ok(d !== null, 'rasterDiff lehnt gleiche Gitter ab');
ok(d && d[0][1] === 0 && d[0][0] === 0, 'rasterDiff verrechnet die Basislinien nicht',
   JSON.stringify(d));
ok(d && d[1][2] === null, 'rasterDiff: null-Zelle wird nicht durchgereicht');

// 3: Anderes Gitter -> null
const C = map('T2', 0, [[1, 2, 3]], xs, [129.5]);
ok(NozzleMap.rasterDiff(A, C) === null, 'rasterDiff akzeptiert verschiedene Gitter');
const D = map('T3', 0, [[1, 2, 3], [4, 5, 6]], [122.5, 123.5, 124.6], ys);
ok(NozzleMap.rasterDiff(A, D) === null, 'rasterDiff akzeptiert verschobene Spalten');

// 4: Wertebereich ignoriert null, degeneriert nicht
ok(JSON.stringify(NozzleMap.valueRange([[1, null, 3]])) === '[1,3]', 'valueRange falsch');
ok(NozzleMap.valueRange([[null]])[1] > NozzleMap.valueRange([[null]])[0],
   'valueRange degeneriert bei leerem Raster');
ok(NozzleMap.valueRange([[7, 7]])[1] > 7, 'valueRange degeneriert bei konstantem Raster');

// 5: Log-Skala ist vorzeichenbehaftet und monoton
ok(NozzleMap.transform(-99, 'log') === -2 && NozzleMap.transform(99, 'log') === 2,
   'transform log ist nicht vorzeichenbehaftet');
ok(NozzleMap.transform(null, 'log') === null, 'transform log verliert null');
ok(NozzleMap.transform(42, 'linear') === 42, 'transform linear veraendert den Wert');

// 6: Farbrampe: Enden und Mitte, geklemmt
const stops = [[0, [0, 0, 0]], [1, [100, 200, 250]]];
ok(JSON.stringify(NozzleMap.colorFor(0.5, stops)) === '[50,100,125]', 'colorFor interpoliert nicht');
ok(JSON.stringify(NozzleMap.colorFor(-1, stops)) === '[0,0,0]', 'colorFor klemmt unten nicht');
ok(JSON.stringify(NozzleMap.colorFor(2, stops)) === '[100,200,250]', 'colorFor klemmt oben nicht');

console.log(checks + ' Zusicherungen geprueft');
if (findings.length) {
  findings.forEach(f => console.log('BEFUND: ' + f));
  process.exit(1);
}
console.log('ALLE TESTS OK');
