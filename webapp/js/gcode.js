
// --------------------------
// Klipper Console + GCode
// --------------------------
let _consoleWs = null;
let _consoleReconnectTimer = null;
let _consoleInitialized = false;

function consoleAppend(text, type) {
  var $out = $("#console-output");
  if (!$out.length) return;

  var $line = $("<div>");
  if (type === "command") {
    $line.css("color", "#58a6ff").text(">>> " + text);
  } else if (type === "error") {
    $line.css("color", "#f85149").text(text);
  } else {
    $line.text(text);
  }
  $out.append($line);

  // Keep max 500 lines
  var children = $out.children();
  if (children.length > 500) children.slice(0, children.length - 500).remove();

  // Auto-scroll to bottom
  $out.scrollTop($out[0].scrollHeight);
}

function consoleConnect() {
  if (!printerIp) return;
  if (_consoleWs && _consoleWs.readyState <= 1) return; // already open/connecting

  var wsUrl = "ws://" + printerIp + "/websocket";
  _consoleWs = new WebSocket(wsUrl);

  _consoleWs.onopen = function() {
    // Subscribe to gcode responses
    _consoleWs.send(JSON.stringify({
      jsonrpc: "2.0",
      method: "server.connection.identify",
      params: { client_name: "offset-console", version: "1.0", type: "web", url: "" },
      id: 1
    }));
    // Fetch recent history
    consoleFetchHistory();
  };

  _consoleWs.onmessage = function(evt) {
    try {
      var data = JSON.parse(evt.data);
      if (data.method === "notify_gcode_response") {
        var params = data.params;
        if (Array.isArray(params)) {
          params.forEach(function(msg) { consoleAppend(msg); });
        }
      }
    } catch (_) {}
  };

  _consoleWs.onclose = function() {
    // Reconnect after 5s if still connected to printer
    if (printerIp && _consoleInitialized) {
      _consoleReconnectTimer = setTimeout(consoleConnect, 5000);
    }
  };

  _consoleWs.onerror = function() {
    // onclose will fire after this
  };
}

function consoleDisconnect() {
  _consoleInitialized = false;
  if (_consoleReconnectTimer) {
    clearTimeout(_consoleReconnectTimer);
    _consoleReconnectTimer = null;
  }
  if (_consoleWs) {
    _consoleWs.onclose = null; // prevent reconnect
    _consoleWs.close();
    _consoleWs = null;
  }
}

function consoleFetchHistory() {
  $.get(printerUrl(printerIp, "/server/gcode_store?count=50"))
    .done(function(data) {
      var store = data.result && data.result.gcode_store;
      if (!Array.isArray(store)) return;
      store.forEach(function(entry) {
        consoleAppend(entry.message, entry.type);
      });
    });
}

function consoleInit() {
  if (_consoleInitialized) return;
  _consoleInitialized = true;
  $("#klipper-console").show();
  $("#console-output").empty();
  consoleConnect();
}

// --------------------------
// GCode send
// --------------------------
function sendGcode() {
  var cmd = $("#gcode-input").val();
  if (!cmd || !cmd.trim()) return;
  var url = printerUrl(printerIp, "/printer/gcode/script?script=" + encodeURIComponent(cmd));
  $("#gcode-input").val("");

  consoleAppend(cmd, "command");

  $.get(url)
    .fail(function(jqXHR){
      consoleAppend("Error: " + extractErrorMessage(jqXHR), "error");
      showToast("GCode failed: " + extractErrorMessage(jqXHR), "danger");
    });
}

$(document).ready(function() {
  $(document).on("click", "#gcode-send", function(e){
    sendGcode();
  });

  $("#gcode-input").on("enterKey", function(e){
    sendGcode();
  });

  $("#gcode-input").keyup(function(e){
    if(e.keyCode == 13){
      $(this).trigger("enterKey");
    }
  });

  $(document).on("click", "#console-clear", function(){
    $("#console-output").empty();
  });
});
