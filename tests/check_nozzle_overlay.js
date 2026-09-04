#!/usr/bin/env node
// Prueft die reinen Funktionen des Ueberlagerungs-Editors
// (webapp/js/overlay.js): Ebene aus einem Messbild, Ausrichten auf den
// gemessenen Scheitel, Verschieben, Normieren, Hoehenlinien (Marching
// Squares) und die Plotly-Spuren. Kein DOM, kein Drucker.
//
//   node tests/check_nozzle_overlay.js
'use strict';
const path = require('path');
const O = require(path.join(__dirname, '..', 'webapp', 'js', 'overlay.js'));

let checks = 0;
const findings = [];
function ok(cond, what, detail) {
  checks++;
  if (!cond) findings.push(what + (detail ? ' -- ' + detail : ''));
}
function near(a, b, tol) { return Math.abs(a - b) <= tol; }

// Synthetischer Buckel: Gauss um (x0, y0), Gitter mit Raster 1 mm.
function bump(x0, y0, base, amp, n) {
  const xs = [], ys = [];
  for (let i = 0; i < n; i++) { xs.push(x0 - (n - 1) / 2 + i); ys.push(y0 - (n - 1) / 2 + i); }
  const values = ys.map(y => xs.map(x => base + amp * Math.exp(-((x - x0) ** 2 + (y - y0) ** 2))));
  return { kind: 'raster', xs, ys, values, baseline: base, x: x0, y: y0, gap: 0.8, pitch: 1 };
}

// 1: layerFromImage zieht die Basislinie ab, behaelt null, uebernimmt den Scheitel
const imgA = bump(10, 20, 1000, 500, 7);
imgA.values[0][0] = null;
const A = O.layerFromImage(imgA, 'T0 Spalt 0.80');
ok(A.values[0][0] === null, 'layerFromImage: null bleibt null');
ok(near(A.values[3][3], 500, 1e-9), 'layerFromImage zieht die Basislinie ab', String(A.values[3][3]));
ok(A.vx === 10 && A.vy === 20 && A.label === 'T0 Spalt 0.80', 'layerFromImage uebernimmt Scheitel und Label');
ok(A.xs !== imgA.xs, 'layerFromImage kopiert die Achsen (kein Alias)');
ok(O.layerFromImage(null) === null && O.layerFromImage({ kind: 'profiles' }) === null,
   'layerFromImage: kein Raster -> null');

// 2: alignShift verschiebt B auf den Scheitel von A
const imgB = bump(11.5, 15, 2000, 300, 7);
const B = O.layerFromImage(imgB, 'T1');
const s = O.alignShift(A, B);
ok(near(s.dx, -1.5, 1e-9) && near(s.dy, 5, 1e-9), 'alignShift = Scheitel A minus Scheitel B', JSON.stringify(s));

// 3: shiftLayer verschiebt Achsen und Scheitel, laesst die Werte
const Bs = O.shiftLayer(B, s.dx, s.dy);
ok(near(Bs.xs[0], B.xs[0] - 1.5, 1e-9) && near(Bs.ys[0], B.ys[0] + 5, 1e-9), 'shiftLayer verschiebt die Achsen');
ok(near(Bs.vx, 10, 1e-9) && near(Bs.vy, 20, 1e-9), 'shiftLayer verschiebt den Scheitel mit');
ok(Bs.values === B.values || Bs.values[3][3] === B.values[3][3], 'shiftLayer laesst die Werte unveraendert');
ok(B.xs[0] === imgB.xs[0], 'shiftLayer veraendert das Original nicht');

// 4: normalizeLayer skaliert auf Spitze 1
const An = O.normalizeLayer(A);
ok(near(An.values[3][3], 1, 1e-9), 'normalizeLayer: Spitze wird 1', String(An.values[3][3]));
ok(An.values[0][0] === null, 'normalizeLayer: null bleibt null');
ok(near(A.values[3][3], 500, 1e-9), 'normalizeLayer veraendert das Original nicht');
const flat = O.normalizeLayer({ xs: [0, 1], ys: [0, 1], values: [[0, 0], [0, 0]] });
ok(flat.values[0][0] === 0, 'normalizeLayer: flaches Raster bleibt 0 (keine Division durch 0)');

