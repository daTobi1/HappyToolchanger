/* =========================================================
   Offset tools.js (Global Master + Config-default Z calc)
   - Fixes: updateTools is defined (used by index.js)
   - Z calc dropdown:
       Default = Config (offset.cfg)
       Label shows offset status z_calc_method (e.g. trimmed)
       Only sends Z_CALC if user explicitly overrides
   ========================================================= */

let offsetMasterTool = null;
let _probeInterval = null;

// Offset status cache
let _offsetPresent = false;
let _offsetZCalcDefault = null; // "median" | "average" | "trimmed" | null

// Remember UI dropdown selection across rerenders
let _uiZCalcSelection = "config"; // "config" | "median" | "average" | "trimmed"

// Probe calibration state
let _availableProbes = [];    // ["probe", "probe_eddy_ng my_eddy"]
let _probeCalConfig = null;   // { ref_tool, ref_probe, tool_probes: { "0": "probe", ... } }
let _toolProbeOffsets = {};    // { "0": 0.05, "1": -0.02, ... } current tool_probe z_offsets
let _probeCalResults = {};     // { "0": { probe_z_offset: 0.05 }, ... } from probe_results
let _eddyTapDeviations = {};   // { "0": { deviation: 0.01, probe: "..." } } — info only, not applicable
let _pidResults = {};        // { "0": {pid_kp, pid_ki, pid_kd, temp, height, fan} }
let _pidDefaults = null;     // [offset] pid_temp / pid_height / pid_fan_speed / pid_tool
let _toolPid = {};           // aktuell in Klipper aktive PID-Werte je Tool
let _dockResults = {};       // { "0": {params_park_x, params_park_y, params_park_z} }
let _dockDefaults = null;    // [offset] dock_*
let _toolParkPositions = {}; // aktuell konfigurierte Dock-Positionen je Tool
let _tapMinTemp = null;        // _TAP_PROBE_ACTIVATE variable_min_temp — Untergrenze, auf die der Tap heizt
let _toolGcodeOffsets = {};    // { "0": {x:0, y:0, z:0}, ... } current tool gcode offsets
let _zSwitchResults = {};      // { "0": { z_offset: 0.0, z_trigger: 1.23 }, ... }
let _xyResults = {};        // { ref_tool: 0, "0": {x, y, z_compare, x_fwd, x_rev, ...} }
// Kamera zuerst: das Eddy-Verfahren kann erst Werte liefern, wenn die
// zweite Spule und das Klipper-seitige nozzle_locator-Modul existieren.
// Bis dahin waere "eddy" als Vorauswahl ein Block, der garantiert nur
// "nicht gemessen" zeigt.
let _xyMethod = "camera";   // "eddy" | "camera"
let _xyProbeActive = null;  // null = unbekannt, true/false = Config-Zustand (Task 8)
let _cameraPositions = {};  // { "0": {x, y} } aus "Position uebernehmen" (Task 9)

// --------------------------
// Helpers
// --------------------------
// printerUrl is defined in index.js (loaded after tools.js)

const OffsetDebug = (() => {
  const key = "offset_debug";
  let enabled = false;

  function init() {
    try {
      const params = new URLSearchParams(window.location.search || "");
      enabled = params.get("debug") === "1" || params.get("offset_debug") === "1" || localStorage.getItem(key) === "1";
    } catch (_) {
      enabled = false;
    }
    if (enabled) console.log("[Offset] Debug enabled");
  }

  function set(value) {
    enabled = !!value;
    try {
      localStorage.setItem(key, enabled ? "1" : "0");
    } catch (_) {}
    if (enabled) console.log("[Offset] Debug enabled");
  }

  function log(...args) { if (enabled) console.log("[Offset]", ...args); }
  function error(...args) { if (enabled) console.error("[Offset]", ...args); }

  return {
    init,
    set,
    log,
    error,
    get enabled() { return enabled; }
  };
})();

window.OffsetDebug = {
  enable: () => OffsetDebug.set(true),
  disable: () => OffsetDebug.set(false),
  status: () => OffsetDebug.enabled
};

OffsetDebug.init();

// --------------------------
// Confirmation dialog
// --------------------------
// Shows #confirmModal and resolves true on OK, false on Cancel / dismiss.
// opts: { title, body (HTML), okLabel, okClass }
// Bootstrap verwirft show(), solange das Modal noch aus der vorigen Nutzung
// ausblendet - und meldet das nicht. Genau daran scheiterte der Fehlerdialog:
// der Bestaetigungsdialog war noch am Zufahren, der HTTP-400 kam sofort
// danach zurueck, und das Popup verschwand lautlos.
//
// Waehrend des Ausblendens ist die Klasse "show" bereits weg (daran haengt
// die Animation), display steht aber noch auf block. Deshalb beides pruefen.
function modalIdle(el) {
  return new Promise(function (resolve) {
    var busy = el.classList.contains("show") ||
               window.getComputedStyle(el).display !== "none";
    if (!busy) { resolve(); return; }

    var done = false;
    function go() {
      if (done) return;
      done = true;
      // Einen Tick warten, damit Bootstrap seinen Zustand fertig aufraeumt
      setTimeout(resolve, 20);
    }
    $(el).one("hidden.bs.modal", go);
    // Notausstieg, falls das Event ausbleibt - lieber ein hakeliger Dialog
    // als gar keiner.
    setTimeout(go, 800);
  });
}

function confirmDialog(opts) {
  opts = opts || {};
  var title = opts.title || "Confirm";
  var okLabel = opts.okLabel || "OK";
  var okClass = opts.okClass || "btn-primary";

  return new Promise(function(resolve) {
    var el = document.getElementById("confirmModal");
    if (!el || typeof bootstrap === "undefined") {
      // Fallback if the modal markup is missing
      resolve(window.confirm(title));
      return;
    }

    modalIdle(el).then(function () { open(el, resolve); });
  });

  function open(el, resolve) {
    $("#confirmModalLabel").text(title);
    $("#confirmModalBody").html(opts.body || "");

    var $ok = $("#confirmModalOk");
    // .html() wie beim Extra-Button: die Beschriftungen enthalten Icons.
    // Die Texte kommen aus dem Code, nicht vom Nutzer.
    // class komplett neu setzen statt einzelne zu entfernen - die
    // Entfernliste war schon einmal unvollstaendig und ein Button trug
    // zwei Farbklassen aus zwei Dialogen gleichzeitig.
    $ok.html(okLabel).attr("class", "btn py-2 " + okClass);
    // Reine Meldung: kein Abbrechen anbieten, es gibt nichts abzubrechen
    $("#confirmModalCancel").text(opts.cancelLabel || "Cancel")
                            .toggle(!opts.hideCancel);

    // Optionaler dritter Button, loest mit "extra" statt true/false auf
    var $extra = $("#confirmModalExtra");
    if (opts.extraLabel) {
      $extra.html(opts.extraLabel)
            .attr("class", "btn py-2 " + (opts.extraClass || "btn-warning"))
            .show();
    } else {
      $extra.hide();
    }

    // Zweiter optionaler Button, loest mit "extra2" auf. Das Dock braucht
    // vier Aktionen: Testfahrt, Uebernehmen, Uebernehmen+schreiben, Abbrechen.
    var $extra2 = $("#confirmModalExtra2");
    if (opts.extra2Label) {
      $extra2.html(opts.extra2Label)
             .attr("class", "btn py-2 " + (opts.extra2Class || "btn-success"))
             .show();
    } else {
      $extra2.hide();
    }

    var modal = bootstrap.Modal.getOrCreateInstance(el);
    var settled = false;

    function settle(result) {
      if (settled) return;
      settled = true;
      $ok.off("click.confirmDialog");
      $extra.off("click.confirmDialog");
      $extra2.off("click.confirmDialog");
      $(el).off("hidden.bs.modal.confirmDialog");
      resolve(result);
    }

    $ok.off("click.confirmDialog").on("click.confirmDialog", function() {
      settle(true);
      modal.hide();
    });
    $extra.off("click.confirmDialog").on("click.confirmDialog", function() {
      settle("extra");
      modal.hide();
    });
    $extra2.off("click.confirmDialog").on("click.confirmDialog", function() {
      settle("extra2");
      modal.hide();
    });
    $(el).off("hidden.bs.modal.confirmDialog")
         .on("hidden.bs.modal.confirmDialog", function() { settle(false); });

    modal.show();
    // Doppelt genaeht: verwirft Bootstrap den Aufruf trotzdem, nachfassen -
    // ein unsichtbarer Fehlerdialog ist schlimmer als ein spaeter.
    setTimeout(function () {
      if (!settled && !el.classList.contains("show")) modal.show();
    }, 400);
  }
}

// Vorbelegung der Temperaturfelder: die Untergrenze, auf die
// _TAP_PROBE_ACTIVATE ohnehin heizt. Kalibriert man darunter, misst der
// Z-Switch kalt und der Tap heiss - die Duesenausdehnung (Groessenordnung
// 0.1mm) landet dann im probe_z_offset.
function tapMinTempDefault() {
  return (typeof _tapMinTemp === 'number' && _tapMinTemp > 0) ? _tapMinTemp : 0;
}

function tapMinTempHint() {
  if (tapMinTempDefault() > 0) {
    return 'Tap heizt auf mind. ' + tapMinTempDefault() + '&deg;C';
  }
  return '0 = no heating';
}

// Haelt die Untergrenze des Taps mit dem gewaehlten Wert im Gleichlauf.
// Ohne das wuerde ein niedrigerer UI-Wert ins Leere laufen: der Tap heizt
// trotzdem auf min_temp und misst damit bei einer anderen Temperatur als
// der Z-Switch.
function syncTapMinTemp(value) {
  var v = parseInt(value, 10);
  if (!(v > 0) || v === _tapMinTemp) return $.Deferred().resolve().promise();
  var script = 'SET_GCODE_VARIABLE MACRO=_TAP_PROBE_ACTIVATE VARIABLE=min_temp VALUE=' + v;
  return $.get(printerUrl(printerIp, "/printer/gcode/script?script=" + encodeURIComponent(script)))
    .done(function () {
      _tapMinTemp = v;
      OffsetDebug.log("tap min_temp synced", v);
    })
    .fail(function () {
      if (typeof showToast === 'function') {
        showToast("Konnte die Tap-Temperatur nicht setzen", "warning");
      }
    });
}

// /printer/gcode/script ist synchron: der Request bleibt offen, bis das
// Skript durchgelaufen ist. Eine Kalibrierung dauert Minuten, und bricht die
// Verbindung vorher ab, meldet jQuery einen Fehler - obwohl Klipper unbeirrt
// weiterarbeitet. Nur eine Fehler-Payload von Moonraker heisst, dass das
// Kommando wirklich gescheitert ist; die kommt dann als 400 in Sekunden.
function gcodeErrorMessage(err) {
  try {
    if (err.responseJSON.error.message) return String(err.responseJSON.error.message);
  } catch (_) {}
  try {
    if ((err.responseJSON || err).message) return String((err.responseJSON || err).message);
  } catch (_) {}
  return null;
}

// Fehler, die sich direkt beheben lassen: der Drucker ist nicht kaputt,
// er ist nur nicht vorbereitet. Statt "geht nicht" bietet der Dialog an,
// das Fehlende nachzuholen und den Lauf danach fortzusetzen.
function recoveryFor(detail) {
  var d = String(detail || '').toLowerCase();
  if (d.indexOf('must home') !== -1 || d.indexOf('not homed') !== -1) {
    // Nicht gehomed heisst auch: kein gueltiges Leveling. Beides nachholen.
    return { steps: ['G28', 'QUAD_GANTRY_LEVEL', 'G28 Z'],
             label: '<i class="bi bi-house-gear"></i> Home + QGL, dann weiter' };
  }
  if (d.indexOf('has not been applied') !== -1) {
    // Gehomed ist er bereits, es fehlt nur das Leveling. QGL kippt das
    // Gantry, deshalb danach Z neu referenzieren.
    var lvl = (d.indexOf('z tilt') !== -1) ? 'Z_TILT_ADJUST' : 'QUAD_GANTRY_LEVEL';
    return { steps: [lvl, 'G28 Z'],
             label: '<i class="bi bi-rulers"></i> ' + lvl + ', dann weiter' };
  }
  return null;
}

// Schickt ein Skript und bietet bei behebbaren Fehlern die Nachbereitung an.
// Vorbereitung und Lauf gehen als EIN Request raus: Moonraker arbeitet die
// Zeilen der Reihe nach ab, so kann dazwischen nichts anderes reinlaufen.
// Ergebnis: {ok:true} | {transport:true} (Lauf laeuft weiter) | {handled:true}
function sendGcodeWithRecovery(script, title, onSend) {
  function send(full, attempt) {
    if (onSend) onSend(attempt);
    // $.get kann synchron werfen - kein Netz, kaputte URL, fehlendes
    // jQuery. Ohne dieses try entstuende gar keine Kette, der Fehler flöge
    // am Aufrufer vorbei und dessen .catch wuerde nie greifen: der Button
    // bliebe gesperrt.
    var req;
    try {
      req = $.get(printerUrl(printerIp,
        "/printer/gcode/script?script=" + encodeURIComponent(full)));
    } catch (e) {
      return Promise.resolve({ err: e });
    }
    return Promise.resolve(req).then(function () { return { ok: true }; },
                                     function (err) { return { err: err }; });
  }

  function fail(err) {
    var detail = gcodeErrorMessage(err);
    // Kein Payload = Verbindung weg, der Drucker rechnet weiter. Kein Fehler.
    if (!detail) return { transport: true };
    return { detail: detail };
  }

  return send(script, 1).then(function (r) {
    if (r.ok) return r;
    var f = fail(r.err);
    if (!f.detail) return f;

    var rec = recoveryFor(f.detail);
    var body = '<p class="mb-0">' + escapeHtml(f.detail) + '</p>';
    if (rec) {
      body += '<p class="mt-2 mb-0 text-secondary">Der Button fuehrt <code>' +
              rec.steps.map(escapeHtml).join('</code> &rarr; <code>') +
              '</code> aus und startet den Lauf danach automatisch neu. ' +
              'Der Drucker bewegt sich dabei.</p>';
    }

    return alertDialog(title, body, rec ? {
      extraLabel: rec.label, extraClass: 'btn-warning'
    } : null).then(function (choice) {
      if (choice !== 'extra') return { handled: true };
      // Sofort sichtbar quittieren: G28 braucht ein paar Sekunden, bis sich
      // etwas ruehrt, und ohne Rueckmeldung sieht das aus wie ein toter Button.
      if (typeof showToast === 'function') {
        showToast(rec.steps.join(" -> ") + " laeuft...", "info");
      }
      return send(rec.steps.concat([script]).join("\n"), 2).then(function (r2) {
        if (r2.ok) return r2;
        var f2 = fail(r2.err);
        if (!f2.detail) return f2;
        // Zweiter Versuch gescheitert: nur melden, nicht endlos anbieten.
        return alertDialog(title, '<p class="mb-0">' + escapeHtml(f2.detail) + '</p>')
          .then(function () { return { handled: true }; });
      });
    });
  });
}

// Echte Klipper-Fehler bekommen ein Popup, keinen Toast: der Toast unten
// rechts ist leicht zu uebersehen, waehrend man auf den Drucker schaut -
// und diese Fehler bedeuten, dass der Lauf gar nicht erst gestartet ist.
// Alle Meldungen teilen sich das eine Modal, also nacheinander zeigen -
// sonst ueberschreibt die zweite Meldung die erste, bevor man sie liest.
var _alertQueue = Promise.resolve();

function alertDialog(title, message, opts) {
  _alertQueue = _alertQueue.then(function () {
    return _showAlert(title, message, opts);
  }, function () {
    return _showAlert(title, message, opts);
  });
  return _alertQueue;
}

function _showAlert(title, message, opts) {
  opts = opts || {};
  return confirmDialog({
    title: title,
    body: '<div class="small">' + message + '</div>',
    okLabel: opts.okLabel || "OK",
    okClass: opts.okClass || "btn-danger",
    // Reine Meldungen haben nichts abzubrechen; mehrstufige Ablaeufe schon.
    hideCancel: !opts.showCancel,
    cancelLabel: opts.cancelLabel,
    extraLabel: opts.extraLabel,
    extraClass: opts.extraClass,
    extra2Label: opts.extra2Label,
    extra2Class: opts.extra2Class
  });
}

function escapeHtml(s) {
  return String(s)
    .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
}

// Renders the "what gets written where" body of a confirmation dialog.
// entries: [{ tool, file, section, changes: [{key, from, to}] }]
// from/to are strings (or null/undefined for "unknown"); a numeric diff is
// shown whenever both sides parse as numbers.
// Moonraker liefert Config-Dateien mit Last-Modified, aber ohne
// Cache-Control. Browser wenden darauf heuristisches Caching an und
// beantworten ein fetch() minutenlang aus dem Cache, ohne nachzufragen.
//
// Die Schreiber hier lesen die Datei, ersetzen einzelne Zeilen und laden
// die GANZE Datei zurueck. Mit einer veralteten Kopie faellt damit alles
// zurueck, was seit dem Cache-Stand geschrieben wurde - beobachtet am
// 250er: eine APPLY-XY-Uebernahme setzte nebenbei die PID-Werte auf den
// Stand von vorher zurueck und loeschte deactivate_on_each_sample.
//
// Betrifft auch die Bestaetigungsdialoge: deren "Current"-Spalte kommt aus
// derselben Datei. Eine gecachte Kopie zeigt also womoeglich Werte, die auf
// dem Drucker laengst anders sind.
var NO_CACHE = { cache: 'no-store' };

function offsetChangeListHtml(entries, note) {
  var html = "";
  var zeroCount = 0;

  entries.forEach(function(e) {
    var rows = e.changes.map(function(c) {
      var fromTxt = (c.from === null || c.from === undefined || c.from === "")
        ? "-" : String(c.from);
      var toTxt = String(c.to);
      var diffTxt = "";
      var fromNum = parseFloat(fromTxt);
      var toNum = parseFloat(toTxt);
      if (!Number.isNaN(fromNum) && !Number.isNaN(toNum)) {
        var d = toNum - fromNum;
        diffTxt = (d >= 0 ? "+" : "") + d.toFixed(3);
      }
      // Einen kalibrierten Wert auf 0 zu setzen ist fast immer ein
      // Versehen - typisch, wenn in dieser Sitzung nie gemessen wurde und
      // die Felder auf 0.000 stehen. Passiert ist das auf dem 250er
      // bereits zweimal, deshalb faellt es hier optisch heraus.
      var zeroing = !Number.isNaN(fromNum) && !Number.isNaN(toNum)
                    && fromNum !== 0 && toNum === 0;
      if (zeroing) zeroCount++;
      return '<tr' + (zeroing ? ' class="table-warning"' : '') + '>' +
        '<td class="px-1 py-0 text-nowrap"><code>' + escapeHtml(c.key) + '</code></td>' +
        '<td class="px-1 py-0 text-end text-secondary">' + escapeHtml(fromTxt) + '</td>' +
        '<td class="px-1 py-0 text-center text-secondary">&rarr;</td>' +
        '<td class="px-1 py-0 text-end fw-bold ' +
          (zeroing ? 'text-warning' : 'text-success') + '">' +
          escapeHtml(toTxt) + '</td>' +
        '<td class="px-1 py-0 text-end text-info">' + escapeHtml(diffTxt) + '</td>' +
      '</tr>';
    }).join("");

    html += '<div class="border border-secondary-subtle rounded p-2 mb-2 bg-dark">' +
      '<div class="fw-bold">T' + escapeHtml(e.tool) + '</div>' +
      '<div class="small text-secondary mb-1"><code>' + escapeHtml(e.file) + '</code>' +
        (e.section ? ' &rarr; <code>[' + escapeHtml(e.section) + ']</code>' : '') +
      '</div>' +
      '<table class="table table-sm table-borderless mb-0" style="font-size:0.85rem;">' +
        '<tbody>' + rows + '</tbody>' +
      '</table>' +
    '</div>';
  });

  if (zeroCount) {
    html += '<div class="alert alert-warning py-2 px-3 small mb-2">' +
      '<i class="bi bi-exclamation-triangle"></i> <strong>' + zeroCount +
      ' kalibrierte' + (zeroCount === 1 ? 'r Wert wird' : ' Werte werden') +
      ' auf 0 gesetzt.</strong> Wurde in dieser Sitzung nichts gemessen, ' +
      'stehen die Eingabefelder auf 0.000 und die vorhandene Kalibrierung ' +
      'geht verloren.</div>';
  }
  if (note) html += '<div class="small text-secondary">' + note + '</div>';
  return html;
}

// --------------------------
// Config file update via Moonraker File API
// --------------------------
// Liest eine Config-Datei, laesst sie vom mutator veraendern und laedt sie
// wieder hoch. Ungecacht lesen ist Pflicht -- ein gecachter Lesevorgang hat
// hier schon einmal Uebernahmen verschluckt (e489c494).
// Liefert der mutator null, wird nicht hochgeladen.
function updateConfigFile(filePath, mutator) {
  var baseUrl = printerUrl(printerIp, "");
  return fetch(baseUrl + "/server/files/config/" + filePath, NO_CACHE)
    .then(function (r) { return r.text(); })
    .then(function (content) {
      var updated = mutator(content);
      if (updated === null || updated === undefined) return null;
      var formData = new FormData();
      var blob = new Blob([updated], { type: 'text/plain' });
      formData.append('file', blob, filePath);
      formData.append('root', 'config');
      return fetch(baseUrl + "/server/files/upload", { method: 'POST', body: formData });
    });
}

// Updates gcode offsets directly in tool config files (avoids SAVE_CONFIG conflicts with included files)
// Uploads sequentially to avoid Moonraker 500 errors from concurrent writes.
function updateToolConfigOffsets(toolOffsets) {
  // toolOffsets: { "0": {x: "0.000", y: "0.000", z: "0.000"}, "1": {x: "0.53", z: "0.640"}, ... }
  // Only keys present in each tool's object are updated.
  var tools = Object.keys(toolOffsets);
  var missing = [];

  function processNext(idx) {
    if (idx >= tools.length) {
      reportMissingKeys(missing);
      return Promise.resolve();
    }
    var t = tools[idx];
    var offsets = toolOffsets[t];
    var filePath = "toolchanger/tools/T" + t + ".cfg";
    return updateConfigFile(filePath, function (content) {
      var modified = false;
      if ('x' in offsets) {
        var rxX = /^(gcode_x_offset\s*[:=]\s*).*$/m;
        if (rxX.test(content)) { content = content.replace(rxX, "$1" + offsets.x); modified = true; }
      }
      if ('y' in offsets) {
        var rxY = /^(gcode_y_offset\s*[:=]\s*).*$/m;
        if (rxY.test(content)) { content = content.replace(rxY, "$1" + offsets.y); modified = true; }
      }
      if ('z' in offsets) {
        var rxZ = /^(gcode_z_offset\s*[:=]\s*).*$/m;
        if (rxZ.test(content)) { content = content.replace(rxZ, "$1" + offsets.z); modified = true; }
      }
      if (!modified) {
        ['x', 'y', 'z'].forEach(function (a) {
          if (a in offsets) {
            missing.push({file: filePath, section: 'tool T' + t,
                          key: 'gcode_' + a + '_offset'});
          }
        });
        return null;
      }
      return content;
    }).then(function () { return processNext(idx + 1); });
  }
  return processNext(0);
}

// Replaces `key` inside the given config section only. Returns the new
// content, or null if the section/key was not found.
// Needed for tool_probe z_offset: T<n>.cfg also has x_offset/y_offset in the
// same section and gcode_z_offset in [tool T<n>].
function replaceInConfigSection(content, sectionName, key, value) {
  var lines = content.split('\n');
  var escName = sectionName.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  var sectionRx = new RegExp('^\\s*\\[\\s*' + escName + '\\s*\\]');
  var anySectionRx = /^\s*\[[^\]]+\]/;
  // Klipper normalisiert Optionsnamen (ConfigParser), die Configs hier
  // nicht: T0 schreibt pid_Kp, die uebrigen pid_kp. Case-sensitiv wuerde
  // das fuer einen Teil der Tools still nichts tun.
  var keyRx = new RegExp('^(\s*' + key + '\s*[:=]\s*).*$', 'i');
  var inSection = false;

  for (var i = 0; i < lines.length; i++) {
    if (anySectionRx.test(lines[i])) {
      inSection = sectionRx.test(lines[i]);
      continue;
    }
    if (!inSection) continue;
    if (keyRx.test(lines[i])) {
      lines[i] = lines[i].replace(keyRx, "$1" + value);
      return lines.join('\n');
    }
  }
  return null;
}

// A key the writers could not find. Case is already ignored when matching,
// so a miss means the option is genuinely absent from that section - or
// spelled differently than expected. Reported instead of skipped silently:
// a write that quietly does nothing looks exactly like a successful one.
function reportMissingKeys(missing) {
  if (!missing.length) return;
  var lines = missing.map(function (m) {
    return m.file + ' → [' + m.section + '] ' + m.key;
  });
  OffsetDebug.error("Config keys not found", missing);
  alertDialog("Nicht geschrieben - Eintrag fehlt in der Config",
    "<p class=\"mb-2\">Diese Optionen wurden in der Config nicht gefunden, " +
    "die Werte sind <b>nicht</b> gespeichert:</p>" +
    "<ul class=\"mb-2\"><li><code>" +
    lines.map(escapeHtml).join("</code></li><li><code>") +
    "</code></li></ul>" +
    "<p class=\"mb-0 text-secondary\">Gross-/Kleinschreibung wird beim Suchen " +
    "bereits ignoriert - der Eintrag fehlt also wirklich oder heisst anders. " +
    "Bitte in der Config pruefen und ggf. anlegen.</p>");
}

// Reads a key out of a config file. sectionName null = first match anywhere
// (mirrors updateToolConfigOffsets); otherwise the key must sit in that section.
function readConfigValue(content, sectionName, key) {
  var lines = content.split('\n');
  var anySectionRx = /^\s*\[[^\]]+\]/;
  var sectionRx = sectionName
    ? new RegExp('^\\s*\\[\\s*' + sectionName.replace(/[.*+?^${}()|[\]\\]/g, '\\$&') + '\\s*\\]')
    : null;
  var keyRx = new RegExp('^\s*' + key + '\s*[:=]\s*([^#]*)', 'i');
  var inSection = !sectionRx;

  for (var i = 0; i < lines.length; i++) {
    if (anySectionRx.test(lines[i])) {
      inSection = sectionRx ? sectionRx.test(lines[i]) : true;
      continue;
    }
    if (!inSection) continue;
    var m = lines[i].match(keyRx);
    if (m) return m[1].trim();
  }
  return null;
}

// Reads the current values straight out of the tool config files, so the
// confirmation dialog shows what actually changes on disk. The runtime value
// can differ — the XY measuring flow zeroes gcode_x/y_offset at runtime while
// the file still holds the calibrated value.
// requests: [{ tool, key, section }] -> Promise<{ "<tool>|<key>": string|null }>
function fetchToolConfigValues(requests) {
  var baseUrl = printerUrl(printerIp, "");
  var tools = [];
  requests.forEach(function(r) {
    if (tools.indexOf(String(r.tool)) === -1) tools.push(String(r.tool));
  });

  var out = {};
  return Promise.all(tools.map(function(t) {
    var mine = requests.filter(function(r) { return String(r.tool) === t; });
    return fetch(baseUrl + "/server/files/config/toolchanger/tools/T" + t + ".cfg",
                 NO_CACHE)
      .then(function(r) { return r.ok ? r.text() : null; })
      .then(function(content) {
        mine.forEach(function(r) {
          out[t + "|" + r.key] = (content === null)
            ? null : readConfigValue(content, r.section, r.key);
        });
      })
      .catch(function() {
        mine.forEach(function(r) { out[t + "|" + r.key] = null; });
      });
  })).then(function() { return out; });
}

// Updates tool_probe z_offset directly in tool config files.
// toolZOffsets: { "0": "-0.812", "1": "-0.831", ... }
function updateToolProbeOffsets(toolZOffsets) {
  var tools = Object.keys(toolZOffsets);
  var missing = [];

  function processNext(idx) {
    if (idx >= tools.length) {
      reportMissingKeys(missing);
      return Promise.resolve();
    }
    var t = tools[idx];
    var filePath = "toolchanger/tools/T" + t + ".cfg";
    return updateConfigFile(filePath, function (content) {
      var updated = replaceInConfigSection(
        content, "tool_probe T" + t, "z_offset", toolZOffsets[t]);
      if (updated === null) {
        missing.push({file: filePath, section: 'tool_probe T' + t,
                      key: 'z_offset'});
        return null;
      }
      return updated;
    }).then(function () { return processNext(idx + 1); });
  }
  return processNext(0);
}

// --------------------------
// Probe Discovery
// --------------------------
function fetchAvailableProbes() {
  return $.get(printerUrl(printerIp, "/printer/objects/query?offset"))
    .then(function(data) {
      var st = data?.result?.status?.offset;
      _availableProbes = (st?.available_probes || []).filter(function(name) {
        return name && name.indexOf('tool_probe_endstop') === -1;
      });
      OffsetDebug.log("Available probes:", _availableProbes);
      return _availableProbes;
    })
    .catch(function() {
      _availableProbes = [];
      return [];
    });
}

function loadProbeCalConfig() {
  if (!printerIp) return;
  var key = 'offset_probe_config_' + printerIp.replace(/[^a-zA-Z0-9]/g, '_');
  try {
    _probeCalConfig = JSON.parse(localStorage.getItem(key));
  } catch (_) {
    _probeCalConfig = null;
  }
}

function saveProbeCalConfig() {
  if (!printerIp || !_probeCalConfig) return;
  var key = 'offset_probe_config_' + printerIp.replace(/[^a-zA-Z0-9]/g, '_');
  localStorage.setItem(key, JSON.stringify(_probeCalConfig));
}

