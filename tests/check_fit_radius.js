#!/usr/bin/env node
// Prueft die Fit-Radius-Ermittlung (webapp/js/fitradius.js): Paraboloid-
// Fit wie in klippy/extras/nozzle_locator_fit.py (Kleinstequadrate ueber
// alle Punkte im Umkreis), Sweep ueber Radien und der Vorschlag nach dem
// Stabilitaetskriterium. Kein DOM, kein Drucker.
//
//   node tests/check_fit_radius.js
'use strict';
const path = require('path');
const F = require(path.join(__dirname, '..', 'webapp', 'js', 'fitradius.js'));

let checks = 0;
const findings = [];
function ok(cond, what, detail) {
  checks++;
  if (!cond) findings.push(what + (detail ? ' -- ' + detail : ''));
}
const near = (a, b, tol) => Math.abs(a - b) <= tol;

// Gitter 6 x 6 mm, Raster 0,5 mm, wie locate2d.
function grid(x0, y0, fn) {
  const xs = [], ys = [];
  for (let i = 0; i < 13; i++) { xs.push(x0 - 3 + i * 0.5); ys.push(y0 - 3 + i * 0.5); }
  const values = ys.map(y => xs.map(x => fn(x, y)));
  return { kind: 'raster', xs, ys, values, baseline: 0, x: x0, y: y0, gap: 0.4, pitch: 0.5 };
}
// Exakte Parabel: der Fit muss sie bei jedem Radius exakt treffen.
const parab = grid(124, 126, (x, y) => 10000 - 300 * (x - 124.2) ** 2 - 500 * (y - 125.9) ** 2);
// Glocke (keine Parabel): breiter in X, eng in Y -- wie der Heizblock.
const bell = grid(124, 126, (x, y) => 12000 * Math.exp(-(((x - 124.2) / 3) ** 2) - (((y - 125.9) / 1.6) ** 2)));
// Glocke mit einseitigem Ausläufer ab 1,5 mm in +X (Heizpatronenseite):
// innen symmetrisch, aussen kippt es.
const lopsided = grid(124, 126, (x, y) => {
  const b = 12000 * Math.exp(-(((x - 124.2) / 3) ** 2) - (((y - 125.9) / 1.6) ** 2));
  const d = x - 124.2;
  return b + (d > 1.5 ? 900 * (d - 1.5) : 0);
});

// 1: paraboloidFit trifft eine exakte Parabel bei jedem Radius
[0.75, 1.0, 1.5, 2.0, 2.5].forEach(r => {
  const f = F.paraboloidFit(F.imagePoints(parab), 124, 126, r);
  ok(near(f.x, 124.2, 1e-6) && near(f.y, 125.9, 1e-6), 'paraboloidFit trifft die Parabel nicht (r=' + r + ')', JSON.stringify(f));
  ok(near(f.axx, 300, 1e-6) && near(f.ayy, 500, 1e-6), 'paraboloidFit: Kruemmungen (r=' + r + ')', JSON.stringify(f));
  ok(f.rms < 1e-6, 'paraboloidFit: Rest einer exakten Parabel muss 0 sein', String(f.rms));
});
ok(F.paraboloidFit(F.imagePoints(parab), 124, 126, 2.0).n === 49, 'paraboloidFit: Punktzahl im 2-mm-Kreis (0,5-mm-Raster)', String(F.paraboloidFit(F.imagePoints(parab), 124, 126, 2.0).n));

// 2: zu wenige Punkte / Sattel werfen
let threw = false;
try { F.paraboloidFit(F.imagePoints(parab), 124, 126, 0.4); } catch (e) { threw = /Punkt/.test(e.message); }
ok(threw, 'paraboloidFit: < 6 Punkte muss werfen');
threw = false;
const saddle = grid(124, 126, (x, y) => 300 * (x - 124) ** 2 - 300 * (y - 126) ** 2);
try { F.paraboloidFit(F.imagePoints(saddle), 124, 126, 2.0); } catch (e) { threw = /Sattel|Tal/.test(e.message); }
ok(threw, 'paraboloidFit: Sattel muss werfen');

// 3: imagePoints laesst null-Zellen aus und zieht die Basislinie ab
const withNull = grid(124, 126, () => 1000);
withNull.baseline = 400; withNull.values[0][0] = null;
const pts = F.imagePoints(withNull);
ok(pts.length === 168 && pts.every(p => p[2] === 600), 'imagePoints: null weg, Basislinie ab', pts.length + ' ' + pts[0][2]);

