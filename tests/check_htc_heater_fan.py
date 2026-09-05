#!/usr/bin/env python3
"""Prueft die Entscheidungslogik von klippy/extras/htc_heater_fan.py.

Braucht kein Klipper und keinen Drucker -- nur Python 3:

    python3 tests/check_htc_heater_fan.py

Exit-Code 0 = sauber, 1 = Befunde.
"""
import importlib.util
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
CANDIDATES = [
    os.path.join(HERE, "..", "klippy", "extras", "htc_heater_fan.py"),
    os.path.join(HERE, "htc_heater_fan.py"),
]

FINDINGS = []
CHECKS = [0]


def ok(cond, what, detail=""):
    CHECKS[0] += 1
    if not cond:
        FINDINGS.append("%s%s" % (what, (" -- " + detail) if detail else ""))


def load_module():
    for path in CANDIDATES:
        if os.path.exists(path):
            spec = importlib.util.spec_from_file_location("htc_heater_fan", path)
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            return mod
    raise SystemExit("htc_heater_fan.py nicht gefunden (%s)" % CANDIDATES)


def main():
    m = load_module()
    OFF, ACT, PARK, COOL = (m.STATE_OFF, m.STATE_ACTIVE,
                            m.STATE_PARKED, m.STATE_COOLDOWN)
    speeds = {ACT: 0.8, PARK: 0.5, COOL: 0.6}

    # --- classify_state: wie [heater_fan], plus aktiv/geparkt/abkuehlen ---
    ok(m.classify_state([(22., 0.)], 50., True) == OFF,
       "kalt ohne Sollwert muss 'off' sein")
    ok(m.classify_state([(22., 200.)], 50., True) == ACT,
       "Sollwert gesetzt (noch kalt) muss sofort 'active' sein",
       "wie [heater_fan]: Luefter an, sobald geheizt wird")
    ok(m.classify_state([(22., 200.)], 50., False) == PARK,
       "Sollwert gesetzt, Tool nicht montiert -> 'parked'")
    ok(m.classify_state([(180., 0.)], 50., True) == COOL,
       "Sollwert 0, aber noch heiss -> 'cooldown'")
    ok(m.classify_state([(180., 0.)], 50., False) == COOL,
       "abkuehlen haengt nicht davon ab, ob das Tool montiert ist")
    ok(m.classify_state([(50., 0.)], 50., True) == OFF,
       "genau heater_temp ist noch nicht 'ueber' heater_temp",
       "Klipper prueft current_temp > heater_temp")
    ok(m.classify_state([(22., 0.), (120., 0.)], 50., True) == COOL,
       "mehrere Heizungen: eine heisse reicht")
    ok(m.classify_state([(22., 0.), (22., 210.)], 50., True) == ACT,
       "mehrere Heizungen: ein Sollwert reicht")

    # --- target_speed: Drehzahl je Zustand ---
    ok(m.target_speed(OFF, speeds, None, 0.) == 0.,
       "'off' muss 0 liefern")
    ok(m.target_speed(ACT, speeds, None, 0.) == 0.8, "aktiv -> fan_speed")
    ok(m.target_speed(PARK, speeds, None, 0.) == 0.5, "geparkt -> parked_speed")
    ok(m.target_speed(COOL, speeds, None, 0.) == 0.6,
       "abkuehlen -> cooldown_speed")

    # --- min_speed: Untergrenze nur, solange der Luefter laeuft ---
    ok(m.target_speed(PARK, speeds, None, 0.7) == 0.7,
       "min_speed hebt eine zu niedrige Zustandsdrehzahl an")
    ok(m.target_speed(OFF, speeds, None, 0.7) == 0.,
       "min_speed darf einen kalten Luefter nicht einschalten")

    # --- override: feste Grunddrehzahl, Schutz bleibt ---
    ok(m.target_speed(ACT, speeds, 0.3, 0.) == 0.3,
       "SPEED= ersetzt die Zustandsdrehzahl")
    ok(m.target_speed(PARK, speeds, 0.3, 0.) == 0.3,
       "SPEED= gilt in jedem Zustand gleich")
    ok(m.target_speed(OFF, speeds, 0.3, 0.) == 0.,
       "SPEED= schaltet einen kalten Luefter nicht ein")
    ok(m.target_speed(ACT, speeds, 0.1, 0.4) == 0.4,
       "min_speed gilt auch gegen SPEED=",
       "ein Tippfehler im Override darf die Duese nicht verstopfen")

    # --- chamber_boost: linear, monoton, nie unter base ---
    ch = (40., 60., 1.0)
    ok(m.chamber_boost(0.5, 30., *ch) == 0.5, "unter temp_start: base")
    ok(m.chamber_boost(0.5, 40., *ch) == 0.5, "bei temp_start: noch base")
    ok(abs(m.chamber_boost(0.5, 50., *ch) - 0.75) < 1e-9,
       "auf halbem Weg: Mitte zwischen base und max")
    ok(m.chamber_boost(0.5, 60., *ch) == 1.0, "bei temp_full: max_speed")
    ok(m.chamber_boost(0.5, 80., *ch) == 1.0, "ueber temp_full: bleibt max")
    ok(m.chamber_boost(0.5, None, *ch) == 0.5,
       "kein Messwert (Sensorfehler) -> base, nicht 0")
    ok(m.chamber_boost(0.9, 60., 40., 60., 0.7) == 0.9,
       "chamber_max_speed unter base senkt nicht ab")
    ok(m.chamber_boost(0.5, 55., 50., 50., 1.0) == 0.5,
       "temp_full <= temp_start: keine Anhebung statt Division durch 0")

    # --- target_speed mit Anhebung ---
    ok(abs(m.target_speed(PARK, speeds, None, 0., 50., ch) - 0.75) < 1e-9,
       "Anhebung setzt auf der Zustandsdrehzahl auf")
    ok(abs(m.target_speed(ACT, speeds, 0.4, 0., 50., ch) - 0.7) < 1e-9,
       "Anhebung setzt auch auf SPEED= auf")
    ok(m.target_speed(OFF, speeds, None, 0., 70., ch) == 0.,
       "heisses Gehaeuse schaltet einen kalten Hotend-Luefter nicht ein")
    ok(m.target_speed(ACT, {ACT: 1.0, PARK: 1., COOL: 1.}, None, 0.,
                      80., (40., 60., 1.0)) == 1.0,
       "Ergebnis ist auf 1.0 begrenzt")

    # --- needs_update: Totband gegen sekuendliches Nachstellen ---
    ok(not m.needs_update(0.5, 0.5, False), "gleiche Drehzahl: nichts senden")
    ok(m.needs_update(0.5, 0.8, True), "Zustandswechsel: immer senden")
    ok(m.needs_update(0.5, 0.505, True),
       "Zustandswechsel: auch kleine Differenz senden")
    ok(m.needs_update(0., 0.3, False), "Einschalten: immer senden")
    ok(m.needs_update(0.3, 0., False), "Ausschalten: immer senden")
    ok(not m.needs_update(0.5, 0.51, False),
       "Anhebung um 1 %: unter dem Totband, nicht senden")
    ok(m.needs_update(0.5, 0.52, False),
       "Anhebung um 2 %: Totband erreicht, senden")
    ok(m.needs_update(0.52, 0.5, False),
       "Absenken um 2 %: Totband erreicht, senden")

    print("%d Pruefungen, %d Befunde" % (CHECKS[0], len(FINDINGS)))
    for f in FINDINGS:
        print("  - " + f)
    return 1 if FINDINGS else 0


if __name__ == "__main__":
    sys.exit(main())