function getProbeCalConfig(toolNumbers) {
  loadProbeCalConfig();
  if (_probeCalConfig && _probeCalConfig.tool_probes) return _probeCalConfig;

  // Build defaults
  var eddyProbe = _availableProbes.find(function(n) { return n.indexOf('eddy') !== -1; });
  var tapProbe = _availableProbes.find(function(n) { return n === 'probe'; }) || 'probe';
  var refTool = 0;
  var refProbe = eddyProbe || tapProbe;

  var toolProbes = {};
  (toolNumbers || []).forEach(function(t) {
    toolProbes[String(t)] = (t === refTool && eddyProbe) ? eddyProbe : tapProbe;
  });

  _probeCalConfig = {
    ref_tool: refTool,
    ref_probe: refProbe,
    tool_probes: toolProbes
  };
  saveProbeCalConfig();
  return _probeCalConfig;
}

function computeDefaultRef(toolNumbers) {
  const sorted = [...toolNumbers].sort((a, b) => a - b);
  if (offsetMasterTool !== null && sorted.includes(offsetMasterTool)) return offsetMasterTool;
  if (sorted.includes(0)) return 0;
  return sorted.length ? sorted[0] : 0;
}

function getSelectedReferenceTool(fallback = 0) {
  const $checked = $(".calibrate-ref-checkbox:checked").first();
  if ($checked.length) {
    const v = parseInt($checked.val(), 10);
    return Number.isNaN(v) ? fallback : v;
  }
  return offsetMasterTool ?? fallback;
}

function syncSelectAllState() {
  const $all = $(".calibrate-tool-checkbox");
  const $checked = $(".calibrate-tool-checkbox:checked");
  $("#calibrate-select-all").prop("checked", $all.length > 0 && $all.length === $checked.length);
}

function formatClipboardNumber(value) {
  if (!Number.isFinite(value)) return null;
  const fixed = value.toFixed(3);
  const trimmed = fixed.replace(/(\.\d*?[1-9])0+$/u, "$1");
  return trimmed.replace(/\.0+$/u, ".0");
}

function copyTextToClipboard(text, context = "") {
  OffsetDebug.log("copyTextToClipboard start", {context, text});
  if (navigator.clipboard && navigator.clipboard.writeText) {
    OffsetDebug.log("Using navigator.clipboard.writeText");
    return navigator.clipboard.writeText(text);
  }

  return new Promise(function(resolve, reject) {
    const $tmp = $('<textarea>');
    $tmp.val(text).css({position: 'fixed', left: '-9999px', top: '-9999px'});
    $('body').append($tmp);
    const el = $tmp.get(0);
    if (el && el.select) {
      el.select();
      if (el.setSelectionRange) el.setSelectionRange(0, el.value.length);
    } else {
      $tmp.trigger('select');
    }

    try {
      const ok = document.execCommand('copy');
      $tmp.remove();
      OffsetDebug.log("execCommand copy result", ok);
      if (ok) resolve();
      else reject(new Error('copy failed'));
    } catch (err) {
      $tmp.remove();
      reject(err);
    }
  });
}

function applyMasterReferenceXY(axis) {
  const master = getSelectedReferenceTool(0);
  const $masterEl = $(`#T${master}-${axis}-new`);
  const masterRaw = parseFloat($masterEl.attr("data-raw")) || 0.0;

  $('button.toolchange-btn').each(function(){
    const tool = $(this).data("tool");
    const $el = $(`#T${tool}-${axis}-new`);
    if (!$el.length) return; // master row has no XY new fields
    const raw = parseFloat($el.attr("data-raw")) || 0.0;
    const rel = (parseInt(tool, 10) === parseInt(master, 10)) ? 0.0 : (raw - masterRaw);
    $el.find('>:first-child').text(rel.toFixed(3));
  });
}

// --------------------------
// Accordion Templates
// --------------------------
function accordionSection(id, title, statusHtml, contentHtml, defaultOpen) {
  var show = defaultOpen ? ' show' : '';
  var collapsed = defaultOpen ? '' : ' collapsed';
  return `
  <div class="accordion-item bg-body-tertiary border-secondary-subtle">
    <h2 class="accordion-header">
      <button class="accordion-button${collapsed} bg-body-tertiary py-2" type="button"
              data-bs-toggle="collapse" data-bs-target="#${id}-body"
              aria-expanded="${defaultOpen}" aria-controls="${id}-body">
        <span class="me-auto fw-bold">${title}</span>
        <span class="me-2 small" id="${id}-status">${statusHtml}</span>
      </button>
    </h2>
    <div id="${id}-body" class="accordion-collapse collapse${show}">
      <div class="accordion-body p-2">
        ${contentHtml}
      </div>
    </div>
  </div>`;
}

// --------------------------
// Templates
// --------------------------
const masterToolItem = ({tool_number, disabled, tc_disabled}) => `
<li class="list-group-item bg-body-tertiary p-2">
  <div class="container">
    <div class="row">
      <div class="col-2">
        <button type="button" class="btn btn-secondary btn-sm w-100 h-100 toolchange-btn ${tc_disabled}"
                name="T${tool_number}" data-tool="${tool_number}">
          <h1>T${tool_number}</h1>
        </button>
      </div>

      <div class="col-6">
        <div class="border border-secondary-subtle rounded p-2 bg-dark h-100 d-flex flex-column justify-content-center">
          <div class="d-flex justify-content-between align-items-center mb-2">
            <span class="fs-6">Master Capture</span>
            <small class="text-secondary" id="master-status-badge">Master: T${tool_number}</small>
          </div>
          <button type="button"
                  class="btn btn-sm btn-secondary fs-6 border text-center w-100 ${disabled}"
                  style="padding-bottom:10px; padding-top:10px;"
                  id="capture-pos">
            CAPTURE <br/> CURRENT <br/> POSITION
          </button>
          <small class="text-secondary mt-2">
            Tip: switch to Master tool first (tool must be active).
          </small>
        </div>
      </div>

      <div class="col-4">
        <div class="border border-secondary-subtle rounded p-2 bg-dark h-100">
          <div class="d-flex justify-content-between align-items-center mb-2">
            <span class="fs-6">Captured Position</span>
          </div>

          <div class="row">
            <div class="col-4"><small>X:</small></div>
            <div class="col-8 text-end"><span id="captured-x"><small></small></span></div>
          </div>
          <div class="row">
            <div class="col-4"><small>Y:</small></div>
            <div class="col-8 text-end"><span id="captured-y"><small></small></span></div>
          </div>
          <div class="row">
            <div class="col-4"><small>Z:</small></div>
            <div class="col-8 text-end"><span id="captured-z"><small></small></span></div>
          </div>

          <hr class="my-2"/>

          <div class="row">
            <div class="col-6"><small>Z-Trigger:</small></div>
            <div class="col-6 text-end"><span id="T${tool_number}-z-trigger"><small>-</small></span></div>
          </div>
        </div>
      </div>
    </div>
  </div>
</li>
`;

const nonMasterToolItem = ({tool_number, cx_offset, cy_offset, disabled, tc_disabled}) => `
<li class="list-group-item bg-body-tertiary p-2">
  <div class="container">
    <div class="row">

      <div class="col-2">
        <button type="button" class="btn btn-secondary btn-sm w-100 h-100 toolchange-btn ${tc_disabled}"
                name="T${tool_number}" data-tool="${tool_number}">
          <h1>T${tool_number}</h1>
        </button>
      </div>

      <div class="col-6">
        <div class="row pb-3">
          <div class="input-group ps-1 pe-1">
            <button class="btn btn-secondary ${disabled}" type="button"
                    id="T${tool_number}-fetch-x" data-axis="x" data-tool="${tool_number}">X</button>
            <input type="number" name="T${tool_number}-x-pos"
                   class="form-control"
                   placeholder="0.0"
                   data-axis="x"
                   data-tool="${tool_number}"
                   ${disabled}>
          </div>
        </div>

        <div class="row">
          <div class="input-group ps-1 pe-1">
            <button class="btn btn-secondary ${disabled}" type="button"
                    id="T${tool_number}-fetch-y" data-axis="y" data-tool="${tool_number}">Y</button>
            <input type="number" name="T${tool_number}-y-pos"
                   class="form-control"
                   placeholder="0.0"
                   data-axis="y"
                   data-tool="${tool_number}"
                   ${disabled}>
          </div>
        </div>
      </div>

      <div class="col-4 border rounded bg-dark">
        <div class="row">
          <div class="col-6 pt-2 pb-2">
            <div class="row pb-1">
              <span class="fs-6 lh-sm text-secondary"><small>Current X</small></span>
              <span class="fs-5 lh-sm text-secondary" id="T${tool_number}-x-offset"><small>${cx_offset}</small></span>
            </div>
            <div class="row">
              <span class="fs-6 lh-sm text-secondary"><small>Current Y</small></span>
              <span class="fs-5 lh-sm text-secondary" id="T${tool_number}-y-offset"><small>${cy_offset}</small></span>
            </div>

            <div class="z-fields d-none mt-2">
              <div class="row">
                <span class="fs-6 lh-sm text-secondary"><small>Z-Trigger</small></span>
                <span class="fs-5 lh-sm text-secondary" id="T${tool_number}-z-trigger"><small>-</small></span>
              </div>
            </div>
          </div>

          <div class="col-6 pt-2 pb-2">
            <div class="row pb-1">
              <span class="fs-6 lh-sm"><small>New X</small></span>
              <span class="fs-5 lh-sm" id="T${tool_number}-x-new" data-raw="0.000" title="Click to copy gcode_x_offset" style="cursor:pointer;"><small>0.0</small></span>
            </div>
            <div class="row pb-1">
              <span class="fs-6 lh-sm"><small>New Y</small></span>
              <span class="fs-5 lh-sm" id="T${tool_number}-y-new" data-raw="0.000" title="Click to copy gcode_y_offset" style="cursor:pointer;"><small>0.0</small></span>
            </div>
            <div class="row pb-1">
              <span class="fs-6 lh-sm"><small>New Z</small></span>
              <span class="fs-5 lh-sm" id="T${tool_number}-z-new" title="Click to copy gcode_z_offset" style="cursor:pointer;"><small>0.000</small></span>
            </div>
            <div class="z-fields d-none">
              <div class="row pb-1">
                <span class="fs-6 lh-sm"><small>Probe Z</small></span>
                <span class="fs-5 lh-sm" id="T${tool_number}-pz-new" data-raw="" title="Click to copy z_offset (tool_probe)" style="cursor:pointer;"><small>-</small></span>
              </div>
            </div>
            <div class="row pt-1">
              <button type="button" class="btn btn-sm btn-outline-secondary" data-copy-all="${tool_number}">Copy all offsets</button>
            </div>
          </div>
        </div>
      </div>

    </div>
  </div>
</li>
`;

// --------------------------
// Offset status fetch (for dropdown label + z fields)
// --------------------------
function fetchOffsetStatus() {
  return $.get(printerUrl(printerIp,
      "/printer/objects/query?offset&" +
      encodeURIComponent("gcode_macro _TAP_PROBE_ACTIVATE")))
    .then(function(ax){
      const st = ax?.result?.status?.offset;
      _offsetPresent = !!st;
      _offsetZCalcDefault = (st?.z_calc_method || null);
      // Der Tap heizt ueber _TAP_PROBE_ACTIVATE ohnehin auf min_temp. Wird
      // niedriger kalibriert, messen Z-Switch und Tap bei verschiedenen
      // Temperaturen und die Waermeausdehnung landet im probe_z_offset.
      _pidResults = (st?.pid_results || {});
      _pidDefaults = (st?.pid_defaults || null);
      _toolPid = (st?.tool_pid || {});
      _dockResults = (st?.dock_results || {});
      _dockDefaults = (st?.dock_defaults || null);
      _toolParkPositions = (st?.tool_park_positions || {});
      _xyResults = (st?.xy_results || {});
      var tapMacro = ax?.result?.status?.["gcode_macro _TAP_PROBE_ACTIVATE"];
      if (typeof tapMacro?.min_temp === 'number') _tapMinTemp = tapMacro.min_temp;
      _toolProbeOffsets = (st?.tool_probe_offsets || {});
      _toolGcodeOffsets = (st?.tool_gcode_offsets || {});
      // Extract results from probe_results per tool
      _probeCalResults = {};
      _eddyTapDeviations = {};
      _zSwitchResults = {};
      var pr = st?.probe_results || {};
      for (var k in pr) {
        if (pr[k] && typeof pr[k].probe_z_offset === 'number') {
          _probeCalResults[k] = { probe_z_offset: pr[k].probe_z_offset };
        }
        // Eddy-measured tools: informational only, never applied to
        // the mechanical Tap's z_offset
        if (pr[k] && typeof pr[k].eddy_tap_deviation === 'number') {
          _eddyTapDeviations[k] = {
            deviation: pr[k].eddy_tap_deviation,
            probe: pr[k].eddy_probe || 'eddy'
          };
        }
        if (pr[k] && typeof pr[k].z_offset === 'number') {
          _zSwitchResults[k] = { z_offset: pr[k].z_offset, z_trigger: pr[k].z_trigger };
        }
      }
      return st || null;
    })
    .catch(function(){
      _offsetPresent = false;
      _offsetZCalcDefault = null;
      _toolProbeOffsets = {};
      _toolGcodeOffsets = {};
      _probeCalResults = {};
      _eddyTapDeviations = {};
      _zSwitchResults = {};
      _pidResults = {};
      _toolPid = {};
      _dockResults = {};
      _toolParkPositions = {};
      _xyResults = {};
      return null;
    });
}

// --------------------------
// Probe results (Z)
// --------------------------
// Ein Query, beide Tabellen: probe_results und pid_results stecken im
// selben [offset]-Objekt.
function getOffsetSnapshot() {
  return $.get(printerUrl(printerIp, "/printer/objects/query?offset"))
    .then(data => data?.result?.status?.offset || {})
    .catch(() => ({}));
}

function getProbeResults() {
  return getOffsetSnapshot().then(st => st.probe_results || {});
}

function updateProbeResults(tool, probeResults) {
  if (!probeResults || !probeResults[tool]) return;
  const r = probeResults[tool];
  if (typeof r.z_trigger === "number") $(`#T${tool}-z-trigger small`).text(r.z_trigger.toFixed(3));
  if (typeof r.z_offset === "number") {
    const zTxt = r.z_offset.toFixed(3);
    $(`#T${tool}-z-new`).attr("data-raw", zTxt);
    $(`#T${tool}-z-new small`).text(zTxt);
  }
  if (typeof r.probe_z_offset === "number") {
    const pzTxt = r.probe_z_offset.toFixed(3);
    $(`#T${tool}-pz-new`).attr("data-raw", pzTxt);
    $(`#T${tool}-pz-new small`).text(pzTxt);
  }
}

// Diese eine Kette bedient Dock-, PID-, Probe- UND XY-Tabelle. Wirft
// irgendein Renderer darin (Fix-Runde 3: renderXyBlock auf einem Eintrag
// ohne x/y), bleiben ohne diesen Fang ALLE vier auf dem Stand von vorher
// stehen - alle 2s aufs Neue, sichtbar nur als Stack-Trace in der Konsole.
function updateAllProbeResults() {
  return getOffsetSnapshot().then(function(offsetStatus) {
    var probeResults = offsetStatus.probe_results || {};
    updatePidResults(offsetStatus);
    updateDockResults(offsetStatus);
    updateXyResults(offsetStatus);
    pollXySparkline();
    $('button.toolchange-btn').each(function(){
      updateProbeResults($(this).data("tool"), probeResults);
    });
    var changed = false;
    for (var k in probeResults) {
      var r = probeResults[k];
      if (!r) continue;
      if (typeof r.probe_z_offset === 'number') {
        if (!_probeCalResults[k] || _probeCalResults[k].probe_z_offset !== r.probe_z_offset) {
          changed = true;
        }
        _probeCalResults[k] = { probe_z_offset: r.probe_z_offset };
      } else if (_probeCalResults[k]) {
        // Tool switched to an Eddy measurement — drop the stale Tap value
        delete _probeCalResults[k];
        changed = true;
      }
      if (typeof r.eddy_tap_deviation === 'number') {
        if (!_eddyTapDeviations[k] || _eddyTapDeviations[k].deviation !== r.eddy_tap_deviation) {
          changed = true;
        }
        _eddyTapDeviations[k] = {
          deviation: r.eddy_tap_deviation,
          probe: r.eddy_probe || 'eddy'
        };
      } else if (_eddyTapDeviations[k]) {
        delete _eddyTapDeviations[k];
        changed = true;
      }
    }
    if (changed) {
      var $container = $('#probe-cal-results-container');
      if ($container.length) {
        var tools = Object.keys(_toolProbeOffsets).map(Number).sort(function(a,b){ return a-b; });
        $container.html(probeCalResultsTable(tools));
      }
    }
  }).catch(function (err) {
    OffsetDebug.error("updateAllProbeResults failed", err);
  });
}

// Ein PID-Lauf ueber mehrere Tools dauert Minuten - deutlich laenger, als
// die HTTP-Anfrage lebt. Deren .then() feuert also beim Verbindungsabbruch,
// nicht am Ende des Laufs, und die Tabelle bliebe auf dem Stand von vorher
// stehen: die fertigen Werte und damit der APPLY-Button erschienen erst nach
// einem Reload. Deshalb wie bei den Probe-Offsets aus dem Polling nachziehen.
function updateDockResults(offsetStatus) {
  var changed = false;
  var res = offsetStatus.dock_results || {};
  if (JSON.stringify(res) !== JSON.stringify(_dockResults)) {
    _dockResults = res; changed = true;
  }
  var park = offsetStatus.tool_park_positions || {};
  if (JSON.stringify(park) !== JSON.stringify(_toolParkPositions)) {
    _toolParkPositions = park; changed = true;
  }
  if (!changed) return;
  var $c = $('#dock-results-container');
  if (!$c.length) return;
  var tools = Object.keys(_toolParkPositions).map(Number)
                .sort(function (a, b) { return a - b; });
  $c.html(dockResultsTable(tools));
}

// Wie updateDockResults/updatePidResults: patcht xy_results aus dem
// laufenden Poll nach, damit ein Messlauf nicht erst nach einem Reload in
// der Tabelle auftaucht.
function updateXyResults(offsetStatus) {
  var res = offsetStatus.xy_results || {};
  if (JSON.stringify(res) === JSON.stringify(_xyResults)) return;
  _xyResults = res;
  renderXyBlock();
}

function updatePidResults(offsetStatus) {
  var changed = false;
  var pid = offsetStatus.pid_results || {};
  if (JSON.stringify(pid) !== JSON.stringify(_pidResults)) {
    _pidResults = pid;
    changed = true;
  }
  var cur = offsetStatus.tool_pid || {};
  if (JSON.stringify(cur) !== JSON.stringify(_toolPid)) {
    _toolPid = cur;
    changed = true;
  }
  if (!changed) return;
  var $c = $('#pid-results-container');
  if (!$c.length) return;
  var tools = Object.keys(_toolPid).map(Number).sort(function (a, b) {
    return a - b;
  });
  $c.html(pidResultsTable(tools));
}

function startProbeResultsUpdatesOnce() {
  if (_probeInterval) return;
  _probeInterval = setInterval(updateAllProbeResults, 2000);
}

// --------------------------
// Calibration UI
// --------------------------
// Tool-Auswahl und Referenztool ueber ALLEN Abschnitten (Tobi,
// 2026-09-04: "uns ist diese Sektion verloren gegangen, bei der Kamera-
// wie bei der Eddy-Vermessung"). Sie sass bis dahin nur im zugeklappten
// Z-Switch-Abschnitt, obwohl Kamera-Block (Master) und Eddy-Lauf
// (REF_TOOL/TOOLS) dieselben Checkboxen lesen. Jetzt einmal sichtbar
// oberhalb des Akkordeons; die Klassen bleiben, damit alle Leser
// (getSelectedReferenceTool, #calibrate-all-btn, xyRefTool) weiter passen.
function toolSelectionPanel(toolNumbers = []) {
  const sortedTools = [...toolNumbers].sort((a, b) => a - b);
  const defaultRef = computeDefaultRef(sortedTools);

  const toolsMarkup = sortedTools.map(t => `
    <div class="form-check form-check-inline me-3 mb-1">
      <input class="form-check-input calibrate-tool-checkbox" type="checkbox" id="calibrate-tool-${t}" value="${t}" checked>
      <label class="form-check-label" for="calibrate-tool-${t}">T${t}</label>
    </div>
  `).join("");

  const refMarkup = sortedTools.map(t => `
    <div class="form-check form-check-inline me-3 mb-1">
      <input class="form-check-input calibrate-ref-checkbox" type="checkbox" id="calibrate-ref-${t}" value="${t}" ${t === defaultRef ? "checked" : ""}>
      <label class="form-check-label" for="calibrate-ref-${t}">T${t}</label>
    </div>
  `).join("");

  return `
  <div class="row g-2">
    <div class="col-md-6">
      <div class="border border-secondary-subtle rounded p-2 bg-dark h-100">
        <div class="d-flex justify-content-between align-items-center mb-2">
          <span class="fs-6">Tools to calibrate</span>
          <div class="form-check mb-0">
            <input class="form-check-input" type="checkbox" id="calibrate-select-all" checked>
            <label class="form-check-label" for="calibrate-select-all"><small class="text-secondary">Select all</small></label>
          </div>
        </div>
        <div>${toolsMarkup}</div>
      </div>
    </div>
    <div class="col-md-6">
      <div class="border border-secondary-subtle rounded p-2 bg-dark h-100">
        <div class="d-flex justify-content-between align-items-center mb-1">
          <span class="fs-6">Reference (Master) tool</span>
          <small class="text-secondary">Default: ${defaultRef === 0 ? "T0" : `T${defaultRef}`}</small>
        </div>
        <div>${refMarkup}</div>
      </div>
    </div>
    <div class="col-12"><small class="text-secondary">Gilt f&uuml;r Kamera (XY Offsets), Z-Switch und Eddy-Messlauf.</small></div>
  </div>`;
}

function calibrateButton(toolNumbers = [], enabled = false) {
  const btnClass = enabled ? "btn-primary" : "btn-secondary";
  const disabledAttr = enabled ? "" : "disabled";

  const cfg = (_offsetZCalcDefault || "unknown").toLowerCase();
  const cfgLabel = `Config (offset.cfg: ${cfg})`;

  const sel = (_uiZCalcSelection || "config").toLowerCase();
  const selConfig = sel === "config" ? "selected" : "";
  const selMedian = sel === "median" ? "selected" : "";
  const selAvg    = sel === "average" ? "selected" : "";
  const selTrim   = sel === "trimmed" ? "selected" : "";

  // Tool-Auswahl und Referenz: siehe toolSelectionPanel() ueber dem
  // Akkordeon -- hier nur noch Z-Rechnung, Temperatur und der Knopf.
  return `
<li class="list-group-item bg-body-tertiary p-2">
  <div class="container">
    <div class="row pb-2">
      <div class="col-12">
        <div class="border border-secondary-subtle rounded p-2 bg-dark">
          <div class="d-flex justify-content-between align-items-center mb-1">
            <span class="fs-6">Z calculation</span>
            <small class="text-secondary">Default = Config</small>
          </div>
          <select id="z-calc-method" class="form-select form-select-sm w-auto d-inline-block">
            <option value="config" ${selConfig}>${cfgLabel}</option>
            <option value="median" ${selMedian}>Median</option>
            <option value="average" ${selAvg}>Average</option>
            <option value="trimmed" ${selTrim}>Trimmed mean</option>
          </select>
        </div>
      </div>
    </div>

    <div class="row pb-2">
      <div class="col-12">
        <div class="border border-secondary-subtle rounded p-2 bg-dark">
          <div class="d-flex justify-content-between align-items-center mb-1">
            <span class="fs-6">Extruder temperature</span>
            <small class="text-secondary">${tapMinTempHint()}</small>
          </div>
          <div class="input-group input-group-sm w-auto">
            <input type="number" id="calibrate-extruder-temp" class="form-control form-control-sm" style="max-width:80px;" min="0" max="350" step="5" value="${tapMinTempDefault()}" placeholder="0">
            <span class="input-group-text">°C</span>
          </div>
        </div>
      </div>
    </div>

    <div class="row">
      <div class="col-12">
        <button class="btn ${btnClass} w-100" id="calibrate-all-btn" ${disabledAttr}>
          CALIBRATE Z-OFFSETS
        </button>
      </div>
    </div>
  </div>
</li>`;
}

// --------------------------
// Probe Calibration Section
// --------------------------
function probeCalResultsTable(sortedTools) {
  var hasAny = sortedTools.some(function(t) {
    var k = String(t);
    return _toolProbeOffsets[k] !== undefined
        || _probeCalResults[k] || _eddyTapDeviations[k];
  });
  if (!hasAny) return '';

  var rows = sortedTools.map(function(t) {
    var k = String(t);
    var current = _toolProbeOffsets[k];
    var currentStr = (typeof current === 'number') ? current.toFixed(3) : '-';
    var eddy = _eddyTapDeviations[k];

    // Measured with an Eddy nozzle tap: the deviation describes the Eddy's
    // tap zero, not the mechanical Tap's trigger height, so it is shown
    // for information and never offered for apply.
    if (eddy) {
      var devTxt = (eddy.deviation >= 0 ? '+' : '') + eddy.deviation.toFixed(3);
      return '<tr>' +
        '<td class="px-2 py-1 fw-bold">T' + t + '</td>' +
        '<td class="px-2 py-1 text-end text-secondary">' + currentStr + '</td>' +
        '<td class="px-2 py-1 text-end text-secondary" colspan="2">' +
          '<span class="badge bg-secondary me-1">Eddy</span>' +
          'tap dev ' + devTxt +
        '</td>' +
      '</tr>';
    }

    var calResult = _probeCalResults[k];
    var newStr = calResult ? calResult.probe_z_offset.toFixed(3) : '-';
    var diffStr = '-';
    if (typeof current === 'number' && calResult) {
      var diff = calResult.probe_z_offset - current;
      diffStr = (diff >= 0 ? '+' : '') + diff.toFixed(3);
    }
    return '<tr>' +
      '<td class="px-2 py-1 fw-bold">T' + t + '</td>' +
      '<td class="px-2 py-1 text-end text-secondary">' + currentStr + '</td>' +
      '<td class="px-2 py-1 text-end">' + (calResult ? '<span class="text-success">' + newStr + '</span>' : newStr) + '</td>' +
      '<td class="px-2 py-1 text-end text-info">' + diffStr + '</td>' +
    '</tr>';
  }).join('');

  var eddyNote = sortedTools.some(function(t) { return !!_eddyTapDeviations[String(t)]; })
    ? '<div class="small text-secondary mt-1">' +
        'Eddy rows show how far the Eddy tap zero sits from the Z-switch ' +
        'result. Correct via <code>tap_adjust_z</code> — it is not a ' +
        '<code>tool_probe</code> offset.' +
      '</div>'
    : '';

  // Apply button only makes sense once a calibration produced new values
  var hasResults = sortedTools.some(function(t) { return !!_probeCalResults[String(t)]; });
  var applyBtn = hasResults
    ? '<div class="pt-2">' +
        '<button class="btn btn-success w-100" id="apply-probe-btn">' +
          '<i class="bi bi-check-circle"></i> APPLY PROBE OFFSETS TO CONFIG' +
        '</button>' +
      '</div>'
    : '';

  return '<div class="border border-secondary-subtle rounded p-2 bg-dark">' +
    '<span class="fs-6 fw-bold d-block mb-1">Probe Z-Offsets</span>' +
    '<table class="table table-sm table-borderless mb-0" style="font-size:0.85rem;">' +
      '<thead><tr>' +
        '<th class="px-2 py-1 text-secondary">Tool</th>' +
        '<th class="px-2 py-1 text-end text-secondary">Current</th>' +
        '<th class="px-2 py-1 text-end text-secondary">New</th>' +
        '<th class="px-2 py-1 text-end text-secondary">Diff</th>' +
      '</tr></thead>' +
      '<tbody>' + rows + '</tbody>' +
    '</table>' +
    eddyNote +
    applyBtn +
  '</div>';
}

