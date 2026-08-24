#!/usr/bin/env python3
"""Prueft, dass jedes Tool seinen eigenen Extruder und Luefter hat.

Anlass: [tool T5] stand auf `extruder: extruder` statt `extruder: extruder5`.
Aufgefallen ist es erst bei der PID-Uebernahme, weil die Webapp daraufhin in
T5.cfg nach einer Sektion [extruder] suchte, die dort nicht existiert. Die
eigentliche Folge waere schlimmer gewesen: SELECT_TOOL haette bei T5 den
Extruder von T0 aktiviert - also mit dem falschen Motor extrudiert und die
falsche Duese geheizt.

Geprueft wird nicht gegen die Dateistruktur, sondern gegen die Bedeutung:
auf einem Toolchanger hat jedes Tool seine eigene Hardware. Zwei Tools, die
auf denselben Extruder oder Luefter zeigen, sind ein Konfigurationsfehler -
unabhaengig davon, wie die Configs auf Dateien verteilt sind.

Auf dem Drucker laufen lassen:

    scp tests/check_tool_extruders.py biqu@<IP>:/tmp/
    ssh biqu@<IP> '~/klippy-env/bin/python /tmp/check_tool_extruders.py'

Exit-Code 0 = sauber, 1 = Befunde. Bewegt nichts.
"""

import json
import sys
import urllib.request

URL = "http://localhost:7125"


def fetch(path):
    with urllib.request.urlopen(URL + path, timeout=15) as r:
        return json.load(r)["result"]


def main():
    settings = fetch("/printer/objects/query?configfile")["status"]
    settings = settings["configfile"]["settings"]

    tools = {}
    for key, sec in settings.items():
        # "tool t5" -> T5; die Nummer kommt aus tool_number, nicht aus dem Namen
        if not key.startswith("tool ") or key.startswith("tool_"):
            continue
        name = key.split(" ", 1)[1].upper()
        tools[name] = {
            "extruder": (sec.get("extruder") or "").strip() or None,
            "fan": (sec.get("fan") or "").strip() or None,
        }

    if not tools:
        print("  keine [tool ...] Sektionen - nichts zu pruefen")
        return 0

    bad = 0

    # 1) Verweist jedes Tool auf existierende Hardware?
    for name in sorted(tools):
        ext = tools[name]["extruder"]
        if ext is None:
            print("  FAIL %s hat kein extruder: gesetzt" % name)
            bad += 1
        elif ext.lower() not in settings:
            print("  FAIL %s -> extruder %r gibt es nicht" % (name, ext))
            bad += 1

    # 2) Teilt sich mehr als ein Tool dieselbe Hardware? Auf einem
    #    Toolchanger ist das immer ein Fehler.
    for what in ("extruder", "fan"):
        seen = {}
        for name in sorted(tools):
            val = tools[name][what]
            if val is None:
                continue
            seen.setdefault(val.lower(), []).append(name)
        for val, names in sorted(seen.items()):
            if len(names) > 1:
                print("  FAIL %s teilen sich %s %r"
                      % (" und ".join(names), what, val))
                bad += 1

    # 3) Uebersicht, damit man sieht was geprueft wurde
    print("  %d Tools geprueft:" % len(tools))
    for name in sorted(tools):
        print("    %-4s extruder=%-12s fan=%s"
              % (name, tools[name]["extruder"], tools[name]["fan"]))

    if bad:
        print("\n%d Befund(e). Ein Tool, das auf fremde Hardware zeigt, "
              "heizt und extrudiert\nbeim Werkzeugwechsel am falschen Kopf."
              % bad)
    else:
        print("\nJedes Tool hat seinen eigenen Extruder und Luefter.")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
