#!/usr/bin/env python3
"""Prueft, ob der Homing-Rebound die Tool-Offsets vertraegt. Bewegt nichts.

Anlass: Homing mit T4 scheiterte an

    !! Move out of range: 0.000 332.580 9.417

Das Homing faehrt Y auf `position_max - homing_rebound_y`. Steht der Rebound
auf 0, endet der Kopf exakt auf position_max. Der gcode-Offset des montierten
Tools wird danach obendrauf gerechnet - bei T4 sind das +2.58mm - und
330 + 2.58 = 332.58 liegt ausserhalb. Danach scheitert nicht nur das Homing,
sondern JEDER weitere Move, solange der Kopf dort steht.

Betroffen war jedes Tool mit positivem Y-Offset, also T1-T5; nur das
Referenz-Tool T0 mit Offset 0 kam durch. Die Offsets kommen aus der
Kalibrierung, koennen sich also jederzeit aendern - deshalb dieser Test.

Auf dem Drucker laufen lassen:

    scp tests/check_homing_rebound.py biqu@<IP>:/tmp/
    ssh biqu@<IP> '~/klippy-env/bin/python /tmp/check_homing_rebound.py'

Exit-Code 0 = sauber, 1 = Befunde.
"""

import json
import sys
import urllib.request

URL = "http://localhost:7125"


def fetch(path):
    with urllib.request.urlopen(URL + path, timeout=15) as r:
        return json.load(r)["result"]


def as_float(v, default=None):
    try:
        return float(str(v).split('#')[0].strip())
    except (TypeError, ValueError):
        return default


def main():
    s = fetch("/printer/objects/query?configfile")["status"]["configfile"]
    settings = s["settings"]

    cfg = settings.get("gcode_macro homing_override_config")
    if cfg is None:
        print("  kein [gcode_macro homing_override_config] - nichts zu pruefen")
        return 0

    # Offsets aller konfigurierten Tools einsammeln
    tools = {}
    for key, sec in settings.items():
        if not key.startswith("tool t") or " " not in key:
            continue
        name = key.split(" ", 1)[1].upper()
        tools[name] = (as_float(sec.get("gcode_x_offset"), 0.0),
                       as_float(sec.get("gcode_y_offset"), 0.0))
    if not tools:
        print("  keine [tool Tn] Sektionen - nichts zu pruefen")
        return 0

    bad = 0
    for axis, idx in (("x", 0), ("y", 1)):
        rebound = as_float(cfg.get("variable_homing_rebound_%s" % axis))
        if rebound is None:
            print("  %s: kein homing_rebound_%s gesetzt - uebersprungen"
                  % (axis.upper(), axis))
            continue

        # Nur positive Offsets schieben ueber position_max hinaus.
        worst_tool, worst = None, 0.0
        for name in sorted(tools):
            off = tools[name][idx]
            if off > worst:
                worst_tool, worst = name, off

        limit = as_float(settings["stepper_%s" % axis].get("position_max"))
        if worst_tool is None:
            print("  %s: rebound=%.2fmm, kein Tool mit positivem Offset  ok"
                  % (axis.upper(), rebound))
            continue

        target = (limit - rebound + worst) if limit is not None else None
        ok = rebound >= worst
        if not ok:
            bad += 1
        print("  %s %s: rebound=%.2fmm, groesster positiver Offset %+.2fmm (%s)"
              % ("ok " if ok else "FAIL", axis.upper(), rebound, worst,
                 worst_tool))
        if target is not None:
            print("       Homing endet bei %s=%.2f, Limit ist %.2f%s"
                  % (axis.upper(), target, limit,
                     "" if ok else "  -> Move out of range"))
        if ok and rebound - worst < 1.0:
            print("       Warnung: nur %.2fmm Luft - die naechste "
                  "Kalibrierung kann das kippen" % (rebound - worst))

    if bad:
        print("\n%d Befund(e): homing_rebound erhoehen. Achtung, die Sektion "
              "in toolchanger-config.cfg\nueberschreibt die gleichnamige in "
              "readonly-configs/homing.cfg - dort aendern wirkt nicht." % bad)
    else:
        print("\nHoming-Rebound vertraegt alle Tool-Offsets.")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