function probeCalibrationSection(toolNumbers, enabled) {
  var sortedTools = toolNumbers.slice().sort(function(a, b) { return a - b; });
  var config = getProbeCalConfig(sortedTools);
  var btnClass = enabled ? "btn-primary" : "btn-secondary";
  var disabledAttr = enabled ? "" : "disabled";

  var probeOptions = function(selectedProbe) {
    return _availableProbes.map(function(p) {
      var sel = (p === selectedProbe) ? ' selected' : '';
      var label = p;
      if (p === 'probe') label = 'probe (Tap)';
      return '<option value="' + p + '"' + sel + '>' + label + '</option>';
    }).join('');
  };

  // Reference section
  var refToolOptions = sortedTools.map(function(t) {
    var sel = (t === config.ref_tool) ? ' selected' : '';
    return '<option value="' + t + '"' + sel + '>T' + t + '</option>';
  }).join('');

  var toolRows = sortedTools.map(function(t) {
    var isRef = (t === config.ref_tool);
    var currentProbe = config.tool_probes[String(t)] || 'probe';
    var refBadge = isRef
      ? '<span class="badge bg-success ms-2">REF</span>'
      : '';

    return '<div class="d-flex align-items-center gap-2 p-2 bg-dark rounded mb-1">' +
      '<div class="form-check mb-0">' +
        '<input class="form-check-input probe-cal-tool-cb" type="checkbox" value="' + t + '" id="probe-cal-tool-' + t + '" checked>' +
      '</div>' +
      '<span class="fw-bold text-nowrap" style="width:30px;">T' + t + '</span>' +
      '<select class="form-select form-select-sm probe-cal-probe-select" data-tool="' + t + '">' +
        probeOptions(currentProbe) +
      '</select>' +
      refBadge +
    '</div>';
  }).join('');

  return '<div class="container p-0">' +
    '<div class="border border-secondary-subtle rounded p-2 bg-dark mb-2">' +
      '<div class="d-flex justify-content-between align-items-center mb-2">' +
        '<span class="fs-6 fw-bold">Reference Probe</span>' +
      '</div>' +
      '<div class="row g-2">' +
        '<div class="col-4">' +
          '<label class="form-label small text-secondary mb-1">Tool</label>' +
          '<select class="form-select form-select-sm" id="probe-cal-ref-tool">' +
            refToolOptions +
          '</select>' +
        '</div>' +
        '<div class="col-8">' +
          '<label class="form-label small text-secondary mb-1">Probe</label>' +
          '<select class="form-select form-select-sm" id="probe-cal-ref-probe">' +
            probeOptions(config.ref_probe) +
          '</select>' +
        '</div>' +
      '</div>' +
    '</div>' +
    '<div class="border border-secondary-subtle rounded p-2 bg-dark mb-2">' +
      '<div class="d-flex justify-content-between align-items-center mb-2">' +
        '<span class="fs-6 fw-bold">Tool Probes</span>' +
      '</div>' +
      toolRows +
    '</div>' +
    '<div class="border border-secondary-subtle rounded p-2 bg-dark mb-2">' +
      '<div class="d-flex justify-content-between align-items-center mb-1">' +
        '<span class="fs-6">Extruder temperature</span>' +
        '<small class="text-secondary">' + tapMinTempHint() + '</small>' +
      '</div>' +
      '<div class="input-group input-group-sm w-auto">' +
        '<input type="number" id="probe-cal-extruder-temp" class="form-control form-control-sm" style="max-width:80px;" min="0" max="350" step="5" value="' + tapMinTempDefault() + '" placeholder="0">' +
        '<span class="input-group-text">&deg;C</span>' +
      '</div>' +
    '</div>' +
    '<button class="btn ' + btnClass + ' w-100 mb-2" id="probe-cal-btn" ' + disabledAttr + '>' +
      'CALIBRATE PROBE OFFSETS' +
    '</button>' +
    '<div id="probe-cal-results-container">' + probeCalResultsTable(sortedTools) + '</div>' +
  '</div>';
}

// Remember dropdown selection
$(document).on("change", "#z-calc-method", function(){
  _uiZCalcSelection = ($(this).val() || "config").toLowerCase();
});

// Calibrate click
$(document).on("click", "#calibrate-all-btn", function() {
  const selectedTools = $(".calibrate-tool-checkbox:checked")
    .map(function(){ return parseInt(this.value, 10); })
    .get()
    .filter(v => !Number.isNaN(v));

  const refTool = getSelectedReferenceTool(0);
  if (!selectedTools.includes(refTool)) selectedTools.unshift(refTool);

  const method = ($("#z-calc-method").val() || "config").toLowerCase();
  const extruderTemp = parseInt($("#calibrate-extruder-temp").val(), 10) || 0;
  syncTapMinTemp(extruderTemp);

  // Only send override if not config
  const zCalcPart = (method !== "config") ? ` Z_CALC=${method}` : "";
  const tempPart = (extruderTemp > 0) ? ` EXTRUDER_TEMP=${extruderTemp}` : "";
  const script = `CALIBRATE_ALL_Z_OFFSETS TOOLS=${selectedTools.join(",")}${zCalcPart}${tempPart} REF=${refTool}`;

  const body =
    '<div class="border border-secondary-subtle rounded p-2 mb-2 bg-dark">' +
      '<table class="table table-sm table-borderless mb-0" style="font-size:0.85rem;"><tbody>' +
        '<tr><td class="px-1 py-0 text-secondary">Tools</td>' +
            '<td class="px-1 py-0 fw-bold">' + selectedTools.map(t => "T" + t).join(", ") + '</td></tr>' +
        '<tr><td class="px-1 py-0 text-secondary">Reference</td>' +
            '<td class="px-1 py-0 fw-bold">T' + escapeHtml(refTool) + '</td></tr>' +
        '<tr><td class="px-1 py-0 text-secondary">Z calc</td>' +
            '<td class="px-1 py-0">' + escapeHtml(method) + '</td></tr>' +
        '<tr><td class="px-1 py-0 text-secondary">Extruder temp</td>' +
            '<td class="px-1 py-0">' + (extruderTemp > 0 ? escapeHtml(extruderTemp) + ' &deg;C' : 'no heating') + '</td></tr>' +
      '</tbody></table>' +
    '</div>' +
    '<div class="small text-secondary mb-2">Command: <code>' + escapeHtml(script) + '</code></div>' +
    '<div class="small text-warning">' +
      '<i class="bi bi-exclamation-triangle"></i> The printer moves and changes tools. ' +
      'Nothing is written to a config file — the results appear in the table for review.' +
    '</div>';

  confirmDialog({
    title: "Start Z-switch calibration?",
    body: body,
    okLabel: "OK — start",
    okClass: "btn-primary"
  }).then(function(ok) {
    if (!ok) return;

    const $btn = $("#calibrate-all-btn");
    $btn.prop("disabled", true).text("Calibrating...");
    if (typeof showToast === 'function') showToast("Calibration started...", "info");

    sendGcodeWithRecovery(script, "Calibration failed", function (attempt) {
      $btn.text(attempt === 1 ? "Calibrating..." : "Home/QGL...");
    })
      .then(function (r) {
        if (r.ok) console.log("Calibration started:", script);
        if (typeof showToast !== 'function') return r;
        if (r.ok) {
          showToast("Calibration command sent", "success");
        } else if (r.transport) {
          // Verbindung weg, der Drucker rechnet weiter - kein Fehler
          showToast("Verbindung zum Lauf verloren - er laeuft weiter. "
                    + "Fortschritt in der Konsole.", "warning");
        }
        return r;
      })
      .then(function () {
        $btn.prop("disabled", false).text("CALIBRATE Z-OFFSETS");
      });
  });
});

$(document).on("click", "span[id$='-x-new'], span[id$='-y-new'], span[id$='-z-new'], span[id$='-pz-new']", function() {
  const id = $(this).attr("id") || "";

  // Probe Z offset (tool_probe z_offset)
  if (id.endsWith("-pz-new")) {
    const rawText = $(this).attr("data-raw") || $(this).find(":first-child").text();
    const numericValue = parseFloat(rawText);
    if (Number.isNaN(numericValue)) return;
    const value = formatClipboardNumber(numericValue);
    if (value === null) return;
    const payload = `z_offset: ${value}`;
    copyTextToClipboard(payload, "copy probe z")
      .then(function() { console.log(`Copied ${payload}`); })
      .catch(function(err) { console.error('Clipboard copy failed:', err); });
    return;
  }

  const match = id.match(/-([xyz])-new$/u);
  if (!match) return;

  const axis = match[1];
  const rawText = $(this).attr("data-raw") || $(this).find(":first-child").text();
  const numericValue = parseFloat(rawText);
  if (Number.isNaN(numericValue)) {
    OffsetDebug.error("Copy failed: NaN value", {id, rawText});
    return;
  }

  const value = formatClipboardNumber(numericValue);
  if (value === null) {
    OffsetDebug.error("Copy failed: formatClipboardNumber returned null", {id, numericValue});
    return;
  }

  const payload = `gcode_${axis}_offset: ${value}`;
  copyTextToClipboard(payload, `copy ${axis}`)
    .then(function() {
      console.log(`Copied ${payload}`);
      OffsetDebug.log("Copied single offset", {axis, payload});
    })
    .catch(function(err) {
      console.error('Clipboard copy failed:', err);
      OffsetDebug.error("Clipboard copy failed", err);
    });
});

$(document).on("click", "button[data-copy-all]", function() {
  const tool = $(this).attr("data-copy-all");
  const $x = $("#T" + tool + "-x-new");
  const $y = $("#T" + tool + "-y-new");
  const $z = $("#T" + tool + "-z-new");

  if (!$x.length || !$y.length || !$z.length) {
    OffsetDebug.error("Copy all failed: missing elements", {tool, hasX: $x.length, hasY: $y.length, hasZ: $z.length});
    return;
  }

  const rawX = $x.attr("data-raw") || $x.find(":first-child").text();
  const rawY = $y.attr("data-raw") || $y.find(":first-child").text();
  const rawZ = $z.attr("data-raw") || $z.find(":first-child").text();

  const xVal = formatClipboardNumber(parseFloat(rawX));
  const yVal = formatClipboardNumber(parseFloat(rawY));
  const zVal = formatClipboardNumber(parseFloat(rawZ));

  if (xVal === null || yVal === null || zVal === null) {
    OffsetDebug.error("Copy all failed: invalid values", {tool, rawX, rawY, rawZ});
    return;
  }

  const payload = `gcode_x_offset: ${xVal}\n` +
                  `gcode_y_offset: ${yVal}\n` +
                  `gcode_z_offset: ${zVal}`;

  copyTextToClipboard(payload, "copy all")
    .then(function() {
      console.log(`Copied all offsets for T${tool}`);
      OffsetDebug.log("Copied all offsets", {tool, payload});
    })
    .catch(function(err) {
      console.error('Clipboard copy failed:', err);
      OffsetDebug.error("Clipboard copy failed", err);
    });
});
// Select all
$(document).on("change", "#calibrate-select-all", function () {
  const checked = $(this).is(":checked");
  $(".calibrate-tool-checkbox").prop("checked", checked);
  const refTool = getSelectedReferenceTool(0);
  $(`#calibrate-tool-${refTool}`).prop("checked", true);
  syncSelectAllState();
});

$(document).on("change", ".calibrate-tool-checkbox", function () {
  const refTool = getSelectedReferenceTool(0);
  $(`#calibrate-tool-${refTool}`).prop("checked", true);
  syncSelectAllState();
});

$(document).on("change", ".calibrate-ref-checkbox", function () {
  $(".calibrate-ref-checkbox").not(this).prop("checked", false);
  $(this).prop("checked", true);

  const refVal = parseInt($(this).val(), 10);
  if (!Number.isNaN(refVal)) offsetMasterTool = refVal;

  $(`#calibrate-tool-${refVal}`).prop("checked", true);

  // Rerender so Master row moves
  getTools();
});

// --------------------------
// Probe Calibration Events
// --------------------------

// Ref tool change
$(document).on("change", "#probe-cal-ref-tool", function() {
  if (!_probeCalConfig) return;
  _probeCalConfig.ref_tool = parseInt($(this).val(), 10);
  saveProbeCalConfig();
  getTools();
});

// Ref probe change
$(document).on("change", "#probe-cal-ref-probe", function() {
  if (!_probeCalConfig) return;
  _probeCalConfig.ref_probe = $(this).val();
  _probeCalConfig.tool_probes[String(_probeCalConfig.ref_tool)] = $(this).val();
  saveProbeCalConfig();
  getTools();
});

// Per-tool probe change
$(document).on("change", ".probe-cal-probe-select", function() {
  if (!_probeCalConfig) return;
  var tool = $(this).data("tool");
  _probeCalConfig.tool_probes[String(tool)] = $(this).val();
  saveProbeCalConfig();
});

// --------------------------
// PID Calibration Section
// --------------------------
function pidDefault(key, fallback) {
  var v = _pidDefaults ? _pidDefaults[key] : null;
  return (typeof v === 'number') ? v : fallback;
}

function pidResultsTable(sortedTools) {
  var hasAny = sortedTools.some(function (t) {
    return _toolPid[String(t)] || _pidResults[String(t)];
  });
  if (!hasAny) return '';

  var rows = sortedTools.map(function (t) {
    var k = String(t);
    var cur = _toolPid[k];
    var neu = _pidResults[k];
    if (!cur && !neu) return '';

    var cell = function (key) {
      var c = cur ? cur[key].toFixed(3) : '-';
      if (!neu) return '<td class="px-2 py-1 text-end text-secondary">' + c + '</td>';
      return '<td class="px-2 py-1 text-end">' +
             '<span class="text-secondary">' + c + '</span> &rarr; ' +
             '<span class="text-success">' + neu[key].toFixed(3) + '</span></td>';
    };

    var cond = neu
      ? '<div class="small text-secondary">' + neu.temp + '&deg;C, ' +
        neu.height + 'mm, Fan ' + neu.fan + '%</div>'
      : '';

    return '<tr>' +
      '<td class="px-2 py-1 fw-bold align-top">T' + t + cond + '</td>' +
      cell('pid_kp') + cell('pid_ki') + cell('pid_kd') +
    '</tr>';
  }).join('');

  var hasNew = sortedTools.some(function (t) { return !!_pidResults[String(t)]; });
  var applyBtn = hasNew
    ? '<div class="pt-2">' +
        '<button class="btn btn-success w-100" id="apply-pid-btn">' +
          '<i class="bi bi-check-circle"></i> APPLY PID TO CONFIG' +
        '</button>' +
      '</div>'
    : '';

  return '<div class="border border-secondary-subtle rounded p-2 bg-dark">' +
    '<span class="fs-6 fw-bold d-block mb-1">Extruder PID</span>' +
    '<table class="table table-sm table-borderless mb-0" style="font-size:0.85rem;">' +
      '<thead><tr>' +
        '<th class="px-2 py-1 text-secondary">Tool</th>' +
        '<th class="px-2 py-1 text-end text-secondary">Kp</th>' +
        '<th class="px-2 py-1 text-end text-secondary">Ki</th>' +
        '<th class="px-2 py-1 text-end text-secondary">Kd</th>' +
      '</tr></thead>' +
      '<tbody>' + rows + '</tbody>' +
    '</table>' +
    applyBtn +
  '</div>';
}

function pidCalibrationSection(toolNumbers, enabled) {
  var sortedTools = toolNumbers.slice().sort(function (a, b) { return a - b; });
  var btnClass = enabled ? 'btn-primary' : 'btn-secondary';
  var disabledAttr = enabled ? '' : 'disabled';

  var defTool = pidDefault('tool', null);
  // Vorauswahl: pid_tool falls gesetzt, sonst alle - wie bei der
  // Z-Switch-Kalibrierung, damit mehrere Tools in einem Lauf durchlaufen.
  var toolsMarkup = sortedTools.map(function (t) {
    var checked = (defTool === null || t === defTool) ? ' checked' : '';
    return '<div class="form-check form-check-inline me-3 mb-1">' +
      '<input class="form-check-input pid-tool-checkbox" type="checkbox" ' +
        'id="pid-tool-' + t + '" value="' + t + '"' + checked + '>' +
      '<label class="form-check-label" for="pid-tool-' + t + '">T' + t + '</label>' +
    '</div>';
  }).join('');

  var num = function (id, value, min, max, step, unit) {
    return '<div class="input-group input-group-sm">' +
      '<input type="number" id="' + id + '" class="form-control form-control-sm" ' +
        'min="' + min + '" max="' + max + '" step="' + step + '" value="' + value + '">' +
      '<span class="input-group-text">' + unit + '</span>' +
    '</div>';
  };

  return '<div class="container p-0">' +
    '<div class="border border-secondary-subtle rounded p-2 bg-dark mb-2">' +
      '<div class="row g-2">' +
        '<div class="col-12">' +
          '<div class="d-flex justify-content-between align-items-center mb-1">' +
            '<span class="small text-secondary">Tools to tune</span>' +
            '<div class="form-check mb-0">' +
              '<input class="form-check-input" type="checkbox" id="pid-select-all">' +
              '<label class="form-check-label" for="pid-select-all">' +
                '<small class="text-secondary">Select all</small></label>' +
            '</div>' +
          '</div>' +
          '<div>' + toolsMarkup + '</div>' +
        '</div>' +
        '<div class="col-6">' +
          '<label class="form-label small text-secondary mb-1">Temperature</label>' +
          num('pid-temp', pidDefault('temp', 200), 60, 500, 5, '&deg;C') +
        '</div>' +
        '<div class="col-6">' +
          '<label class="form-label small text-secondary mb-1">Height over bed</label>' +
          num('pid-height', pidDefault('height', 10), 0, 100, 1, 'mm') +
        '</div>' +
        '<div class="col-6">' +
          '<label class="form-label small text-secondary mb-1">Part fan</label>' +
          num('pid-fan', pidDefault('fan_speed', 100), 0, 100, 5, '%') +
        '</div>' +
      '</div>' +
      '<div class="small text-secondary mt-2">' +
        'Selected tools are tuned one after another, each over the bed centre. ' +
        'Fan and distance to the bed dominate ' +
        'the thermal response, so tune at the values you actually print with. ' +
        'Defaults come from <code>[offset] pid_temp / pid_height / pid_fan_speed</code>.' +
      '</div>' +
    '</div>' +
    '<button class="btn ' + btnClass + ' w-100 mb-2" id="pid-cal-btn" ' + disabledAttr + '>' +
      'CALIBRATE PID' +
    '</button>' +
    '<div id="pid-results-container">' + pidResultsTable(sortedTools) + '</div>' +
  '</div>';
}

// Updates pid_Kp/Ki/Kd in the [extruderN] section of the tool config files.
// Klipper stages these for SAVE_CONFIG itself, but pid_Kp lives in the
// included T<n>.cfg, so SAVE_CONFIG would refuse with "conflicts with
// included value" - CALIBRATE_TOOL_PID therefore un-stages them and the
// file is written here instead.
function updateToolPidValues(toolValues) {
  var tools = Object.keys(toolValues);
  var missing = [];

  function processNext(idx) {
    if (idx >= tools.length) {
      reportMissingKeys(missing);
      return Promise.resolve();
    }
    var t = tools[idx];
    var v = toolValues[t];
    var filePath = "toolchanger/tools/T" + t + ".cfg";
    return updateConfigFile(filePath, function (content) {
      var updated = content;
      var ok = true;
      [['pid_Kp', v.pid_kp], ['pid_Ki', v.pid_ki], ['pid_Kd', v.pid_kd]]
        .forEach(function (pair) {
          var next = replaceInConfigSection(updated, v.extruder, pair[0],
                                            pair[1].toFixed(3));
          if (next === null) {
            ok = false;
            missing.push({file: filePath, section: v.extruder, key: pair[0]});
          } else {
            updated = next;
          }
        });
      if (!ok) return null;
      return updated;
    }).then(function () { return processNext(idx + 1); });
  }
  return processNext(0);
}

// Calibrate button click
$(document).on("click", "#probe-cal-btn", function() {
  var config = getProbeCalConfig([]);
  if (!config) return;

  var selectedTools = $(".probe-cal-tool-cb:checked")
    .map(function() { return parseInt(this.value, 10); })
    .get()
    .filter(function(v) { return !Number.isNaN(v); });

  if (!selectedTools.length) {
    if (typeof showToast === 'function') showToast("No tools selected", "warning");
    return;
  }

  // Build GCode script: SET_PROBE_CAL_MAP per tool, then CALIBRATE
  var lines = [];
  selectedTools.forEach(function(t) {
    var probe = config.tool_probes[String(t)] || 'probe';
    lines.push('SET_PROBE_CAL_MAP TOOL=' + t + ' PROBE="' + probe + '"');
  });
  var probeTemp = parseInt($("#probe-cal-extruder-temp").val(), 10) || 0;
  syncTapMinTemp(probeTemp);
  var tempPart = (probeTemp > 0) ? ' EXTRUDER_TEMP=' + probeTemp : '';
  // REF_PROBE must be sent explicitly — it is not part of probe_cal_map,
  // and without it Klipper falls back to "first Eddy found".
  var refProbePart = config.ref_probe
    ? ' REF_PROBE="' + config.ref_probe + '"' : '';
  lines.push('CALIBRATE_PROBE_OFFSETS TOOLS=' + selectedTools.join(',') +
             ' REF_TOOL=' + config.ref_tool + refProbePart + tempPart);

  var script = lines.join('\n');

  var toolRows = selectedTools.map(function(t) {
    var probe = config.tool_probes[String(t)] || 'probe';
    var isRef = (parseInt(t, 10) === parseInt(config.ref_tool, 10));
    var isEddy = probe.indexOf('eddy') !== -1;
    return '<tr>' +
      '<td class="px-1 py-0 fw-bold text-nowrap">T' + escapeHtml(t) +
        (isRef ? ' <span class="badge bg-success">REF</span>' : '') + '</td>' +
      '<td class="px-1 py-0 text-secondary">&rarr;</td>' +
      '<td class="px-1 py-0"><code>' + escapeHtml(probe) + '</code></td>' +
      '<td class="px-1 py-0 text-end small text-secondary">' +
        (isEddy ? 'info only' : 'writes z_offset') + '</td>' +
    '</tr>';
  }).join('');

  var body =
    '<div class="border border-secondary-subtle rounded p-2 mb-2 bg-dark">' +
      '<div class="fw-bold mb-1">Tools &amp; probes</div>' +
      '<table class="table table-sm table-borderless mb-0" style="font-size:0.85rem;">' +
        '<tbody>' + toolRows + '</tbody>' +
      '</table>' +
    '</div>' +
    '<div class="border border-secondary-subtle rounded p-2 mb-2 bg-dark">' +
      '<table class="table table-sm table-borderless mb-0" style="font-size:0.85rem;"><tbody>' +
        '<tr><td class="px-1 py-0 text-secondary">Reference tool</td>' +
            '<td class="px-1 py-0 fw-bold">T' + escapeHtml(config.ref_tool) + '</td></tr>' +
        '<tr><td class="px-1 py-0 text-secondary">Reference probe</td>' +
            '<td class="px-1 py-0"><code>' + escapeHtml(config.ref_probe) + '</code></td></tr>' +
        '<tr><td class="px-1 py-0 text-secondary">Extruder temp</td>' +
            '<td class="px-1 py-0">' + (probeTemp > 0 ? escapeHtml(probeTemp) + ' &deg;C' : 'no heating') + '</td></tr>' +
      '</tbody></table>' +
    '</div>' +
    '<div class="small text-secondary mb-2">Command:<br><code>' +
      escapeHtml(script).replace(/\n/g, '<br>') + '</code></div>' +
    '<div class="small text-warning">' +
      '<i class="bi bi-exclamation-triangle"></i> The printer moves and changes tools. ' +
      'Every probe measures by touching the nozzle to the bed.' +
    '</div>' +
    '<div class="small text-secondary mt-1">' +
      'Tools measured with their mechanical Tap get <code>z_offset</code> set at ' +
      'runtime and staged for <code>SAVE_CONFIG</code> — use APPLY PROBE OFFSETS ' +
      'afterwards to write it into the tool config files instead. Tools measured ' +
      'with an Eddy only report how far the Eddy tap zero deviates; that value is ' +
      'not a <code>tool_probe</code> offset and is never applied.' +
    '</div>';

  confirmDialog({
    title: "Start probe calibration?",
    body: body,
    okLabel: "OK — start",
    okClass: "btn-primary"
  }).then(function(ok) {
    if (!ok) return;

    var $btn = $("#probe-cal-btn");
    $btn.prop("disabled", true).text("Calibrating...");
    if (typeof showToast === 'function') showToast("Probe calibration started...", "info");

    sendGcodeWithRecovery(script, "Probe calibration failed", function (attempt) {
      $btn.text(attempt === 1 ? "Calibrating..." : "Home/QGL...");
    })
      .then(function (r) {
        if (r.ok) console.log("Probe calibration started:", script);
        if (typeof showToast !== 'function') return r;
        if (r.ok) {
          showToast("Probe calibration command sent", "success");
        } else if (r.transport) {
          // Verbindung weg, der Drucker rechnet weiter - kein Fehler
          showToast("Verbindung zum Lauf verloren - er laeuft weiter. "
                    + "Fortschritt in der Konsole.", "warning");
        }
        return r;
      })
      .then(function () {
        $btn.prop("disabled", false).text("CALIBRATE PROBE OFFSETS");
      });
  });
});

// Apply XY offsets to Klipper
$(document).on("click", "#apply-xy-btn", function() {
  var $btn = $(this);
  var btnHtml = '<i class="bi bi-check-circle"></i> APPLY XY OFFSETS TO KLIPPER';
  var master = getSelectedReferenceTool(0);
  var lines = [];
  var toolOffsets = {};
  var pending = [];
  var requests = [];

  $('button.toolchange-btn').each(function(){
    var tool = $(this).data("tool");
    if (parseInt(tool, 10) === parseInt(master, 10)) return;
    var rawX = $("#T" + tool + "-x-new").attr("data-raw");
    var rawY = $("#T" + tool + "-y-new").attr("data-raw");
    if (rawX && rawY) {
      lines.push("SET_TOOL_GCODE_OFFSET T=" + tool + " X=" + rawX + " Y=" + rawY);
      toolOffsets[tool] = { x: rawX, y: rawY };
      pending.push({ tool: tool, x: rawX, y: rawY });
      requests.push({ tool: tool, key: "gcode_x_offset", section: null });
      requests.push({ tool: tool, key: "gcode_y_offset", section: null });
    }
  });

  if (!lines.length) {
    if (typeof showToast === 'function') showToast("No XY offsets to apply", "warning");
    return;
  }

  // Es gibt inzwischen zwei unabhaengige Wege, die dieselben zwei
  // Config-Schluessel schreiben (dieser hier und applyXyOffset/
  // applyAllXyOffsets im XY-Block). Beide nennen deshalb ausdruecklich,
  // GEGEN WELCHES Referenztool gerechnet wurde - sonst uebernimmt man
  // Werte, deren Bezugspunkt man gar nicht kennt und dem man widersprechen
  // koennte.
  var note = 'Values are computed against reference tool <strong>T' +
    escapeHtml(master) + '</strong>, which is not changed.<br>' +
    '"Current" is read from the config file. The new values are also set at runtime ' +
    'via <code>SET_TOOL_GCODE_OFFSET</code>.';

  $btn.prop("disabled", true).text("Loading...");
  fetchToolConfigValues(requests).then(function(cur) {
    $btn.prop("disabled", false).html(btnHtml);

    var entries = pending.map(function(p) {
      return {
        tool: p.tool,
        file: "toolchanger/tools/T" + p.tool + ".cfg",
        section: "tool T" + p.tool,
        changes: [
          { key: "gcode_x_offset", from: cur[p.tool + "|gcode_x_offset"], to: p.x },
          { key: "gcode_y_offset", from: cur[p.tool + "|gcode_y_offset"], to: p.y }
        ]
      };
    });

    return confirmDialog({
      title: "Apply XY offsets?",
      body: offsetChangeListHtml(entries, note),
      okLabel: "OK — apply",
      okClass: "btn-success"
    });
  }).then(function(ok) {
    if (!ok) return;

    $btn.prop("disabled", true).text("Applying...");
    // Set runtime offsets immediately
    var script = lines.join('\n');
    var runtimeDone = $.get(printerUrl(printerIp, "/printer/gcode/script?script=" + encodeURIComponent(script)));
    // Also persist to config files directly (avoids SAVE_CONFIG conflict with included files)
    var configDone = updateToolConfigOffsets(toolOffsets);
    Promise.all([runtimeDone, configDone])
      .then(function() {
        if (typeof showToast === 'function') showToast("XY offsets applied and saved to config", "success");
      })
      .catch(function(err) {
        var detail = "";
        try { detail = (err.responseJSON || err).message || ""; } catch (_) {}
        // Eine fehlgeschlagene Uebernahme sieht sonst aus wie eine erfolgreiche.
        alertDialog("Apply XY offsets failed", escapeHtml(detail || "Unbekannter Fehler"));
      })
      .finally(function() {
        $btn.prop("disabled", false).html(btnHtml);
      });
  });
});

// Apply Z-switch offsets to Klipper
$(document).on("click", "#apply-z-btn", function() {
  var $btn = $(this);
  var btnHtml = '<i class="bi bi-check-circle"></i> APPLY Z OFFSETS TO KLIPPER';
  var lines = [];
  var toolOffsets = {};
  var pending = [];
  var requests = [];

  var keys = Object.keys(_zSwitchResults).sort(function(a, b){ return a - b; });
  keys.forEach(function(k) {
    var zOff = _zSwitchResults[k].z_offset;
    if (typeof zOff !== 'number') return;
    var zTxt = zOff.toFixed(6);
    lines.push("SET_TOOL_GCODE_OFFSET T=" + k + " Z=" + zTxt);
    toolOffsets[k] = { z: zTxt };
    pending.push({ tool: k, z: zTxt });
    requests.push({ tool: k, key: "gcode_z_offset", section: null });
  });

  if (!lines.length) {
    if (typeof showToast === 'function') showToast("No Z offsets to apply", "warning");
    return;
  }

  var note = '"Current" is read from the config file. The new values are also set at ' +
    'runtime via <code>SET_TOOL_GCODE_OFFSET</code>.';

  $btn.prop("disabled", true).text("Loading...");
  fetchToolConfigValues(requests).then(function(cur) {
    $btn.prop("disabled", false).html(btnHtml);

    var entries = pending.map(function(p) {
      return {
        tool: p.tool,
        file: "toolchanger/tools/T" + p.tool + ".cfg",
        section: "tool T" + p.tool,
        changes: [
          { key: "gcode_z_offset", from: cur[p.tool + "|gcode_z_offset"], to: p.z }
        ]
      };
    });

    return confirmDialog({
      title: "Apply Z offsets?",
      body: offsetChangeListHtml(entries, note),
      okLabel: "OK — apply",
      okClass: "btn-success"
    });
  }).then(function(ok) {
    if (!ok) return;

    $btn.prop("disabled", true).text("Applying...");
    // Set runtime offsets immediately
    var script = lines.join('\n');
    var runtimeDone = $.get(printerUrl(printerIp, "/printer/gcode/script?script=" + encodeURIComponent(script)));
    // Also persist to config files directly (avoids SAVE_CONFIG conflict with included files)
    var configDone = updateToolConfigOffsets(toolOffsets);
    Promise.all([runtimeDone, configDone])
      .then(function() {
        if (typeof showToast === 'function') showToast("Z offsets applied and saved to config", "success");
      })
      .catch(function(err) {
        var detail = "";
        try { detail = (err.responseJSON || err).message || ""; } catch (_) {}
        // Eine fehlgeschlagene Uebernahme sieht sonst aus wie eine erfolgreiche.
        alertDialog("Apply Z offsets failed", escapeHtml(detail || "Unbekannter Fehler"));
      })
      .finally(function() {
        $btn.prop("disabled", false).html(btnHtml);
      });
  });
});