// 5: contourLevels liegt zwischen Minimum und Maximum, aufsteigend
const lv = O.contourLevels(An.values, 5);
ok(lv.length === 5 && lv.every((v, i) => i === 0 || v > lv[i - 1]), 'contourLevels: 5 aufsteigende Stufen', JSON.stringify(lv));
ok(lv[0] > 0 && lv[4] < 1, 'contourLevels bleibt innerhalb des Wertebereichs', JSON.stringify(lv));

// 6: contourSegments -- Hoehenlinie 0.5 des Gauss-Buckels ist ein Kreis
// mit Radius sqrt(ln 2) = 0.833 um den Scheitel.
// Stufe 0,2: Radius sqrt(ln 5) = 1,27 mm, also mehr als die vier Zellen um den Scheitel.
const seg = O.contourSegments(An.xs, An.ys, An.values, 0.2);
ok(seg.length >= 8, 'contourSegments liefert Segmente', 'n=' + seg.length);
const r0 = Math.sqrt(Math.log(5));
const radii = [];
seg.forEach(sg => sg.forEach(p => radii.push(Math.hypot(p[0] - 10, p[1] - 20))));
ok(radii.every(r => r > r0 - 0.25 && r < r0 + 0.25),
   'contourSegments: Endpunkte liegen auf dem Kreis um den Scheitel',
   'r=' + radii.map(r => r.toFixed(2)).join(','));
// geschlossen: jeder Endpunkt kommt (bis auf Rundung) zweimal vor
const key = p => p[0].toFixed(4) + '/' + p[1].toFixed(4);
const count = {};
seg.forEach(sg => sg.forEach(p => { count[key(p)] = (count[key(p)] || 0) + 1; }));
ok(Object.values(count).every(c => c === 2), 'contourSegments: Linie ist geschlossen');
ok(O.contourSegments(An.xs, An.ys, An.values, 2).length === 0, 'contourSegments: Stufe ueber dem Maximum -> leer');
// null-Zellen werden uebersprungen, kein Wurf
const withNull = An.values.map(r => r.slice()); withNull[3][3] = null;
ok(Array.isArray(O.contourSegments(An.xs, An.ys, withNull, 0.5)), 'contourSegments uebersteht null-Zellen');

// 7: segmentsToXY trennt die Segmente durch null
const xy = O.segmentsToXY([[[0, 0], [1, 1]], [[2, 2], [3, 3]]]);
ok(xy.x.length === 6 && xy.x[2] === null && xy.y[4] === 3 && xy.y[5] === null, 'segmentsToXY: Segmente durch null getrennt', JSON.stringify(xy));

// 8: overlayTraces2d -- zwei Linienspuren plus Scheitelmarker, Layout in mm
const t2 = O.overlayTraces2d(A, Bs, { levels: 4 });
ok(t2.data.length === 4, 'overlayTraces2d: 2 Linien + 2 Marker', 'n=' + t2.data.length);
ok(t2.data[0].mode === 'lines' && t2.data[1].mode === 'lines', 'overlayTraces2d: Hoehenlinien als lines');
ok(t2.data[2].mode === 'markers' && t2.data[2].x[0] === 10, 'overlayTraces2d: Marker auf dem Scheitel von A');
ok(t2.layout.yaxis.scaleanchor === 'x', 'overlayTraces2d: X und Y gleich skaliert (mm)');
ok(t2.data[0].name === 'T0 Spalt 0.80' && t2.data[1].name === 'T1', 'overlayTraces2d: Namen aus den Ebenen');
const t2a = O.overlayTraces2d(A, null, {});
ok(t2a.data.length === 2, 'overlayTraces2d ohne B: nur A');

