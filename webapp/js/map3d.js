// Live-3D-Ansicht eines Rasters (NOZZLE_LOCATOR_MAP) als Hoehenflaeche.
//
// Datenquelle ist printer.nozzle_locator.map: Klipper legt nach jeder
// gescannten Zeile das Gitter bis dahin in den Status, die Webapp pollt es
// alle 2 s und zeichnet die Flaeche nach -- man sieht das Bild Zeile fuer
// Zeile entstehen. Dieselbe Funktion zeichnet ein fertiges Raster aus der
// JSON-Datei (webapp/map.html).
//
// Zeichnen uebernimmt plotly.js (gl3d-Bundle, ~1 MB), das erst beim ersten
// Bild vom CDN nachgeladen wird. Ohne Netz bleibt die 2D-Heatmap in
// map.html der Weg; das Panel sagt dann, warum nichts erscheint.
//
// mapToSurface() ist rein und ohne DOM testbar (tests/check_nozzle_map.js).

(function (global) {
  'use strict';

  var PLOTLY_URL = 'https://cdn.jsdelivr.net/npm/plotly.js-gl3d-dist-min@2.35.2/plotly-gl3d.min.js';

  // Klipper-Status (oder Datei-JSON mit .grid) -> Plotly-Flaeche.
  // z relativ zur Basislinie, null bleibt null (Plotly laesst die Zelle
  // aus). progress 0..1, title mit Label und Zeilenstand.
  //
  // opts.log: vorzeichenbehafteter log10(1+|z|). Der Heizblock am
  // Rasterrand ist ~12x hoeher als der Duesenbuckel -- linear sieht man
  // nur seine Flanke wie einen Schnitt, die Duese liegt flach am Boden.
  // aspect: Seitenverhaeltnis aus den Rastermassen, damit 20 x 30 mm
  // nicht zum Quadrat verzerrt wird.
  function mapToSurface(map, opts) {
    opts = opts || {};
    if (!map) return null;
    var g = map.grid || map;
    if (!g.xs || !g.ys || !g.values || !g.xs.length || !g.ys.length) return null;
    var base = (typeof map.baseline === 'number') ? map.baseline : 0;
    var z = g.values.map(function (row) {
      return row.map(function (v) {
        if (v === null || v === undefined) return null;
        var d = v - base;
        return opts.log ? Math.sign(d) * Math.log10(1 + Math.abs(d)) : d;
      });
    });
    var total = map.rows_total || g.ys.length;
    var done = (map.rows_done !== undefined) ? map.rows_done : g.ys.length;
    var progress = total ? done / total : 1;
    var title = (map.label || 'Raster') + ' · Zeile ' + done + ' / ' + total;
    if (map.done) title = (map.label || 'Raster') + ' · fertig (' + total + ' Zeilen)';
    if (typeof map.gap === 'number') title += ' · Spalt ' + map.gap.toFixed(2) + ' mm';
    var xr = g.xs[g.xs.length - 1] - g.xs[0], yr = g.ys[g.ys.length - 1] - g.ys[0];
    var aspect = { x: 1, y: (xr > 0 && yr > 0) ? yr / xr : 1, z: 0.6 };
    return { x: g.xs.slice(), y: g.ys.slice(), z: z, progress: progress,
             title: title, centre: [map.x, map.y], aspect: aspect,
             zlabel: opts.log ? 'log10(Hz ueber Basislinie)' : 'Hz ueber Basislinie' };
  }

  var _loading = null;
  function ensurePlotly() {
    if (global.Plotly) return Promise.resolve(global.Plotly);
    if (_loading) return _loading;
    _loading = new Promise(function (resolve, reject) {
      var s = document.createElement('script');
      s.src = PLOTLY_URL;
      s.onload = function () { resolve(global.Plotly); };
      s.onerror = function () { _loading = null; reject(new Error('plotly.js nicht ladbar (' + PLOTLY_URL + ')')); };
      document.head.appendChild(s);
    });
    return _loading;
  }

  // Zeichnet oder aktualisiert die Flaeche in `el`. `key` haelt die
  // Kameraposition ueber Aktualisierungen hinweg (Plotly uirevision).
  // opts.log wie bei mapToSurface. Kamera schaut von vorn (-Y) schraeg
  // auf das Raster, damit der Block-Huegel die Duese nicht verdeckt.
  function renderMap3d(el, map, key, opts) {
    opts = opts || {};
    var s = mapToSurface(map, opts);
    if (!s) {
      el.textContent = '';
      return Promise.resolve(false);
    }
    return ensurePlotly().then(function (Plotly) {
      var trace = {
        type: 'surface', x: s.x, y: s.y, z: s.z,
        colorscale: 'Viridis', showscale: true,
        colorbar: { title: opts.log ? 'log10' : 'Hz', thickness: 12, len: 0.6 },
        contours: { z: { show: true, usecolormap: true, project: { z: true } } },
        hovertemplate: 'X %{x:.2f}<br>Y %{y:.2f}<br>%{z:.2f}' +
                       (opts.log ? ' (log10)' : ' Hz') + '<extra></extra>'
      };
      var data = [trace];
      if (s.centre && isFinite(s.centre[0]) && isFinite(s.centre[1])) {
        // Senkrechte durch die Rastermitte (Duesenposition beim Start)
        var zs = [].concat.apply([], s.z).filter(function (v) { return v !== null; });
        var zlo = zs.length ? Math.min.apply(null, zs) : 0;
        var zhi = zs.length ? Math.max.apply(null, zs) : 1;
        data.push({ type: 'scatter3d', mode: 'lines',
                    x: [s.centre[0], s.centre[0]], y: [s.centre[1], s.centre[1]],
                    z: [zlo, zhi], line: { color: '#fff', width: 4 },
                    hoverinfo: 'skip', showlegend: false });
      }
      var layout = {
        title: { text: s.title, font: { size: 13 } },
        margin: { l: 0, r: 0, t: 30, b: 0 },
        uirevision: key || 'map',
        scene: {
          xaxis: { title: 'X (mm)' }, yaxis: { title: 'Y (mm)' },
          zaxis: { title: s.zlabel },
          aspectmode: 'manual', aspectratio: s.aspect,
          camera: { eye: { x: 1.2, y: -1.7, z: 0.9 } }
        }
      };
      return Plotly.react(el, data, layout, { responsive: true, displaylogo: false })
        .then(function () { return true; });
    }, function (e) {
      el.textContent = '3D-Ansicht nicht verfuegbar: ' + e.message +
        ' -- die 2D-Heatmap in map.html braucht kein CDN.';
      return false;
    });
  }

  var api = { mapToSurface: mapToSurface, renderMap3d: renderMap3d,
              ensurePlotly: ensurePlotly, PLOTLY_URL: PLOTLY_URL };
  if (typeof module !== 'undefined' && module.exports) {
    module.exports = api;
  } else {
    global.NozzleMap3d = api;
  }
})(typeof window !== 'undefined' ? window : this);