// Apply probe offsets (tool_probe z_offset) to the tool config files
$(document).on("click", "#apply-probe-btn", function() {
  var $btn = $(this);
  var btnHtml = '<i class="bi bi-check-circle"></i> APPLY PROBE OFFSETS TO CONFIG';
  var toolZOffsets = {};
  var pending = [];
  var requests = [];

  var keys = Object.keys(_probeCalResults).sort(function(a, b){ return a - b; });
  keys.forEach(function(k) {
    var pz = _probeCalResults[k].probe_z_offset;
    if (typeof pz !== 'number') return;
    var pzTxt = pz.toFixed(3);
    toolZOffsets[k] = pzTxt;
    pending.push({ tool: k, z: pzTxt });
    requests.push({ tool: k, key: "z_offset", section: "tool_probe T" + k });
  });

  if (!pending.length) {
    if (typeof showToast === 'function') showToast("No probe offsets to apply", "warning");
    return;
  }

  var note = '"Current" is read from the config file. ' +
    'CALIBRATE_PROBE_OFFSETS already set the new values at runtime — writing them ' +
    'into the files above makes them permanent, no <code>SAVE_CONFIG</code> needed.';

  $btn.prop("disabled", true).text("Loading...");
  fetchToolConfigValues(requests).then(function(cur) {
    $btn.prop("disabled", false).html(btnHtml);

    var entries = pending.map(function(p) {
      return {
        tool: p.tool,
        file: "toolchanger/tools/T" + p.tool + ".cfg",
        section: "tool_probe T" + p.tool,
        changes: [
          { key: "z_offset", from: cur[p.tool + "|z_offset"], to: p.z }
        ]
      };
    });

    return confirmDialog({
      title: "Apply probe offsets?",
      body: offsetChangeListHtml(entries, note),
      okLabel: "OK — apply",
      okClass: "btn-success"
    });
  }).then(function(ok) {
    if (!ok) return;

    $btn.prop("disabled", true).text("Applying...");
    updateToolProbeOffsets(toolZOffsets)
      .then(function() {
        if (typeof showToast === 'function') showToast("Probe offsets saved to config", "success");
        // Refresh the "Current" column so the table reflects the new state
        return fetchOffsetStatus().then(function() {
          var $container = $('#probe-cal-results-container');
          if ($container.length) {
            var tools = Object.keys(_toolProbeOffsets).map(Number).sort(function(a, b){ return a - b; });
            $container.html(probeCalResultsTable(tools));
          }
        });
      })
      .catch(function(err) {
        var detail = "";
        try { detail = (err.responseJSON || err).message || ""; } catch (_) {}
        // Eine fehlgeschlagene Uebernahme sieht sonst aus wie eine erfolgreiche.
        alertDialog("Apply probe offsets failed", escapeHtml(detail || "Unbekannter Fehler"));
      })
      .finally(function() {
        $btn.prop("disabled", false).html(btnHtml);
      });
  });
});

// PID calibration click
$(document).on("click", "#pid-cal-btn", function () {
  var $btn = $(this);
  var tools = $(".pid-tool-checkbox:checked")
    .map(function () { return parseInt(this.value, 10); })
    .get()
    .filter(function (v) { return !Number.isNaN(v); });
  var temp = parseInt($("#pid-temp").val(), 10);
  var height = parseFloat($("#pid-height").val());
  var fan = parseInt($("#pid-fan").val(), 10);

  if (!tools.length) {
    if (typeof showToast === 'function') showToast("Kein Tool ausgewaehlt", "warning");
    return;
  }
  if (Number.isNaN(temp)) {
    if (typeof showToast === 'function') showToast("Temperatur waehlen", "warning");
    return;
  }
  if (Number.isNaN(height)) height = 10;
  if (Number.isNaN(fan)) fan = 0;

  var script = 'CALIBRATE_TOOL_PID TOOLS=' + tools.join(',') + ' TEMP=' + temp +
               ' HEIGHT=' + height + ' FAN=' + fan;

  var curRow = tools.map(function (tn) {
    var cur = _toolPid[String(tn)];
    return '<tr><td class="px-1 py-0 text-secondary">T' + escapeHtml(tn) + '</td>' +
      '<td class="px-1 py-0">' + (cur
        ? 'Kp ' + cur.pid_kp.toFixed(3) + ' &nbsp;Ki ' + cur.pid_ki.toFixed(3) +
          ' &nbsp;Kd ' + cur.pid_kd.toFixed(3)
        : '<span class="text-secondary">noch nicht getunt</span>') + '</td></tr>';
  }).join('');

  var body =
    '<div class="border border-secondary-subtle rounded p-2 mb-2 bg-dark">' +
      '<table class="table table-sm table-borderless mb-0" style="font-size:0.85rem;"><tbody>' +
        '<tr><td class="px-1 py-0 text-secondary">Tools</td>' +
            '<td class="px-1 py-0 fw-bold">' +
            tools.map(function (x) { return 'T' + x; }).join(', ') + '</td></tr>' +
        '<tr><td class="px-1 py-0 text-secondary">Temperature</td>' +
            '<td class="px-1 py-0 fw-bold">' + escapeHtml(temp) + ' &deg;C</td></tr>' +
        '<tr><td class="px-1 py-0 text-secondary">Height over bed</td>' +
            '<td class="px-1 py-0">' + escapeHtml(height) + ' mm (bed centre)</td></tr>' +
        '<tr><td class="px-1 py-0 text-secondary">Part fan</td>' +
            '<td class="px-1 py-0">' + escapeHtml(fan) + ' %</td></tr>' +
      '</tbody></table>' +
      '<div class="small text-secondary mt-1">Aktuelle Werte</div>' +
      '<table class="table table-sm table-borderless mb-0" style="font-size:0.85rem;"><tbody>' +
        curRow +
      '</tbody></table>' +
    '</div>' +
    '<div class="small text-secondary mb-2">Command: <code>' + escapeHtml(script) + '</code></div>' +
    '<div class="small text-warning">' +
      '<i class="bi bi-exclamation-triangle"></i> Each tool is picked up and heated ' +
      'to ' + escapeHtml(temp) + '&deg;C. Tuning takes several minutes per tool - ' +
      escapeHtml(tools.length) + ' selected.' +
    '</div>' +
    '<div class="small text-secondary mt-1">' +
      'Nothing is written yet — the result appears in the table for review. ' +
      'Persist it with APPLY PID, never with <code>SAVE_CONFIG</code>: ' +
      '<code>pid_Kp</code> lives in the included <code>T&lt;n&gt;.cfg</code>, ' +
      'which SAVE_CONFIG cannot write.' +
    '</div>';

  confirmDialog({
    title: "Start PID tuning?",
    body: body,
    okLabel: "OK — start",
    okClass: "btn-primary"
  }).then(function (ok) {
    if (!ok) return;

    $btn.prop("disabled", true).text("Tuning...");
    if (typeof showToast === 'function') showToast("PID tuning started...", "info");

    sendGcodeWithRecovery(script, "PID tuning failed", function (attempt) {
      $btn.text(attempt === 1 ? "Tuning..." : "Home/QGL...");
    })
      .then(function (r) {
        if (typeof showToast !== 'function') return r;
        if (r.ok) {
          showToast("PID tuning done", "success");
        } else if (r.transport) {
          // Verbindung weg, der Drucker rechnet weiter - kein Fehler
          showToast("Verbindung zum Lauf verloren - er laeuft weiter. "
                    + "Fortschritt in der Konsole.", "warning");
        }
        return r;
      })
      .then(function () {
        $btn.prop("disabled", false).text("CALIBRATE PID");
        fetchOffsetStatus().then(function () {
          var $c = $('#pid-results-container');
          if ($c.length) {
            var tools = Object.keys(_toolPid).map(Number).sort(function (a, b) { return a - b; });
            $c.html(pidResultsTable(tools));
          }
        });
      });
  });
});

// --------------------------
// Dock Calibration
//
// Ein Lauf besteht aus vielen kurzen Kommandos mit Wartezeiten fuer den
// Menschen dazwischen. Der Ablauf wird deshalb hier gesteuert, nicht per
// Polling auf dock_state: jede Antwort fuehrt zum naechsten Dialog. Das
// ist deterministisch und bricht nicht, wenn eine Statusabfrage ausfaellt.
// --------------------------
function dockDefault(key, fallback) {
  var v = _dockDefaults ? _dockDefaults[key] : null;
  return (typeof v === 'number') ? v : fallback;
}

function dockParkOf(t) {
  var o = _toolParkPositions[String(t)];
  return o || null;
}

function dockResultsTable(sortedTools) {
  var rows = sortedTools.map(function (t) {
    var cur = dockParkOf(t);
    var neu = _dockResults[String(t)];
    if (!cur && !neu) return '';
    var cell = function (key) {
      var c = cur && typeof cur[key] === 'number' ? cur[key].toFixed(3) : '-';
      if (!neu) return '<td class="px-2 py-1 text-end text-secondary">' + c + '</td>';
      return '<td class="px-2 py-1 text-end">' +
        '<span class="text-secondary">' + c + '</span> &rarr; ' +
        '<span class="text-success">' + neu[key].toFixed(3) + '</span></td>';
    };
    return '<tr><td class="px-2 py-1 fw-bold">T' + t + '</td>' +
      cell('params_park_x') + cell('params_park_y') + cell('params_park_z') +
    '</tr>';
  }).join('');
  if (!rows) return '';

  var hasNew = sortedTools.some(function (t) { return !!_dockResults[String(t)]; });
  var applyBtn = hasNew
    ? '<div class="pt-2"><button class="btn btn-success w-100" id="apply-dock-btn">' +
      '<i class="bi bi-check-circle"></i> APPLY DOCK TO CONFIG</button></div>'
    : '';

  return '<div class="border border-secondary-subtle rounded p-2 bg-dark">' +
    '<span class="fs-6 fw-bold d-block mb-1">Dock-Positionen</span>' +
    '<table class="table table-sm table-borderless mb-0" style="font-size:0.85rem;">' +
      '<thead><tr><th class="px-2 py-1 text-secondary">Tool</th>' +
        '<th class="px-2 py-1 text-end text-secondary">park_x</th>' +
        '<th class="px-2 py-1 text-end text-secondary">park_y</th>' +
        '<th class="px-2 py-1 text-end text-secondary">park_z</th></tr></thead>' +
      '<tbody>' + rows + '</tbody></table>' + applyBtn +
  '</div>';
}

function dockCalibrationSection(toolNumbers, enabled) {
  var sortedTools = toolNumbers.slice().sort(function (a, b) { return a - b; });
  var btnClass = enabled ? 'btn-primary' : 'btn-secondary';
  var disabledAttr = enabled ? '' : 'disabled';

  var toolsMarkup = sortedTools.map(function (t) {
    return '<div class="form-check form-check-inline me-3 mb-1">' +
      '<input class="form-check-input dock-tool-checkbox" type="checkbox" ' +
        'id="dock-tool-' + t + '" value="' + t + '">' +
      '<label class="form-check-label" for="dock-tool-' + t + '">T' + t + '</label>' +
    '</div>';
  }).join('');

  var num = function (id, value, min, max, step, unit) {
    return '<div class="input-group input-group-sm">' +
      '<input type="number" id="' + id + '" class="form-control form-control-sm" ' +
        'min="' + min + '" max="' + max + '" step="' + step + '" value="' + value + '">' +
      '<span class="input-group-text">' + unit + '</span></div>';
  };

  return '<div class="container p-0">' +
    '<div class="border border-secondary-subtle rounded p-2 bg-dark mb-2">' +
      '<div class="row g-2">' +
        '<div class="col-12">' +
          '<div class="d-flex justify-content-between align-items-center mb-1">' +
            '<span class="small text-secondary">Tools zu kalibrieren</span>' +
            '<div class="form-check mb-0">' +
              '<input class="form-check-input" type="checkbox" id="dock-select-all">' +
              '<label class="form-check-label" for="dock-select-all">' +
                '<small class="text-secondary">Alle</small></label></div>' +
          '</div>' +
          '<div>' + toolsMarkup + '</div>' +
        '</div>' +
        '<div class="col-6">' +
          '<label class="form-label small text-secondary mb-1">Anfahrhöhe Z</label>' +
          num('dock-start-z', dockDefault('start_z', 100), 1, 400, 1, 'mm') +
        '</div>' +
        '<div class="col-6">' +
          '<label class="form-label small text-secondary mb-1">Y bei Neukalibrierung</label>' +
          num('dock-new-y', dockDefault('new_y', 0), -200, 400, 1, 'mm') +
        '</div>' +
        '<div class="col-6">' +
          '<label class="form-label small text-secondary mb-1">Testfahrt Tiefe</label>' +
          num('dock-test-depth', dockDefault('test_depth', 15), 0.5, 100, 0.5, 'mm') +
        '</div>' +
        '<div class="col-6">' +
          '<label class="form-label small text-secondary mb-1">Testfahrt Wiederholungen</label>' +
          num('dock-test-repeats', dockDefault('test_repeats', 1), 1, 20, 1, '&times;') +
        '</div>' +
        '<div class="col-6">' +
          '<label class="form-label small text-secondary mb-1">Testfahrt Geschwindigkeit</label>' +
          num('dock-test-speed', dockDefault('test_speed', 5), 0.5, 50, 0.5, 'mm/s') +
        '</div>' +
      '</div>' +
      '<div class="small text-secondary mt-2">' +
        'Die Tool-Offsets werden während des Laufs abgeschaltet — sonst läge ' +
        'die Dockposition um genau diese Offsets daneben. Defaults aus ' +
        '<code>[offset] dock_*</code>.' +
      '</div>' +
      '<div class="form-check form-switch mt-2 mb-0">' +
        '<input class="form-check-input" type="checkbox" id="dock-dry-run">' +
        '<label class="form-check-label small" for="dock-dry-run">' +
          '<span class="text-warning">Trockenlauf</span>' +
          '<span class="text-secondary"> — alle Fenster durchklicken, ' +
          'ohne dass ein Kommando an den Drucker geht</span></label>' +
      '</div>' +
    '</div>' +
    '<button class="btn ' + btnClass + ' w-100 mb-2" id="dock-cal-btn" ' + disabledAttr + '>' +
      'MOUNT KALIBRIERUNG' +
    '</button>' +
    '<div id="dock-results-container">' + dockResultsTable(sortedTools) + '</div>' +
  '</div>';
}

function syncDockSelectAllState() {
  var $all = $(".dock-tool-checkbox");
  var $checked = $(".dock-tool-checkbox:checked");
  $("#dock-select-all").prop("checked", $all.length > 0 && $all.length === $checked.length);
}

$(document).on("change", "#dock-select-all", function () {
  $(".dock-tool-checkbox").prop("checked", $(this).is(":checked"));
});
$(document).on("change", ".dock-tool-checkbox", syncDockSelectAllState);

function dockIsDryRun() {
  return $("#dock-dry-run").is(":checked");
}

// Im Trockenlauf sichtbar machen, was gefahren WUERDE. Ohne diese Zeile
// waere der Durchlauf stumm und man koennte nicht pruefen, ob die richtigen
// Kommandos in der richtigen Reihenfolge kaemen.
function dockDryLog(script) {
  console.log("[Trockenlauf] " + script);
  var $c = $('#console-output');
  if ($c.length) {
    $c.append($('<div>').css('color', '#d29922')
        .text('[Trockenlauf] ' + script));
    $c.scrollTop($c[0].scrollHeight);
  }
  if (typeof showToast === 'function') {
    showToast("Trockenlauf: " + script, "warning");
  }
}

function dockDryNote() {
  return dockIsDryRun()
    ? '<div class="alert alert-warning py-2 px-3 small mb-2">' +
      '<i class="bi bi-eye"></i> <strong>Trockenlauf</strong> — der Drucker ' +
      'bewegt sich nicht, es wird nichts gemessen und nichts gespeichert. ' +
      'Die Kommandos stehen in der Klipper-Konsole.</div>'
    : '';
}

// Ein Schritt der Prozedur. Liefert das Ergebnis von sendGcodeWithRecovery.
function dockStep(script, title) {
  if (dockIsDryRun()) {
    dockDryLog(script);
    return Promise.resolve({ ok: true, dry: true });
  }
  return sendGcodeWithRecovery(script, title);
}

function dockAbort() {
  if (dockIsDryRun()) {
    dockDryLog("DOCK_CALIBRATE_ABORT");
    return Promise.resolve(null);
  }
  // Promise.resolve() drumherum: die Kette darf nicht davon abhaengen, dass
  // hier ein jqXHR mit .always zurueckkommt.
  return Promise.resolve(
    $.get(printerUrl(printerIp,
      "/printer/gcode/script?script=" + encodeURIComponent("DOCK_CALIBRATE_ABORT")))
  ).catch(function () { return null; })
   .then(function () { return refreshDockTable(); });
}

function refreshDockTable() {
  return fetchOffsetStatus().then(function () {
    var $c = $('#dock-results-container');
    if (!$c.length) return;
    var tools = Object.keys(_toolParkPositions).map(Number)
                  .sort(function (a, b) { return a - b; });
    $c.html(dockResultsTable(tools));
  });
}

// Schritt 2/3: Tool mounten lassen, dann anfahren, dann joggen + testen.
function dockToolLoop(tools, idx, opts) {
  if (idx >= tools.length) {
    if (typeof showToast === 'function') {
      showToast(dockIsDryRun()
        ? "Trockenlauf beendet - es wurde nichts gemessen und nichts geschrieben"
        : "Dock-Kalibrierung fertig - mit APPLY DOCK schreiben",
        dockIsDryRun() ? "warning" : "success");
    }
    return refreshDockTable();
  }
  var t = tools[idx];

  return alertDialog(
    (dockIsDryRun() ? "Trockenlauf: " : "") + "T" + t + " montieren",
    dockDryNote() +
    '<p class="mb-2">Der Kopf steht auf Bettmitte, Z=' +
      escapeHtml(opts.startZ) + 'mm.</p>' +
    '<p class="mb-0"><strong>Ist T' + escapeHtml(t) + ' montiert?</strong> ' +
    'Nach dem Bestätigen fährt der Kopf den Dock-Weg an. Der Drucker ' +
    'bewegt sich dabei.</p>',
    // Der zweite Button bricht ab. Ein rotes "OK" daneben laedt zum
    // genau falschen Klick ein, deshalb heisst er, was er tut.
    { extraLabel: '<i class="bi bi-check2"></i> T' + t + ' ist montiert - anfahren',
      extraClass: 'btn-primary',
      okLabel: 'Abbrechen',
      okClass: 'btn-secondary' }
  ).then(function (choice) {
    if (choice !== 'extra') return dockAbort();
    return dockStep("DOCK_CALIBRATE_MOUNTED", "Dock-Anfahrt fehlgeschlagen")
      .then(function (r) {
        if (!r.ok) return dockAbort();
        return dockJogLoop(tools, idx, opts);
      });
  });
}

function dockJogLoop(tools, idx, opts) {
  var t = tools[idx];
  return alertDialog(
    (dockIsDryRun() ? "Trockenlauf: " : "") + "T" + t + ": Dockposition einstellen",
    dockDryNote() +
    '<p class="mb-2">Fahre den Kopf per Jogging (Offset-UI oder Mainsail), ' +
      'bis T' + escapeHtml(t) + ' sauber in seinem Dock sitzt.</p>' +
    '<p class="mb-2 text-secondary"><strong>Testfahrt</strong> fährt ' +
      escapeHtml(opts.depth) + 'mm nach unten und zurück, ' +
      escapeHtml(opts.repeats) + '&times;, mit ' + escapeHtml(opts.speed) +
      'mm/s.</p>' +
    '<p class="mb-2 text-secondary"><strong>Übernehmen</strong> liest die ' +
      'aktuelle Position als <code>params_park_x/y/z</code> und geht zum ' +
      'nächsten Tool. Geschrieben wird erst am Ende mit APPLY DOCK.</p>' +
    '<p class="mb-0 text-secondary"><strong>Übernehmen + schreiben</strong> ' +
      'schreibt die Position von T' + escapeHtml(t) + ' sofort in die Config — ' +
      'nach einem Bestätigungsfenster mit Vorher/Nachher. Nützlich bei vielen ' +
      'Tools: ein Abbruch später kostet dann nicht den ganzen Lauf.</p>',
    { extraLabel: '<i class="bi bi-arrow-down-up"></i> Testfahrt',
      extraClass: 'btn-warning',
      extra2Label: '<i class="bi bi-save"></i> Übernehmen + schreiben',
      extra2Class: 'btn-success',
      okLabel: '<i class="bi bi-check-circle"></i> Übernehmen',
      okClass: 'btn-outline-success',
      showCancel: true,
      cancelLabel: 'Abbrechen' }
  ).then(function (choice) {
    if (choice === 'extra') {
      var script = "DOCK_CALIBRATE_TEST DEPTH=" + opts.depth +
                   " REPEATS=" + opts.repeats + " SPEED=" + opts.speed;
      return dockStep(script, "Testfahrt fehlgeschlagen").then(function (r) {
        if (!r.ok && !r.transport) return dockAbort();
        return dockJogLoop(tools, idx, opts);
      });
    }
    if (choice !== true && choice !== 'extra2') return dockAbort();
    var writeNow = (choice === 'extra2');
    return dockStep("DOCK_CALIBRATE_ACCEPT", "Übernehmen fehlgeschlagen")
      .then(function (r) {
        if (!r.ok) return dockAbort();
        return refreshDockTable().then(function () {
          // "Uebernehmen + schreiben" gilt nur fuer dieses Tool. Sonst
          // sammeln sich die Werte bis zum Schluss, und ein Abbruch danach
          // kostet den ganzen Lauf. Im Trockenlauf gibt es nichts zu
          // schreiben.
          if (!writeNow || dockIsDryRun()) return null;
          return applyDockValues([String(t)]);
        }).then(function () {
          return dockToolLoop(tools, idx + 1, opts);
        });
      });
  });
}

$(document).on("click", "#dock-cal-btn", function () {
  var $btn = $(this);
  var tools = $(".dock-tool-checkbox:checked").map(function () {
    return parseInt($(this).val(), 10);
  }).get().sort(function (a, b) { return a - b; });

  if (!tools.length) {
    if (typeof showToast === 'function') showToast("Kein Tool ausgewählt", "warning");
    return;
  }

  var opts = {
    startZ: parseFloat($("#dock-start-z").val()) || dockDefault('start_z', 100),
    newY: parseFloat($("#dock-new-y").val()) || dockDefault('new_y', 0),
    depth: parseFloat($("#dock-test-depth").val()) || dockDefault('test_depth', 15),
    repeats: parseInt($("#dock-test-repeats").val(), 10) || dockDefault('test_repeats', 1),
    speed: parseFloat($("#dock-test-speed").val()) || dockDefault('test_speed', 5)
  };

  var haveAll = tools.every(function (t) { return !!dockParkOf(t); });
  var body =
    '<div class="border border-secondary-subtle rounded p-2 mb-2 bg-dark">' +
      '<table class="table table-sm table-borderless mb-0" style="font-size:0.85rem;"><tbody>' +
        '<tr><td class="px-1 py-0 text-secondary">Tools</td><td class="px-1 py-0 fw-bold">' +
          tools.map(function (t) { return 'T' + t; }).join(', ') + '</td></tr>' +
        '<tr><td class="px-1 py-0 text-secondary">Anfahrhöhe</td><td class="px-1 py-0">' +
          escapeHtml(opts.startZ) + ' mm über Bettmitte</td></tr>' +
        '<tr><td class="px-1 py-0 text-secondary">Testfahrt</td><td class="px-1 py-0">' +
          escapeHtml(opts.depth) + ' mm, ' + escapeHtml(opts.repeats) + '&times;, ' +
          escapeHtml(opts.speed) + ' mm/s</td></tr>' +
      '</tbody></table></div>' +
    '<div class="small text-secondary mb-2">' +
      '<strong>Neukalibrierung</strong> startet auf Bettmitte mit Y=' +
      escapeHtml(opts.newY) + 'mm — für Docks ohne brauchbare Werte.<br>' +
      '<strong>Nachkalibrierung</strong> fährt die gespeicherte Dockposition ' +
      'an und korrigiert von dort' +
      (haveAll ? '.' : ' — <span class="text-warning">nicht für alle ' +
        'gewählten Tools vorhanden</span>.') +
    '</div>' +
    '<div class="small text-warning">' +
      '<i class="bi bi-exclamation-triangle"></i> Der Drucker bewegt sich. ' +
      'Die Tool-Offsets werden für die Dauer des Laufs abgeschaltet.' +
    '</div>';

  confirmDialog({
    title: (dockIsDryRun() ? "Trockenlauf: " : "") + "Dock-Kalibrierung starten",
    body: dockDryNote() + body,
    okLabel: '<i class="bi bi-arrow-repeat"></i> Nachkalibrierung',
    okClass: haveAll ? 'btn-primary' : 'btn-secondary',
    extraLabel: '<i class="bi bi-plus-circle"></i> Neukalibrierung',
    extraClass: 'btn-warning',
    cancelLabel: 'Abbrechen'
  }).then(function (choice) {
    if (choice !== true && choice !== 'extra') return;
    var mode = (choice === 'extra') ? 'NEW' : 'RECAL';
    var script = "DOCK_CALIBRATE_START MODE=" + mode +
                 " TOOLS=" + tools.join(',') +
                 " START_Z=" + opts.startZ + " NEW_Y=" + opts.newY;
    $btn.prop("disabled", true).text("Läuft...");
    var release = function () {
      $btn.prop("disabled", false).text("MOUNT KALIBRIERUNG");
    };
    return dockStep(script, "Dock-Kalibrierung fehlgeschlagen")
      .then(function (r) {
        if (!r.ok) return null;
        return dockToolLoop(tools, 0, opts);
      })
      // Ein gesperrter Button waere sonst nur per Reload zu loesen, und der
      // Lauf laeuft auf dem Drucker womoeglich noch.
      .catch(function (e) {
        console.error("Dock calibration aborted:", e);
        return dockAbort();
      })
      .then(release, release);
  });
});

// Schreibt params_park_x/y/z in die [tool Tn]-Sektion der Tool-Configs.
function updateToolDockValues(toolValues) {
  var tools = Object.keys(toolValues);
  var missing = [];

  function processNext(idx) {
    if (idx >= tools.length) {
      reportMissingKeys(missing);
      return Promise.resolve();
    }
    var t = tools[idx];
    var v = toolValues[t];
    var filePath = "toolchanger/tools/T" + t + ".cfg";
    var section = "tool T" + t;
    return updateConfigFile(filePath, function (content) {
      var updated = content;
      var ok = true;
      ['params_park_x', 'params_park_y', 'params_park_z'].forEach(function (key) {
        var next = replaceInConfigSection(updated, section, key,
                                          v[key].toFixed(3));
        if (next === null) {
          ok = false;
          missing.push({file: filePath, section: section, key: key});
        } else {
          updated = next;
        }
      });
      if (!ok) return null;
      return updated;
    }).then(function () { return processNext(idx + 1); });
  }
  return processNext(0);
}

// Schreibt die Dock-Werte der genannten Tools in die Configs. keys=null
// heisst: alles, was gemessen wurde. Immer mit Bestaetigungsdialog - eine
// falsche Dockposition laesst das Tool beim naechsten Wechsel danebengreifen.
function applyDockValues(keys, onBusy) {
  var toolValues = {};
  var requests = [];
  var wanted = (keys || Object.keys(_dockResults)).map(String);
  wanted.sort(function (a, b) { return a - b; }).forEach(function (k) {
    var v = _dockResults[k];
    if (!v || typeof v.params_park_x !== 'number') return;
    toolValues[k] = v;
    ['params_park_x', 'params_park_y', 'params_park_z'].forEach(function (key) {
      requests.push({ tool: k, key: key, section: "tool T" + k });
    });
  });
  var names = Object.keys(toolValues).sort(function (a, b) { return a - b; });
  if (!names.length) {
    if (typeof showToast === 'function') {
      showToast("Keine Dock-Werte zu übernehmen", "warning");
    }
    return Promise.resolve(false);
  }

  var busy = function (b) { if (onBusy) onBusy(b); };
  busy(true);
  return fetchToolConfigValues(requests).then(function (cur) {
    busy(false);
    var entries = names.map(function (k) {
      var v = _dockResults[k];
      return {
        tool: k,
        file: "toolchanger/tools/T" + k + ".cfg",
        section: "tool T" + k,
        changes: ['params_park_x', 'params_park_y', 'params_park_z'].map(function (key) {
          return { key: key, from: cur[k + "|" + key], to: v[key].toFixed(3) };
        })
      };
    });
    return confirmDialog({
      title: names.length === 1
        ? "Dock-Position von T" + names[0] + " übernehmen?"
        : "Dock-Positionen übernehmen?",
      body: offsetChangeListHtml(entries,
        '"Current" kommt aus der Config-Datei. Ein falscher Wert lässt das ' +
        'Tool beim nächsten Wechsel neben dem Dock landen — die Zahlen bitte ' +
        'gegen den Testlauf prüfen.'),
      okLabel: "OK — übernehmen",
      okClass: "btn-success",
      cancelLabel: "Später"
    });
  }).then(function (ok) {
    if (!ok) return false;
    busy(true);
    return updateToolDockValues(toolValues)
      .then(function () {
        if (typeof showToast === 'function') {
          showToast(names.length === 1
            ? "T" + names[0] + ": Dock-Position geschrieben"
            : "Dock-Positionen in die Config geschrieben", "success");
        }
        return true;
      })
      .catch(function (err) {
        var detail = "";
        try { detail = (err.responseJSON || err).message || ""; } catch (_) {}
        return alertDialog("Dock-Positionen übernehmen fehlgeschlagen",
                           escapeHtml(detail || "Unbekannter Fehler"))
          .then(function () { return false; });
      })
      .then(function (res) { busy(false); return res; });
  });
}