// 4: radiusSweep liefert je Radius Scheitel und Punktzahl, Fehler statt Wurf
const sw = F.radiusSweep(bell, [0.75, 1.0, 1.5, 2.0, 2.5, 3.0]);
ok(sw.length === 6 && sw.every(r => typeof r.radius === 'number'), 'radiusSweep: ein Eintrag je Radius');
ok(sw.filter(r => !r.error).length >= 5, 'radiusSweep: Glocke passt bei den meisten Radien', JSON.stringify(sw.map(r => r.error || r.n)));
// 0,5-mm-Gitter um ein Zentrum neben den Gitterpunkten: der Kreis nimmt je
// Radius verschieden viele Punkte je Seite mit -- das streut den Scheitel
// um einige 10 um, ohne Drift. Darum 50 um Toleranz.
ok(sw.every(r => r.error || (near(r.x, 124.2, 0.05) && near(r.y, 125.9, 0.05))),
   'radiusSweep: symmetrische Glocke -> Scheitel bei jedem Radius am Zentrum', JSON.stringify(sw.map(r => r.error || [r.x.toFixed(3), r.y.toFixed(3)])));
const swBad = F.radiusSweep(saddle, [1.0, 2.0]);
ok(swBad.every(r => r.error), 'radiusSweep: Sattel als Fehler je Radius, kein Wurf');

// 5: suggestFitRadius -- symmetrische Glocken bei allen Tools: Scheitel
// stabil ueber alle Radien, Vorschlag = der groesste (meiste Punkte).
const resSym = { ref_tool: 0, '0': { images: [bell] }, '1': { images: [grid(124.5, 121, (x, y) => 9000 * Math.exp(-(((x - 124.6) / 3) ** 2) - (((y - 120.9) / 1.6) ** 2)))] } };
const sug = F.suggestFitRadius(resSym, { radii: [0.75, 1.0, 1.25, 1.5, 1.75, 2.0, 2.5] });
// Abweichungen je Radius (Gitter-Streuung): 1,25 -> 0,9 um (bester), 1,5 ->
// 1,7, 1,75 -> 4,6, 2,0 -> 8,4, 2,5 -> 27. Plateau = bis doppelt so weit wie
// der beste plus 5 um = 6,8 um -> groesster Radius darin ist 1,75.
ok(sug.radius === 1.75, 'suggestFitRadius: stabile Scheitel -> groesster Radius im Plateau', JSON.stringify(sug.rows.map(r => [r.radius, r.devUm, r.ok])));
ok(sug.rows.length === 7 && sug.rows.every(r => typeof r.devUm === 'number' || r.ok === false), 'suggestFitRadius: Tabelle je Radius');
ok(sug.tools.length === 2 && sug.tools[0].tool === '0', 'suggestFitRadius: Tools aufgelistet');
ok(typeof sug.reason === 'string' && sug.reason.length > 10, 'suggestFitRadius: Begruendung');

// 6: einseitiger Auslaeufer ab 1,5 mm: der Scheitel kippt bei grossen
// Radien, der Vorschlag bleibt darunter.
const resLop = { ref_tool: 0, '0': { images: [lopsided] }, '1': { images: [bell] } };
const sug2 = F.suggestFitRadius(resLop, { radii: [0.75, 1.0, 1.25, 1.5, 1.75, 2.0, 2.5, 3.0] });
const rowsLop = sug2.rows.map(r => [r.radius, r.devUm]);
// Der Auslaeufer ab 1,5 mm wiegt erst ab 2,5 mm Radius (52 um bei 3,0):
// 3,0 darf nicht vorgeschlagen werden.
ok(sug2.radius < 3.0 && sug2.rows.find(r => r.radius === 3.0).devUm > 25, 'suggestFitRadius: Auslaeufer -> grosser Radius fliegt raus', JSON.stringify(rowsLop));
const big = sug2.rows.find(r => r.radius === 3.0), small = sug2.rows.find(r => r.radius === 1.25);
ok(big && small && big.devUm > small.devUm, 'suggestFitRadius: Abweichung steigt am Auslaeufer', JSON.stringify(rowsLop));

// 7: ohne Raster -> null mit Begruendung
const none = F.suggestFitRadius({ ref_tool: 0, '0': { images: [{ kind: 'profiles' }] } }, {});
ok(none.radius === null && /Raster/.test(none.reason), 'suggestFitRadius ohne Raster: kein Vorschlag, Grund genannt');

// 8: Punktzahl-Untergrenze: Radien mit < 9 Punkten bei einem Tool sind nicht ok
const sug3 = F.suggestFitRadius(resSym, { radii: [0.75, 2.0], minPoints: 12 });
ok(sug3.rows.find(r => r.radius === 0.75).ok === false && sug3.radius === 2.0, 'suggestFitRadius: zu wenige Punkte -> Radius nicht waehlbar', JSON.stringify(sug3.rows));

console.log(checks + ' Zusicherungen, ' + findings.length + ' Befunde');
findings.forEach(f => console.log('  FEHLT: ' + f));
process.exit(findings.length ? 1 : 0);
