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
let _toolGcodeOffsets = {};    // { "0": {x:0, y:0, z:0}, ... } current tool gcode offsets
let _zSwitchResults = {};      // { "0": { z_offset: 0.0, z_trigger: 1.23 }, ... }

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

    $("#confirmModalLabel").text(title);
    $("#confirmModalBody").html(opts.body || "");

    var $ok = $("#confirmModalOk");
    $ok.text(okLabel)
       .removeClass("btn-primary btn-success btn-warning btn-danger")
       .addClass(okClass);

    var modal = bootstrap.Modal.getOrCreateInstance(el);
    var settled = false;

    function settle(result) {
      if (settled) return;
      settled = true;
      $ok.off("click.confirmDialog");
      $(el).off("hidden.bs.modal.confirmDialog");
      resolve(result);
    }

    $ok.off("click.confirmDialog").on("click.confirmDialog", function() {
      settle(true);
      modal.hide();
    });
    $(el).off("hidden.bs.modal.confirmDialog")
         .on("hidden.bs.modal.confirmDialog", function() { settle(false); });

    modal.show();
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
function offsetChangeListHtml(entries, note) {
  var html = "";

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
      return '<tr>' +
        '<td class="px-1 py-0 text-nowrap"><code>' + escapeHtml(c.key) + '</code></td>' +
        '<td class="px-1 py-0 text-end text-secondary">' + escapeHtml(fromTxt) + '</td>' +
        '<td class="px-1 py-0 text-center text-secondary">&rarr;</td>' +
        '<td class="px-1 py-0 text-end text-success fw-bold">' + escapeHtml(toTxt) + '</td>' +
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

  if (note) html += '<div class="small text-secondary">' + note + '</div>';
  return html;
}

