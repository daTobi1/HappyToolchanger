// Fit-Radius selbst ermitteln (Tobi, 2026-09-05): aus den gespeicherten
// Rastern des letzten Laufs (printer.offset.xy_results[t].images) den
// Paraboloid-Scheitel fuer eine Reihe von Radien rechnen und den Radius
// vorschlagen, bei dem der Scheitel am wenigsten vom Radius abhaengt --
// dort sitzt der Fit auf dem Buckel und nicht auf seinen Flanken, und er
// hat trotzdem so viele Punkte wie moeglich.
//
// Der Fit ist derselbe wie in klippy/extras/nozzle_locator_fit.py
// (paraboloid_fit): z = c + gx x + gy y + axx x^2 + ayy y^2 + axy xy ueber
// alle Punkte im Umkreis, Scheitel aus dem Gradienten. Hier ueber das
// gebinnte Gitter (0,5 mm) statt der Rohsamples -- fuer die Radiusfrage
// reicht das (Lauf 12: Abweichung zum Klipper-Fit ~10 um).
//
// Rein und ohne DOM testbar (tests/check_fit_radius.js); Dialog in tools.js.

(function (global) {
  'use strict';

  // Gauss mit Spaltenpivot, A n x n, b n.
  function solve(A, b) {
    var n = b.length, i, j, k;
    var M = A.map(function (row, r) { return row.slice().concat([b[r]]); });
    for (i = 0; i < n; i++) {
      var p = i;
      for (k = i + 1; k < n; k++) if (Math.abs(M[k][i]) > Math.abs(M[p][i])) p = k;
      if (Math.abs(M[p][i]) < 1e-12) throw new Error('Gleichungssystem singulaer');
      var t = M[i]; M[i] = M[p]; M[p] = t;
      for (k = i + 1; k < n; k++) {
        var f = M[k][i] / M[i][i];
        for (j = i; j <= n; j++) M[k][j] -= f * M[i][j];
      }
    }
    var x = new Array(n);
    for (i = n - 1; i >= 0; i--) {
      var s = M[i][n];
      for (j = i + 1; j < n; j++) s -= M[i][j] * x[j];
      x[i] = s / M[i][i];
    }
    return x;
  }

  // points: [[x, y, z], ...]; cx, cy Mitte; radius in mm.
  // -> {x, y, axx, ayy, axy, rho, n, rms}
  function paraboloidFit(points, cx, cy, radius) {
    var rows = [];
    points.forEach(function (p) {
      var x = p[0] - cx, y = p[1] - cy;
      if (x * x + y * y > radius * radius) return;
      rows.push([1, x, y, x * x, y * y, x * y, p[2]]);
    });
    if (rows.length < 6) {
      throw new Error('nur ' + rows.length + ' Punkte im Radius ' + radius + ' mm (mindestens 6)');
    }
    // Normalgleichungen 6 x 6
    var A = [], b = [], i, j;
    for (i = 0; i < 6; i++) { A.push([0, 0, 0, 0, 0, 0]); b.push(0); }
    rows.forEach(function (r) {
      for (i = 0; i < 6; i++) {
        b[i] += r[i] * r[6];
        for (j = 0; j < 6; j++) A[i][j] += r[i] * r[j];
      }
    });
    var c = solve(A, b);
    var gx = c[1], gy = c[2], axx = -c[3], ayy = -c[4], axy = -c[5];
    var det = 4 * axx * ayy - axy * axy;
    if (axx <= 0 || ayy <= 0 || det <= 0) {
      throw new Error('kein Hochpunkt (Sattel oder Tal) im Radius ' + radius + ' mm');
    }
    // Gradient 0: gx - 2 axx x - axy y = 0, gy - 2 ayy y - axy x = 0
    var vx = (2 * ayy * gx - axy * gy) / det;
    var vy = (2 * axx * gy - axy * gx) / det;
    var ss = 0;
    rows.forEach(function (r) {
      var z = c[0] + c[1] * r[1] + c[2] * r[2] + c[3] * r[3] + c[4] * r[4] + c[5] * r[5];
      ss += (z - r[6]) * (z - r[6]);
    });
    return { x: cx + vx, y: cy + vy, axx: axx, ayy: ayy, axy: axy,
             rho: axy / (2 * Math.sqrt(axx * ayy)), n: rows.length,
             rms: Math.sqrt(ss / rows.length) };
  }

  // Raster-Messbild -> Punkte relativ zur Basislinie, null-Zellen weg.
  function imagePoints(image) {
    var base = (typeof image.baseline === 'number') ? image.baseline : 0;
    var out = [];
    image.ys.forEach(function (y, j) {
      image.xs.forEach(function (x, i) {
        var v = image.values[j][i];
        if (v === null || v === undefined) return;
        out.push([x, y, v - base]);
      });
    });
    return out;
  }

  function isRaster(im) {
    return im && im.kind === 'raster' && im.xs && im.ys && im.values;
  }

  // Je Radius den Fit; Fehler landen als {radius, error} statt Wurf.
  function radiusSweep(image, radii) {
    var pts = imagePoints(image);
    var cx = image.x, cy = image.y;
    return radii.map(function (r) {
      try {
        var f = paraboloidFit(pts, cx, cy, r);
        return { radius: r, x: f.x, y: f.y, n: f.n, rms: f.rms };
      } catch (e) {
        return { radius: r, error: e.message };
      }
    });
  }

  var DEFAULT_RADII = [0.75, 1.0, 1.25, 1.5, 1.75, 2.0, 2.25, 2.5, 2.75, 3.0];

  function median(arr) {
    var a = arr.slice().sort(function (p, q) { return p - q; });
    var m = a.length >> 1;
    return a.length % 2 ? a[m] : (a[m - 1] + a[m]) / 2;
  }

  // Vorschlag ueber alle Tools mit Raster (erstes Bild je Tool = kleinster
  // Spalt). Je Tool ist der robuste Bezug der Median der Scheitel ueber
  // alle passenden Radien; je Radius zaehlt die groesste Abweichung davon
  // ueber alle Tools (um). Das 0,5-mm-Gitter streut den Scheitel um einige
  // 10 um je nach Radius, ohne Drift -- deshalb kein Nachbarvergleich,
  // sondern ein Plateau: waehlbar sind Radien, bei denen jedes Tool passt,
  // >= minPoints Punkte hat und die Abweichung <= tolUm bleibt. Gewaehlt
  // wird der GROESSTE Radius im Plateau (meiste Punkte, robust gegen
  // Rauschen); liegt kein Radius im Plateau, der mit der kleinsten
  // Abweichung.
  function suggestFitRadius(results, opts) {
    opts = opts || {};
    var radii = opts.radii || DEFAULT_RADII;
    var minPoints = (opts.minPoints !== undefined) ? opts.minPoints : 9;
    var tolUm = (opts.tolUm !== undefined) ? opts.tolUm : 25;
    var tools = Object.keys(results || {}).filter(function (k) { return /^\d+$/.test(k); })
      .sort(function (a, b) { return parseInt(a, 10) - parseInt(b, 10); })
      .map(function (t) {
        var imgs = (results[t] && Array.isArray(results[t].images)) ? results[t].images : [];
        var im = imgs.filter(isRaster)[0];
        return im ? { tool: t, gap: im.gap, sweep: radiusSweep(im, radii) } : null;
      }).filter(Boolean);
    if (!tools.length) {
      return { radius: null, rows: [], tools: [],
               reason: 'Keine Raster-Messbilder im Ergebnis -- erst einen Lauf mit FIT2D fahren.' };
    }
    tools.forEach(function (t) {
      var good = t.sweep.filter(function (r) { return !r.error; });
      t.medX = good.length ? median(good.map(function (r) { return r.x; })) : null;
      t.medY = good.length ? median(good.map(function (r) { return r.y; })) : null;
    });
    var rows = radii.map(function (r, k) {
      var ok = true, nMin = Infinity, dev = 0, why = '';
      tools.forEach(function (t) {
        var cur = t.sweep[k];
        if (cur.error) { ok = false; why = why || ('T' + t.tool + ': ' + cur.error); return; }
        if (cur.n < nMin) nMin = cur.n;
        if (cur.n < minPoints) { ok = false; why = why || ('T' + t.tool + ': nur ' + cur.n + ' Punkte'); }
        var d = Math.hypot(cur.x - t.medX, cur.y - t.medY) * 1000;
        if (d > dev) dev = d;
      });
      return { radius: r, nMin: isFinite(nMin) ? nMin : 0, devUm: ok ? dev : null, ok: ok, why: why };
    });
    var plateau = rows.filter(function (row) { return row.ok && row.devUm <= tolUm; });
    var best = null, reason;
    if (plateau.length) {
      best = plateau[plateau.length - 1];
      reason = 'Bis ' + best.radius.toFixed(2) + ' mm bleibt der Scheitel bei allen ' + tools.length +
        ' Tools innerhalb ' + tolUm + ' um um seinen Bezug (hier ' + best.devUm.toFixed(0) +
        ' um); groesster Radius im Plateau = meiste Punkte im Fit (mindestens ' + best.nMin + ').';
    } else {
      rows.forEach(function (row) {
        if (row.ok && (!best || row.devUm < best.devUm)) best = row;
      });
      if (!best) {
        return { radius: null, rows: rows, tools: tools,
                 reason: 'Kein Radius, bei dem alle Tools passen (' + (rows[0].why || '') + ').' };
      }
      reason = 'Kein Radius haelt ' + tolUm + ' um; ' + best.radius.toFixed(2) + ' mm hat die kleinste ' +
        'Abweichung (' + best.devUm.toFixed(0) + ' um).';
    }
    return { radius: best.radius, rows: rows, tools: tools, tolUm: tolUm, reason: reason };
  }

  var api = { solve: solve, paraboloidFit: paraboloidFit, imagePoints: imagePoints,
              radiusSweep: radiusSweep, suggestFitRadius: suggestFitRadius,
              DEFAULT_RADII: DEFAULT_RADII };
  if (typeof module !== 'undefined' && module.exports) {
    module.exports = api;
  } else {
    global.NozzleFitRadius = api;
  }
})(typeof window !== 'undefined' ? window : this);
