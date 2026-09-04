// Viewer fuer NOZZLE_LOCATOR_MAP (C-Scan der XY-Sonde).
//
// Laedt ein oder zwei Raster-JSONs (Datei oder direkt vom Drucker ueber
// Moonrakers /server/files/logs/) und zeichnet sie als Heatmap. Zwei Raster
// desselben Gitters lassen sich voneinander abziehen -- T0 minus T1 zeigt
// genau das Metall, das nur T0 traegt (Eddy-NG-Sonde).
//
// Die reinen Funktionen (rasterValues, rasterDiff, colorFor, valueRange)
// haengen nicht am DOM, damit `node --check` und ein spaeterer Test sie
// erreichen.

(function (global) {
  'use strict';

  // Werte relativ zur Basislinie (Freiluft), wenn eine vorliegt.
  function rasterValues(map) {
    var base = (map && typeof map.baseline === 'number') ? map.baseline : 0;
    var g = map.grid;
    return g.values.map(function (row) {
      return row.map(function (v) { return (v === null) ? null : v - base; });
    });
  }

  // A - B auf demselben Gitter; null wenn die Gitter nicht passen.
  function rasterDiff(a, b) {
    var ga = a.grid, gb = b.grid;
    var same = ga.xs.length === gb.xs.length && ga.ys.length === gb.ys.length
      && ga.xs.every(function (x, i) { return Math.abs(x - gb.xs[i]) < 0.05; })
      && ga.ys.every(function (y, j) { return Math.abs(y - gb.ys[j]) < 0.05; });
    if (!same) return null;
    var va = rasterValues(a), vb = rasterValues(b);
    return va.map(function (row, j) {
      return row.map(function (v, i) {
        var w = vb[j][i];
        return (v === null || w === null) ? null : v - w;
      });
    });
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
    if (lo === Infinity) return [0, 1];
    if (hi === lo) hi = lo + 1;
    return [lo, hi];
  }

  // Vorzeichenbehafteter Logarithmus fuer die Anzeige: der Heizblock liegt
  // bei +100.000 Hz, die Duese bei +6.000 -- linear ist die Duese dann ein
  // blasser Fleck neben einer weissen Wand.
  function transform(v, mode) {
    if (v === null) return null;
    if (mode === 'log') return Math.sign(v) * Math.log10(1 + Math.abs(v));
    return v;
  }

  // Viridis-aehnliche Rampe aus wenigen Stuetzstellen.
  var STOPS = [
    [0.00, [68, 1, 84]], [0.25, [59, 82, 139]], [0.50, [33, 145, 140]],
    [0.75, [94, 201, 98]], [1.00, [253, 231, 37]]
  ];
  // Divergierend fuer Differenzbilder: blau - weiss - rot.
  var DIV = [[0.0, [33, 102, 172]], [0.5, [247, 247, 247]], [1.0, [178, 24, 43]]];

  function colorFor(t, stops) {
    if (t <= 0) return stops[0][1];
    if (t >= 1) return stops[stops.length - 1][1];
    for (var k = 1; k < stops.length; k++) {
      if (t <= stops[k][0]) {
        var a = stops[k - 1], b = stops[k];
        var f = (t - a[0]) / (b[0] - a[0]);
        return [0, 1, 2].map(function (c) {
          return Math.round(a[1][c] + f * (b[1][c] - a[1][c]));
        });
      }
    }
    return stops[stops.length - 1][1];
  }

  function rgb(c) { return 'rgb(' + c[0] + ',' + c[1] + ',' + c[2] + ')'; }

  // ---------------------------------------------------------------- DOM
  var state = { a: null, b: null, mode: 'a', scale: 'linear', hover: null };

  function $(id) { return document.getElementById(id); }

  function setStatus(msg, bad) {
    var el = $('status');
    el.textContent = msg;
    el.className = bad ? 'text-danger' : 'text-muted';
  }

  function describe(map) {
    if (!map) return '–';
    var d = new Date((map.time || 0) * 1000);
    return (map.label ? map.label + ' · ' : '') +
      map.grid.ys.length + '×' + map.grid.xs.length + ' Zellen, Raster ' +
      map.pitch + ' mm, Spalt ' + (map.gap != null ? map.gap.toFixed(2) : '?') +
      ' mm, ' + map.speed + ' mm/s' +
      (map.baseline ? ', Basislinie ' + Math.round(map.baseline) + ' Hz' : '') +
      (map.coil_temp != null ? ', Spule ' + map.coil_temp.toFixed(1) + ' °C' : '') +
      ' · ' + d.toLocaleString();
  }

  function currentValues() {
    if (state.mode === 'diff') {
      if (!state.a || !state.b) return null;
      var d = rasterDiff(state.a, state.b);
      if (!d) { setStatus('Die beiden Raster liegen nicht auf demselben Gitter.', true); return null; }
      return { values: d, grid: state.a.grid, map: state.a, diverging: true };
    }
    var m = state[state.mode];
    if (!m) return null;
    return { values: rasterValues(m), grid: m.grid, map: m, diverging: false };
  }

  // 3D-Flaeche (js/map3d.js) fuer die aktuelle Anzeige: A, B oder A - B.
  // Fuer die Differenz ein Pseudo-Raster ohne Basislinie.
  function draw3d(cur) {
    var el = $('map3d');
    if (!el || !state.show3d || typeof NozzleMap3d === 'undefined') return;
    if (!cur) { el.textContent = ''; return; }
    var pseudo = { label: (state.mode === 'diff') ? 'A − B' : (cur.map.label || 'Raster'),
                   baseline: 0, x: cur.map.x, y: cur.map.y, gap: cur.map.gap,
                   done: true, rows_total: cur.grid.ys.length, rows_done: cur.grid.ys.length,
                   xs: cur.grid.xs, ys: cur.grid.ys, values: cur.values };
    NozzleMap3d.renderMap3d(el, pseudo, 'map-' + state.mode + '-' + state.scale,
                            { log: state.scale === 'log' });
  }

  function draw() {
    var cur = currentValues();
    var canvas = $('map');
    var ctx = canvas.getContext('2d');
    ctx.clearRect(0, 0, canvas.width, canvas.height);
    $('legend').innerHTML = '';
    draw3d(cur);
    if (!cur) return;

    var vals = cur.values.map(function (row) {
      return row.map(function (v) { return transform(v, state.scale); });
    });
    var range = valueRange(vals);
    var lo = range[0], hi = range[1];
    var stops = STOPS;
    if (cur.diverging) {
      var m = Math.max(Math.abs(lo), Math.abs(hi));
      lo = -m; hi = m; stops = DIV;
    }

    var xs = cur.grid.xs, ys = cur.grid.ys;
    var margin = { l: 50, r: 20, t: 20, b: 40 };
    var w = canvas.width - margin.l - margin.r;
    var h = canvas.height - margin.t - margin.b;
    var cw = w / xs.length, ch = h / ys.length;

    // y aufsteigend nach oben zeichnen (Bettkoordinaten)
    for (var j = 0; j < ys.length; j++) {
      for (var i = 0; i < xs.length; i++) {
        var v = vals[j][i];
        var px = margin.l + i * cw;
        var py = margin.t + (ys.length - 1 - j) * ch;
        if (v === null) {
          ctx.fillStyle = '#ddd';
        } else {
          ctx.fillStyle = rgb(colorFor((v - lo) / (hi - lo), stops));
        }
        ctx.fillRect(px, py, Math.ceil(cw), Math.ceil(ch));
      }
    }

    // Achsen
    ctx.fillStyle = '#333';
    ctx.font = '11px sans-serif';
    ctx.textAlign = 'center';
    var pitch = cur.map.pitch || 1;
    var everyX = Math.max(1, Math.round(5 / pitch));
    for (var i2 = 0; i2 < xs.length; i2 += everyX) {
      ctx.fillText(xs[i2].toFixed(1), margin.l + (i2 + 0.5) * cw, canvas.height - margin.b + 14);
    }
    ctx.textAlign = 'right';
    for (var j2 = 0; j2 < ys.length; j2 += everyX) {
      ctx.fillText(ys[j2].toFixed(1), margin.l - 4, margin.t + (ys.length - 1 - j2 + 0.5) * ch + 4);
    }
    ctx.textAlign = 'center';
    ctx.fillText('X (mm)', margin.l + w / 2, canvas.height - 6);
    ctx.save();
    ctx.translate(12, margin.t + h / 2);
    ctx.rotate(-Math.PI / 2);
    ctx.fillText('Y (mm)', 0, 0);
    ctx.restore();

    // Kreuz auf der Rastermitte (= Position der Duese beim Start) und
    // Kreis auf dem Maximum
    var map = cur.map;
    function toPx(x, y) {
      var fx = (x - (xs[0] - pitch / 2)) / (xs.length * pitch);
      var fy = (y - (ys[0] - pitch / 2)) / (ys.length * pitch);
      return [margin.l + fx * w, margin.t + (1 - fy) * h];
    }
    var c = toPx(map.x, map.y);
    ctx.strokeStyle = '#fff';
    ctx.lineWidth = 1.5;
    ctx.beginPath();
    ctx.moveTo(c[0] - 8, c[1]); ctx.lineTo(c[0] + 8, c[1]);
    ctx.moveTo(c[0], c[1] - 8); ctx.lineTo(c[0], c[1] + 8);
    ctx.stroke();
    var best = null;
    vals.forEach(function (row, j3) {
      row.forEach(function (v, i3) {
        if (v !== null && (best === null || v > best[0])) best = [v, i3, j3];
      });
    });
    if (best && !cur.diverging) {
      var p = toPx(xs[best[1]], ys[best[2]]);
      ctx.beginPath();
      ctx.arc(p[0], p[1], 7, 0, 2 * Math.PI);
      ctx.stroke();
      $('peak').textContent = 'Maximum ' + Math.round(cur.values[best[2]][best[1]]) +
        ' Hz bei X ' + xs[best[1]].toFixed(2) + ' / Y ' + ys[best[2]].toFixed(2) +
        ' (Rastermitte X ' + map.x.toFixed(2) + ' / Y ' + map.y.toFixed(2) + ')';
    } else {
      $('peak').textContent = '';
    }

    // Legende
    var leg = $('legend');
    var bar = document.createElement('div');
    bar.className = 'legend-bar';
    var grad = [];
    for (var k = 0; k <= 10; k++) grad.push(rgb(colorFor(k / 10, stops)));
    bar.style.background = 'linear-gradient(to right,' + grad.join(',') + ')';
    leg.appendChild(bar);
    var lab = document.createElement('div');
    lab.className = 'd-flex justify-content-between small';
    var fmt = function (t) {
      if (state.scale === 'log') return Math.round(Math.sign(t) * (Math.pow(10, Math.abs(t)) - 1)) + ' Hz';
      return Math.round(t) + ' Hz';
    };
    lab.innerHTML = '<span>' + fmt(lo) + '</span><span>' + fmt(hi) + '</span>';
    leg.appendChild(lab);

    // Hover
    canvas.onmousemove = function (ev) {
      var r = canvas.getBoundingClientRect();
      var mx = (ev.clientX - r.left) * canvas.width / r.width;
      var my = (ev.clientY - r.top) * canvas.height / r.height;
      var i4 = Math.floor((mx - margin.l) / cw);
      var j4 = ys.length - 1 - Math.floor((my - margin.t) / ch);
      if (i4 < 0 || j4 < 0 || i4 >= xs.length || j4 >= ys.length) { $('hover').textContent = ''; return; }
      var v4 = cur.values[j4][i4];
      $('hover').textContent = 'X ' + xs[i4].toFixed(2) + ' / Y ' + ys[j4].toFixed(2) + ': ' +
        (v4 === null ? 'keine Daten' : Math.round(v4) + ' Hz');
    };
  }

  function acceptMap(slot, map, source) {
    if (!map || map.kind !== 'nozzle_locator_map' || !map.grid) {
      setStatus(source + ': kein NOZZLE_LOCATOR_MAP-JSON', true);
      return;
    }
    state[slot] = map;
    $('desc-' + slot).textContent = describe(map);
    setStatus(source + ' geladen');
    draw();
  }

  function loadFile(slot, file) {
    var rd = new FileReader();
    rd.onload = function () {
      try { acceptMap(slot, JSON.parse(rd.result), file.name); }
      catch (e) { setStatus(file.name + ': ' + e, true); }
    };
    rd.readAsText(file);
  }

  function printerBase() {
    var ip = $('ip').value.trim();
    return ip ? 'http://' + ip + ':7125' : '';
  }

  function loadFromPrinter(slot) {
    var name = $('file-' + slot).value.trim();
    var base = printerBase();
    if (!base || !name) { setStatus('IP und Dateiname angeben', true); return; }
    fetch(base + '/server/files/logs/' + encodeURIComponent(name))
      .then(function (r) { if (!r.ok) throw new Error('HTTP ' + r.status); return r.json(); })
      .then(function (j) { acceptMap(slot, j, name); })
      .catch(function (e) { setStatus(name + ': ' + e, true); });
  }

  function listMaps() {
    var base = printerBase();
    if (!base) { setStatus('IP angeben', true); return; }
    fetch(base + '/server/files/list?root=logs')
      .then(function (r) { return r.json(); })
      .then(function (j) {
        var names = (j.result || []).map(function (f) { return f.path; })
          .filter(function (p) { return /^nozzle_locator_map.*\.json$/.test(p); })
          .sort().reverse();
        var sel = $('maps');
        sel.innerHTML = '';
        names.forEach(function (n) {
          var o = document.createElement('option');
          o.value = n; o.textContent = n;
          sel.appendChild(o);
        });
        sel.hidden = names.length === 0;
        setStatus(names.length + ' Raster auf dem Drucker');
        if (names.length) {
          $('file-a').value = names[0];
          if (names.length > 1) $('file-b').value = names[1];
        }
      })
      .catch(function (e) { setStatus('Liste: ' + e, true); });
  }

  function init() {
    try {
      var q = new URLSearchParams(location.search);
      if (q.get('ip')) $('ip').value = q.get('ip');
      if (q.get('a')) $('file-a').value = q.get('a');
      if (q.get('b')) $('file-b').value = q.get('b');
    } catch (e) { /* egal */ }
    $('pick-a').onchange = function (ev) { if (ev.target.files[0]) loadFile('a', ev.target.files[0]); };
    $('pick-b').onchange = function (ev) { if (ev.target.files[0]) loadFile('b', ev.target.files[0]); };
    $('load-a').onclick = function () { loadFromPrinter('a'); };
    $('load-b').onclick = function () { loadFromPrinter('b'); };
    $('list').onclick = listMaps;
    $('maps').onchange = function (ev) { $('file-a').value = ev.target.value; };
    document.querySelectorAll('input[name=mode]').forEach(function (r) {
      r.onchange = function () { state.mode = r.value; draw(); };
    });
    document.querySelectorAll('input[name=scale]').forEach(function (r) {
      r.onchange = function () { state.scale = r.value; draw(); };
    });
    $('show3d').onclick = function () {
      state.show3d = !state.show3d;
      $('map3d').style.height = state.show3d ? '480px' : '0';
      $('show3d').textContent = state.show3d ? '3D ausblenden' : '3D-Ansicht';
      draw();
    };
    if ($('ip').value && $('file-a').value) loadFromPrinter('a');
    if ($('ip').value && $('file-b').value) loadFromPrinter('b');
  }

  var api = {
    rasterValues: rasterValues, rasterDiff: rasterDiff, colorFor: colorFor,
    valueRange: valueRange, transform: transform, init: init
  };
  if (typeof module !== 'undefined' && module.exports) {
    module.exports = api;            // node: tests/check_nozzle_map.js
  } else {
    global.NozzleMap = api;
    document.addEventListener('DOMContentLoaded', init);
  }
})(typeof window !== 'undefined' ? window : this);