// 9: overlayTraces3d -- zwei Flaechen, B halbdurchsichtig
const t3 = O.overlayTraces3d(A, Bs, { opacity: 0.55 });
ok(t3.data.length === 2 && t3.data[0].type === 'surface' && t3.data[1].type === 'surface', 'overlayTraces3d: zwei Flaechen');
ok(t3.data[1].opacity === 0.55, 'overlayTraces3d: Deckkraft von B aus opts');
ok(t3.data[0].colorscale !== t3.data[1].colorscale, 'overlayTraces3d: verschiedene Farbskalen');

// 10: layerOptions -- Auswahlliste aus xy_results (nur Raster)
const results = {
  ref_tool: 0,
  '0': { x: 0, y: 0, images: [imgA, { kind: 'profiles' }, bump(10, 20, 1000, 200, 7)] },
  '1': { x: 0.5, y: -5, images: [imgB] },
  '2': { x: 0.6, y: -4 }
};
results['0'].images[2].gap = 1.2;
const opts = O.layerOptions(results);
ok(opts.length === 3, 'layerOptions: nur Raster-Bilder', 'n=' + opts.length);
ok(opts[0].tool === '0' && opts[0].index === 0 && /T0/.test(opts[0].label) && /0\.80/.test(opts[0].label),
   'layerOptions: Tool, Index und Label', JSON.stringify(opts[0]));
ok(opts[1].index === 2 && /1\.20/.test(opts[1].label), 'layerOptions: Index zeigt auf das Bild in images[]');

// 11: Spitzenpunkt (Extrapolation auf Spalt 0) je Ebene -- getrennt vom
// Scheitel des Rasters, wird mitverschoben und in 2D wie 3D gezeichnet.
const At = O.layerFromImage(imgA, 'T0', { x: 10.3, y: 19.6 });
ok(At.tx === 10.3 && At.ty === 19.6, 'layerFromImage uebernimmt den Spitzenpunkt');
ok(A.tx === undefined || A.tx === null, 'layerFromImage ohne Spitze: keine Spitze');
const Bt = O.shiftLayer(O.layerFromImage(imgB, 'T1', { x: 11.4, y: 15.2 }), -1.5, 5);
ok(near(Bt.tx, 9.9, 1e-9) && near(Bt.ty, 20.2, 1e-9), 'shiftLayer verschiebt die Spitze mit', JSON.stringify([Bt.tx, Bt.ty]));
const Nt = O.normalizeLayer(At);
ok(Nt.tx === 10.3, 'normalizeLayer behaelt die Spitze');
const t2t = O.overlayTraces2d(At, Bt, { levels: 4 });
ok(t2t.data.length === 6, 'overlayTraces2d: Linien, Scheitel und Spitzen je Ebene', 'n=' + t2t.data.length);
const tips2 = t2t.data.filter(t => /Spitze/.test(t.name));
ok(tips2.length === 2 && tips2[0].x[0] === 10.3 && near(tips2[1].y[0], 20.2, 1e-9), 'overlayTraces2d: Spitzenmarker an der extrapolierten Position');
ok(tips2[0].marker.symbol !== t2t.data[2].marker.symbol, 'overlayTraces2d: Spitze und Scheitel verschieden markiert');
ok(O.overlayTraces2d(A, Bs, {}).data.length === 4, 'overlayTraces2d ohne Spitzen: unveraendert');
const t3t = O.overlayTraces3d(At, Bt, {});
ok(t3t.data.length === 4, 'overlayTraces3d: zwei Flaechen plus zwei Spitzen-Lote', 'n=' + t3t.data.length);
ok(t3t.data[2].type === 'scatter3d' && t3t.data[2].x[0] === 10.3 && t3t.data[2].x[1] === 10.3, 'overlayTraces3d: Lot senkrecht durch die Spitze');

console.log(checks + ' Zusicherungen, ' + findings.length + ' Befunde');
findings.forEach(f => console.log('  FEHLT: ' + f));
process.exit(findings.length ? 1 : 0);