$(document).on("click", "#apply-dock-btn", function () {
  var $btn = $(this);
  var btnHtml = '<i class="bi bi-check-circle"></i> APPLY DOCK TO CONFIG';
  applyDockValues(null, function (b) {
    if (b) $btn.prop("disabled", true).text("Schreibe...");
    else $btn.prop("disabled", false).html(btnHtml);
  });
});

// --------------------------
// XY-Offset Vergleich (Eddy-Sweep vs. Kamera)
// --------------------------
// Stellt beide Messverfahren fuer den XY-Offset nebeneinander: die neue
// automatische Eddy-Messung (Klipper-seitig noch nicht gebaut, Task 2/3/5)
// und die bisherige manuelle Kameramethode (Erfassen ueber
// captureMountedToolPosition(), Task 9). Ohne Messwerte zeigt jede Zeile
// "nicht gemessen" - fuer die Eddy-Methode heute noch der einzige
// erreichbare Zustand, da weder die zweite Eddy-Spule noch das
// Locator-Modul existieren.

// Haelt bei zentriertem Fadenkreuz die aktuelle Kopfposition fest. Der
// Offset ist spaeter die Differenz zum Referenztool -- genau wie beim
// Eddy-Verfahren, damit beide vergleichbar bleiben.
function captureCameraPosition(toolNr) {
  var baseUrl = printerUrl(printerIp, "");
  return fetch(baseUrl + "/printer/objects/query?gcode_move", NO_CACHE)
    .then(function (r) { return r.json(); })
    .then(function (j) {
      var p = j.result.status.gcode_move.gcode_position;
      _cameraPositions[String(toolNr)] = {x: p[0], y: p[1]};
      renderXyBlock();
      if (typeof showToast === 'function') {
        showToast("T" + toolNr + ": Position festgehalten (" +
          p[0].toFixed(3) + " / " + p[1].toFixed(3) + ")", "info");
      }
    })
    .catch(function (err) {
      // Ohne diesen Fang bleibt ein Netzwerkfehler unsichtbar: der Nutzer
      // hat die Duese gerade muehsam zentriert, klickt "Position
      // uebernehmen", und ohne Meldung sieht er erst beim naechsten Blick
      // in die Tabelle, dass nichts festgehalten wurde - ohne zu wissen ob
      // der Klick verpufft ist oder die Position verrissen war.
      var detail = "";
      try { detail = (err.responseJSON || err).message || ""; } catch (_) {}
      return alertDialog("T" + toolNr + ": Position konnte nicht festgehalten werden",
                         escapeHtml(detail || "Unbekannter Fehler"));
    });
}

// Das MONTIERTE Tool, nicht das in der UI angehakte Referenztool: der
// Nutzer hat es aufgenommen und zentriert es gerade ueber dem Fadenkreuz.
function captureMountedToolPosition() {
  return $.get(printerUrl(printerIp, "/printer/objects/query?toolchanger"))
    .then(function (data) {
      var t = data && data.result && data.result.status &&
              data.result.status.toolchanger &&
              data.result.status.toolchanger.tool_number;
      if (t === undefined || t === null || t < 0) {
        return alertDialog("Kein Tool montiert",
          "Es ist kein Werkzeug aufgenommen. Erst ein Tool wählen, dann " +
          "die Düse über dem Fadenkreuz zentrieren.");
      }
      return captureCameraPosition(t);
    })
    .catch(function (err) {
      // Eigener Fang fuer die toolchanger-Abfrage selbst (getrennt vom
      // Fang in captureCameraPosition oben) - z.B. wenn Moonraker gerade
      // neu startet oder die Abfrage mitten im Werkzeugwechsel kommt.
      var detail = "";
      try { detail = (err.responseJSON || err).message || ""; } catch (_) {}
      return alertDialog("Montiertes Tool konnte nicht ermittelt werden",
                         escapeHtml(detail || "Unbekannter Fehler"));
    });
}

// Referenztool des XY-Blocks. Erste Quelle ist das, wogegen Klipper
// tatsaechlich gemessen hat (_xyResults.ref_tool); solange es das nicht
// gibt, gilt dieselbe Auswahl wie im Kalibrier-Abschnitt darueber.
//
// Vorher stand hier dreimal ein hart verdrahtetes 0. Das war auf zwei Arten
// falsch: waehlt der Nutzer T2 als Referenz, beschriftete der Block
// trotzdem T0 als "Referenztool" - und auf einer fremden Config ganz ohne
// T0 war die Kameramethode unbenutzbar, weil die Referenzposition unter
// einer Toolnummer gesucht wurde, die es nicht gibt (jede Zeile "nicht
// gemessen", ohne Erklaerung).
function xyRefTool() {
  var ref = _xyResults.ref_tool;
  if (ref !== undefined && ref !== null) return ref;
  var tools = Object.keys(_toolGcodeOffsets).map(function (t) {
    return parseInt(t, 10);
  }).filter(function (n) { return !isNaN(n); });
  return getSelectedReferenceTool(computeDefaultRef(tools));
}

// Offset der Kameramethode = Differenz zum Referenztool, genau wie beim
// Eddy-Verfahren. Nur so sind beide Verfahren vergleichbar.
function _cameraOffsetFor(toolNr) {
  var ref = xyRefTool();
  var here = _cameraPositions[String(toolNr)];
  var base = _cameraPositions[String(ref)];
  if (!here || !base) return null;
  return {x: here.x - base.x, y: here.y - base.y};
}

// Bootstrap-Accordion statt statischer Card in index.html - alle anderen
// Offset-Bereiche (Dock, PID, Z-Switch, Probe) werden ebenfalls hier
// dynamisch gebaut und in #offset-accordion eingehaengt. Die Kopfzeile des
// Accordion-Buttons selbst ist ein <button>; Radio-Gruppe und
// Assistent-Knopf koennen deshalb nicht dort hinein (verschachtelte
// interaktive Elemente sind ungueltiges HTML) und stehen stattdessen oben
// im Body.
function _xyFineGapStored() {
  try { return localStorage.getItem('offset_xy_fine_gap') || ''; } catch (e) { return ''; }
}

function xyOffsetSection() {
  var checkedAttr = function (m) { return (_xyMethod === m) ? ' checked' : ''; };
  return '<div class="container p-0">' +
    '<div class="d-flex align-items-center justify-content-between mb-2">' +
      '<div class="btn-group btn-group-sm" role="group" id="xy-method">' +
        '<input type="radio" class="btn-check" name="xy-method" ' +
          'id="xy-method-camera" value="camera"' + checkedAttr('camera') + '>' +
        '<label class="btn btn-outline-secondary" for="xy-method-camera">' +
          'Kamera (manuell)</label>' +
        '<input type="radio" class="btn-check" name="xy-method" ' +
          'id="xy-method-eddy" value="eddy"' + checkedAttr('eddy') + '>' +
        '<label class="btn btn-outline-secondary" for="xy-method-eddy">' +
          'Eddy-Sweep</label>' +
      '</div>' +
      '<div class="d-flex gap-1 align-items-center">' +
        // Feinspalt fuer den Messlauf (FINE_GAP), leer = Klipper-Default
        // 0,75 mm. Kleinerer Spalt = Spitze dominiert (offene Arbeiten 10.12).
        '<div class="input-group input-group-sm flex-nowrap" style="width:13.5em" title="Messspalt ueber der Spule fuer alle Tools (FINE_GAP); leer = Config-Default 0,75 mm. Kleiner = Spitze dominiert, mindestens 0,2 mm.">' +
          '<span class="input-group-text">Feinspalt</span>' +
          '<input type="number" class="form-control" id="xy-fine-gap" min="0.2" max="3" step="0.05" placeholder="0.75" value="' +
          escapeHtml(_xyFineGapStored()) + '">' +
          '<span class="input-group-text">mm</span>' +
        '</div>' +
        // Nur sichtbar, solange die Sonde aktiv ist (nozzle_locator geladen):
        // direkter Lauf ohne Neustart/Homen/Aufsetzen und das Deaktivieren
        // fuer den Fall "Sonde aktiv lassen" am Ende des Assistenten.
        '<button class="btn btn-sm btn-outline-primary d-none" id="xy-run-btn" ' +
          'title="Sonde ist aktiv: Messlauf direkt starten">Messlauf</button>' +
        '<button class="btn btn-sm btn-outline-warning d-none" id="xy-deactivate-btn" ' +
          'title="Sonde aus der Config nehmen, Klipper neu starten">Sonde deaktivieren</button>' +
        '<button class="btn btn-sm btn-primary" id="xy-wizard-btn">Assistent&hellip;</button>' +
      '</div>' +
    '</div>' +
    '<div id="xy-sparkline" class="mb-2"></div>' +
    xyMapPanel() +
    '<div id="xy-offset-body"></div>' +
  '</div>';
}

// Raster-Panel: startet NOZZLE_LOCATOR_MAP und zeigt das Bild live in 3D
// (printer.nozzle_locator.map, gezeichnet von js/map3d.js). Der Kopf muss
// dafuer schon auf Messhoehe ueber der Sonde stehen -- das Kommando prueft
// das, das Panel bewegt Z nie selbst.
function xyMapPanel() {
  function num(id, label, val, step) {
    return '<div class="col-auto"><label class="form-label small mb-0" for="' +
      id + '">' + label + '</label><input type="number" step="' + step +
      '" min="0" class="form-control form-control-sm" id="' + id +
      '" value="' + val + '" style="width:5.5em"></div>';
  }
  return '<details class="mb-2" id="xy-map-panel">' +
    '<summary class="small text-muted">Raster (C-Scan) mit Live-3D-Ansicht</summary>' +
    '<div class="row g-2 align-items-end mt-1">' +
      num('xy-map-width', 'Breite mm', 20, 1) +
      num('xy-map-height', 'H&ouml;he mm', 20, 1) +
      num('xy-map-pitch', 'Raster mm', 1, 0.5) +
      '<div class="col-auto"><label class="form-label small mb-0" for="xy-map-label">Label</label>' +
      '<input class="form-control form-control-sm" id="xy-map-label" value="T0" style="width:5.5em"></div>' +
      '<div class="col-auto"><button class="btn btn-sm btn-outline-primary" id="xy-map-start" ' +
      'onclick="xyStartMap()">Raster starten</button></div>' +
      '<div class="col-auto form-check small"><input type="checkbox" class="form-check-input" ' +
      'id="xy-map-log" checked onchange="renderXyMap3d(_xyMapLastStatus)">' +
      '<label class="form-check-label" for="xy-map-log">H&ouml;he logarithmisch ' +
      '(Block &uuml;berstrahlt die D&uuml;se sonst)</label></div>' +
    '</div>' +
    '<div id="xy-map-status" class="small text-muted mt-1"></div>' +
    '<div id="xy-map3d" style="width:100%;max-width:640px;height:420px"></div>' +
  '</details>';
}

// Baut das Kommando aus den Feldern. Wirft bei Unsinn, damit nie ein
// kaputter Maschinenbefehl rausgeht. Getestet in check_xy_offset_ui.js.
function xyMapCommand(p) {
  function num(v, name, lo, hi) {
    var n = parseFloat(v);
    if (!isFinite(n) || n <= lo || n > hi) {
      throw new Error(name + ' muss zwischen ' + lo + ' und ' + hi + ' mm liegen');
    }
    return n;
  }
  var w = num(p.width, 'Breite', 0, 100);
  var h = num(p.height, 'Hoehe', 0, 100);
  var pitch = num(p.pitch, 'Raster', 0, Math.min(w, h) / 2);
  var label = String(p.label || '').trim();
  if (label && !/^[A-Za-z0-9_.-]{1,16}$/.test(label)) {
    throw new Error('Label: nur Buchstaben, Ziffern, _ . - (max. 16)');
  }
  function fmt(n) { return String(+n.toFixed(3)); }
  var cmd = 'NOZZLE_LOCATOR_MAP WIDTH=' + fmt(w) + ' HEIGHT=' + fmt(h) +
            ' PITCH=' + fmt(pitch);
  if (label) cmd += ' LABEL=' + label;
  return cmd;
}

function xyStartMap() {
  var cmd;
  try {
    cmd = xyMapCommand({
      width: $('#xy-map-width').val(), height: $('#xy-map-height').val(),
      pitch: $('#xy-map-pitch').val(), label: $('#xy-map-label').val()
    });
  } catch (e) {
    $('#xy-map-status').text(e.message);
    return Promise.resolve(false);
  }
  return confirmDialog({
    title: 'Raster starten',
    body: '<p class="small">Der Kopf f&auml;hrt auf der aktuellen H&ouml;he ' +
          'Zeile f&uuml;r Zeile &uuml;ber die Sonde, davor einmal auf ' +
          '<code>park_z</code> und seitlich f&uuml;r die Basislinie. Er muss ' +
          'jetzt auf Messh&ouml;he &uuml;ber der Sonde stehen. Dauer etwa ' +
          'eine Minute je 20 Zeilen.</p><p class="mb-0"><code>' +
          escapeHtml(cmd) + '</code></p>',
    okLabel: 'Starten'
  }).then(function (ok) {
    if (!ok) return false;
    $('#xy-map-start').prop('disabled', true);
    $('#xy-map-status').text('Raster läuft …');
    // Halterung steht auf dem Bett: kein Recovery-Knopf, und {transport}
    // heisst "laeuft noch" -- die Live-Ansicht zeigt den Fortschritt.
    return xySendMounted(cmd, 'Raster').then(function () {
      return true;
    }, function (e) {
      $('#xy-map-status').text(e.message);
      $('#xy-map-start').prop('disabled', false);
      return false;
    });
  });
}

// Live-3D aus printer.nozzle_locator.map. Laeuft im selben Poll wie die
// Sparkline. Nach dem letzten Zeile: Knopf wieder frei, Link zur 2D-Ansicht.
var _xyMapKey = null;
var _xyMapLastStatus = null;
function renderXyMap3d(status) {
  var el = document.getElementById('xy-map3d');
  if (!el || typeof NozzleMap3d === 'undefined') return;
  var map = status && status.map;
  if (!map || !map.xs || !map.xs.length) return;
  _xyMapLastStatus = status;
  var log = $('#xy-map-log').is(':checked');
  var key = (map.label || '') + ':' + map.rows_total + ':' + map.x + ':' + map.y +
            ':' + (log ? 'log' : 'lin');
  if (key !== _xyMapKey) {
    _xyMapKey = key;
    $('#xy-map-panel').prop('open', true);
  }
  NozzleMap3d.renderMap3d(el, map, key, { log: log });
  var st = $('#xy-map-status');
  if (map.done) {
    $('#xy-map-start').prop('disabled', false);
    var url = 'map.html?ip=' + encodeURIComponent(printerIp) +
              (map.file ? '&a=' + encodeURIComponent(map.file) : '');
    st.html('Fertig' + (map.file ? ': <code>' + escapeHtml(map.file) + '</code>' : '') +
            ' &middot; <a href="' + url + '" target="_blank">2D-Ansicht und Differenzbild</a>');
  } else {
    st.text('Zeile ' + map.rows_done + ' von ' + map.rows_total);
  }
}

$(document).on("change", 'input[name="xy-method"]', function () {
  _xyMethod = this.value;
  renderXyBlock();
});

// Baut die Vergleichstabelle in #xy-offset-body neu auf. Wird nach dem
// Einhaengen des Accordion-Abschnitts sowie bei jedem Methodenwechsel
// aufgerufen.
// Ein Eintrag zaehlt erst als Messwert, wenn BEIDE Achsen als Zahl da
// sind. Eine blosse Wahrheitspruefung reichte nicht: das noch zu
// schreibende Klipper-Modul legt fuer ein Tool auch dann einen Eintrag an,
// wenn der Fit fehlschlaegt - dann fehlt x/y, und res.x.toFixed(3) wirft
// mitten in updateAllProbeResults(). Das lief bis Fix-Runde 3 in einer
// ungefangenen Promise-Kette: der Wurf haette Dock-, PID- und
// Probe-Tabelle gleich mit eingefroren, alle 2s neu, sichtbar nur in der
// Konsole.
function xyMeasured(res) {
  return !!(res && typeof res.x === 'number' && typeof res.y === 'number');
}

function renderXyBlock() {
  var body = document.getElementById('xy-offset-body');
  if (!body) return;
  var ref = xyRefTool();
  var rows = Object.keys(_toolGcodeOffsets).sort(function (a, b) {
    return parseInt(a, 10) - parseInt(b, 10);
  }).map(function (t) {
    var cur = _toolGcodeOffsets[t] || {x: 0, y: 0};
    var res = (_xyMethod === 'eddy') ? _xyResults[t] : _cameraOffsetFor(t);
    var isRef = (String(ref) === String(t));
    if (isRef) {
      var refName = (_xyMethod === 'eddy' && xyImageEntries(_xyResults[t]).length)
        ? '<a href="#" onclick="xyShowImage(\x27' + t + '\x27); return false;" title="Messbild anzeigen">T' + t + '</a>'
        : 'T' + t;
      return '<tr><td>' + refName + '</td>' +
             '<td>' + cur.x.toFixed(3) + ' / ' + cur.y.toFixed(3) + '</td>' +
             '<td colspan="4" class="text-muted">Referenztool</td></tr>';
    }
    if (!xyMeasured(res)) {
      return '<tr><td>T' + t + '</td>' +
             '<td>' + cur.x.toFixed(3) + ' / ' + cur.y.toFixed(3) + '</td>' +
             '<td colspan="4" class="text-muted">nicht gemessen</td></tr>';
    }
    var dx = (res.x - cur.x) * 1000, dy = (res.y - cur.y) * 1000;
    var name = xyImageEntries(res).length
      ? '<a href="#" onclick="xyShowImage(\x27' + t + '\x27); return false;" title="Messbild anzeigen">T' + t + '</a>'
      : 'T' + t;
    var bias = (res.x_fwd !== undefined)
      ? ('<span title="Differenz Hin- gegen Ruecksweep = gemessener ' +
         'Drift-Bias">' + ((res.x_fwd - res.x_rev) * 1000).toFixed(1) +
         ' / ' + ((res.y_fwd - res.y_rev) * 1000).toFixed(1) + ' &micro;m</span>')
      : '&mdash;';
    return '<tr><td>' + name + '</td>' +
      '<td>' + cur.x.toFixed(3) + ' / ' + cur.y.toFixed(3) + '</td>' +
      '<td>' + res.x.toFixed(3) + ' / ' + res.y.toFixed(3) + '</td>' +
      '<td>' + dx.toFixed(0) + ' / ' + dy.toFixed(0) + ' &micro;m</td>' +
      '<td>' + (res.z_compare !== undefined
                ? (res.z_compare * 1000).toFixed(0) + ' &micro;m' : '&mdash;') + '</td>' +
      '<td>' + bias + '</td>' +
      '<td><button class="btn btn-sm btn-outline-primary" ' +
      'onclick="applyXyOffset(\'' + t + '\', false)">&Uuml;bernehmen</button> ' +
      '<button class="btn btn-sm btn-primary" ' +
      'onclick="applyXyOffset(\'' + t + '\', true)">+ schreiben</button></td>' +
      '</tr>';
  }).join('');
  body.innerHTML =
    '<table class="table table-sm align-middle mb-2">' +
    '<thead><tr><th>Tool</th><th>aktuell X/Y</th><th>gemessen X/Y</th>' +
    '<th>&Delta;</th><th>Z-Vgl.</th><th>Drift-Bias</th><th></th></tr></thead>' +
    '<tbody>' + rows + '</tbody></table>' +
    '<button class="btn btn-sm btn-primary" onclick="applyAllXyOffsets()">' +
    'Alle &uuml;bernehmen + schreiben</button>';
}

// Messbild je Tool (Tobi, 2026-09-04): Klick auf den Toolnamen in der
// Tabelle zeigt die Messbilder des Laufs -- je Spalt das 2D-Raster
// (FIT2D) oder die X/Y-Profile der Linienmessung. Die Bilder kommen aus
// printer.offset.xy_results[t].images (Klipper legt sie je Spalt ab).
function xyImageEntries(entry) {
  if (!entry) return [];
  var list = Array.isArray(entry.images) ? entry.images.slice() : [];
  if (!list.length && entry.image) list = [entry.image];
  return list.filter(function (im) {
    return im && (im.kind === 'raster' || im.kind === 'profiles');
  }).map(function (im, i) {
    var gap = (typeof im.gap === 'number') ? im.gap.toFixed(2) + ' mm' : '?';
    return { label: 'Spalt ' + gap + (im.kind === 'raster' ? ' (Raster)' : ' (Profile)'),
             kind: im.kind, gap: im.gap, data: im, index: i };
  });
}

function xyImageBodyHtml(t, entry, entries, opts) {
  opts = opts || {};
  var head = '<div class="mb-2">Messbilder <b>T' + escapeHtml(String(t)) + '</b>';
  var facts = [];
  if (typeof entry.x === 'number') facts.push('Offset ' + entry.x.toFixed(4) + ' / ' + entry.y.toFixed(4));
  if (typeof entry.amplitude === 'number') facts.push('Amplitude ' + Math.round(entry.amplitude) + ' Hz');
  if (typeof entry.tip_slope_x === 'number') {
    facts.push('Steigung ' + (entry.tip_slope_x * 1000).toFixed(0) + ' / ' +
               (entry.tip_slope_y * 1000).toFixed(0) + ' &micro;m je mm Spalt');
  }
  if (entry.tip_method) facts.push('Extrapolation ' + escapeHtml(entry.tip_method === 'quadratic' ? 'quadratisch' : 'linear'));
  if (typeof entry.x_peak === 'number' && typeof entry.y_peak === 'number') {
    // Spitzenpunkt in Maschinenkoordinaten (Tobi, 2026-09-04) -- im Raster
    // als oranges Lot neben der weissen Scheitel-Linie
    facts.push('Spitze X ' + entry.x_peak.toFixed(3) + ' / Y ' + entry.y_peak.toFixed(3) +
               (entry.tip_method ? ' (extrapoliert auf Spalt 0)' : ' (Scheitel beim Messspalt)') +
               ' <span style="color:#ff7f0e">&#9646;</span>');
  }
  if (typeof entry.rho === 'number') facts.push('&rho; ' + entry.rho.toFixed(3));
  head += (facts.length ? '<div class="small text-muted">' + facts.join(' &middot; ') + '</div>' : '') + '</div>';
  if (!entries.length) return head + '<div class="text-muted">Keine Messbilder im Ergebnis (Lauf vor 2026-09-04?).</div>';
  // Dieselbe Ansicht wie das Live-Raster im XY-Block (Tobi, 2026-09-04):
  // Hoehe logarithmisch, dazu je Raster der Weg in die 2D-Ansicht und
  // der Ueberlagerungs-Editor.
  var ipq = encodeURIComponent(opts.ip || '');
  var anyRaster = entries.some(function (e) { return e.kind === 'raster'; });
  head += '<div class="d-flex flex-wrap gap-3 align-items-center mb-2 small">' +
    (anyRaster
      ? '<div class="form-check mb-0"><input type="checkbox" class="form-check-input" id="xy-img-log" checked ' +
        'onchange="xyRenderImages()"><label class="form-check-label" for="xy-img-log">H&ouml;he logarithmisch</label></div>'
      : '') +
    '<button type="button" class="btn btn-sm btn-outline-primary" onclick="xyOverlayFromImage(\x27' +
    escapeHtml(String(t)) + '\x27)">Mit anderem Tool &uuml;berlagern&hellip;</button></div>';
  return head + entries.map(function (e, i) {
    var link = (e.kind === 'raster' && opts.ip)
      ? ' &middot; <a href="map.html?ip=' + ipq + '&src=xy&t=' + encodeURIComponent(String(t)) +
        '&i=' + e.index + '" target="_blank">2D-Ansicht</a>'
      : '';
    return '<div class="mb-3"><div class="small fw-semibold">' + escapeHtml(e.label) + link + '</div>' +
      '<div id="xy-img-' + i + '" style="width:100%;height:' + (e.kind === 'raster' ? '360px' : '120px') + '"></div></div>';
  }).join('');
}

// X/Y-Profile als zwei kleine SVG-Kurven (Koerbe des letzten Sweeps).
function xyProfileSvg(points, label) {
  if (!points || points.length < 2) return '<span class="text-muted">' + label + ': keine Punkte</span>';
  var xs = points.map(function (p) { return p[0]; }), ys = points.map(function (p) { return p[1]; });
  var x0 = Math.min.apply(null, xs), x1 = Math.max.apply(null, xs);
  var y0 = Math.min.apply(null, ys), y1 = Math.max.apply(null, ys);
  var W = 300, H = 100;
  var sx = (x1 > x0) ? (W - 40) / (x1 - x0) : 0, sy = (y1 > y0) ? (H - 20) / (y1 - y0) : 0;
  var d = points.map(function (p, i) {
    return (i ? 'L' : 'M') + (30 + (p[0] - x0) * sx).toFixed(1) + ' ' + (H - 10 - (p[1] - y0) * sy).toFixed(1);
  }).join(' ');
  return '<svg width="' + W + '" height="' + H + '"><path d="' + d + '" fill="none" stroke="currentColor" stroke-width="1.5"/>' +
    '<text x="2" y="12" font-size="10">' + escapeHtml(label) + '</text>' +
    '<text x="30" y="' + (H - 1) + '" font-size="9">' + x0.toFixed(1) + '</text>' +
    '<text x="' + (W - 40) + '" y="' + (H - 1) + '" font-size="9">' + x1.toFixed(1) + '</text></svg>';
}

var _xyImageShown = null;
function xyShowImage(t) {
  var entry = _xyResults[t];
  var entries = xyImageEntries(entry);
  _xyImageShown = { t: t, entries: entries };
  alertDialog('Messbild T' + t, xyImageBodyHtml(t, entry || {}, entries, { ip: printerIp }),
              { okClass: 'btn-secondary' });
  // Der Dialogrumpf steht jetzt im DOM; Raster per plotly, Profile als SVG.
  setTimeout(xyRenderImages, 80);
}

function xyRenderImages() {
  var s = _xyImageShown;
  if (!s) return;
  var log = $('#xy-img-log').is(':checked');
  var entry = _xyResults[s.t] || {};
  var tip = (typeof entry.x_peak === 'number' && typeof entry.y_peak === 'number')
    ? { x: entry.x_peak, y: entry.y_peak,
        label: 'T' + s.t + (entry.tip_method ? ' Spitze (Spalt 0)' : ' Spitze (Messspalt)') } : null;
  s.entries.forEach(function (e, i) {
    var el = document.getElementById('xy-img-' + i);
    if (!el) return;
    if (e.kind === 'raster' && typeof NozzleMap3d !== 'undefined') {
      NozzleMap3d.renderMap3d(el, Object.assign({ done: true, label: 'T' + s.t + ' ' + e.label },
                                                 e.data), 'xyimg-' + s.t + '-' + i + (log ? '-log' : ''),
                              { log: log, tip: tip });
    } else if (e.kind === 'profiles') {
      el.innerHTML = xyProfileSvg(e.data.x, 'X') + ' ' + xyProfileSvg(e.data.y, 'Y');
    }
  });
}

// ---------------------------------------------------------------------
// Ueberlagerungs-Editor (Tobi, 2026-09-04): zwei Messbilder -- je Tool
// und Spalt -- uebereinander legen. B wird auf den gemessenen Scheitel
// von A geschoben (dann muessen die Buckel deckungsgleich sein, wenn die
// Messung stimmt) und darf von Hand um dx/dy in um nachgeschoben werden.
// Rechnen tut webapp/js/overlay.js (rein, getestet); hier nur Felder und
// Zeichnen.
// ---------------------------------------------------------------------
function xyOverlayLayers(results, f) {
  var O = NozzleOverlay;
  function pick(sel) {
    var m = /^(\d+):(\d+)$/.exec(String(sel || ''));
    if (!m) return null;
    var e = results[m[1]];
    var im = e && Array.isArray(e.images) ? e.images[parseInt(m[2], 10)] : null;
    if (!im) return null;
    var gap = (typeof im.gap === 'number') ? im.gap.toFixed(2) + ' mm' : '?';
    // Spitzenpunkt = Ergebnis der Extrapolation auf Spalt 0 (x_peak/y_peak),
    // in Maschinenkoordinaten wie das Raster (Tobi, 2026-09-04).
    var tip = (typeof e.x_peak === 'number' && typeof e.y_peak === 'number')
      ? { x: e.x_peak, y: e.y_peak } : null;
    return O.layerFromImage(im, 'T' + m[1] + ' Spalt ' + gap, tip);
  }
  var a = pick(f.a), b = pick(f.b);
  var shift = { dx: 0, dy: 0 };
  if (a && b) {
    if (f.align) shift = O.alignShift(a, b);
    shift = { dx: shift.dx + (parseFloat(f.dx) || 0) / 1000,
              dy: shift.dy + (parseFloat(f.dy) || 0) / 1000 };
    b = O.shiftLayer(b, shift.dx, shift.dy);
  }
  if (f.normalize) {
    if (a) a = O.normalizeLayer(a);
    if (b) b = O.normalizeLayer(b);
  }
  return { a: a, b: b, shift: shift };
}

// Aus dem Messbild-Dialog heraus (Tobi, 2026-09-04: "passiert nichts"):
// alertDialog reiht Dialoge hintereinander, der Editor kaeme also erst
// nach dem OK des Messbilds. Deshalb das Messbild erst schliessen.
function xyOverlayFromImage(t) {
  $('#confirmModalOk').trigger('click');
  return xyShowOverlay(t);
}

