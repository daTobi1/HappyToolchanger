// Ueberlagerungs-Editor fuer die Messbilder der XY-Sonde (Tobi, 2026-09-04):
// zwei Raster -- je ein Tool und ein Spalt aus printer.offset.xy_results --
// uebereinander legen. Jedes Raster liegt in Maschinenkoordinaten um die
// eigene Duese; damit die Buckel aufeinander liegen, wird B auf den
// gemessenen Scheitel von A geschoben (alignShift) und darf zusaetzlich um
// dx/dy verschoben werden. Wo die Buckel nach dem Verschieben um den
// gemessenen Offset NICHT deckungsgleich sind, stimmt die Messung (oder
// die Duese) nicht.
//
// 2D: Hoehenlinien (Marching Squares) beider Ebenen in zwei Farben plus
// Scheitelmarker -- das gl3d-Bundle von plotly hat keinen contour-Typ,
// Linien als scatter kann jedes Bundle. 3D: zwei Flaechen, B halb
// durchsichtig. Alles hier ist rein und ohne DOM testbar
// (tests/check_nozzle_overlay.js); das Zeichnen macht tools.js.

(function (global) {
  'use strict';

  var COLOR_A = '#1f77b4', COLOR_B = '#d62728';

  // Messbild (xy_results[t].images[i], kind 'raster') -> Ebene.
  // Werte relativ zur Basislinie, null bleibt null. vx/vy ist der Scheitel
  // des 2D-Fits in diesem Spalt (image.x/y), nicht die Rastermitte.
  // tip (optional): der auf Spalt 0 extrapolierte Spitzenpunkt des Tools
  // (xy_results[t].x_peak/y_peak) -- nicht der Scheitel dieses Rasters,
  // sondern das Ergebnis ueber alle Spalte. Wird getrennt gezeichnet.
  function layerFromImage(image, label, tip) {
    if (!image || image.kind !== 'raster' || !image.xs || !image.ys || !image.values) return null;
    var base = (typeof image.baseline === 'number') ? image.baseline : 0;
    var hasTip = tip && typeof tip.x === 'number' && typeof tip.y === 'number';
    return {
      xs: image.xs.slice(), ys: image.ys.slice(),
      values: image.values.map(function (row) {
        return row.map(function (v) { return (v === null || v === undefined) ? null : v - base; });
      }),
      vx: image.x, vy: image.y, gap: image.gap, label: label || '',
      tx: hasTip ? tip.x : null, ty: hasTip ? tip.y : null
    };
  }

  function hasTip(layer) {
    return typeof layer.tx === 'number' && typeof layer.ty === 'number';
  }

  // Verschiebung, die den Scheitel von B auf den von A legt.
  function alignShift(a, b) {
    return { dx: a.vx - b.vx, dy: a.vy - b.vy };
  }

  function shiftLayer(layer, dx, dy) {
    return {
      xs: layer.xs.map(function (x) { return x + dx; }),
      ys: layer.ys.map(function (y) { return y + dy; }),
      values: layer.values, vx: layer.vx + dx, vy: layer.vy + dy,
      gap: layer.gap, label: layer.label,
      tx: hasTip(layer) ? layer.tx + dx : null,
      ty: hasTip(layer) ? layer.ty + dy : null
    };
  }

  function maxAbs(values) {
    var m = 0;
    values.forEach(function (row) {
      row.forEach(function (v) { if (v !== null && Math.abs(v) > m) m = Math.abs(v); });
    });
    return m;
  }

  // Spitze auf 1: zwei Spalte oder zwei Duesen geben verschieden viel
  // Signal, die Form ist das Interessante.
  function normalizeLayer(layer) {
    var m = maxAbs(layer.values);
    var f = m > 0 ? 1 / m : 1;
    return {
      xs: layer.xs, ys: layer.ys,
      values: layer.values.map(function (row) {
        return row.map(function (v) { return (v === null) ? null : v * f; });
      }),
      vx: layer.vx, vy: layer.vy, gap: layer.gap, label: layer.label,
      tx: layer.tx, ty: layer.ty
    };
  }

  function valueRange(values) {
    var lo = Infinity, hi = -Infinity;
    values.forEach(function (row) {
      row.forEach(function (v) {
        if (v === null) return;
        if (v < lo) lo = v;
        if (v > hi) hi = v;
      });
    });
    return (lo === Infinity) ? [0, 1] : [lo, hi];
  }

  // n Stufen zwischen 15 % und 95 % des Wertebereichs.
  function contourLevels(values, n) {
    var r = valueRange(values), lo = r[0], hi = r[1];
    var out = [];
    for (var i = 0; i < n; i++) {
      out.push(lo + (hi - lo) * (0.15 + 0.8 * i / Math.max(1, n - 1)));
    }
    return out;
  }

  // Marching Squares: Segmente [[x1,y1],[x2,y2]] der Hoehenlinie `level`.
  // Zellen mit null werden uebersprungen. Lineare Interpolation auf den
  // Kanten; der Sattelfall (5/10) wird ueber den Mittelwert aufgeloest.
  function contourSegments(xs, ys, values, level) {
    var segs = [];
    function lerp(p, q, vp, vq) {
      var t = (vq === vp) ? 0.5 : (level - vp) / (vq - vp);
      return [p[0] + t * (q[0] - p[0]), p[1] + t * (q[1] - p[1])];
    }
    for (var j = 0; j + 1 < ys.length; j++) {
      for (var i = 0; i + 1 < xs.length; i++) {
        var v00 = values[j][i], v10 = values[j][i + 1],
            v11 = values[j + 1][i + 1], v01 = values[j + 1][i];
        if (v00 === null || v10 === null || v11 === null || v01 === null) continue;
        var p00 = [xs[i], ys[j]], p10 = [xs[i + 1], ys[j]],
            p11 = [xs[i + 1], ys[j + 1]], p01 = [xs[i], ys[j + 1]];
        var idx = (v00 >= level ? 1 : 0) | (v10 >= level ? 2 : 0) |
                  (v11 >= level ? 4 : 0) | (v01 >= level ? 8 : 0);
        if (idx === 0 || idx === 15) continue;
        // Kanten: 0 unten (p00-p10), 1 rechts (p10-p11), 2 oben (p01-p11), 3 links (p00-p01)
        var e = [lerp(p00, p10, v00, v10), lerp(p10, p11, v10, v11),
                 lerp(p01, p11, v01, v11), lerp(p00, p01, v00, v01)];
        var table = {
          1: [[3, 0]], 2: [[0, 1]], 3: [[3, 1]], 4: [[1, 2]], 6: [[0, 2]], 7: [[3, 2]],
          8: [[2, 3]], 9: [[0, 2]], 11: [[1, 2]], 12: [[1, 3]], 13: [[0, 1]], 14: [[3, 0]]
        };
        var pairs = table[idx];
        if (!pairs) {
          var mid = (v00 + v10 + v11 + v01) / 4;
          if (idx === 5) pairs = (mid >= level) ? [[3, 2], [0, 1]] : [[3, 0], [1, 2]];
          else pairs = (mid >= level) ? [[0, 1], [2, 3]] : [[0, 3], [1, 2]];
          // idx 10: Ecken p10 und p01 ueber der Stufe
          if (idx === 10) pairs = (mid >= level) ? [[3, 0], [1, 2]] : [[0, 1], [2, 3]];
        }
        pairs.forEach(function (pr) { segs.push([e[pr[0]], e[pr[1]]]); });
      }
    }
    return segs;
  }

  function segmentsToXY(segs) {
    var x = [], y = [];
    segs.forEach(function (s) {
      x.push(s[0][0], s[1][0], null);
      y.push(s[0][1], s[1][1], null);
    });
    return { x: x, y: y };
  }

  function lineTrace(layer, color, nLevels) {
    var levels = contourLevels(layer.values, nLevels || 6);
    var segs = [];
    levels.forEach(function (lv) {
      segs = segs.concat(contourSegments(layer.xs, layer.ys, layer.values, lv));
    });
    var xy = segmentsToXY(segs);
    return { type: 'scatter', mode: 'lines', x: xy.x, y: xy.y, name: layer.label,
             line: { color: color, width: 1.5 }, hoverinfo: 'skip' };
  }

  function markerTrace(layer, color) {
    return { type: 'scatter', mode: 'markers', x: [layer.vx], y: [layer.vy],
             name: layer.label + ' Scheitel', marker: { color: color, size: 10, symbol: 'x' },
             hovertemplate: 'X %{x:.3f}<br>Y %{y:.3f}<extra>' + layer.label + '</extra>' };
  }

  // Spitzenpunkt (Extrapolation auf Spalt 0) als Stern, umrandet, damit er
  // sich vom Kreuz des Raster-Scheitels unterscheidet.
  function tipTrace(layer, color) {
    return { type: 'scatter', mode: 'markers', x: [layer.tx], y: [layer.ty],
             name: layer.label + ' Spitze',
             marker: { color: color, size: 14, symbol: 'star', line: { color: '#fff', width: 1 } },
             hovertemplate: 'Spitze X %{x:.3f}<br>Y %{y:.3f}<extra>' + layer.label + '</extra>' };
  }

  // Zwei Ebenen als Hoehenlinien (A blau, B rot), Scheitel als Kreuz,
  // Spitzenpunkt als Stern.
  function overlayTraces2d(a, b, opts) {
    opts = opts || {};
    var data = [lineTrace(a, COLOR_A, opts.levels)];
    if (b) data.push(lineTrace(b, COLOR_B, opts.levels));
    data.push(markerTrace(a, COLOR_A));
    if (b) data.push(markerTrace(b, COLOR_B));
    if (hasTip(a)) data.push(tipTrace(a, COLOR_A));
    if (b && hasTip(b)) data.push(tipTrace(b, COLOR_B));
    var layout = {
      margin: { l: 50, r: 10, t: 10, b: 40 },
      xaxis: { title: 'X (mm)' },
      yaxis: { title: 'Y (mm)', scaleanchor: 'x', scaleratio: 1 },
      legend: { orientation: 'h', y: -0.15 },
      uirevision: opts.key || 'overlay'
    };
    return { data: data, layout: layout };
  }

  function surfaceTrace(layer, colorscale, opacity, showscale) {
    return { type: 'surface', x: layer.xs, y: layer.ys, z: layer.values,
             colorscale: colorscale, opacity: opacity, showscale: !!showscale,
             name: layer.label, hovertemplate: 'X %{x:.2f}<br>Y %{y:.2f}<br>%{z:.3f}<extra>' + layer.label + '</extra>' };
  }

  function zRange(layers) {
    var lo = Infinity, hi = -Infinity;
    layers.forEach(function (l) {
      l.values.forEach(function (row) {
        row.forEach(function (v) {
          if (v === null) return;
          if (v < lo) lo = v;
          if (v > hi) hi = v;
        });
      });
    });
    return (lo === Infinity) ? [0, 1] : [lo, hi];
  }

  // Senkrechtes Lot durch den Spitzenpunkt, ueber die ganze Hoehe.
  function tipLine3d(layer, color, zr) {
    return { type: 'scatter3d', mode: 'lines+markers',
             x: [layer.tx, layer.tx], y: [layer.ty, layer.ty], z: [zr[0], zr[1]],
             name: layer.label + ' Spitze',
             line: { color: color, width: 6 }, marker: { size: 4, color: color },
             hovertemplate: 'Spitze X %{x:.3f}<br>Y %{y:.3f}<extra>' + layer.label + '</extra>' };
  }

  // Zwei Flaechen: A deckend in Blau-Toenen, B halbdurchsichtig in Rot;
  // Spitzenpunkte als senkrechte Lote.
  function overlayTraces3d(a, b, opts) {
    opts = opts || {};
    var data = [surfaceTrace(a, 'Blues', 1, false)];
    if (b) data.push(surfaceTrace(b, 'Reds', (opts.opacity !== undefined) ? opts.opacity : 0.6, false));
    var zr = zRange(b ? [a, b] : [a]);
    if (hasTip(a)) data.push(tipLine3d(a, COLOR_A, zr));
    if (b && hasTip(b)) data.push(tipLine3d(b, COLOR_B, zr));
    var layout = {
      margin: { l: 0, r: 0, t: 10, b: 0 },
      uirevision: opts.key || 'overlay3d',
      scene: { xaxis: { title: 'X (mm)' }, yaxis: { title: 'Y (mm)' },
               zaxis: { title: opts.zlabel || 'Hoehe' },
               camera: { eye: { x: 1.2, y: -1.7, z: 0.9 } } }
    };
    return { data: data, layout: layout };
  }

  // Auswahlliste fuer die Ebenen aus xy_results: je Tool und Raster-Bild
  // ein Eintrag {tool, index, label}.
  function layerOptions(results) {
    var out = [];
    Object.keys(results || {}).filter(function (k) { return /^\d+$/.test(k); })
      .sort(function (p, q) { return parseInt(p, 10) - parseInt(q, 10); })
      .forEach(function (t) {
        var imgs = (results[t] && Array.isArray(results[t].images)) ? results[t].images : [];
        imgs.forEach(function (im, i) {
          if (!im || im.kind !== 'raster') return;
          var gap = (typeof im.gap === 'number') ? im.gap.toFixed(2) + ' mm' : '?';
          out.push({ tool: t, index: i, label: 'T' + t + ' Spalt ' + gap });
        });
      });
    return out;
  }

  var api = {
    layerFromImage: layerFromImage, alignShift: alignShift, shiftLayer: shiftLayer,
    normalizeLayer: normalizeLayer, contourLevels: contourLevels,
    contourSegments: contourSegments, segmentsToXY: segmentsToXY,
    overlayTraces2d: overlayTraces2d, overlayTraces3d: overlayTraces3d,
    layerOptions: layerOptions, COLOR_A: COLOR_A, COLOR_B: COLOR_B
  };
  if (typeof module !== 'undefined' && module.exports) {
    module.exports = api;
  } else {
    global.NozzleOverlay = api;
  }
})(typeof window !== 'undefined' ? window : this);
