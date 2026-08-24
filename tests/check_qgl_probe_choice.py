#!/usr/bin/env python3
"""Prueft, welche Probe QUAD_GANTRY_LEVEL waehlt - ohne den Drucker zu bewegen.

Anlass: T0 mit Eddy war montiert, QGL nahm trotzdem den mechanischen Tap.
Ursache war kein Logikfehler in der Auswahl, sondern der Zeitpunkt: Klipper
rendert ein gcode_macro komplett, bevor die erste Zeile laeuft. Die
Tool-Erkennung stand im selben Makro und kam damit zu spaet. Nach einem
QGL-Abbruch stand tool_number auf -1, _T-1_QGL gibt es nicht, also griff der
Default "tap" - still und mit Aufheizen der Duese.

Zweiter Fall: der Eddy liest nur bis rund 2.5mm zuverlaessig. Ist das Gantry
noch nie geleveled worden, steht es womoeglich mehrere Millimeter schief, der
Sensor liest an der tiefen Ecke Unsinn (gemessen: -381mm) und QGL bricht ab.
Der grobe Durchgang faellt dann auf den Tap zurueck, der feine bleibt Eddy.

Der Test rendert _QGL_FOR_ACTIVE_TOOL gegen den Live-Status des Druckers,
einmal je Szenario, und liest aus den gerenderten Zeilen "M117 QGL coarse (..)"
und "M117 QGL fine (..)" ab, welche Proben gewaehlt worden waeren.

Auf dem Drucker laufen lassen (dort gibt es jinja2 und Moonraker):

    scp tests/check_qgl_probe_choice.py biqu@<IP>:/tmp/
    ssh biqu@<IP> '~/klippy-env/bin/python /tmp/check_qgl_probe_choice.py'

Exit-Code 0 = sauber, 1 = Befunde. Bewegt nichts.
"""

import json
import re
import sys
import urllib.parse
import urllib.request

URL = "http://localhost:7125"
MACRO = "_QGL_FOR_ACTIVE_TOOL"


def fetch(path):
    with urllib.request.urlopen(URL + path, timeout=15) as r:
        return json.load(r)["result"]


class Status(dict):
    """printer.foo und printer['foo'], wie Klippers Wrapper."""

    def __getattr__(self, k):
        try:
            return self[k]
        except KeyError:
            raise AttributeError(k)


def wrap(o):
    if isinstance(o, dict):
        return Status((k, wrap(v)) for k, v in o.items())
    if isinstance(o, list):
        return [wrap(v) for v in o]
    return o


class Raised(Exception):
    pass


def render(body, printer, params=None):
    import jinja2

    def raise_error(msg=""):
        raise Raised(str(msg))

    noop = lambda *a, **k: ""
    env = jinja2.Environment("{%", "%}", "{", "}",
                             extensions=["jinja2.ext.do"])
    ctx = {
        "printer": printer,
        "params": params or {},
        "rawparams": "",
        "action_respond_info": noop,
        "action_raise_error": raise_error,
        "action_emergency_stop": noop,
        "action_call_remote_method": noop,
        "action_log": noop,
    }
    return env.from_string(body).render(ctx)


def choice_for(body, status, probe_tool, changer_tool,
               applied=True, params=None):
    """Rendert das Makro mit gesetztem Tool- und Leveling-Zustand und gibt
    "<coarse>/<fine>" zurueck - oder ("error", <meldung>) beim Abbruch."""
    st = wrap(json.loads(json.dumps(status)))
    st.setdefault("tool_probe_endstop", Status())
    st.setdefault("toolchanger", Status())
    st.setdefault("quad_gantry_level", Status())
    st["tool_probe_endstop"]["active_tool_number"] = probe_tool
    st["toolchanger"]["tool_number"] = changer_tool
    st["quad_gantry_level"]["applied"] = applied
    try:
        out = render(body, st, params)
    except Raised as e:
        return ("error", str(e))
    c = re.search(r"QGL coarse \(([a-z]+)\)", out)
    f = re.search(r"QGL fine \(([a-z]+)\)", out)
    if not c or not f:
        return ("kein M117-Treffer", out[:200])
    return ("%s/%s" % (c.group(1), f.group(1)), None)


def main():
    settings = fetch("/printer/objects/query?configfile")["status"]
    settings = settings["configfile"]["settings"]

    key = "gcode_macro " + MACRO.lower()
    if key not in settings:
        print("FEHLER: %s ist nicht geladen - alte Config?" % MACRO)
        return 1
    body = settings[key]["gcode"]

    names = fetch("/printer/objects/list")["objects"]
    q = "&".join(urllib.parse.quote(n) for n in names)
    status = fetch("/printer/objects/query?" + q)["status"]

    # Welche Tools haben laut _Tn_QGL welche Probe?
    eddy_tool = tap_tool = None
    for n in range(10):
        k = "gcode_macro _t%d_qgl" % n
        if k not in settings:
            continue
        cp = str(settings[k].get("variable_coarse_probe", "")).strip('"\' ').lower()
        if cp == "eddy" and eddy_tool is None:
            eddy_tool = n
        if cp == "tap" and tap_tool is None:
            tap_tool = n
    print("  Tools laut _Tn_QGL:  eddy=T%s  tap=T%s" % (eddy_tool, tap_tool))

    E, T = eddy_tool, tap_tool
    cases = []
    if E is not None:
        cases.append(("Eddy-Tool, Gantry geleveled",
                      E, E, True, None, "eddy/eddy"))
        # Der Fall aus dem Fehlerbericht: nach einem QGL-Abbruch steht der
        # Toolchanger auf -1, die Probe-Erkennung kennt das Tool aber.
        cases.append(("Eddy-Tool, Toolchanger auf -1",
                      E, -1, True, None, "eddy/eddy"))
        # Ungelevelt kann das Gantry mehrere mm schief stehen - dort ist der
        # Eddy ausser Reichweite, also grob mit Tap, fein mit Eddy.
        cases.append(("Eddy-Tool, Gantry NICHT geleveled",
                      E, E, False, None, "tap/eddy"))
        cases.append(("dito, aber COARSE_PROBE=eddy erzwungen",
                      E, E, False, {"COARSE_PROBE": "eddy"}, "eddy/eddy"))
    if T is not None:
        cases.append(("Tap-Tool montiert", T, T, True, None, "tap/tap"))
        cases.append(("Tap-Tool, Gantry NICHT geleveled",
                      T, T, False, None, "tap/tap"))
    # Kein Tool ermittelbar: lieber abbrechen als still tappen.
    cases.append(("kein Tool erkennbar", -1, -1, True, None, "error"))

    bad = 0
    for name, probe_tool, changer_tool, applied, params, want in cases:
        got, extra = choice_for(body, status, probe_tool, changer_tool,
                                applied, params)
        ok = (got == want)
        if not ok:
            bad += 1
        print("  %s %-38s erwartet %-10s -> %s%s"
              % ("ok " if ok else "FAIL", name, want, got,
                 "" if ok or not extra else "  (%s)" % extra[:70]))

    if bad:
        print("\n%d Befund(e): QGL wuerde die falsche Probe waehlen." % bad)
    else:
        print("\nQGL waehlt die Probe des tatsaechlich montierten Tools.")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