var _xyOverlayForm = null;
function xyShowOverlay(toolA) {
  if (typeof NozzleOverlay === 'undefined') {
    return alertDialog('Überlagerung', 'overlay.js nicht geladen.');
  }
  var opts = NozzleOverlay.layerOptions(_xyResults);
  if (!opts.length) {
    return alertDialog('Überlagerung', 'Keine Raster-Messbilder im Ergebnis. Erst einen Lauf mit FIT2D=1 fahren.');
  }
  var ref = String(_xyResults.ref_tool !== undefined ? _xyResults.ref_tool : '0');
  var prev = _xyOverlayForm || {};
  function firstOf(t) {
    var o = opts.filter(function (x) { return x.tool === String(t); })[0];
    return o ? o.tool + ':' + o.index : '';
  }
  var selA = prev.a || firstOf(toolA !== undefined ? toolA : ref) || (opts[0].tool + ':' + opts[0].index);
  var selB = prev.b || (function () {
    var other = opts.filter(function (x) { return x.tool !== selA.split(':')[0]; })[0];
    return other ? other.tool + ':' + other.index : '';
  })();
  function select(id, val, allowNone) {
    var h = '<select class="form-select form-select-sm" id="' + id + '" onchange="xyRenderOverlay()">';
    if (allowNone) h += '<option value="">&ndash; keins &ndash;</option>';
    opts.forEach(function (o) {
      var v = o.tool + ':' + o.index;
      h += '<option value="' + v + '"' + (v === val ? ' selected' : '') + '>' + escapeHtml(o.label) + '</option>';
    });
    return h + '</select>';
  }
  function num(id, label, val) {
    return '<div class="col-auto"><label class="form-label small mb-0" for="' + id + '">' + label +
      '</label><input type="number" step="10" class="form-control form-control-sm" id="' + id +
      '" value="' + val + '" style="width:6em" onchange="xyRenderOverlay()" oninput="xyRenderOverlay()"></div>';
  }
  var body =
    '<div class="row g-2 align-items-end mb-2">' +
      '<div class="col-sm-5"><label class="form-label small mb-0">A (blau)</label>' + select('xy-ov-a', selA, false) + '</div>' +
      '<div class="col-sm-5"><label class="form-label small mb-0">B (rot)</label>' + select('xy-ov-b', selB, true) + '</div>' +
      '<div class="col-sm-2"><label class="form-label small mb-0">Ansicht</label>' +
        '<select class="form-select form-select-sm" id="xy-ov-mode" onchange="xyRenderOverlay()">' +
        '<option value="2d"' + (prev.mode === '3d' ? '' : ' selected') + '>2D Linien</option>' +
        '<option value="3d"' + (prev.mode === '3d' ? ' selected' : '') + '>3D Fl&auml;chen</option></select></div>' +
    '</div>' +
    '<div class="row g-2 align-items-end mb-2 small">' +
      '<div class="col-auto form-check ms-2"><input type="checkbox" class="form-check-input" id="xy-ov-align"' +
        (prev.align === false ? '' : ' checked') + ' onchange="xyRenderOverlay()">' +
        '<label class="form-check-label" for="xy-ov-align">B auf den gemessenen Scheitel von A legen</label></div>' +
      '<div class="col-auto form-check"><input type="checkbox" class="form-check-input" id="xy-ov-norm"' +
        (prev.normalize === false ? '' : ' checked') + ' onchange="xyRenderOverlay()">' +
        '<label class="form-check-label" for="xy-ov-norm">H&ouml;he auf Spitze 1 normieren</label></div>' +
      num('xy-ov-dx', 'B zus&auml;tzlich X (&micro;m)', prev.dx || 0) +
      num('xy-ov-dy', 'B zus&auml;tzlich Y (&micro;m)', prev.dy || 0) +
      '<div class="col-auto"><label class="form-label small mb-0" for="xy-ov-op">Deckkraft B</label>' +
        '<input type="range" class="form-range" id="xy-ov-op" min="0.2" max="1" step="0.05" value="' +
        (prev.opacity || 0.6) + '" style="width:6em" oninput="xyRenderOverlay()"></div>' +
    '</div>' +
    '<div id="xy-ov-status" class="small text-muted mb-1"></div>' +
    '<div id="xy-ov-plot" style="width:100%;height:440px"></div>' +
    '<div class="small text-muted mt-2">Kreuz = Scheitel des 2D-Fits in diesem Raster, Stern (3D: Lot) = ' +
    'ermitteltes Ergebnis des Tools (mit Extrapolation: Spitze bei Spalt 0, sonst Scheitel beim Messspalt). Liegen die Buckel nach dem ' +
    'Verschieben um den gemessenen Offset nicht aufeinander, weicht die Form der D&uuml;se (oder der ' +
    'Messung) ab; die Handverschiebung zeigt, um wie viel.</div>';
  alertDialog('Messbilder überlagern', body, { okClass: 'btn-secondary' });
  setTimeout(xyRenderOverlay, 80);
}

function xyRenderOverlay() {
  var el = document.getElementById('xy-ov-plot');
  if (!el || typeof NozzleOverlay === 'undefined' || typeof NozzleMap3d === 'undefined') return;
  var f = {
    a: $('#xy-ov-a').val(), b: $('#xy-ov-b').val(), mode: $('#xy-ov-mode').val(),
    align: $('#xy-ov-align').is(':checked'), normalize: $('#xy-ov-norm').is(':checked'),
    dx: $('#xy-ov-dx').val(), dy: $('#xy-ov-dy').val(), opacity: parseFloat($('#xy-ov-op').val())
  };
  _xyOverlayForm = f;
  var L = xyOverlayLayers(_xyResults, f);
  var st = $('#xy-ov-status');
  if (!L.a) { st.text('Ebene A fehlt.'); return; }
  if (L.b) {
    st.html('B verschoben um X ' + (L.shift.dx * 1000).toFixed(0) + ' / Y ' +
            (L.shift.dy * 1000).toFixed(0) + ' &micro;m' +
            (f.align ? ' (Scheiteldifferenz' + ((parseFloat(f.dx) || parseFloat(f.dy)) ? ' plus Hand' : '') + ')' : ' (nur Hand)'));
  } else {
    st.text('Nur A.');
  }
  var key = 'xy-ov-' + f.mode;
  var spec = (f.mode === '3d')
    ? NozzleOverlay.overlayTraces3d(L.a, L.b, { opacity: f.opacity, key: key,
                                                 zlabel: f.normalize ? 'Anteil der Spitze' : 'Hz ueber Basislinie' })
    : NozzleOverlay.overlayTraces2d(L.a, L.b, { key: key, levels: 8 });
  NozzleMap3d.ensurePlotly().then(function (Plotly) {
    return Plotly.react(el, spec.data, spec.layout, { responsive: true, displaylogo: false });
  }, function (e) {
    el.textContent = '3D-Ansicht nicht verfuegbar: ' + e.message;
  });
}

// ---------------------------------------------------------------------
// Fortschrittsdialog des Messlaufs (Tobi, 2026-09-04: "das sollte offen
// bleiben, damit der User sieht, dass die Messung noch laeuft und was
// gerade passiert"). Quelle ist printer.offset.xy_progress, das Klipper
// je Schritt nachfuehrt, dazu die letzten Konsolenzeilen des Laufs und
// das Live-Raster aus printer.nozzle_locator.map.
// ---------------------------------------------------------------------
function xyProgressHtml(p, lines, nowMs, doneHint) {
  // Leeres Objekt (Klipper hat noch nie einen Lauf gemeldet) zaehlt wie
  // kein Status -- sonst stuende "Fertig" da, bevor irgendetwas lief.
  if (p && typeof p.running !== 'boolean') p = null;
  function mmss(sec) {
    sec = Math.max(0, Math.round(sec));
    return Math.floor(sec / 60) + ':' + ('0' + (sec % 60)).slice(-2);
  }
  var h = '';
  if (!p && doneHint) {
    h += '<div class="mb-2 text-success"><b>Der Drucker steht wieder.</b> ' + escapeHtml(doneHint) + '</div>';
  } else if (!p) {
    h += '<div class="mb-2"><span class="spinner-border spinner-border-sm me-1"></span> Messlauf startet &hellip; ' +
         '(wartet auf die erste Meldung von Klipper)</div>';
  } else {
    var tools = Array.isArray(p.tools) ? p.tools : [];
    var done = Array.isArray(p.done) ? p.done.map(String) : [];
    var cur = (p.tool !== null && p.tool !== undefined) ? String(p.tool) : null;
    h += '<div class="d-flex flex-wrap gap-2 mb-2">' + tools.map(function (t) {
      t = String(t);
      var cls, mark;
      if (done.indexOf(t) !== -1) { cls = 'bg-success xy-prog-done'; mark = '&#10003; '; }
      else if (p.running && t === cur) { cls = 'bg-primary xy-prog-current'; mark = '&#9654; '; }
      else { cls = 'bg-secondary'; mark = ''; }
      return '<span class="badge ' + cls + '">' + mark + 'T' + escapeHtml(t) + '</span>';
    }).join('') + '</div>';
    if (p.running) {
      var el = (typeof p.started === 'number') ? (nowMs / 1000 - p.started) : 0;
      h += '<div class="mb-2"><span class="spinner-border spinner-border-sm me-1"></span> ' +
           (cur !== null ? '<b>T' + escapeHtml(cur) + '</b>: ' : '') +
           escapeHtml(p.step || '') + ' <span class="text-muted">&middot; Laufzeit ' + mmss(el) + '</span></div>';
    } else if (p.error) {
      h += '<div class="mb-2 text-danger"><b>Abgebrochen:</b> ' + escapeHtml(p.error) + '</div>';
    } else {
      h += '<div class="mb-2 text-success"><b>Fertig.</b> ' + escapeHtml(p.step || '') + '</div>';
    }
  }
  h += '<div id="xy-prog-map3d" style="width:100%;height:300px"></div>';
  h += '<pre class="small bg-body-tertiary p-2 mb-0" style="max-height:9em;overflow:auto">' +
       (lines || []).map(function (l) { return escapeHtml(l); }).join('\n') + '</pre>';
  return h;
}

// Fertig, sobald Klipper running=false meldet, nachdem wir es laufen sahen.
// Ohne xy_progress (aelteres Klipper) bleibt nur der idle-Zustand des
// Druckers -- und der zaehlt erst nach 20 s, weil idle_timeout mit dem
// ersten bewegenden Kommando auf Printing springt.
function xyProgressDone(p, seenRunning, idle, elapsedMs) {
  if (p && p.running) return false;
  if (seenRunning) return true;
  return (elapsedMs > 20000 && idle === true);
}

function xyRunProgressDialog(title) {
  var t0 = Date.now();
  var seenRunning = false;
  var stopped = false;
  function fetchAll() {
    return Promise.all([
      Promise.resolve($.get(printerUrl(printerIp, '/printer/objects/query?offset=xy_progress&nozzle_locator=map')))
        .then(function (d) { return (d && d.result && d.result.status) || {}; }, function () { return {}; }),
      Promise.resolve($.get(printerUrl(printerIp, '/server/gcode_store?count=60')))
        .then(function (d) {
          var gs = (d && d.result && d.result.gcode_store) || [];
          return gs.filter(function (g) {
            // nur Zeilen dieses Laufs, nicht die des letzten vom Abend
            return g.type === 'response' && (g.time || 0) * 1000 >= t0 - 5000 &&
                   /^\/\/ (T\d+:|XY[:-]|nozzle_locator)/.test(g.message || '');
          }).map(function (g) { return g.message.replace(/^\/\/ /, ''); }).slice(-12);
        }, function () { return []; }),
      xyPrinterIdle()
    ]);
  }
  var dlg = confirmDialog({
    title: title || 'XY-Messlauf', body: xyProgressHtml(null, [], t0),
    okLabel: 'Weiter', hideCancel: true
  });
  $('#confirmModalOk').prop('disabled', true);
  function tick() {
    if (stopped) return;
    fetchAll().then(function (r) {
      if (stopped) return;
      var st = r[0], lines = r[1], idle = r[2];
      var p = (st.offset && st.offset.xy_progress) || null;
      if (p && !p.running && typeof p.started === 'number' && p.started * 1000 < t0 - 5000) {
        // Rest eines frueheren Laufs -- dieser hier hat noch nicht gemeldet.
        p = null;
      }
      if (p && p.running) seenRunning = true;
      var body = document.getElementById('confirmModalBody');
      if (body) {
        var mapEl = document.getElementById('xy-prog-map3d');
        var keep = mapEl && mapEl.firstChild ? mapEl : null;
        body.innerHTML = xyProgressHtml(p, lines, Date.now());
        var slot = document.getElementById('xy-prog-map3d');
        if (keep && slot) slot.replaceWith(keep);
        var map = st.nozzle_locator && st.nozzle_locator.map;
        var el = document.getElementById('xy-prog-map3d');
        if (map && map.xs && map.xs.length && el && typeof NozzleMap3d !== 'undefined') {
          NozzleMap3d.renderMap3d(el, map, 'xy-prog', { log: true });
        }
      }
      var done = xyProgressDone(p, seenRunning, idle, Date.now() - t0);
      if (done) {
        stopped = true;
        if (!p && body) {
          body.innerHTML = xyProgressHtml(null, lines, Date.now(),
            'Klipper hat keinen Laufstatus gemeldet (aelteres Modul?); die Konsole unten zeigt, was lief.');
        }
        $('#confirmModalOk').prop('disabled', false).html(p && p.error ? 'Schlie&szlig;en' : 'Weiter');
        return;
      }
      setTimeout(tick, 2000);
    });
  }
  setTimeout(tick, 1500);
  return dlg.then(function (ok) {
    stopped = true;
    return ok;
  });
}

// Zeichnet die laufende Glocke aus nozzle_locator.points. Zeigt sofort,
// ob das Ziel sauber im Fenster liegt oder ob die Halterung wackelt.
function renderXySparkline(status) {
  var el = document.getElementById('xy-sparkline');
  if (!el) return;
  var pts = (status && status.points) || [];
  if (pts.length < 2) {
    el.innerHTML = '<span class="text-muted">' +
      (status && status.state !== 'idle' ? escapeHtml(status.state) : '') +
      '</span>';
    return;
  }
  var xs = pts.map(function (p) { return p[0]; });
  var ys = pts.map(function (p) { return p[1]; });
  var x0 = Math.min.apply(null, xs), x1 = Math.max.apply(null, xs);
  var y0 = Math.min.apply(null, ys), y1 = Math.max.apply(null, ys);
  var W = 240, H = 48;
  var sx = (x1 > x0) ? (W - 4) / (x1 - x0) : 0;
  var sy = (y1 > y0) ? (H - 4) / (y1 - y0) : 0;
  var d = pts.map(function (p, i) {
    return (i ? 'L' : 'M') + (2 + (p[0] - x0) * sx).toFixed(1) + ' ' +
           (H - 2 - (p[1] - y0) * sy).toFixed(1);
  }).join(' ');
  el.innerHTML =
    '<svg width="' + W + '" height="' + H + '" role="img" ' +
    'aria-label="Frequenzverlauf des laufenden Sweeps">' +
    '<path d="' + d + '" fill="none" stroke="currentColor" ' +
    'stroke-width="1.5"/></svg> ' +
    '<small class="text-muted">' + (y1 - y0).toFixed(0) + ' Hz &uuml;ber ' +
    (x1 - x0).toFixed(1) + ' mm</small>';
}

// Eigene, tolerante Anfrage fuer die Sparkline: das Objekt "nozzle_locator"
// existiert erst, wenn Sonde und Klipper-Modul (Task 2/3/5) da sind. Bis
// dahin bleibt sie leer, ohne die eigentliche Offset-Abfrage zu gefaehrden
// - deshalb bewusst nicht Teil von fetchOffsetStatus()/getOffsetSnapshot().
// Einmal nachsehen, ob es das Objekt ueberhaupt gibt, und das Ergebnis
// merken. Ohne diese Sperre setzte der 2s-Poll dauerhaft eine Anfrage ab,
// die auf JEDER heutigen Config mit HTTP 400 endet - das .catch() versteckt
// das nur vor dem Nutzer, nicht vor Konsole und Netzwerk-Log, und
// verdoppelte nebenbei die Requests des Polls.
//
// Ein Fehlschlag der Abfrage selbst (Moonraker startet gerade neu) setzt
// die Sperre bewusst zurueck, statt "gibt es nicht" fuer den Rest der
// Sitzung festzuschreiben.
var _xyLocatorProbe = null;
function xyLocatorAvailable() {
  if (_xyLocatorProbe === null) {
    _xyLocatorProbe = Promise.resolve(
      $.get(printerUrl(printerIp, "/printer/objects/list"))
    ).then(function (data) {
      var objs = (data && data.result && data.result.objects) || [];
      return objs.indexOf('nozzle_locator') !== -1;
    }, function () {
      _xyLocatorProbe = null;
      return false;
    });
  }
  return _xyLocatorProbe;
}

function pollXySparkline() {
  return xyLocatorAvailable().then(function (have) {
    $('#xy-run-btn, #xy-deactivate-btn').toggleClass('d-none', !have);
    if (!have) { renderXySparkline(null); return null; }
    return Promise.resolve(
      $.get(printerUrl(printerIp, "/printer/objects/query?nozzle_locator"))
    ).then(function (data) {
      var st = data && data.result && data.result.status
               && data.result.status.nozzle_locator;
      renderXySparkline(st);
      renderXyMap3d(st);
    }, function () { renderXySparkline(null); });
  });
}

// Schreibt gcode_x_offset/gcode_y_offset fuer die genannten Tools in die
// Configs, sequentiell wie updateToolDockValues/updateToolPidValues
// (parallele Uploads verursachen Moonraker-500er). toolValues:
// { "0": {x: "0.5300", y: "-0.0200"}, ... } - Werte bereits als Text.
// Liefert true nur, wenn fuer JEDES Tool BEIDE Schluessel gefunden und
// geschrieben wurden - fehlende Schluessel loesen reportMissingKeys() aus
// UND liefern false, statt gleichzeitig Erfolg zu behaupten.
function writeXyConfigs(toolValues) {
  var tools = Object.keys(toolValues);
  var missing = [];

  function processNext(idx) {
    if (idx >= tools.length) {
      reportMissingKeys(missing);
      return Promise.resolve(missing.length === 0);
    }
    var t = tools[idx];
    var v = toolValues[t];
    var filePath = "toolchanger/tools/T" + t + ".cfg";
    var section = "tool T" + t;
    return updateConfigFile(filePath, function (content) {
      var updated = content;
      var allOk = true;
      [["gcode_x_offset", v.x], ["gcode_y_offset", v.y]].forEach(function (pair) {
        var next = replaceInConfigSection(updated, section, pair[0], pair[1]);
        if (next === null) {
          allOk = false;
          missing.push({file: filePath, section: section, key: pair[0]});
        } else {
          updated = next;
        }
      });
      if (!allOk) return null;
      return updated;
    }).then(function () { return processNext(idx + 1); });
  }
  return processNext(0);
}

// Schreibt den XY-Offset eines Tools sofort per SET_TOOL_GCODE_OFFSET
// (Laufzeit). alsoWrite=true schreibt danach zusaetzlich in die
// Tool-Config - wie bei den anderen APPLY-...-Aktionen erst nach einem
// Bestaetigungsdialog mit Vorher/Nachher, denn ein falscher XY-Offset laesst
// die Duese daneben drucken.
function applyXyOffset(toolNr, alsoWrite) {
  var res = (_xyMethod === 'eddy') ? _xyResults[String(toolNr)]
                                   : _cameraOffsetFor(toolNr);
  if (!xyMeasured(res)) {
    if (typeof showToast === 'function') {
      showToast("T" + toolNr + ": kein Messwert", "warning");
    }
    return Promise.resolve(false);
  }
  var xTxt = res.x.toFixed(4), yTxt = res.y.toFixed(4);
  var script = "SET_TOOL_GCODE_OFFSET T=" + toolNr +
               " X=" + xTxt + " Y=" + yTxt;
  var filePath = "toolchanger/tools/T" + toolNr + ".cfg";
  var section = "tool T" + toolNr;

  function writeConfig() {
    return fetchToolConfigValues([
      { tool: toolNr, key: "gcode_x_offset", section: section },
      { tool: toolNr, key: "gcode_y_offset", section: section }
    ]).then(function (cur) {
      var entries = [{
        tool: toolNr,
        file: filePath,
        section: section,
        changes: [
          { key: "gcode_x_offset", from: cur[toolNr + "|gcode_x_offset"], to: xTxt },
          { key: "gcode_y_offset", from: cur[toolNr + "|gcode_y_offset"], to: yTxt }
        ]
      }];
      return confirmDialog({
        title: "XY-Offset T" + toolNr + " in die Config schreiben?",
        body: offsetChangeListHtml(entries,
          'Gerechnet gegen Referenztool <strong>T' + escapeHtml(xyRefTool()) +
          '</strong> (Verfahren: ' +
          escapeHtml(_xyMethod === 'eddy' ? 'Eddy-Sweep' : 'Kamera') +
          ').<br>' +
          '"Current" kommt aus der Config-Datei. Der Laufzeitwert wurde ' +
          'bereits per <code>SET_TOOL_GCODE_OFFSET</code> gesetzt.'),
        okLabel: "OK — schreiben",
        okClass: "btn-success",
        cancelLabel: "Nur Laufzeit"
      });
    }).then(function (ok) {
      if (!ok) return false;
      var toolValues = {};
      toolValues[toolNr] = { x: xTxt, y: yTxt };
      return writeXyConfigs(toolValues);
    });
  }

  return sendGcodeWithRecovery(script, "XY-Offset T" + toolNr)
    .then(function (r) {
      if (!r || !r.ok) return false;
      if (typeof showToast === 'function') {
        showToast("T" + toolNr + ": XY-Offset gesetzt (Laufzeit)", "success");
      }
      if (!alsoWrite) return true;
      // Toast fuer den Schreibvorgang nur bei echtem Erfolg - sonst zeigt
      // die UI "geschrieben" und den reportMissingKeys()-Alarm gleichzeitig
      // fuer dieselbe Aktion. "Nur Laufzeit" im Dialog ist eine gueltige
      // Wahl, kein Fehler, und bleibt deshalb ebenfalls ohne zweiten Toast.
      return writeConfig().then(function (wrote) {
        if (wrote && typeof showToast === 'function') {
          showToast("T" + toolNr + ": in die Config geschrieben", "success");
        }
        return wrote;
      });
    })
    .catch(function (err) {
      var detail = "";
      try { detail = (err.responseJSON || err).message || ""; } catch (_) {}
      return alertDialog("XY-Offset T" + toolNr + " uebernehmen fehlgeschlagen",
                         escapeHtml(detail || "Unbekannter Fehler"))
        .then(function () { return false; });
    });
}

// "Alle uebernehmen + schreiben": sammelt die Aenderungen aller Tools mit
// Messwert im aktuell gewaehlten Verfahren in EINEM Bestaetigungsdialog -
// wie applyDockValues(null), statt wie zuvor pro Tool einzeln nachzufragen.
// Erst nach der einen Bestaetigung werden alle Laufzeitwerte in einem
// GCode-Request gesetzt und die Configs geschrieben.
function applyAllXyOffsets() {
  var ref = xyRefTool();

  var toolValues = {};
  var requests = [];
  Object.keys(_toolGcodeOffsets).forEach(function (t) {
    if (String(ref) === String(t)) return;
    var res = (_xyMethod === 'eddy') ? _xyResults[t] : _cameraOffsetFor(t);
    if (!xyMeasured(res)) return;
    toolValues[t] = { x: res.x.toFixed(4), y: res.y.toFixed(4) };
    requests.push({ tool: t, key: "gcode_x_offset", section: "tool T" + t });
    requests.push({ tool: t, key: "gcode_y_offset", section: "tool T" + t });
  });

  var names = Object.keys(toolValues).sort(function (a, b) {
    return parseInt(a, 10) - parseInt(b, 10);
  });
  if (!names.length) {
    if (typeof showToast === 'function') {
      showToast("Keine XY-Messwerte zum Uebernehmen", "warning");
    }
    return Promise.resolve(false);
  }

  return fetchToolConfigValues(requests).then(function (cur) {
    var entries = names.map(function (t) {
      var v = toolValues[t];
      return {
        tool: t,
        file: "toolchanger/tools/T" + t + ".cfg",
        section: "tool T" + t,
        changes: [
          { key: "gcode_x_offset", from: cur[t + "|gcode_x_offset"], to: v.x },
          { key: "gcode_y_offset", from: cur[t + "|gcode_y_offset"], to: v.y }
        ]
      };
    });
    return confirmDialog({
      title: names.length === 1
        ? "XY-Offset von T" + names[0] + " uebernehmen?"
        : "XY-Offsets uebernehmen?",
      body: offsetChangeListHtml(entries,
        'Gerechnet gegen Referenztool <strong>T' + escapeHtml(ref) +
        '</strong> (Verfahren: ' +
        escapeHtml(_xyMethod === 'eddy' ? 'Eddy-Sweep' : 'Kamera') + ').<br>' +
        '"Current" kommt aus der Config-Datei. Die Werte werden auch zur ' +
        'Laufzeit per <code>SET_TOOL_GCODE_OFFSET</code> gesetzt.'),
      okLabel: "OK — uebernehmen",
      okClass: "btn-success",
      cancelLabel: "Abbrechen"
    });
  }).then(function (ok) {
    if (!ok) return false;
    var script = names.map(function (t) {
      var v = toolValues[t];
      return "SET_TOOL_GCODE_OFFSET T=" + t + " X=" + v.x + " Y=" + v.y;
    }).join("\n");
    return sendGcodeWithRecovery(script, "XY-Offsets uebernehmen")
      .then(function (r) {
        if (!r || !r.ok) return false;
        return writeXyConfigs(toolValues).then(function (wrote) {
          if (wrote && typeof showToast === 'function') {
            showToast(names.length === 1
              ? "T" + names[0] + ": XY-Offset gesetzt und geschrieben"
              : "XY-Offsets gesetzt und geschrieben", "success");
          }
          return wrote;
        });
      });
  }).catch(function (err) {
    var detail = "";
    try { detail = (err.responseJSON || err).message || ""; } catch (_) {}
    return alertDialog("XY-Offsets uebernehmen fehlgeschlagen",
                       escapeHtml(detail || "Unbekannter Fehler"))
      .then(function () { return false; });
  });
}

// --------------------------
// XY-Sonde: Aktivieren/Deaktivieren, Assistent, Rettungsnetz (Task 8)
// --------------------------
// Die Sonde steckt nur waehrend der Messung am USB - danach wird sie
// abgezogen. Klipper startet gar nicht erst, wenn die Config eine MCU
// nennt, die nicht da ist. xy_probe.cfg.disabled ist die Vorlage (bleibt
// unangetastet, damit UUID/Halterungsmasse jeden Zyklus ueberleben),
// xy_probe.cfg ist die scharfe Datei, die printer.cfg included - leer
// heisst deaktiviert, Vorlageninhalt heisst aktiv.

// Aktivieren = Inhalt aus der Vorlage nach xy_probe.cfg kopieren.
function xyProbeActivate() {
  var baseUrl = printerUrl(printerIp, "");
  return fetch(baseUrl + "/server/files/config/xy_probe.cfg.disabled",
               NO_CACHE)
    .then(function (r) {
      if (!r.ok) throw new Error(
        "xy_probe.cfg.disabled fehlt -- dort muessen serial-Pfad und " +
        "Halterungsmasse einmalig eingetragen werden.");
      return r.text();
    })
    .then(function (template) {
      if (template.indexOf("HIER_EINTRAGEN") !== -1) throw new Error(
        "In xy_probe.cfg.disabled steht noch HIER_EINTRAGEN statt des " +
        "serial-Pfads der Sonde. Einmal 'ls /dev/serial/by-id/' auf dem " +
        "Drucker ausfuehren, waehrend die Sonde steckt.");
      return updateConfigFile("xy_probe.cfg", function () { return template; });
    })
    .then(function () { return restartKlipperAndWait(); })
    // Sichtbarkeit von "Messlauf"/"Sonde deaktivieren" haengt daran
    .then(function (r) { _xyLocatorProbe = null; return r; });
}

function xyProbeDeactivate() {
  return updateConfigFile("xy_probe.cfg", function () {
    return "# XY-Sonde deaktiviert.\n";
  }).then(function () { return restartKlipperAndWait(); })
    .then(function (r) { _xyLocatorProbe = null; return r; });
}

// FIRMWARE_RESTART und warten, bis Klipper wieder 'ready' meldet. Ein
// Config-Include-Wechsel braucht nur FIRMWARE_RESTART -- der volle
// Service-Neustart ist nur noetig, wenn sich .py-Module geaendert haben
// (RESTART laedt sys.modules nicht neu, siehe restart-klipper-btn oben).
function restartKlipperAndWait(timeoutMs) {
  var baseUrl = printerUrl(printerIp, "");
  timeoutMs = timeoutMs || 60000;
  var deadline = Date.now() + timeoutMs;
  return fetch(baseUrl + "/printer/firmware_restart", {method: 'POST'})
    .then(function () {
      function poll() {
        if (Date.now() > deadline) {
          throw new Error("Klipper ist nach dem Neustart nicht bereit " +
                          "geworden. Steckt die Sonde wirklich?");
        }
        return new Promise(function (r) { setTimeout(r, 1000); })
          .then(function () { return fetch(baseUrl + "/printer/info", NO_CACHE); })
          .then(function (r) { return r.json(); })
          .then(function (j) {
            var st = j && j.result && j.result.state;
            if (st === 'ready') return true;
            if (st === 'error' || st === 'shutdown') {
              throw new Error(
                "Klipper startet nicht: " +
                ((j.result.state_message || '').split("\n")[0]));
            }
            return poll();
          });
      }
      return poll();
    });
}