// --------------------------
// Config file update via Moonraker File API
// --------------------------
// Updates gcode offsets directly in tool config files (avoids SAVE_CONFIG conflicts with included files)
// Uploads sequentially to avoid Moonraker 500 errors from concurrent writes.
function updateToolConfigOffsets(toolOffsets) {
  // toolOffsets: { "0": {x: "0.000", y: "0.000", z: "0.000"}, "1": {x: "0.53", z: "0.640"}, ... }
  // Only keys present in each tool's object are updated.
  var tools = Object.keys(toolOffsets);
  var baseUrl = printerUrl(printerIp, "");

  function processNext(idx) {
    if (idx >= tools.length) return Promise.resolve();
    var t = tools[idx];
    var offsets = toolOffsets[t];
    var filePath = "toolchanger/tools/T" + t + ".cfg";
    return fetch(baseUrl + "/server/files/config/" + filePath)
      .then(function(r) { return r.text(); })
      .then(function(content) {
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
          OffsetDebug.log("No offset lines found in " + filePath);
          return processNext(idx + 1);
        }
        var formData = new FormData();
        var blob = new Blob([content], {type: 'text/plain'});
        formData.append('file', blob, filePath);
        formData.append('root', 'config');
        return fetch(baseUrl + "/server/files/upload", { method: 'POST', body: formData })
          .then(function() { return processNext(idx + 1); });
      });
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
  var keyRx = new RegExp('^(\\s*' + key + '\\s*[:=]\\s*).*$');
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

// Reads a key out of a config file. sectionName null = first match anywhere
// (mirrors updateToolConfigOffsets); otherwise the key must sit in that section.
function readConfigValue(content, sectionName, key) {
  var lines = content.split('\n');
  var anySectionRx = /^\s*\[[^\]]+\]/;
  var sectionRx = sectionName
    ? new RegExp('^\\s*\\[\\s*' + sectionName.replace(/[.*+?^${}()|[\]\\]/g, '\\$&') + '\\s*\\]')
    : null;
  var keyRx = new RegExp('^\\s*' + key + '\\s*[:=]\\s*([^#]*)');
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
    return fetch(baseUrl + "/server/files/config/toolchanger/tools/T" + t + ".cfg")
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
  var baseUrl = printerUrl(printerIp, "");

  function processNext(idx) {
    if (idx >= tools.length) return Promise.resolve();
    var t = tools[idx];
    var filePath = "toolchanger/tools/T" + t + ".cfg";
    return fetch(baseUrl + "/server/files/config/" + filePath)
      .then(function(r) { return r.text(); })
      .then(function(content) {
        var updated = replaceInConfigSection(
          content, "tool_probe T" + t, "z_offset", toolZOffsets[t]);
        if (updated === null) {
          OffsetDebug.log("No [tool_probe T" + t + "] z_offset line in " + filePath);
          return processNext(idx + 1);
        }
        var formData = new FormData();
        var blob = new Blob([updated], {type: 'text/plain'});
        formData.append('file', blob, filePath);
        formData.append('root', 'config');
        return fetch(baseUrl + "/server/files/upload", { method: 'POST', body: formData })
          .then(function() { return processNext(idx + 1); });
      });
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
  return $.get(printerUrl(printerIp, "/printer/objects/query?offset"))
    .then(function(ax){
      const st = ax?.result?.status?.offset;
      _offsetPresent = !!st;
      _offsetZCalcDefault = (st?.z_calc_method || null);
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
      return null;
    });
}

// --------------------------
// Probe results (Z)
// --------------------------
function getProbeResults() {
  return $.get(printerUrl(printerIp, "/printer/objects/query?offset"))
    .then(data => data?.result?.status?.offset?.probe_results || {})
    .catch(() => ({}));
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

function updateAllProbeResults() {
  getProbeResults().then(function(probeResults) {
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
  });
}

function startProbeResultsUpdatesOnce() {
  if (_probeInterval) return;
  _probeInterval = setInterval(updateAllProbeResults, 2000);
}

// --------------------------
// Calibration UI
// --------------------------
function calibrateButton(toolNumbers = [], enabled = false) {
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

  const btnClass = enabled ? "btn-primary" : "btn-secondary";
  const disabledAttr = enabled ? "" : "disabled";

  const cfg = (_offsetZCalcDefault || "unknown").toLowerCase();
  const cfgLabel = `Config (offset.cfg: ${cfg})`;

  const sel = (_uiZCalcSelection || "config").toLowerCase();
  const selConfig = sel === "config" ? "selected" : "";
  const selMedian = sel === "median" ? "selected" : "";
  const selAvg    = sel === "average" ? "selected" : "";
  const selTrim   = sel === "trimmed" ? "selected" : "";

  return `
<li class="list-group-item bg-body-tertiary p-2">
  <div class="container">
    <div class="row pb-2">
      <div class="col-12">
        <div class="border border-secondary-subtle rounded p-2 bg-dark">
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
    </div>

    <div class="row pb-2">
      <div class="col-12">
        <div class="border border-secondary-subtle rounded p-2 bg-dark">
          <div class="d-flex justify-content-between align-items-center mb-1">
            <span class="fs-6">Reference (Master) tool</span>
            <small class="text-secondary">Default: ${defaultRef === 0 ? "T0" : `T${defaultRef}`}</small>
          </div>
          <div>${refMarkup}</div>
        </div>
      </div>
    </div>

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
            <small class="text-secondary">0 = no heating</small>
          </div>
          <div class="input-group input-group-sm w-auto">
            <input type="number" id="calibrate-extruder-temp" class="form-control form-control-sm" style="max-width:80px;" min="0" max="350" step="5" value="0" placeholder="0">
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
        '<small class="text-secondary">0 = no heating</small>' +
      '</div>' +
      '<div class="input-group input-group-sm w-auto">' +
        '<input type="number" id="probe-cal-extruder-temp" class="form-control form-control-sm" style="max-width:80px;" min="0" max="350" step="5" value="0" placeholder="0">' +
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

    $.get(printerUrl(printerIp, `/printer/gcode/script?script=${encodeURIComponent(script)}`))
      .done(() => {
        console.log("Calibration started:", script);
        if (typeof showToast === 'function') showToast("Calibration command sent", "success");
      })
      .fail(err => {
        console.error("Calibration failed:", err);
        var msg = "Calibration failed";
        try { msg += ": " + err.responseJSON.error.message; } catch(_){}
        if (typeof showToast === 'function') showToast(msg, "danger");
      })
      .always(() => {
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

    $.get(printerUrl(printerIp, "/printer/gcode/script?script=" + encodeURIComponent(script)))
      .done(function() {
        console.log("Probe calibration started:", script);
        if (typeof showToast === 'function') showToast("Probe calibration command sent", "success");
      })
      .fail(function(err) {
        console.error("Probe calibration failed:", err);
        var msg = "Probe calibration failed";
        try { msg += ": " + err.responseJSON.error.message; } catch(_){}
        if (typeof showToast === 'function') showToast(msg, "danger");
      })
      .always(function() {
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

  var note = 'Reference tool <strong>T' + escapeHtml(master) + '</strong> is not changed.<br>' +
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
        var msg = "Apply XY offsets failed";
        try { msg += ": " + (err.responseJSON || err).message; } catch(_){}
        if (typeof showToast === 'function') showToast(msg, "danger");
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
        var msg = "Apply Z offsets failed";
        try { msg += ": " + (err.responseJSON || err).message; } catch(_){}
        if (typeof showToast === 'function') showToast(msg, "danger");
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
        var msg = "Apply probe offsets failed";
        try { msg += ": " + (err.responseJSON || err).message; } catch(_){}
        if (typeof showToast === 'function') showToast(msg, "danger");
      })
      .finally(function() {
        $btn.prop("disabled", false).html(btnHtml);
      });
  });
});

// Global SAVE_CONFIG
$(document).on("click", "#global-save-config-btn", function() {
  var $btn = $(this);
  var btnHtml = '<i class="bi bi-save"></i> SAVE_CONFIG (persist all changes)';

  var body =
    '<div class="border border-secondary-subtle rounded p-2 mb-2 bg-dark">' +
      '<div class="fw-bold mb-1">SAVE_CONFIG</div>' +
      '<div class="small">Writes every value Klipper has staged at runtime into the ' +
      '<code>#*#</code> auto-save block at the bottom of <code>printer.cfg</code>.</div>' +
    '</div>' +
    '<div class="small text-warning">' +
      '<i class="bi bi-exclamation-triangle"></i> Klipper restarts afterwards. ' +
      'Do not run this during a print.' +
    '</div>' +
    '<div class="small text-secondary mt-1">' +
      'The APPLY buttons above already write directly into the ' +
      '<code>toolchanger/tools/T&lt;n&gt;.cfg</code> files, so offsets do not need this.' +
    '</div>';

  confirmDialog({
    title: "Run SAVE_CONFIG?",
    body: body,
    okLabel: "OK — save & restart",
    okClass: "btn-warning"
  }).then(function(ok) {
    if (!ok) return;

    $btn.prop("disabled", true).text("Saving...");
    $.get(printerUrl(printerIp, "/printer/gcode/script?script=SAVE_CONFIG"))
      .done(function() {
        if (typeof showToast === 'function') showToast("Config saved — Klipper restarting", "success");
      })
      .fail(function(err) {
        var msg = "SAVE_CONFIG failed";
        try { msg += ": " + err.responseJSON.error.message; } catch(_){}
        if (typeof showToast === 'function') showToast(msg, "danger");
        $btn.prop("disabled", false).html(btnHtml);
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

            $acc.append(accordionSection(
              'accordion-probecal',
              'Probe Offset Calibration',
              probeStatus,
              probeCalContent,
              false
            ));

            // Global SAVE_CONFIG button
            $acc.after(
              '<div class="mt-2" id="global-save-config-wrap">' +
                '<button class="btn btn-outline-warning w-100" id="global-save-config-btn">' +
                  '<i class="bi bi-save"></i> SAVE_CONFIG (persist all changes)' +
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

