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
  function mapToSurface(map) {
    if (!map) return null;
    var g = map.grid || map;
    if (!g.xs || !g.ys || !g.values || !g.xs.length || !g.ys.length) return null;
    var base = (typeof map.baseline === 'number') ? map.baseline : 0;
    var z = g.values.map(function (row) {
      return row.map(function (v) { return (v === null || v === undefined) ? null : v - base; });
    });
    var total = map.rows_total || g.ys.length;
    var done = (map.rows_done !== undefined) ? map.rows_done : g.ys.length;
    var progress = total ? done / total : 1;
    var title = (map.label || 'Raster') + ' · Zeile ' + done + ' / ' + total;
    if (map.done) title = (map.label || 'Raster') + ' · fertig (' + total + ' Zeilen)';
    if (typeof map.gap === 'number') title += ' · Spalt ' + map.gap.toFixed(2) + ' mm';
    return { x: g.xs.slice(), y: g.ys.slice(), z: z, progress: progress,
             title: title, centre: [map.x, map.y] };
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
  function renderMap3d(el, map, key) {
    var s = mapToSurface(map);
    if (!s) {
      el.textContent = '';
      return Promise.resolve(false);
    }
    return ensurePlotly().then(function (Plotly) {
      var trace = {
        type: 'surface', x: s.x, y: s.y, z: s.z,
        colorscale: 'Viridis', showscale: true,
        colorbar: { title: 'Hz', thickness: 12, len: 0.6 },
        contours: { z: { show: true, usecolormap: true, project: { z: true } } },
        hovertemplate: 'X %{x:.2f}<br>Y %{y:.2f}<br>%{z:.0f} Hz<extra></extra>'
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
          zaxis: { title: 'Hz ueber Basislinie' },
          aspectmode: 'manual', aspectratio: { x: 1, y: 1, z: 0.6 }
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