// Haengt die USB-Sonde dran? Moonrakers serial-Endpunkt listet alle
// seriellen Geraete samt path_by_id; wir suchen den Pfad, der in
// xy_probe.cfg.disabled als serial: eingetragen ist.
//
// Bewusst ueber /machine/peripherals/serial und nicht ueber den
// canbus-Endpunkt: der listet nur Knoten, die noch KEIN laufender Klipper
// beansprucht, und liefert im Normalbetrieb eine leere Liste (am 250er
// nachgemessen: can_uuids ist leer, obwohl zwei CAN-MCUs laufen). Fuer USB
// ist die Auskunft dagegen eindeutig und unabhaengig davon, ob Klipper das
// Geraet gerade haelt.
function xyProbeConnected(serialPath) {
  var baseUrl = printerUrl(printerIp, "");
  return fetch(baseUrl + "/machine/peripherals/serial", NO_CACHE)
    .then(function (r) {
      if (!r.ok) return null;               // Endpunkt gibt es nicht
      return r.json();
    })
    .then(function (j) {
      var devs = j && j.result && j.result.serial_devices;
      if (!devs) return null;
      return devs.some(function (d) {
        return d.path_by_id === serialPath || d.device_path === serialPath;
      });
    })
    .catch(function () { return null; });
}

// Reine Textverarbeitung, absichtlich von readXyProbeSerial() getrennt:
// so ist die Vorlagen-Auswertung ohne fetch-Stub testbar.
function parseXyProbeSerial(text) {
  if (text.indexOf("HIER_EINTRAGEN") !== -1) throw new Error(
    "In xy_probe.cfg.disabled steht noch HIER_EINTRAGEN statt des " +
    "serial-Pfads der Sonde. Einmal 'ls /dev/serial/by-id/' auf dem " +
    "Drucker ausfuehren, waehrend die Sonde steckt.");
  var m = text.match(/^\s*serial\s*:\s*(\S+)/m);
  if (!m) throw new Error(
    "In xy_probe.cfg.disabled steht keine serial-Zeile.");
  return m[1];
}

// Liest die serial-Zeile aus der Vorlage. Ungecacht, sonst liefert ein
// Browser-Cache nach einer Aenderung noch den alten Pfad.
function readXyProbeSerial() {
  var baseUrl = printerUrl(printerIp, "");
  return fetch(baseUrl + "/server/files/config/xy_probe.cfg.disabled",
               NO_CACHE)
    .then(function (r) {
      if (!r.ok) throw new Error("xy_probe.cfg.disabled fehlt");
      return r.text();
    })
    .then(parseXyProbeSerial);
}

// Praesenzpruefung mit Rueckfall: kennt diese Moonraker-Version den
// serial-Endpunkt nicht, fragen wir den Nutzer statt zu scheitern. Liefert
// immer entweder true oder wirft - der Assistent muss den Ausgang nicht
// selbst auswerten (vgl. dockJogLoop/dockToolLoop, die genau deshalb jeden
// confirmDialog-Ausgang selbst pruefen, statt blind weiterzulaufen).
function xyProbeCheckPresent() {
  return readXyProbeSerial()
    .then(function (path) { return xyProbeConnected(path); })
    .then(function (present) {
      if (present === true) return true;
      if (present === false) throw new Error(
        "Die Sonde ist am USB nicht zu sehen. Steckt sie, und stimmt der " +
        "serial-Pfad in xy_probe.cfg.disabled?");
      return confirmDialog({
        title: "Sonde angesteckt?",
        body: "Diese Moonraker-Version kann die seriellen Geräte nicht " +
              "auflisten. Bitte selbst prüfen: ist die Sonde angesteckt?",
        okLabel: "Ja, ist angesteckt"
      }).then(function (yes) {
        if (!yes) throw new Error(
          "Ohne Bestätigung, dass die Sonde angesteckt ist, wird nicht " +
          "aktiviert.");
        return true;
      });
    });
}

// sendGcodeWithRecovery schlaegt nie hart fehl (siehe dort). Nur {ok:true}
// heisst "durchgelaufen".
//
// Fix-Runde 3: {transport:true} zaehlt NICHT mehr als Erfolg.
// /printer/gcode/script bleibt offen, bis das Skript fertig ist - ein
// Verbindungsabbruch heisst also "der Drucker arbeitet noch", nicht
// "fertig". Genau so behandeln es der PID- und der Z-Lauf: Hinweis
// anzeigen und STEHENBLEIBEN. Der Assistent war der einzige Ablauf, der
// darauf einen maschinenbewegenden Folgeschritt gekettet hat.
function xyStepOk(r) {
  return !!(r && r.ok);
}

// Laeuft auf dem Drucker gerade noch etwas? idle_timeout.state ist
// "Printing", solange Kommandos abgearbeitet werden - auch ohne Druckjob.
// Liefert true/false, oder null, wenn die Auskunft selbst nicht zu holen
// war (dann ist "fertig" gerade NICHT bewiesen).
function xyPrinterIdle() {
  return Promise.resolve(
    $.get(printerUrl(printerIp,
      "/printer/objects/query?idle_timeout&print_stats"))
  ).then(function (data) {
    var st = data && data.result && data.result.status;
    if (!st) return null;
    var it = st.idle_timeout, ps = st.print_stats;
    if (it && it.state === 'Printing') return false;
    if (ps && (ps.state === 'printing' || ps.state === 'paused')) return false;
    if (!it && !ps) return null;
    return true;
  }, function () { return null; });
}

// Wartet, bis der Drucker keine Kommandos mehr abarbeitet. Das ist die
// einzige Auskunft, die einen laufenden Messlauf ueberdauert - anders als
// das Aufloesen des HTTP-Requests, das schon beim Verbindungsabbruch
// feuert. Liefert false, wenn die Frist ablaeuft; der Aufrufer bricht dann
// ab, statt in eine laufende Bewegung hinein weiterzumachen.
function waitForPrinterIdle(timeoutMs) {
  var deadline = Date.now() + (timeoutMs || 1800000);
  function step() {
    return xyPrinterIdle().then(function (idle) {
      if (idle === true) return true;
      if (Date.now() > deadline) return false;
      return new Promise(function (r) { setTimeout(r, 2000); }).then(step);
    });
  }
  // Kurz warten, bevor zum ersten Mal gefragt wird: idle_timeout schaltet
  // erst mit dem ersten bewegenden Kommando auf "Printing", eine sofortige
  // Frage koennte also faelschlich "steht schon" melden.
  return new Promise(function (r) { setTimeout(r, 1500); }).then(step);
}

function xyHomedAxes() {
  return Promise.resolve(
    $.get(printerUrl(printerIp, "/printer/objects/query?toolhead"))
  ).then(function (data) {
    return (data && data.result && data.result.status &&
            data.result.status.toolhead &&
            data.result.status.toolhead.homed_axes) || "";
  });
}

function xyIsHomed(axes) {
  axes = String(axes || "");
  return axes.indexOf("x") !== -1 && axes.indexOf("y") !== -1 &&
         axes.indexOf("z") !== -1;
}

// Fix-Runde 1: sendGcodeWithRecovery() ist ab dem Aufsetzen der Halterung
// nicht mehr sicher. Sein "Must home first"-Recovery-Knopf faehrt
// G28 -> QUAD_GANTRY_LEVEL -> G28 Z, und G28 hebt bei unhomed Z nur 10mm
// und faehrt danach Y quer ueber die Bettmitte (homing.cfg:35) - das setzt
// ein leeres Bett voraus. Sobald die Halterung liegt, darf kein Aufruf
// mehr diesen Knopf anbieten. Eigener, bewusst dummer Sender: kein
// Reparaturdialog, ein Fehlschlag wirft direkt und traegt eine Markierung,
// damit der catch() unten weiss, dass die Halterung noch auf dem Bett
// steht.
function xySendMounted(script, title) {
  function send() {
    var req;
    try {
      req = $.get(printerUrl(printerIp,
        "/printer/gcode/script?script=" + encodeURIComponent(script)));
    } catch (e) {
      return Promise.resolve({ err: e });
    }
    return Promise.resolve(req).then(function () { return { ok: true }; },
                                     function (err) { return { err: err }; });
  }
  return send().then(function (r) {
    if (r.ok) return { ok: true };
    var detail = gcodeErrorMessage(r.err);
    // Kein Payload = Verbindung weg, der Drucker rechnet WEITER (wie bei
    // sendGcodeWithRecovery). Fix-Runde 3: das ist kein Erfolg, sondern
    // "unbekannt, laeuft noch" - deshalb als eigenes Ergebnis nach oben
    // gereicht statt wie zuvor als schlichtes true. Ein Messlauf ueber
    // sechs Tools mit Hin- und Ruecksweep dauert viele Minuten, transport
    // ist hier der REGELFALL und nicht der Sonderfall.
    if (!detail) return { transport: true };
    var e = new Error(title + " fehlgeschlagen: " + detail);
    e.xyHolderMounted = true;
    throw e;
  });
}

// Homt bei GARANTIERT leerem Bett: laeuft direkt nach xyProbeActivate(),
// BEVOR die Halterung ueberhaupt aufs Bett kommt. FIRMWARE_RESTART
// verwirft homed_axes immer (siehe der vorhandene RESTART-KLIPPER-Knopf:
// "Der Drucker verliert das Homing") - dieses Homing danach ist deshalb
// der Normalfall, nicht die Ausnahme. Bewusst trotzdem ueber
// sendGcodeWithRecovery: ein G28-Recovery-Lauf ueber die Bettmitte ist
// hier gefahrlos, weil auf dem Bett noch nichts steht. Nach dem Homen wird
// das Ergebnis geprueft - schlaegt es fehl, darf der Assistent nicht zum
// "Halterung aufsetzen"-Schritt weitergehen.
//
// Fix-Runde 3: das Ergebnis des Sendens beweist hier GAR NICHTS. Weder
// {ok:true} noch {transport:true} sagen, dass gehomt ist - transport
// heisst "Verbindung weg, laeuft weiter", und der Recovery-Knopf faehrt
// G28 -> QUAD_GANTRY_LEVEL -> G28 Z, also minutenlange Bewegung. Der
// naechste Dialog fordert dazu auf, die Halterung AUF DAS BETT zu stellen;
// diese Aufforderung darf erst kommen, wenn der Drucker wirklich steht.
// Also: warten, bis nichts mehr laeuft, DANN homed_axes erneut abfragen -
// dieselbe Pruefung wie oben. Nur die beweist, dass das Homing fertig ist.
// Leveling VOR dem Aufsetzen. CALIBRATE_XY_OFFSETS verlangt ein
// geleveltes Gantry (_require_leveled) -- ohne diesen Schritt griffe
// spaeter die Recovery und liesse QUAD_GANTRY_LEVEL mit der Halterung auf
// dem Bett laufen. Ohne QGL/Z-Tilt-Sektion ist das ein No-op.
function xyLevelingState() {
  return Promise.resolve(
    $.get(printerUrl(printerIp,
      "/printer/objects/query?quad_gantry_level=applied&z_tilt=applied"))
  ).then(function (data) {
    var st = (data && data.result && data.result.status) || {};
    if (st.quad_gantry_level) {
      return { cmd: "QUAD_GANTRY_LEVEL", applied: !!st.quad_gantry_level.applied };
    }
    if (st.z_tilt) {
      return { cmd: "Z_TILT_ADJUST", applied: !!st.z_tilt.applied };
    }
    return { cmd: null, applied: true };
  });
}

function ensureLeveledBeforeSetup() {
  return xyLevelingState().then(function (lv) {
    if (!lv.cmd || lv.applied) return true;
    if (typeof showToast === 'function') {
      showToast("Gantry wird gelevelt (" + lv.cmd + "), Bett muss leer sein…",
                "info");
    }
    return sendGcodeWithRecovery(lv.cmd, "Leveling vor dem Aufsetzen")
      .then(function (r) {
        if (!r || r.handled) return false;
        // Leveling kippt das Gantry: Z danach neu referenzieren.
        return sendGcodeWithRecovery("G28 Z", "Z nach dem Leveling");
      })
      .then(function (r) {
        if (!r || r.handled) return false;
        return waitForPrinterIdle(900000).then(function (idle) {
          if (!idle) return false;
          return xyLevelingState().then(function (lv2) {
            return lv2.applied;
          }).then(function (applied) {
            if (!applied) return false;
            return xyHomedAxes().then(xyIsHomed);
          });
        });
      });
  }).then(function (ok) {
    if (!ok) throw new Error(
      "Das Gantry-Leveling vor dem Aufsetzen ist nicht sauber durchgelaufen " +
      "-- Abbruch, solange das Bett noch leer ist.");
    return true;
  });
}

function ensureHomedAfterActivate() {
  return xyHomedAxes()
    .then(function (homed) {
      if (xyIsHomed(homed)) return true;
      return sendGcodeWithRecovery("G28", "Homen nach dem Aktivieren")
        .then(function (r) {
          // "handled" = Fehlerdialog war schon zu sehen, nicht nochmal.
          if (!r || r.handled) return false;
          if (r.transport && typeof showToast === 'function') {
            showToast("Verbindung zum Homing-Lauf verloren - er laeuft " +
                      "weiter. Warte, bis der Drucker steht...", "warning");
          }
          return waitForPrinterIdle(900000).then(function (idle) {
            if (!idle) return false;
            return xyHomedAxes().then(xyIsHomed);
          });
        });
    })
    .then(function (ok) {
      if (!ok) throw new Error(
        "Homing nach dem Aktivieren ist nicht sauber durchgelaufen (der " +
        "Drucker meldet nicht x/y/z als gehomt, oder er arbeitet noch) -- " +
        "Abbruch, solange das Bett noch leer ist.");
      return true;
    });
}

// Der Assistent fuehrt durch An- und Abstecken der XY-Sonde. Zwei
// Reihenfolgen sind zwingend:
//   - JEDES Homing findet bei leerem Bett statt: zuerst anstecken und
//     aktivieren (das verwirft ueber FIRMWARE_RESTART ohnehin jedes
//     bestehende Homing), DANN homen (ensureHomedAfterActivate), und ERST
//     DANACH zum Aufsetzen der Halterung auffordern. Kein G28 -- auch kein
//     Recovery-G28 -- nachdem die Halterung liegt (siehe xySendMounted).
//   - Deaktivieren steht vor dem Abziehen/Abnehmen -- sonst startet
//     Klipper beim naechsten Mal nicht mehr.
//
// Jeder confirmDialog-Ausgang wird geprueft, bevor es weitergeht (wie bei
// dockJogLoop/dockToolLoop) - ein ignorierter Ausgang wuerde einen
// Abbrechen-Klick wirkungslos machen. Der letzte Schritt vor dem
// Deaktivieren ist bewusst ein alertDialog statt eines confirmDialog: ab
// hier ist das Deaktivieren keine Option mehr, nur noch eine Bestaetigung.
// ---------------------------------------------------------------------
// Anfahrposition (Spec R-B'): der Kopf faehrt mit dem Referenztool auf eine
// einstellbare Position, DANACH stellt der Nutzer Sonde samt Halterung
// darunter. Vorbelegung liefert Klipper (printer.nozzle_locator.park:
// Config-Werte, sonst Bettmitte und Z 60). Geaenderte Werte werden in die
// Vorlage und die aktive Config zurueckgeschrieben, damit sie beim
// naechsten Mal wieder da sind; fuer den laufenden Zyklus zaehlt die per
// NOZZLE_LOCATOR_PARK tatsaechlich angefahrene Position.
// ---------------------------------------------------------------------
function xyParkDefaults() {
  return Promise.resolve(
    $.get(printerUrl(printerIp, "/printer/objects/query?nozzle_locator=park"))
  ).then(function (data) {
    var p = data && data.result && data.result.status &&
            data.result.status.nozzle_locator &&
            data.result.status.nozzle_locator.park;
    var okNums = p && p.length >= 3 && p.slice(0, 3).every(function (v) {
      return typeof v === 'number' && isFinite(v);
    });
    if (!okNums) throw new Error(
      "Klipper liefert keine Anfahrposition (printer.nozzle_locator.park) " +
      "-- ist die Sonde aktiviert und Klipper bereit?");
    return { x: p[0], y: p[1], z: p[2] };
  });
}

function xyParkDialog(def) {
  function field(id, label, val) {
    return '<div class="col-4"><label class="form-label small mb-0" for="' +
      id + '">' + label + '</label><input type="number" step="0.1" ' +
      'class="form-control form-control-sm" id="' + id + '" value="' +
      Number(val).toFixed(1) + '"></div>';
  }
  return confirmDialog({
    title: "XY-Sonde: Anfahren",
    body: '<p class="small">Der Kopf faehrt mit dem Referenztool auf diese ' +
          'Position. Danach stellst du Sonde samt Halterung grob mittig ' +
          'darunter. Z ist nur die Freihoehe zum Unterschieben und die ' +
          'Fahrhoehe -- gemessen wird tiefer.</p>' +
          '<div class="row g-2">' + field('xy-park-x', 'X', def.x) +
          field('xy-park-y', 'Y', def.y) + field('xy-park-z', 'Z', def.z) +
          '</div>',
    okLabel: "Anfahren"
  }).then(function (ok) {
    if (!ok) return null;
    // Die Felder stehen noch im Modal-Body, bis der naechste Dialog ihn
    // ueberschreibt -- also sofort lesen.
    var v = {
      x: parseFloat($('#xy-park-x').val()),
      y: parseFloat($('#xy-park-y').val()),
      z: parseFloat($('#xy-park-z').val())
    };
    if ([v.x, v.y, v.z].some(function (n) { return !isFinite(n); })) {
      throw new Error("Anfahrposition: X, Y und Z muessen Zahlen sein.");
    }
    return v;
  });
}

// Reine Funktion: setzt park_x/park_y/park_z im Text einer xy_probe-Config.
// Ersetzt vorhandene Zeilen (auch auskommentierte "#park_x: ..."), sonst
// Einfuegen direkt hinter [nozzle_locator]. Getestet in
// tests/check_xy_offset_ui.js.
function xyPatchParkLines(content, park) {
  var vals = { park_x: park.x, park_y: park.y, park_z: park.z };
  var keys = ['park_x', 'park_y', 'park_z'];
  var lines = String(content).split("\n");
  var out = [], done = {}, secIdx = -1;
  for (var i = 0; i < lines.length; i++) {
    var m = /^\s*#?\s*(park_[xyz])\s*:/.exec(lines[i]);
    if (m) {
      if (!done[m[1]]) {
        out.push(m[1] + ": " + Number(vals[m[1]]).toFixed(1));
        done[m[1]] = true;
      }
      continue;
    }
    out.push(lines[i]);
    if (/^\s*\[nozzle_locator\]\s*$/.test(lines[i])) secIdx = out.length;
  }
  var missing = keys.filter(function (k) { return !done[k]; });
  if (missing.length) {
    if (secIdx < 0) throw new Error(
      "Keine [nozzle_locator]-Sektion in der Sonden-Config gefunden.");
    var ins = missing.map(function (k) {
      return k + ": " + Number(vals[k]).toFixed(1);
    });
    out.splice.apply(out, [secIdx, 0].concat(ins));
  }
  return out.join("\n");
}

function xyWriteParkConfig(park) {
  return updateConfigFile("xy_probe.cfg.disabled", function (c) {
    return xyPatchParkLines(c, park);
  }).then(function () {
    return updateConfigFile("xy_probe.cfg", function (c) {
      return xyPatchParkLines(c, park);
    });
  });
}

function xyToolheadPosition() {
  return Promise.resolve(
    $.get(printerUrl(printerIp, "/printer/objects/query?toolhead=position"))
  ).then(function (data) {
    var p = data && data.result && data.result.status &&
            data.result.status.toolhead && data.result.status.toolhead.position;
    if (!p || p.length < 3) throw new Error("Keine Kopfposition von Klipper.");
    return p;
  });
}

// Faehrt an und weist die Position POSITIV nach. Das Bett ist hier noch
// leer, ein Recovery-G28 waere also erlaubt -- aber {transport} heisst
// "laeuft noch", nicht "fertig": erst warten, bis der Drucker steht, dann
// die Kopfposition abfragen. Der naechste Dialog schickt den Nutzer an
// die Maschine.
function xyParkMove(park) {
  var script = "NOZZLE_LOCATOR_PARK X=" + park.x.toFixed(1) +
               " Y=" + park.y.toFixed(1) + " Z=" + park.z.toFixed(1);
  return sendGcodeWithRecovery(script, "Anfahrposition anfahren")
    .then(function (r) {
      if (!r || r.handled) throw new Error("Anfahren fehlgeschlagen.");
      var wait = r.transport ? waitForPrinterIdle(300000)
                             : Promise.resolve(true);
      return wait;
    })
    .then(function (idle) {
      if (!idle) throw new Error(
        "Der Drucker steht nach dem Anfahren nicht still -- Abbruch, " +
        "solange das Bett noch leer ist.");
      return xyToolheadPosition();
    })
    .then(function (pos) {
      var off = Math.max(Math.abs(pos[0] - park.x), Math.abs(pos[1] - park.y),
                         Math.abs(pos[2] - park.z));
      if (!(off <= 0.5)) throw new Error(
        "Der Kopf steht nicht auf der Anfahrposition (Abweichung " +
        off.toFixed(1) + " mm) -- Abbruch, solange das Bett noch leer ist.");
      return true;
    });
}

// Kommando des Eddy-Messlaufs aus Tool-Auswahl und Referenz. Referenz
// immer dabei, sortiert, ohne Dubletten; ohne Angaben das nackte Kommando
// (Klipper nimmt dann alle Tools und das konfigurierte Referenztool).
// opts.fineGap (Tobi, 2026-09-04, nach Lauf 11): Feinspalt in mm als
// FINE_GAP -- kleinerer Spalt, damit die Spitze gegen Block und Platine
// dominiert. Leer = Klipper-Default (fine_gap der Config); unter 0,2 mm
// (MIN_GAP-Boden) oder Unsinn wirft, damit nie ein kaputtes Kommando
// rausgeht.
function xyCalibrateCommand(selectedTools, refTool, opts) {
  opts = opts || {};
  var cmd;
  if (refTool === null || refTool === undefined || isNaN(parseInt(refTool, 10))) {
    cmd = "CALIBRATE_XY_OFFSETS";
  } else {
    var ref = parseInt(refTool, 10);
    var set = {};
    set[ref] = true;
    (selectedTools || []).forEach(function (t) {
      var n = parseInt(t, 10);
      if (!isNaN(n)) set[n] = true;
    });
    var tools = Object.keys(set).map(Number).sort(function (a, b) { return a - b; });
    cmd = "CALIBRATE_XY_OFFSETS REF_TOOL=" + ref + " TOOLS=" + tools.join(",");
  }
  var raw = opts.fineGap;
  if (raw !== undefined && raw !== null && String(raw).trim() !== '') {
    var g = parseFloat(String(raw).replace(',', '.'));
    if (!isFinite(g) || g < 0.2 || g > 3) {
      throw new Error("Feinspalt muss zwischen 0,2 und 3 mm liegen");
    }
    cmd += " FINE_GAP=" + String(+g.toFixed(3));
  }
  return cmd;
}

function xySelectedTools() {
  return $(".calibrate-tool-checkbox:checked").map(function () {
    return parseInt(this.value, 10);
  }).get().filter(function (v) { return !isNaN(v); });
}

// Feld "Feinspalt" im XY-Block; leer = Default. Wert bleibt im Browser.
function xyFineGapValue() {
  var v = $('#xy-fine-gap').val();
  try { localStorage.setItem('offset_xy_fine_gap', v || ''); } catch (e) { /* egal */ }
  return v;
}

// Baut das Messlauf-Kommando aus Panel und Feld; bei Unsinn im Feld eine
// Meldung statt eines Laufs.
function xyBuildRunCommand() {
  return xyCalibrateCommand(xySelectedTools(), getSelectedReferenceTool(0),
                            { fineGap: xyFineGapValue() });
}

function xyWizard() {
  return confirmDialog({
    title: "XY-Sonde: Anstecken",
    body: "Sonde jetzt per USB anstecken. Die Halterung kommt NOCH NICHT " +
          "aufs Bett -- erst wird aktiviert und neu gehomt, und das " +
          "Bett muss dafuer leer bleiben.",
    okLabel: "Ist angesteckt"
  }).then(function (ok) {
    if (!ok) return null;
    return xyProbeCheckPresent().then(function () {
      if (typeof showToast === 'function') {
        showToast("Sonde wird aktiviert, Klipper startet neu…", "info");
      }
      return xyProbeActivate();
    }).then(function () {
      if (typeof showToast === 'function') {
        showToast("Klipper ist bereit, homt jetzt…", "info");
      }
      return ensureHomedAfterActivate();
    }).then(function () {
      return ensureLeveledBeforeSetup();
    }).then(function () {
      return sendGcodeWithRecovery("NOZZLE_LOCATOR_READ DURATION=1.0",
                                   "Sonde prüfen");
    }).then(function (r) {
      if (!xyStepOk(r)) throw new Error(
        (r && r.transport)
          ? "Die Verbindung zur Sondenpruefung ist abgerissen -- ob die " +
            "Sonde antwortet, ist damit ungeklaert. Abbruch, solange das " +
            "Bett noch leer ist."
          : "Sonde pruefen fehlgeschlagen.");
      // Schritt 5a (Spec R-B'): Anfahrposition waehlen, Referenztool
      // hinfahren, Position nachweisen -- das Bett ist dabei noch leer.
      return xyParkDefaults();
    }).then(function (def) {
      return xyParkDialog(def);
    }).then(function (park) {
      if (!park) throw new Error(
        "Anfahren abgebrochen. Die Sonde ist bereits aktiviert -- bitte " +
        "deaktivieren, bevor sie abgezogen wird.");
      return xyWriteParkConfig(park).then(function () {
        return xyParkMove(park);
      });
    }).then(function () {
      return confirmDialog({
        title: "XY-Sonde: Aufsetzen",
        body: "Sonde samt Halterung jetzt grob mittig unter die Duese " +
              "stellen. AB HIER nicht mehr homen, ohne die Halterung " +
              "vorher wieder abzunehmen.",
        okLabel: "Ist erledigt"
      });
    }).then(function (ok2) {
      // Anders als der Abbruch ganz am Anfang ist die Sonde hier schon
      // aktiviert (siehe Fix-Runde 2) - ein stilles return liesse den
      // Nutzer mit aktiver Sonden-Config zurueck, ohne dass er es merkt.
      // Denselben Weg wie jeden anderen Fehlschlag nach der Aktivierung
      // nehmen: in den catch() werfen, der "Sonde deaktivieren" anbietet.
      if (!ok2) throw new Error(
        "Aufsetzen abgebrochen. Die Sonde ist bereits aktiviert -- bitte " +
        "deaktivieren, bevor sie abgezogen wird.");
      // Kein Trockenlauf mehr im Ablauf (Tobi, 2026-09-04): die Wege sind
      // am 250er ueber viele Laeufe bekannt, und der Messlauf selbst prueft
      // Homing, Leveling und Toolchanger-Status. CALIBRATE_XY_OFFSETS
      // DRY_RUN=1 bleibt als Kommando fuer neue Aufbauten erhalten.
      return null;
    }).then(function () {
        // Der Fortschrittsdialog geht VOR dem Senden auf (Tobi, 2026-09-04:
        // "das sollte offen bleiben, damit der User sieht, dass die
        // Messung noch laeuft"). Er pollt xy_progress, Konsole und das
        // Live-Raster und gibt "Weiter" erst frei, wenn Klipper den Lauf
        // beendet meldet. Das Senden selbst laeuft daneben; sein Ergebnis
        // ist wie gehabt kein Beweis fuer irgendetwas ({transport} =
        // laeuft noch), ein echter Fehler wirft aber sofort.
        // Auswahl, Referenz und Feinspalt aus Panel und Feld (Tobi,
        // 2026-09-04) -- ohne Panel (Tests, alte Seite) das nackte
        // Kommando. Ein unsinniger Feinspalt bricht hier ab, BEVOR der
        // Fortschrittsdialog aufgeht; die Halterung steht schon, also
        // bietet der catch() unten das Deaktivieren an.
        var cmd;
        try {
          cmd = xyBuildRunCommand();
        } catch (e) {
          if (/Feinspalt/.test(e.message)) {
            e.xyHolderMounted = true;
            throw e;
          }
          cmd = "CALIBRATE_XY_OFFSETS";
        }
        var progress = xyRunProgressDialog("XY-Messlauf läuft");
        return xySendMounted(cmd, "XY-Messlauf").then(function (r) {
          if (r && r.transport && typeof showToast === 'function') {
            showToast("Verbindung zum Messlauf verloren - er laeuft weiter, " +
                      "der Dialog zeigt den Stand.", "warning");
          }
          return progress;
        }, function (e) {
          // Dialog schliessen, damit die Fehlermeldung des catch() darf
          $("#confirmModalOk").prop("disabled", false).trigger("click");
          throw e;
        });
      }).then(function () {
        // Der naechste Schritt waere xyProbeDeactivate() ->
        // FIRMWARE_RESTART, also ein Abbruch mitten in der Bewegung.
        // Deshalb zusaetzlich warten, bis der Drucker selbst idle meldet.
        return waitForPrinterIdle(3600000);
      }).then(function (idle) {
        if (!idle) {
          var e = new Error(
            "Der Drucker arbeitet nach dem Messlauf immer noch. Der " +
            "Assistent haelt hier an, damit kein FIRMWARE_RESTART in eine " +
            "laufende Bewegung faellt. Wenn der Drucker steht: die Sonde " +
            "unten deaktivieren, ERST DANACH die Halterung abnehmen.");
          e.xyHolderMounted = true;
          throw e;
        }
        return updateAllProbeResults();
      }).then(function () {
        // Zwei Wege (Tobi, 2026-09-04: "das soll der Nutzer sich aussuchen
        // koennen"): deaktivieren und abstecken wie bisher, oder die Sonde
        // aktiv und angesteckt lassen -- dann kann der naechste Lauf direkt
        // ueber den Knopf "Messlauf" im XY-Block starten, ohne Neustart,
        // Homen und Aufsetzen.
        return alertDialog("Abschließen",
          "<p class=\"mb-2\"><b>Deaktivieren:</b> die Sonde wird aus der Config " +
          "entfernt und Klipper neu gestartet. Erst DANACH die Halterung " +
          "abnehmen und die Sonde abziehen.</p>" +
          "<p class=\"mb-0\"><b>Aktiv lassen:</b> Sonde und Halterung bleiben, wie " +
          "sie sind. Ein weiterer Lauf startet dann direkt über „Messlauf“ im " +
          "XY-Block; deaktivieren geht später über „Sonde deaktivieren“ dort. " +
          "Vor jedem Homen muss die Halterung trotzdem runter.</p>",
          { okLabel: "Deaktivieren",
            extraLabel: "Sonde aktiv lassen", extraClass: "btn-outline-secondary" });
      }).then(function (choice) {
        if (choice === 'extra') {
          return alertDialog("Sonde bleibt aktiv",
            "Sonde und Halterung bleiben. Nächster Lauf: „Messlauf“ im " +
            "XY-Block. Nicht homen, solange die Halterung auf dem Bett steht.",
            { okClass: "btn-secondary" }).then(function () { return 'kept'; });
        }
        // Schlaegt das Deaktivieren selbst fehl, steht die Halterung immer
        // noch auf dem Bett -- der catch() unten muss das wissen.
        return xyProbeDeactivate().catch(function (e) {
          e.xyHolderMounted = true;
          throw e;
        }).then(function () {
          return alertDialog("Fertig",
            "Sonde ist deaktiviert. Halterung jetzt vom Bett nehmen und die " +
            "Sonde abziehen.");
        });
      });
  }).catch(function (err) {
    var mounted = !!(err && err.xyHolderMounted);
    var detail = gcodeErrorMessage(err) || (err && err.message) ||
                 "Unbekannter Fehler";
    var body = '<p class="mb-0">' + escapeHtml(detail) + '</p>';
    if (mounted) {
      body += '<p class="mt-2 mb-0 text-warning">' +
        '<i class="bi bi-exclamation-triangle"></i> Die Halterung steht ' +
        'noch auf dem Bett. ERST die Halterung abnehmen, DANN erst wieder ' +
        'homen -- nicht homen, solange sie noch draufsteht.</p>';
    }
    // Der wichtigste Zweig: egal, welcher Schritt scheitert, die Sonde
    // muss sich von hier aus deaktivieren lassen, ohne den Assistenten
    // erneut zu durchlaufen - sonst bleibt sie aktiviert stehen und der
    // naechste Klipper-Start scheitert. extraLabel/extraClass sind die
    // einzigen Optionen, die confirmDialog/alertDialog kennen; der Choice-
    // Wert 'extra' wird hier selbst ausgewertet (kein automatischer
    // extraAction-Callback in der bestehenden Dialog-Implementierung).
    return alertDialog("XY-Assistent abgebrochen", body,
      { extraLabel: "Sonde deaktivieren", extraClass: "btn-warning" }
    ).then(function (choice) {
      if (choice !== 'extra') return null;
      // Ungeschuetzt wie zuvor waere das der Wurf, der den Assistenten-
      // Knopf dauerhaft auf "Laeuft…" stehen liesse: er faellt in den
      // catch() und damit an jedem .then() vorbei, das ihn wieder
      // freigibt. Die Nachbarfunktionen pruefen aus genau dem Grund.
      if (typeof showToast === 'function') {
        showToast("Sonde wird deaktiviert, Klipper startet neu…", "info");
      }
      return xyProbeDeactivate().then(function () {
        return alertDialog("Sonde deaktiviert", mounted
          ? "Die Sonde ist jetzt aus der Config entfernt. ERST die " +
            "Halterung vom Bett nehmen, DANN erst wieder homen -- danach " +
            "kann auch die Sonde abgezogen werden."
          : "Die Sonde ist jetzt aus der Config entfernt. Sie kann " +
            "abgezogen werden.");
      }).catch(function (err2) {
        var d2 = gcodeErrorMessage(err2) || (err2 && err2.message) ||
                 "Unbekannter Fehler";
        return alertDialog("Deaktivieren fehlgeschlagen",
          '<p class="mb-0">' + escapeHtml(d2) + '</p>');
      });
    });
  });
}

