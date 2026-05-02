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
      _zSwitchResults = {};
      var pr = st?.probe_results || {};
      for (var k in pr) {
        if (pr[k] && typeof pr[k].probe_z_offset === 'number') {
          _probeCalResults[k] = { probe_z_offset: pr[k].probe_z_offset };
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
}

function updateAllProbeResults() {
  getProbeResults().then(function(probeResults) {
    $('button.toolchange-btn').each(function(){
      updateProbeResults($(this).data("tool"), probeResults);
    });
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
    return _toolProbeOffsets[k] !== undefined || _probeCalResults[k];
  });
  if (!hasAny) return '';

  var rows = sortedTools.map(function(t) {
    var k = String(t);
    var current = _toolProbeOffsets[k];
    var currentStr = (typeof current === 'number') ? current.toFixed(3) : '-';
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
    '<button class="btn ' + btnClass + ' w-100 mb-2" id="probe-cal-btn" ' + disabledAttr + '>' +
      'CALIBRATE PROBE OFFSETS' +
    '</button>' +
    probeCalResultsTable(sortedTools) +
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

  // Only send override if not config
  const zCalcPart = (method !== "config") ? ` Z_CALC=${method}` : "";
  const script = `CALIBRATE_ALL_Z_OFFSETS TOOLS=${selectedTools.join(",")}${zCalcPart} REF=${refTool}`;

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

$(document).on("click", "span[id$='-x-new'], span[id$='-y-new'], span[id$='-z-new']", function() {
  const id = $(this).attr("id") || "";
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
  lines.push('CALIBRATE_PROBE_OFFSETS TOOLS=' + selectedTools.join(',') + ' REF_TOOL=' + config.ref_tool);

  var script = lines.join('\n');

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

// Apply XY offsets to Klipper
$(document).on("click", "#apply-xy-btn", function() {
  var $btn = $(this);
  $btn.prop("disabled", true).text("Applying...");
  var master = getSelectedReferenceTool(0);
  var lines = [];
  $('button.toolchange-btn').each(function(){
    var tool = $(this).data("tool");
    if (parseInt(tool, 10) === parseInt(master, 10)) return;
    var rawX = $("#T" + tool + "-x-new").attr("data-raw");
    var rawY = $("#T" + tool + "-y-new").attr("data-raw");
    if (rawX && rawY) {
      lines.push("SET_TOOL_GCODE_OFFSET T=" + tool + " X=" + rawX + " Y=" + rawY);
    }
  });
  if (!lines.length) {
    if (typeof showToast === 'function') showToast("No XY offsets to apply", "warning");
    $btn.prop("disabled", false).html('<i class="bi bi-check-circle"></i> APPLY XY OFFSETS TO KLIPPER');
    return;
  }
  var script = lines.join('\n');
  $.get(printerUrl(printerIp, "/printer/gcode/script?script=" + encodeURIComponent(script)))
    .done(function() {
      if (typeof showToast === 'function') showToast("XY offsets applied — SAVE_CONFIG to persist", "success");
    })
    .fail(function(err) {
      var msg = "Apply XY offsets failed";
      try { msg += ": " + err.responseJSON.error.message; } catch(_){}
      if (typeof showToast === 'function') showToast(msg, "danger");
    })
    .always(function() {
      $btn.prop("disabled", false).html('<i class="bi bi-check-circle"></i> APPLY XY OFFSETS TO KLIPPER');
    });
});

// Apply Z-switch offsets to Klipper
$(document).on("click", "#apply-z-btn", function() {
  var $btn = $(this);
  $btn.prop("disabled", true).text("Applying...");
  var lines = [];
  for (var k in _zSwitchResults) {
    var zOff = _zSwitchResults[k].z_offset;
    if (typeof zOff === 'number') {
      lines.push("SET_TOOL_GCODE_OFFSET T=" + k + " Z=" + zOff.toFixed(6));
    }
  }
  if (!lines.length) {
    if (typeof showToast === 'function') showToast("No Z offsets to apply", "warning");
    $btn.prop("disabled", false).html('<i class="bi bi-check-circle"></i> APPLY Z OFFSETS TO KLIPPER');
    return;
  }
  var script = lines.join('\n');
  $.get(printerUrl(printerIp, "/printer/gcode/script?script=" + encodeURIComponent(script)))
    .done(function() {
      if (typeof showToast === 'function') showToast("Z offsets applied — SAVE_CONFIG to persist", "success");
    })
    .fail(function(err) {
      var msg = "Apply Z offsets failed";
      try { msg += ": " + err.responseJSON.error.message; } catch(_){}
      if (typeof showToast === 'function') showToast(msg, "danger");
    })
    .always(function() {
      $btn.prop("disabled", false).html('<i class="bi bi-check-circle"></i> APPLY Z OFFSETS TO KLIPPER');
    });
});

// Global SAVE_CONFIG
$(document).on("click", "#global-save-config-btn", function() {
  var $btn = $(this);
  $btn.prop("disabled", true).text("Saving...");
  $.get(printerUrl(printerIp, "/printer/gcode/script?script=SAVE_CONFIG"))
    .done(function() {
      if (typeof showToast === 'function') showToast("Config saved — Klipper restarting", "success");
    })
    .fail(function(err) {
      var msg = "SAVE_CONFIG failed";
      try { msg += ": " + err.responseJSON.error.message; } catch(_){}
      if (typeof showToast === 'function') showToast(msg, "danger");
      $btn.prop("disabled", false).html('<i class="bi bi-save"></i> SAVE_CONFIG (persist all changes)');
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
  const old_offset = parseFloat($(`#T${tool}-${axis}-offset`).text());

  if (hasPosition && capturedText !== "" && !Number.isNaN(captured_pos) && !Number.isNaN(old_offset)) {

    let new_offset = (captured_pos - old_offset) - position;

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