// Direkter Messlauf bei aktiver Sonde (Tobi, 2026-09-04: nach "Sonde
// aktiv lassen" am Ende des Assistenten). Kein Neustart, kein Homen, kein
// Aufsetzen: Halterung und Sonde stehen noch, das Referenztool steht auf
// der Anfahrposition, wohin der letzte Lauf es zurueckgebracht hat. Das
// Kommando selbst prueft Homing, Leveling und Toolchanger-Status.
function xyRunDirect() {
  var cmd;
  try {
    cmd = xyBuildRunCommand();
  } catch (e) {
    if (/Feinspalt/.test(e.message)) return alertDialog("Feinspalt", escapeHtml(e.message));
    cmd = "CALIBRATE_XY_OFFSETS";
  }
  return confirmDialog({
    title: "XY-Messlauf starten",
    body: '<p class="small">Sonde und Halterung stehen noch unter der D&uuml;se ' +
          'des Referenztools, das Referenztool ist montiert und steht auf der ' +
          'Anfahrposition (wie am Ende des letzten Laufs). Es wird nicht gehomt.</p>' +
          '<p class="mb-0"><code>' + escapeHtml(cmd) + '</code></p>',
    okLabel: "Starten"
  }).then(function (ok) {
    if (!ok) return null;
    var progress = xyRunProgressDialog("XY-Messlauf läuft");
    return xySendMounted(cmd, "XY-Messlauf").then(function () {
      return progress;
    }, function (e) {
      $("#confirmModalOk").prop("disabled", false).trigger("click");
      throw e;
    }).then(function () {
      return waitForPrinterIdle(3600000);
    }).then(function () {
      return updateAllProbeResults();
    });
  }).catch(function (err) {
    var detail = gcodeErrorMessage(err) || (err && err.message) || "Unbekannter Fehler";
    return alertDialog("XY-Messlauf abgebrochen",
      '<p class="mb-0">' + escapeHtml(detail) + '</p>' +
      '<p class="mt-2 mb-0 text-warning">Die Halterung steht noch auf dem Bett -- ' +
      'nicht homen, solange sie draufsteht.</p>');
  });
}

$(document).on("click", "#xy-run-btn", function () {
  var $btn = $(this);
  $btn.prop("disabled", true);
  xyRunDirect().then(function () { $btn.prop("disabled", false); },
                     function () { $btn.prop("disabled", false); });
});

$(document).on("click", "#xy-deactivate-btn", function () {
  var $btn = $(this);
  $btn.prop("disabled", true);
  confirmDialog({
    title: "Sonde deaktivieren",
    body: "Die Sonde wird aus der Config entfernt und Klipper neu gestartet " +
          "(das Homing geht dabei verloren). Erst DANACH die Halterung " +
          "abnehmen und die Sonde abziehen.",
    okLabel: "Deaktivieren", okClass: "btn-warning"
  }).then(function (ok) {
    if (!ok) return null;
    return xyProbeDeactivate().then(function () {
      return alertDialog("Fertig",
        "Sonde ist deaktiviert. Halterung jetzt vom Bett nehmen und die " +
        "Sonde abziehen.");
    });
  }).catch(function (err) {
    var detail = gcodeErrorMessage(err) || (err && err.message) || "Unbekannter Fehler";
    return alertDialog("Deaktivieren fehlgeschlagen", '<p class="mb-0">' + escapeHtml(detail) + '</p>');
  }).then(function () { $btn.prop("disabled", false); });
});

$(document).on("click", "#xy-wizard-btn", function () {
  var $btn = $(this);
  var btnHtml = $btn.html();
  $btn.prop("disabled", true).text("Läuft…");
  // Wie bei #dock-cal-btn: der Knopf wird auf BEIDEN Wegen wieder
  // freigegeben. Ein Wurf aus xyWizard() selbst - etwa aus dessen eigenem
  // catch() heraus - liesse ihn sonst dauerhaft gesperrt zurueck, und das
  // waere nur per Reload zu loesen.
  var release = function (e) {
    if (e) console.error("XY-Assistent abgebrochen:", e);
    $btn.prop("disabled", false).html(btnHtml);
  };
  xyWizard().then(function () { release(); }, release);
});

// Ist die XY-Sonde in der Config ueberhaupt scharf? xy_probe.cfg ist die
// Datei, die printer.cfg included: leer bzw. nur Kommentar heisst
// deaktiviert, Vorlageninhalt heisst aktiv. Existiert die Datei gar nicht
// (jede fremde Config, und heute auch beide Drucker des Projekts), ist die
// Antwort ebenfalls "nicht aktiv".
function xyProbeConfigActive() {
  var baseUrl = printerUrl(printerIp, "");
  return fetch(baseUrl + "/server/files/config/xy_probe.cfg", NO_CACHE)
    .then(function (r) { return r.ok ? r.text() : ""; })
    .then(function (text) {
      return String(text || "").split("\n").some(function (line) {
        var s = line.trim();
        return s !== "" && s.charAt(0) !== "#" && s.charAt(0) !== ";";
      });
    })
    .catch(function () { return false; });
}

// Aktivierte Sonde + abgesteckter Knoten = Klipper startet nicht. Moonraker
// laeuft weiter, also koennen wir das genau hier noch reparieren - beim
// Laden der Offset-UI aufgerufen (siehe getTools()).
//
// Fix-Runde 3: das Kriterium ist die CONFIG, nicht der Fehlertext. Die
// vorherige Bedingung liess "mcu" als Ausloeser gelten - das steht in
// praktisch jeder Klipper-MCU-Stoerung ("Lost communication with MCU
// 'mcu'", "MCU 'mcu' shutdown: Timer too close"). Da bisher nirgends eine
// xy_probe.cfg existiert, war ein Fehlalarm der EINZIG mogliche Ausgang:
// eine nicht vorhandene Sonde beschuldigt, vor einer nicht aufgestellten
// Halterung gewarnt, und als "Reparatur" eine ueberfluessige xy_probe.cfg
// geschrieben samt Klipper-Neustart.
function checkXyProbeStranded() {
  var baseUrl = printerUrl(printerIp, "");
  return fetch(baseUrl + "/printer/info", NO_CACHE)
    .then(function (r) { return r.json(); })
    .then(function (j) {
      var st = j && j.result && j.result.state;
      if (st !== 'error' && st !== 'shutdown') return;
      var msg = String((j.result && j.result.state_message) || '');
      return xyProbeConfigActive().then(function (active) {
        if (!active) return null;
        return offerXyProbeRescue(msg);
      });
    })
    .catch(function () { return null; }); // Moonraker selbst nicht erreichbar - hier nichts zu melden
}

function offerXyProbeRescue(stateMessage) {
  var first = String(stateMessage || '').split("\n")[0];
  return alertDialog(
    "XY-Sonde blockiert den Start",
    '<p class="mb-2">Klipper startet nicht, und in der Config steht ' +
    'noch die XY-Sonde. Wurde sie abgezogen, ohne sie vorher zu ' +
    'deaktivieren?</p>' +
    (first ? '<p class="mb-2 small text-secondary">Klipper meldet: <code>' +
             escapeHtml(first) + '</code></p>' : '') +
    '<p class="mb-0 text-warning"><i class="bi bi-exclamation-triangle">' +
    '</i> Dieser Check weiss nicht, ob die XY-Halterung noch auf dem ' +
    'Bett steht. Erst pruefen und ggf. abnehmen, bevor irgendwo ' +
    'gehomt wird.</p>',
    { extraLabel: "Sonde deaktivieren und neu starten",
      extraClass: "btn-warning" }
  ).then(function (choice) {
    if (choice !== 'extra') return null;
    return xyProbeDeactivate().then(function () {
      if (typeof showToast === 'function') {
        showToast("Sonde deaktiviert, Klipper laeuft wieder", "success");
      }
    }).catch(function (err) {
      var detail = gcodeErrorMessage(err) || (err && err.message) ||
                   "Unbekannter Fehler";
      return alertDialog("Deaktivieren fehlgeschlagen",
        '<p class="mb-0">' + escapeHtml(detail) + '</p>');
    });
  });
}

function syncPidSelectAllState() {
  var $all = $(".pid-tool-checkbox");
  var $checked = $(".pid-tool-checkbox:checked");
  $("#pid-select-all").prop("checked", $all.length > 0 && $all.length === $checked.length);
}

$(document).on("change", "#pid-select-all", function () {
  $(".pid-tool-checkbox").prop("checked", $(this).prop("checked"));
});

$(document).on("change", ".pid-tool-checkbox", function () {
  syncPidSelectAllState();
});

// Apply PID values to the tool config files
$(document).on("click", "#apply-pid-btn", function () {
  var $btn = $(this);
  var btnHtml = '<i class="bi bi-check-circle"></i> APPLY PID TO CONFIG';
  var toolValues = {};
  var requests = [];
  var pending = [];

  Object.keys(_pidResults).sort(function (a, b) { return a - b; }).forEach(function (k) {
    var v = _pidResults[k];
    if (!v || typeof v.pid_kp !== 'number' || !v.extruder) return;
    toolValues[k] = v;
    pending.push({ tool: k, v: v });
    ['pid_Kp', 'pid_Ki', 'pid_Kd'].forEach(function (key) {
      requests.push({ tool: k, key: key, section: v.extruder });
    });
  });

  if (!pending.length) {
    if (typeof showToast === 'function') showToast("No PID values to apply", "warning");
    return;
  }

  $btn.prop("disabled", true).text("Loading...");
  fetchToolConfigValues(requests).then(function (cur) {
    $btn.prop("disabled", false).html(btnHtml);

    var entries = pending.map(function (p) {
      return {
        tool: p.tool,
        file: "toolchanger/tools/T" + p.tool + ".cfg",
        section: p.v.extruder,
        changes: [
          { key: "pid_Kp", from: cur[p.tool + "|pid_Kp"], to: p.v.pid_kp.toFixed(3) },
          { key: "pid_Ki", from: cur[p.tool + "|pid_Ki"], to: p.v.pid_ki.toFixed(3) },
          { key: "pid_Kd", from: cur[p.tool + "|pid_Kd"], to: p.v.pid_kd.toFixed(3) }
        ]
      };
    });

    var note = '"Current" is read from the config file. Klipper already uses ' +
      'the new values at runtime; writing them here makes that permanent — ' +
      'no <code>SAVE_CONFIG</code>, which cannot write options an included ' +
      'file already defines.';

    return confirmDialog({
      title: "Apply PID values?",
      body: offsetChangeListHtml(entries, note),
      okLabel: "OK — apply",
      okClass: "btn-success"
    });
  }).then(function (ok) {
    if (!ok) return;

    $btn.prop("disabled", true).text("Applying...");
    updateToolPidValues(toolValues)
      .then(function () {
        if (typeof showToast === 'function') showToast("PID values saved to config", "success");
      })
      .catch(function (err) {
        var detail = "";
        try { detail = (err.responseJSON || err).message || ""; } catch (_) {}
        // Eine fehlgeschlagene Uebernahme sieht sonst aus wie eine erfolgreiche.
        alertDialog("Apply PID failed", escapeHtml(detail || "Unbekannter Fehler"));
      })
      .finally(function () {
        $btn.prop("disabled", false).html(btnHtml);
      });
  });
});

// Klipper restart
// Deliberately not SAVE_CONFIG: every value this app calibrates -
// gcode_x/y/z_offset, tool_probe z_offset, pid_Kp/Ki/Kd - lives in an
// included T<n>.cfg, and Klipper refuses to autosave options an include
// already defines ("conflicts with included value"). The APPLY buttons
// write those files directly; a restart is what makes Klipper read them.
$(document).on("click", "#restart-klipper-btn", function() {
  var $btn = $(this);
  var btnHtml = '<i class="bi bi-arrow-clockwise"></i> RESTART KLIPPER';

  var body =
    '<div class="border border-secondary-subtle rounded p-2 mb-2 bg-dark">' +
      '<div class="fw-bold mb-1">RESTART</div>' +
      '<div class="small">Klipper liest die Konfigurationsdateien neu ein. ' +
      'Damit werden die per APPLY geschriebenen Werte aktiv.</div>' +
    '</div>' +
    '<div class="small text-warning">' +
      '<i class="bi bi-exclamation-triangle"></i> Nicht waehrend eines Drucks. ' +
      'Der Drucker verliert das Homing.' +
    '</div>' +
    '<div class="small text-secondary mt-1">' +
      'Nicht angewendete Kalibrierwerte gehen dabei verloren - sie stehen nur ' +
      'zur Laufzeit. Vorher die APPLY-Buttons nutzen.' +
    '</div>';

  confirmDialog({
    title: "Klipper neu starten?",
    body: body,
    okLabel: "OK — neu starten",
    okClass: "btn-warning"
  }).then(function(ok) {
    if (!ok) return;

    $btn.prop("disabled", true).text("Neustart...");
    $.get(printerUrl(printerIp, "/printer/gcode/script?script=RESTART"))
      .always(function() {
        // Die Verbindung bricht durch den Neustart zwangslaeufig ab
        if (typeof showToast === 'function') showToast("Klipper startet neu", "success");
        setTimeout(function() {
          $btn.prop("disabled", false).html(btnHtml);
          fetchOffsetStatus().then(getTools);
        }, 8000);
      });
  });
});

// --------------------------
// Tool change URL (used by index.js)
// --------------------------
function toolChangeURL(tool) {
  let x_pos = parseFloat($("#captured-x").find(":first-child").text());
  let y_pos = parseFloat($("#captured-y").find(":first-child").text());
  let z_pos = parseFloat($("#captured-z").find(":first-child").text());

  if (Number.isNaN(x_pos) || Number.isNaN(y_pos) || Number.isNaN(z_pos)) {
    let url = printerUrl(printerIp, "/printer/gcode/script?script=OFFSET_BEFORE_PICKUP_GCODE");
    url += "%0AT" + tool;
    url += "%0AOFFSET_AFTER_PICKUP_GCODE";
    url += '%0ASET_TOOL_PARAMETER T=' + tool + ' PARAMETER=gcode_x_offset VALUE="0.0"';
    url += '%0ASET_TOOL_PARAMETER T=' + tool + ' PARAMETER=gcode_y_offset VALUE="0.0"';
    return url;
  }

  const master = getSelectedReferenceTool(0);
  if (String(tool) !== String(master)) {
    const rawX = $(`input[name=T${tool}-x-pos]`).val();
    const rawY = $(`input[name=T${tool}-y-pos]`).val();
    const tool_x = parseFloat(rawX);
    const tool_y = parseFloat(rawY);

    const hasX = rawX !== "" && rawX !== undefined && !Number.isNaN(tool_x);
    const hasY = rawY !== "" && rawY !== undefined && !Number.isNaN(tool_y);

    if (hasX && hasY) {
      x_pos = tool_x;
      y_pos = tool_y;
    }
  }

  x_pos = x_pos.toFixed(3);
  y_pos = y_pos.toFixed(3);
  z_pos = z_pos.toFixed(3);

  let url = printerUrl(printerIp, "/printer/gcode/script?script=OFFSET_BEFORE_PICKUP_GCODE");
  url += "%0AT" + tool;
  url += "%0AOFFSET_AFTER_PICKUP_GCODE";
  url += '%0ASET_TOOL_PARAMETER T=' + tool + ' PARAMETER=gcode_x_offset VALUE="0.0"';
  url += '%0ASET_TOOL_PARAMETER T=' + tool + ' PARAMETER=gcode_y_offset VALUE="0.0"';
  url += "%0ASAVE_GCODE_STATE NAME=RESTORE_POS";
  url += "%0AG90";
  url += "%0AG0 Z" + z_pos + " F3000";
  url += "%0AG0 X" + x_pos + " Y" + y_pos + " F12000";
  url += "%0ARESTORE_GCODE_STATE NAME=RESTORE_POS";
  return url;
}

// --------------------------
// Tool list loader (called by index.js)
// --------------------------
function getTools() {
  // Beim Laden der Offset-UI: Rettungsnetz pruefen, unabhaengig vom
  // eigentlichen Toolchanger-Query darunter (der schlaegt fehl, wenn
  // Klipper wegen der Sonde gar nicht erst startet).
  checkXyProbeStranded();

  $.get(printerUrl(printerIp, "/printer/objects/query?toolchanger"))
    .done(function(data){

      var tool_names   = data.result.status.toolchanger.tool_names;
      var tool_numbers = data.result.status.toolchanger.tool_numbers;
      var active_tool  = data.result.status.toolchanger.tool_number;

      var master = computeDefaultRef(tool_numbers);

      // Build query for tool objects
      var queryUrl = "/printer/objects/query?";
      tool_names.forEach(function(name) { queryUrl += encodeURIComponent(name) + "&"; });
      queryUrl = queryUrl.slice(0,-1);

      $.get(printerUrl(printerIp, queryUrl))
        .done(function(toolData){

          // ── Build XY content ──
          var xyContent = '<ul class="list-group list-group-flush">';

          tool_numbers.forEach(function(tool_number, i){
            var toolObj = toolData.result.status[tool_names[i]];
            var cx = toolObj.gcode_x_offset.toFixed(3);
            var cy = toolObj.gcode_y_offset.toFixed(3);

            var disabled = tool_number !== active_tool ? "disabled" : "";
            var tc_disabled = tool_number === active_tool ? "disabled" : "";

            if (tool_number === master) {
              xyContent += masterToolItem({tool_number: tool_number, disabled: disabled, tc_disabled: tc_disabled});
            } else {
              xyContent += nonMasterToolItem({tool_number: tool_number, cx_offset: cx, cy_offset: cy, disabled: disabled, tc_disabled: tc_disabled});
            }
          });

          xyContent += '</ul>';
          xyContent += '<div class="p-2">' +
            '<button class="btn btn-success w-100" id="apply-xy-btn">' +
              '<i class="bi bi-check-circle"></i> APPLY XY OFFSETS TO KLIPPER' +
            '</button></div>';

          // ── Fetch offset status for Z-cal + Probe-cal ──
          fetchOffsetStatus().then(function(){

            var zCalContent = calibrateButton(tool_numbers, _offsetPresent);

            var zHeaderStatus = _offsetPresent
              ? '<span class="text-secondary">Ready</span>'
              : '<span class="text-warning">offset module not found</span>';

            // ── Build Probe Cal content ──
            var probeCalContent = probeCalibrationSection(tool_numbers, _offsetPresent);

            var probeStatus = '';
            if (!_offsetPresent) {
              probeStatus = '<span class="text-warning">offset module not found</span>';
            } else {
              var calTools = Object.keys(_probeCalResults);
              if (calTools.length > 0) {
                probeStatus = '<span class="text-success">Last: ' + calTools.map(function(k){ return 'T'+k; }).join(', ') + '</span>';
              } else {
                probeStatus = '<span class="text-secondary">Configured</span>';
              }
            }

            // ── Assemble accordion ──
            var $acc = $("#offset-accordion");
            $acc.html("");
            $acc.next("#global-save-config-wrap").remove();
            // Tool-Auswahl sichtbar ueber allen Abschnitten (siehe
            // toolSelectionPanel). Vorherige Auswahl uebernehmen, damit ein
            // Rerender (getTools nach Referenzwechsel) nichts zuruecksetzt.
            var prevSel = $(".calibrate-tool-checkbox").map(function () {
              return this.checked ? this.value : null;
            }).get();
            var hadSel = $(".calibrate-tool-checkbox").length > 0;
            $("#tool-selection-wrap").remove();
            $acc.before('<div id="tool-selection-wrap" class="mb-2">' +
                        toolSelectionPanel(tool_numbers) + '</div>');
            if (hadSel) {
              $(".calibrate-tool-checkbox").each(function () {
                this.checked = prevSel.indexOf(this.value) !== -1;
              });
            }

            $acc.append(accordionSection(
              'accordion-xy',
              'XY Offsets',
              '<span class="text-success">Master: T' + master + '</span>',
              xyContent,
              true
            ));

            var zCalFull = '<ul class="list-group list-group-flush">' + zCalContent + '</ul>';
            if (Object.keys(_zSwitchResults).length > 0) {
              zCalFull += '<div class="p-2">' +
                '<button class="btn btn-success w-100" id="apply-z-btn">' +
                  '<i class="bi bi-check-circle"></i> APPLY Z OFFSETS TO KLIPPER' +
                '</button></div>';
            }

            $acc.append(accordionSection(
              'accordion-zcal',
              'Z-Switch Calibration',
              zHeaderStatus,
              zCalFull,
              false
            ));

            var pidContent = pidCalibrationSection(tool_numbers, _offsetPresent);
            var pidStatus = _offsetPresent
              ? (Object.keys(_pidResults).length
                  ? '<span class="text-success">Neu: ' +
                    Object.keys(_pidResults).map(function(k){ return 'T'+k; }).join(', ') +
                    '</span>'
                  : '<span class="text-secondary">Bereit</span>')
              : '<span class="text-warning">offset module not found</span>';

            $acc.append(accordionSection(
              'accordion-probecal',
              'Probe Offset Calibration',
              probeStatus,
              probeCalContent,
              false
            ));

            var dockContent = dockCalibrationSection(tool_numbers, _offsetPresent);
            var dockStatus = _offsetPresent
              ? (Object.keys(_dockResults).length
                  ? '<span class="text-success">Neu: ' +
                    Object.keys(_dockResults).map(function(k){ return 'T'+k; }).join(', ') +
                    '</span>'
                  : '<span class="text-secondary">Bereit</span>')
              : '<span class="text-warning">offset module not found</span>';

            $acc.append(accordionSection(
              'accordion-pid',
              'Extruder PID Calibration',
              pidStatus,
              pidContent,
              false
            ));

            $acc.append(accordionSection(
              'accordion-dock',
              'Dock Calibration',
              dockStatus,
              dockContent,
              false
            ));

            var xyCompareStatus = _offsetPresent
              ? (Object.keys(_xyResults).filter(function(k){ return k !== 'ref_tool'; }).length
                  ? '<span class="text-success">Neu</span>'
                  : '<span class="text-secondary">Bereit</span>')
              : '<span class="text-warning">offset module not found</span>';

            $acc.append(accordionSection(
              'accordion-xy-compare',
              'XY-Offsets: Eddy vs. Kamera',
              xyCompareStatus,
              xyOffsetSection(),
              false
            ));
            renderXyBlock();

            // Klipper-Neustart-Button
            $acc.after(
              '<div class="mt-2" id="global-save-config-wrap">' +
                '<button class="btn btn-outline-warning w-100" id="restart-klipper-btn">' +
                  '<i class="bi bi-arrow-clockwise"></i> RESTART KLIPPER' +
                '</button>' +
              '</div>'
            );

            // Re-apply calibrate button state
            $(".calibrate-ref-checkbox").prop("checked", false);
            $("#calibrate-ref-" + master).prop("checked", true);
            $("#calibrate-tool-" + master).prop("checked", true);
            syncSelectAllState();

            $("#master-status-badge").text("Master: T" + master);

            if (_offsetPresent) $(".z-fields").removeClass("d-none");

            startProbeResultsUpdatesOnce();
            updateAllProbeResults();
          });
        })
        .fail(function(jqXHR){
          if (typeof showToast === 'function') showToast("Failed to load tool data: " + (jqXHR.statusText || "unknown"), "danger");
        });
    })
    .fail(function(jqXHR){
      if (typeof showToast === 'function') showToast("Failed to load tools: " + (jqXHR.statusText || "unknown"), "danger");
    });
}

// --------------------------
// Offset calc (used by index.js handlers)
// --------------------------
function updateOffset(tool, axis) {
  const $newEl = $(`#T${tool}-${axis}-new`);
  if (!$newEl.length) return;

  const rawPosition = $(`input[name=T${tool}-${axis}-pos]`).val();
  const position = parseFloat(rawPosition);
  const hasPosition = rawPosition !== "" && rawPosition !== undefined && !Number.isNaN(position);
  const capturedText = $(`#captured-${axis}`).find(":first-child").text();
  const captured_pos = parseFloat(capturedText);

  if (hasPosition && capturedText !== "" && !Number.isNaN(captured_pos)) {

    // Offsets are zeroed during calibration tool change, so just compare positions
    let new_offset = captured_pos - position;

    // Preserve your sign-flip behavior
    if (new_offset < 0) new_offset = Math.abs(new_offset);
    else new_offset = -new_offset;

    const rawTxt = new_offset.toFixed(3);
    $newEl.attr("data-raw", rawTxt);
    $newEl.find(">:first-child").text(rawTxt);
  } else {
    $newEl.attr("data-raw", "0.000");
    $newEl.find(">:first-child").text("0.0");
  }

  applyMasterReferenceXY(axis);
}

// --------------------------
// REQUIRED by index.js updatePage()
// --------------------------
function updateTools(tool_numbers, tool_number_active) {
  const master = getSelectedReferenceTool(0);
  const activeTool = parseInt(tool_number_active, 10);

  // Capture button enabled only if master tool is active
  const $captureBtn = $("#capture-pos");
  if ($captureBtn.length) {
    if (activeTool !== parseInt(master, 10)) {
      $captureBtn.addClass("disabled").prop("disabled", true);
    } else {
      $captureBtn.removeClass("disabled").prop("disabled", false);
    }
  }

  // Keep tool row controls synced with currently loaded tool.
  // Only the active tool may fetch/write XY values.
  (tool_numbers || []).forEach((tool_no) => {
    const isActive = parseInt(tool_no, 10) === activeTool;

    $(`#T${tool_no}-fetch-x, #T${tool_no}-fetch-y`)
      .toggleClass("disabled", !isActive)
      .prop("disabled", !isActive);

    $(`input[name=T${tool_no}-x-pos], input[name=T${tool_no}-y-pos]`)
      .prop("disabled", !isActive);

    // Active tool cannot be selected again.
    const $tcBtn = $(`button.toolchange-btn[data-tool=${tool_no}]`);
    $tcBtn.toggleClass("disabled", isActive).prop("disabled", isActive);

    updateOffset(tool_no, "x");
    updateOffset(tool_no, "y");
  });
}

