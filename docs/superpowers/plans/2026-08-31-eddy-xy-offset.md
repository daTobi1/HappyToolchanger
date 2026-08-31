# Eddy XY-Offset-Kalibrierung — Implementierungsplan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Eine zweite, aufwärts gerichtete LDC1612-Spule auf dem Druckbett ortet die Düse jedes Tools lateral und liefert daraus automatisch die X/Y-Offsets — als wählbare Alternative zum bisherigen manuellen Kameraverfahren.

**Architecture:** Ein neues Klipper-Modul `nozzle_locator.py` besitzt den Sensor über Klippers eingebauten `ldc1612`-Treiber und beantwortet ausschließlich „wo über mir liegt Metall". Die Fit-Mathematik liegt in einem eigenen, Klipper-freien Modul, damit sie ohne Drucker testbar ist. `offset.py` orchestriert nur den Tool-Durchlauf und speist die Ergebnisse in die vorhandene Apply-/Schreib-Maschinerie. Die Webapp bekommt einen gemeinsamen XY-Block mit Methodenumschalter.

**Tech Stack:** Python 3 (Klipper-Extras, keine externen Abhängigkeiten — bewusst kein numpy im Fit), Vanilla JS + jQuery + Bootstrap (Webapp), Moonraker-API für Config und Restart.

**Spec:** `docs/superpowers/specs/2026-08-31-eddy-xy-offset-design.md`

## Global Constraints

- **Bidirektionaler Sweep ist Pflicht.** Jede Ortung besteht aus Hin- und Rücksweep, deren Scheitel gemittelt werden. Ein einzelner gerichteter Sweep ist nie ein Ergebnis. Grund: ein zeitlinearer Drift wird über den monotonen Sweep zu einem linearen Term in x und verschiebt den Scheitel um `m/(2a)` ≈ 19 µm pro Kelvin.
- **Keine eddy-ng-Abhängigkeit.** Ausschließlich Klippers `ldc1612` und `bulk_sensor`. Eine zweite `[probe_eddy_ng]`-Instanz lässt Klipper nicht starten (`eddy-ng/probe_eddy_ng/probe.py:186` ruft `define_commands()` unbedingt, die Kommandos sind global).
- **Keine Frequenz→Höhe-Kalibrierung.** Es zählt ausschließlich die Rohfrequenz in Hz.
- **`CALIBRATE_XY_OFFSETS` homt nie selbst.** Unhomed → Abbruch mit Meldung.
- **Jeder Abbruchpfad stellt den Idle-Timeout zurück** und kehrt auf das Referenztool zurück.
- **Der Signal-Preflight ist Teil jeder Sweep-Primitive**, kein optionaler Guard.
- **Code muss auf fremden Druckerkonfigurationen laufen** (siehe Memory `generality-requirement`): keine Annahme über Anzahl der Tools, Probe-Typen oder dass überhaupt ein eddy-ng vorhanden ist.
- **Neue Klipper-Module brauchen `sudo systemctl restart klipper`**, nicht nur `RESTART` — `RESTART` lädt `sys.modules` nicht neu.
- **Keine Ziffern in neuen G-Code-Kommandonamen** (Klippers Parser liest nach einer Ziffer jeden Großbuchstaben als Parameter).

---

## Dateistruktur

| Datei | Verantwortung |
|---|---|
| `klippy/extras/nozzle_locator_fit.py` | **neu** — reine Fit-Mathematik, keine Klipper-Imports. Der einzige Teil, der ohne Drucker testbar ist. |
| `klippy/extras/nozzle_locator.py` | **neu** — Sensorbesitz, Sweep-Fahrten, Z-Anfahrt. Kennt keine Tools. |
| `klippy/extras/offset.py` | erweitert — `CALIBRATE_XY_OFFSETS` (die geplante Extraktion `_resolve_tool_run()` ist gestrichen, siehe Task 4) |
| `webapp/js/tools.js` | erweitert — XY-Block, Assistent, Extraktion `updateConfigFile()` |
| `webapp/js/camera.js` | erweitert — „Position übernehmen" |
| `webapp/index.html` | erweitert — Markup des XY-Blocks |
| `configs/{250,350}/xy_probe.cfg` | **neu**, leer = deaktiviert |
| `configs/{250,350}/xy_probe.cfg.disabled` | **neu**, Vorlage mit UUID und Halterungsmaßen |
| `tests/check_nozzle_locator_fit.py` | **neu** — läuft lokal, ohne Drucker |
| `tests/check_klipper_api.py` | erweitert — `ldc1612`/`bulk_sensor`-Oberfläche |
| `tests/README.md` | erweitert |
| `install.sh` | erweitert — **nur** für die beiden Config-Dateien (nach dem „nur initial kopieren"-Muster). Die Module werden von der bestehenden `*.py`-Schleife in Zeile 57 automatisch verlinkt. |

Die Trennung `nozzle_locator_fit.py` / `nozzle_locator.py` ist der wichtigste Schnitt im Plan: sie zieht die gesamte Mathematik — und damit die einzige Stelle, an der der Drift-Fix lebt — aus dem hardwareabhängigen Teil heraus. Alle anderen Python-Tests dieses Repos brauchen einen Drucker; dieser nicht.

---

## Task 1: Fit-Mathematik mit Drift-Nachweis

Der Grundstein. Läuft komplett ohne Drucker und ohne Klipper.

**Files:**
- Create: `klippy/extras/nozzle_locator_fit.py`
- Test: `tests/check_nozzle_locator_fit.py`
- Modify: `tests/README.md`

**Interfaces:**
- Consumes: nichts
- Produces:
  - `parabola_vertex(points) -> float` — `points` ist `[(pos, value), ...]`, mindestens 3 Punkte, wirft `ValueError` bei zu wenigen Punkten oder wenn die Parabel keinen Hochpunkt hat
  - `bidirectional_center(fwd_points, rev_points) -> float`
  - `sweep_quality(points, baseline, min_amplitude) -> (bool, str)`
  - `predicted_drift_shift(drift_per_mm, curvature) -> float` — nur für Tests und Diagnose

- [ ] **Step 1: Test schreiben**

`tests/check_nozzle_locator_fit.py`:

```python
#!/usr/bin/env python3
"""Prueft die Fit-Mathematik der XY-Offset-Ortung.

Braucht KEINE Druckerhardware und kein Klipper -- nur einen
Python-Interpreter. Im Repo:

    python3 tests/check_nozzle_locator_fit.py

Auf dem Drucker (dort liegt ohnehin ein Python; Test und Modul duerfen
dabei nebeneinander in einem Verzeichnis liegen):

    python3 check_nozzle_locator_fit.py

Der Kern ist Test 2 und 3. Sie halten den Befund fest, der das ganze
Verfahren traegt: ein einzelner gerichteter Sweep misst bei driftender
Basislinie systematisch daneben, der bidirektionale Mittelwert nicht.

Exit-Code 0 = sauber, 1 = Befunde.
"""
import os
import sys

# Findet das Modul im Repo-Layout und auch dann, wenn Test und Modul
# nebeneinander liegen (etwa nach einem scp in ein Verzeichnis).
_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)
sys.path.insert(0, os.path.join(_HERE, "..", "klippy", "extras"))

import nozzle_locator_fit as fit  # noqa: E402

FINDINGS = []
CHECKS = [0]


def ok(cond, what, detail=""):
    CHECKS[0] += 1
    if not cond:
        FINDINGS.append("%s%s" % (what, (" -- " + detail) if detail else ""))


def close(a, b, tol, what, detail=""):
    ok(abs(a - b) <= tol, what,
       detail or "%.6f gegen %.6f, Toleranz %.6f" % (a, b, tol))


def bell(center, amplitude, curvature, positions, drift_per_step=0.0):
    """Synthetische Glocke, optional mit linearem Drift in Sweep-Reihenfolge.

    y = amplitude - curvature*(x-center)^2 + drift_per_step * schritt_index

    Der Drift wird ueber den Index aufgetragen, nicht ueber x: genau so
    wirkt er real, weil der Sweep Punkt fuer Punkt in der Zeit laeuft.
    """
    return [(x, amplitude - curvature * (x - center) ** 2 + drift_per_step * i)
            for i, x in enumerate(positions)]


def main():
    # Werte aus dem Vorversuch am 250er:
    # Kruemmung a ~ 115 Hz/mm^2, Amplitude ~ 8000 Hz, Sweep 8 mm in 1-mm-Schritten
    CENTER = 124.0
    AMPL = 8000.0
    CURV = 115.0
    POSITIONS = [120.0, 121.0, 122.0, 123.0, 124.0, 125.0, 126.0, 127.0, 128.0]
    BASELINE = 0.0

    # --- 1: ohne Drift trifft der Fit das bekannte Zentrum exakt ---
    pts = bell(CENTER, AMPL, CURV, POSITIONS)
    close(fit.parabola_vertex(pts), CENTER, 1e-6,
          "Fit ohne Drift trifft das Zentrum nicht")

    # --- 2: ein einzelner Sweep verfehlt es um den vorhergesagten Betrag ---
    # 40 Hz Drift ueber den ganzen Lauf = 1 K laut Vorversuch (39,8 Hz/K).
    # Bei 8 Schritten sind das 5 Hz je Schritt, und weil ein Schritt 1 mm
    # ist, ist die Steigung m = 5 Hz/mm.
    DRIFT_PER_STEP = 5.0
    m = DRIFT_PER_STEP / 1.0                      # Hz pro mm
    expected_shift = m / (2.0 * CURV)             # = 0,0217 mm
    fwd = bell(CENTER, AMPL, CURV, POSITIONS, DRIFT_PER_STEP)
    close(fit.parabola_vertex(fwd), CENTER + expected_shift, 1e-6,
          "Einzelsweep verschiebt sich nicht um m/(2a)")
    close(fit.predicted_drift_shift(m, CURV), expected_shift, 1e-9,
          "predicted_drift_shift stimmt nicht mit dem Fit ueberein")
    # Der Betrag muss in der Groessenordnung liegen, die das Design nennt.
    ok(0.015 <= expected_shift <= 0.030,
       "erwartete Verschiebung ausserhalb der im Design genannten ~19 um",
       "%.1f um" % (expected_shift * 1000.0))

    # --- 3: der bidirektionale Mittelwert hebt den Drift exakt auf ---
    # Rueckweg: dieselben Positionen, aber in umgekehrter Reihenfolge
    # durchfahren -- also traegt der hoechste x-Wert den kleinsten Drift.
    n = len(POSITIONS)
    rev = [(x, AMPL - CURV * (x - CENTER) ** 2 + DRIFT_PER_STEP * (n - 1 - i))
           for i, x in enumerate(POSITIONS)]
    close(fit.bidirectional_center(fwd, rev), CENTER, 1e-9,
          "bidirektionaler Mittelwert hebt den linearen Drift nicht auf")

    # --- 4: eine konstante Ablage verschiebt gar nichts ---
    shifted = [(x, y + 123456.0) for x, y in pts]
    close(fit.parabola_vertex(shifted), CENTER, 1e-6,
          "konstante Ablage verschiebt den Scheitel (darf sie nicht)")

    # --- 5: Guard greift bei zu schwachem Signal ---
    weak = bell(CENTER, 500.0, CURV, POSITIONS)
    good, reason = fit.sweep_quality(weak, BASELINE, 2000.0)
    ok(not good, "sweep_quality laesst zu schwaches Signal durch")
    ok("Amplitude" in reason, "Begruendung nennt die Amplitude nicht", reason)

    # --- 6: Guard greift, wenn der Scheitel am Fensterrand liegt ---
    off = bell(131.0, AMPL, CURV, POSITIONS)   # Zentrum weit rechts vom Fenster
    good, reason = fit.sweep_quality(off, BASELINE, 2000.0)
    ok(not good, "sweep_quality laesst einen Scheitel am Rand durch")
    ok("Rand" in reason, "Begruendung nennt den Rand nicht", reason)

    # --- 7: ein sauberer Sweep kommt durch ---
    good, reason = fit.sweep_quality(pts, BASELINE, 2000.0)
    ok(good, "sweep_quality lehnt einen sauberen Sweep ab", reason)

    # --- 8: zu wenige Punkte und Taeler werden abgewiesen ---
    try:
        fit.parabola_vertex([(1.0, 1.0), (2.0, 2.0)])
        ok(False, "parabola_vertex akzeptiert 2 Punkte")
    except ValueError:
        ok(True, "")
    try:
        valley = [(x, -(AMPL - CURV * (x - CENTER) ** 2)) for x in POSITIONS]
        fit.parabola_vertex(valley)
        ok(False, "parabola_vertex akzeptiert ein Tal als Scheitel")
    except ValueError:
        ok(True, "")

    print("%d Zusicherungen geprueft" % CHECKS[0])
    if FINDINGS:
        for f in FINDINGS:
            print("BEFUND: %s" % f)
        return 1
    print("sauber")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 2: Test laufen lassen, Fehlschlag bestätigen (RED)**

**Auf dem Entwicklungsrechner ist kein Python installiert** (nur die
Microsoft-Store-Platzhalter unter `WindowsApps`). Der Test braucht zwar
keine Druckerhardware, aber einen Interpreter — und der liegt auf dem Pi.
Also dort ausführen. Der Drucker wird dabei **nicht bewegt**, es läuft nur
Rechnerei:

```bash
scp tests/check_nozzle_locator_fit.py biqu@192.168.178.60:/tmp/
sshpass -p biqu ssh biqu@192.168.178.60 'cd /tmp && python3 check_nozzle_locator_fit.py'
```

Expected: `ModuleNotFoundError: No module named 'nozzle_locator_fit'`

Das ist der RED-Schritt: der Test existiert, das Modul noch nicht.

- [ ] **Step 3: Fit-Modul implementieren**

`klippy/extras/nozzle_locator_fit.py`:

```python
# Fit-Helfer fuer die laterale Ortung einer Duese ueber einer Eddy-Spule.
#
# Bewusst ohne Klipper-Importe und ohne numpy: das macht diesen Teil ohne
# Drucker testbar, und er ist die einzige Stelle, an der die Drift-Korrektur
# lebt.
#
# Copyright (C) 2026  HappyToolchanger
# This file may be distributed under the terms of the GNU GPLv3 license.


def parabola_vertex(points):
    """Scheitel der Kleinstequadrate-Parabel durch [(pos, wert), ...].

    Invariant gegen eine *konstante* Ablage: die verschiebt nur c in
    y = a*x^2 + b*x + c, nicht -b/(2a). Ein Drift, der *linear in x* ist,
    verschiebt den Scheitel dagegen um m/(2a) -- siehe bidirectional_center.
    """
    n = len(points)
    if n < 3:
        raise ValueError("Fit braucht mindestens 3 Punkte, hat %d" % n)
    sx = sx2 = sx3 = sx4 = sy = sxy = sx2y = 0.0
    for x, y in points:
        x2 = x * x
        sx += x
        sx2 += x2
        sx3 += x2 * x
        sx4 += x2 * x2
        sy += y
        sxy += x * y
        sx2y += x2 * y
    # Normalgleichungen fuer y = a x^2 + b x + c, geloest per Cramer.
    m11, m12, m13 = sx4, sx3, sx2
    m21, m22, m23 = sx3, sx2, sx
    m31, m32, m33 = sx2, sx, float(n)
    r1, r2, r3 = sx2y, sxy, sy

    def det3(a11, a12, a13, a21, a22, a23, a31, a32, a33):
        return (a11 * (a22 * a33 - a23 * a32)
                - a12 * (a21 * a33 - a23 * a31)
                + a13 * (a21 * a32 - a22 * a31))

    det = det3(m11, m12, m13, m21, m22, m23, m31, m32, m33)
    if det == 0.0:
        raise ValueError("Fit nicht loesbar (entartete Punktlage)")
    a = det3(r1, m12, m13, r2, m22, m23, r3, m32, m33) / det
    b = det3(m11, r1, m13, m21, r2, m23, m31, r3, m33) / det
    if a >= 0.0:
        raise ValueError("Parabel hat keinen Hochpunkt (a = %.6g)" % a)
    return -b / (2.0 * a)


def bidirectional_center(fwd_points, rev_points):
    """Mittelt die Scheitel eines Hin- und eines Ruecksweeps.

    Ein zeitlinearer Drift der Basislinie wird ueber den monoton laufenden
    Sweep zu einem linearen Term in x und verschiebt den Scheitel um
    m/(2a). Der Ruecksweep durchfaehrt x in der Gegenrichtung, seine
    Verschiebung hat damit das umgekehrte Vorzeichen -- der Mittelwert hebt
    den linearen Anteil exakt auf. Stehen bleibt nur die Kruemmung des
    Drifts, und die ist zweiter Ordnung.
    """
    return (parabola_vertex(fwd_points) + parabola_vertex(rev_points)) / 2.0


def predicted_drift_shift(drift_per_mm, curvature):
    """m/(2a) -- um wie viel ein linearer Drift den Scheitel verschiebt.

    drift_per_mm in Hz/mm, curvature als positives a in Hz/mm^2.
    Nur fuer Diagnose und Tests; der Messpfad braucht das nicht.
    """
    return drift_per_mm / (2.0 * curvature)


def sweep_quality(points, baseline, min_amplitude):
    """Preflight, den jeder Sweep bestehen muss. -> (ok, begruendung)

    Faengt die beiden Fehler, die im Vorversuch Messreihen unbrauchbar
    gemacht haben: gar kein Ziel im Fenster, und ein Ziel, das nur halb
    drin liegt.
    """
    if len(points) < 3:
        return False, "Zu wenige Messpunkte (%d)" % len(points)
    values = [v for _, v in points]
    peak = max(values)
    amplitude = peak - baseline
    if amplitude < min_amplitude:
        return False, ("Amplitude nur %.0f Hz, mindestens %.0f noetig"
                       % (amplitude, min_amplitude))
    peak_idx = values.index(peak)
    if peak_idx == 0 or peak_idx == len(values) - 1:
        return False, ("Scheitel liegt am Rand des Fensters (%.3f) -- "
                       "Bereich verfehlt" % points[peak_idx][0])
    return True, ""
```

- [ ] **Step 4: Test laufen lassen, grün bestätigen (GREEN)**

Beide Dateien in dasselbe Verzeichnis, damit der Fallback aus dem
`sys.path`-Block greift:

```bash
scp tests/check_nozzle_locator_fit.py klippy/extras/nozzle_locator_fit.py \
    biqu@192.168.178.60:/tmp/
sshpass -p biqu ssh biqu@192.168.178.60 'cd /tmp && python3 check_nozzle_locator_fit.py; echo "exit=$?"'
```

Expected: `N Zusicherungen geprueft` / `sauber` / `exit=0`

**Die Zahl der Zusicherungen in der Ausgabe im Report festhalten** — sie ist
der Beleg, dass wirklich alle Testfälle gelaufen sind und nicht ein Teil
still übersprungen wurde.

- [ ] **Step 5: `tests/README.md` ergänzen**

Nach dem Abschnitt zu `check_webapp_recovery.js` einfügen:

```markdown
## `check_nozzle_locator_fit.py`

Prüft die Fit-Mathematik der XY-Offset-Ortung. Braucht **keine
Druckerhardware und kein Klipper** — nur einen Python-Interpreter:

```bash
python3 tests/check_nozzle_locator_fit.py
```

Auf dem Windows-Entwicklungsrechner ist keiner installiert, dort also über
den Pi (der Drucker bewegt sich dabei nicht):

```bash
scp tests/check_nozzle_locator_fit.py klippy/extras/nozzle_locator_fit.py biqu@<IP>:/tmp/
ssh biqu@<IP> 'cd /tmp && python3 check_nozzle_locator_fit.py'
```

Der Anlass ist ein Denkfehler, der beinahe ins Verfahren eingebaut worden
wäre. Der ursprüngliche Schluss lautete: ein Peak-Fit ist gegen additive
Ablagen invariant, also stört der Temperaturgang der Basislinie nicht. Das
gilt aber nur für eine *konstante* Ablage. Der Sweep läuft monoton in x —
ein zeitlinearer Drift wird dadurch zu einem linearen Term in x und
verschiebt den Scheitel um `m/(2a)`, bei den gemessenen Werten des 250ers
rund 19 µm pro Kelvin.

Zwei Eigenschaften machen das gefährlich: die Verschiebung ist ein **Bias,
keine Streuung** (alle Läufe fahren dieselbe Richtung, also sieht σ sie
nicht), und sie kürzt sich **nicht** zwischen den Tools weg (das
Referenztool wird mit kalter Spule gemessen, spätere Tools mit warmer).

| Zusicherung | warum sie zählt |
|---|---|
| Fit ohne Drift trifft das bekannte Zentrum | Grundfunktion |
| Einzelsweep verschiebt sich um exakt `m/(2a)` | nagelt den Fehlerbetrag fest, statt ihn zu behaupten |
| bidirektionaler Mittelwert hebt ihn auf | der eigentliche Fix |
| konstante Ablage verschiebt nichts | die Hälfte der Ursprungsannahme, die stimmt |
| `sweep_quality` lehnt zu schwaches Signal ab | ein Ad-hoc-Skript ohne diesen Guard hat im Vorversuch fünf wertlose Läufe produziert |
| `sweep_quality` lehnt Scheitel am Fensterrand ab | halb erfasstes Ziel liefert einen plausibel aussehenden Unsinnswert |
| Tal statt Berg wird abgewiesen | bei gehärtetem Stahl kann das Vorzeichen drehen |

**Deckt nicht ab:** ob die reale Glocke überhaupt einer Parabel ähnelt. Der
Vorversuch zeigt eine leichte Schiefe, die in X stärker ist als in Y — der
Fit über einen *festen* Punktesatz ist deshalb Pflicht, ein
schwellwertabhängiges Fenster verschob den Scheitel schon um 47 µm.
```

- [ ] **Step 6: Commit**

```bash
git add klippy/extras/nozzle_locator_fit.py tests/check_nozzle_locator_fit.py tests/README.md
git commit -m "feat(xy-offset): Fit-Mathematik mit Nachweis der Drift-Korrektur"
```

---

## Task 2: Sensoranbindung und Rohfrequenz

Deliverable: Sonde anstecken, aktivieren, `NOZZLE_LOCATOR_READ` liefert Hz.

**Files:**
- Create: `klippy/extras/nozzle_locator.py`
- Create: `configs/250/xy_probe.cfg`, `configs/350/xy_probe.cfg` (leer)
- Create: `configs/250/xy_probe.cfg.disabled`, `configs/350/xy_probe.cfg.disabled`
- Modify: `configs/250/printer.cfg`, `configs/350/printer.cfg` (Include-Zeile)
- Modify: `install.sh`
- Modify: `tests/check_klipper_api.py`

**Interfaces:**
- Consumes: nichts aus Task 1 (die Fit-Funktionen kommen erst in Task 3)
- Produces:
  - Config-Sektion `[nozzle_locator]`
  - `NozzleLocator.read_frequency(duration) -> (mean_hz, sd_hz, count, errors)`
  - G-Code `NOZZLE_LOCATOR_READ [DURATION=<s>]`
  - `get_status()` mit `{'present': bool, 'last_freq': float, 'errors': int, 'state': str}`

- [ ] **Step 1: Klipper-API-Test erweitern**

Anders als in Task 1 ist das **kein** TDD-Rot-Test. `check_klipper_api.py`
prüft Klippers Oberfläche, nicht unseren Code — er muss von Anfang an grün
sein. Er ist ein Wächter: bricht ein Klipper-Update später eine der hier
benutzten Internas, bricht der Test statt des Druckers.

In `tests/check_klipper_api.py`, im Importblock von `main()` ergänzen:

```python
    from extras import ldc1612, bulk_sensor  # noqa: F401
```

und nach den bestehenden `probe`-Prüfungen einfügen:

```python
    # --- ldc1612 / bulk_sensor: nozzle_locator.py baut direkt darauf auf ---
    has_attrs(ldc1612, ["LDC1612"], "ldc1612")
    if hasattr(ldc1612, "LDC1612"):
        p = arg_names(ldc1612.LDC1612.__init__)
        ok(p[:2] == ["self", "config"],
           "ldc1612.LDC1612.__init__ Signatur geaendert",
           "erwartet (self, config, calibration=None), ist %s" % p)
        ok("calibration" in p,
           "ldc1612.LDC1612 nimmt kein optionales calibration mehr",
           "nozzle_locator laesst es weg -- es gibt keine Hoehenkarte fuer "
           "einen aufwaerts gerichteten Sensor")
        has_attrs(ldc1612.LDC1612, [
            "add_client", "get_samples_per_second", "convert_raw_to_frequency",
        ], "ldc1612.LDC1612")
    has_attrs(bulk_sensor, ["BatchBulkHelper"], "bulk_sensor")
    if hasattr(bulk_sensor, "BatchBulkHelper"):
        has_attrs(bulk_sensor.BatchBulkHelper, ["add_client"],
                  "bulk_sensor.BatchBulkHelper")
        src = source_of(bulk_sensor.BatchBulkHelper._process_batch)
        ok("client_cbs" in src,
           "bulk_sensor verteilt Batches nicht mehr ueber client_cbs",
           "nozzle_locator meldet sich per add_client an und beendet die "
           "Session, indem der Callback False zurueckgibt")
```

- [ ] **Step 2: Test auf dem Drucker laufen lassen**

```bash
scp tests/check_klipper_api.py biqu@192.168.178.60:/tmp/
ssh biqu@192.168.178.60 '~/klippy-env/bin/python /tmp/check_klipper_api.py'
```

Expected: PASS. Dieser Test prüft Klipper, nicht unseren Code — er muss **sofort** grün sein. Ist er es nicht, hat sich Klippers API geändert und der Plan braucht eine Anpassung, bevor irgendetwas gebaut wird.

- [ ] **Step 3: Modul-Grundgerüst implementieren**

`klippy/extras/nozzle_locator.py`:

```python
# Laterale Ortung einer Duese ueber einer ortsfest im Bett stehenden
# LDC1612-Spule.
#
# Das Modul kennt weder Tools noch Offsets. Es beantwortet ausschliesslich
# "wo ueber mir liegt Metall". Die Tool-Logik sitzt in offset.py.
#
# Bewusst auf Klippers eingebautem ldc1612 statt auf eddy-ng: eine zweite
# probe_eddy_ng-Instanz laesst Klipper nicht starten, weil dessen Kommandos
# global registriert werden. Ausserdem brauchen und koennen wir keine
# Frequenz-Hoehen-Kalibrierung -- ein aufwaerts gerichteter Sensor laesst
# sich nicht gegen ein Bett kalibrieren. Es zaehlt die Rohfrequenz.
#
# Copyright (C) 2026  HappyToolchanger
# This file may be distributed under the terms of the GNU GPLv3 license.
import logging

from . import ldc1612


class NozzleLocator:
    def __init__(self, config):
        self.printer = config.get_printer()
        self.gcode = self.printer.lookup_object('gcode')
        self.name = config.get_name()

        # Kein calibration-Argument: es gibt keine Hoehenkarte.
        self.sensor = ldc1612.LDC1612(config)

        # Suchgeometrie
        self.search_x = config.getfloat('search_x')
        self.search_y = config.getfloat('search_y')
        self.search_span = config.getfloat('search_span', 30.0, above=0.)
        self.safe_z = config.getfloat('safe_z', 15.0, above=0.)
        self.holder_top_z = config.getfloat('holder_top_z', 0.0, minval=0.)
        self.min_gap = config.getfloat('min_gap', 0.5, above=0.)

        # Messparameter
        self.sweep_span = config.getfloat('sweep_span', 8.0, above=0.)
        self.sweep_step = config.getfloat('sweep_step', 1.0, above=0.)
        self.dwell_time = config.getfloat('dwell_time', 0.5, above=0.)
        self.runs = config.getint('runs', 3, minval=1)
        self.runs_tolerance = config.getfloat('runs_tolerance', 0.05, above=0.)
        self.min_amplitude = config.getfloat('min_amplitude', 2000., above=0.)
        self.target_amplitude = config.getfloat('target_amplitude', 6000.,
                                                above=0.)
        self.max_offset = config.getfloat('max_offset', 5.0, above=0.)
        self.move_speed = config.getfloat('move_speed', 60., above=0.)
        self.approach_speed = config.getfloat('approach_speed', 5., above=0.)

        # Laufzeitzustand, ueber get_status sichtbar
        self.state = 'idle'
        self.last_freq = 0.0
        self.last_errors = 0
        self.last_points = []

        self.gcode.register_command(
            'NOZZLE_LOCATOR_READ', self.cmd_READ, desc=self.cmd_READ_help)

    def get_status(self, eventtime):
        return {
            'state': self.state,
            'last_freq': self.last_freq,
            'errors': self.last_errors,
            'points': list(self.last_points),
            'min_amplitude': self.min_amplitude,
            'target_amplitude': self.target_amplitude,
        }

    # ------------------------------------------------------------------
    # Rohfrequenz
    # ------------------------------------------------------------------
    def read_frequency(self, duration=None):
        """Mittelt die Sensorfrequenz ueber duration Sekunden.

        Rueckgabe: (mittelwert_hz, sd_hz, anzahl, fehlerzahl).
        Wirft command_error, wenn gar keine Samples ankommen -- das ist der
        Fall "Sonde steckt nicht" und darf nicht als 0 Hz durchgehen.
        """
        if duration is None:
            duration = self.dwell_time
        collected = []
        errors = [0]

        def handle_batch(msg):
            errors[0] += msg.get('errors', 0)
            # Klippers ldc1612 liefert (print_time, freq, dummy_height).
            for sample in msg['data']:
                collected.append(sample[1])
            return True

        self.sensor.add_client(handle_batch)
        toolhead = self.printer.lookup_object('toolhead')
        toolhead.dwell(duration)
        toolhead.wait_moves()
        # Abmelden: derselbe Callback, der ab jetzt False liefert.
        self.sensor.add_client(lambda msg: False)

        if not collected:
            raise self.printer.command_error(
                "nozzle_locator: keine Sensordaten empfangen -- steckt die "
                "Sonde und ist sie eingeschaltet?")
        n = len(collected)
        mean = sum(collected) / n
        var = sum((v - mean) ** 2 for v in collected) / n
        sd = var ** 0.5
        self.last_freq = mean
        self.last_errors = errors[0]
        return mean, sd, n, errors[0]

    cmd_READ_help = ("Read the raw frequency of the XY nozzle locator coil. "
                     "Parameters: DURATION (seconds, default dwell_time)")

    def cmd_READ(self, gcmd):
        duration = gcmd.get_float('DURATION', self.dwell_time, above=0.)
        mean, sd, n, errors = self.read_frequency(duration)
        gcmd.respond_info(
            "nozzle_locator: %.1f Hz (sd %.1f, %d Samples, %d Fehler)"
            % (mean, sd, n, errors))


def load_config(config):
    return NozzleLocator(config)
```

**Hinweis zum Abmelden:** Klippers `BatchBulkHelper` verteilt Batches an alle
`client_cbs` und verwirft die, die `False` liefern. Ein Callback, der beim
ersten Batch nach dem Messfenster `False` zurückgibt, beendet die Session
damit sauber. Prüfe beim Implementieren im tatsächlichen
`bulk_sensor._process_batch`, ob das Verwerfen dort so umgesetzt ist — falls
nicht, muss `handle_batch` selbst ein Flag setzen und ab dann `False`
liefern, statt einen zweiten Callback anzumelden.

- [ ] **Step 4: Config-Vorlagen anlegen**

`configs/250/xy_probe.cfg` — leere Datei mit einer erklärenden Zeile:

```ini
# XY-Sonde deaktiviert. Aktiviert wird ueber die Offset-Webapp; sie kopiert
# den Inhalt von xy_probe.cfg.disabled hierher. Nicht von Hand befuellen,
# solange die Sonde nicht angesteckt ist -- Klipper startet sonst nicht.
```

`configs/250/xy_probe.cfg.disabled`:

```ini
# Vorlage der XY-Sonde. UUID und Halterungsmasse hier EINMALIG eintragen.
# Diese Datei wird nie von Klipper geladen -- die Webapp kopiert ihren
# Inhalt beim Aktivieren nach xy_probe.cfg.
[mcu xyprobe]
canbus_uuid: HIER_EINTRAGEN

[nozzle_locator]
i2c_mcu: xyprobe
i2c_bus: i2c0f

search_x: 125
search_y: 125
search_span: 30
safe_z: 15
holder_top_z: 8
min_gap: 0.5

sweep_span: 8
sweep_step: 1
dwell_time: 0.5
runs: 3
runs_tolerance: 0.05

min_amplitude: 2000
target_amplitude: 6000
max_offset: 5.0
```

Für `configs/350/` dieselben Dateien mit `search_x: 175`, `search_y: 175`
(Bettmitte des 350ers).

In beiden `printer.cfg` die Include-Zeile ergänzen, bei den übrigen
`[include]`-Zeilen:

```ini
[include xy_probe.cfg]
```

- [ ] **Step 5: install.sh — nur die Configs, nicht die Module**

**Die beiden neuen Python-Module brauchen keine Änderung.** `install.sh:57`
globbt `klippy/extras/*.py` und verlinkt alles, was es findet — sie werden
automatisch mitgenommen.

Die beiden Config-Dateien brauchen dagegen eine Ergänzung, und zwar
**zwingend nach dem `printer.cfg`-Muster (nur kopieren, wenn nicht
vorhanden)**, nicht nach dem Muster der Liste in Zeile 174. Diese Liste
(`HEXA.cfg happy_toolchanger.cfg eddy-ng.cfg`) überschreibt bei **jedem**
Install. Landeten unsere Dateien dort, würde jeder Install

- die im `.disabled` eingetragene CAN-UUID und die Halterungsmaße vernichten
  (der Nutzer trägt sie genau einmal ein), und
- den Aktiv-/Inaktiv-Zustand von `xy_probe.cfg` überschreiben.

In Abschnitt 4c, direkt nach dem `printer.cfg`-Block, einfügen:

```bash
    # xy_probe.cfg / .disabled: nur initial anlegen.
    # Die .disabled traegt UUID und Halterungsmasse des Nutzers -- die
    # darf ein Update NIE ueberschreiben. Die aktive Datei traegt den
    # Ein/Aus-Zustand, den die Webapp verwaltet.
    for xy_file in xy_probe.cfg xy_probe.cfg.disabled; do
      if [ -f "${CONFIG_SRC}/${xy_file}" ] && [ ! -f "${CONFIG_DST}/${xy_file}" ]; then
        cp "${CONFIG_SRC}/${xy_file}" "${CONFIG_DST}/${xy_file}"
        echo "  Copied ${xy_file} (initial)"
      elif [ -f "${CONFIG_DST}/${xy_file}" ]; then
        echo "  Skipped ${xy_file} (exists, contains user settings)"
      fi
    done
```

**Achtung auf die dokumentierte Symlink-Falle:** `cat > symlink` schreibt
durch den Symlink und überschreibt die Quelldatei — nur verlinken, nie in
die Zieldatei schreiben.

- [ ] **Step 6: Auf dem Drucker deployen und lesen**

```bash
ssh biqu@192.168.178.60 'cd ~/HappyToolchanger && git stash && git pull && ./install.sh'
ssh biqu@192.168.178.60 'sudo systemctl restart klipper'
```

Dann Sonde anstecken, `xy_probe.cfg` von Hand mit dem Inhalt der
`.disabled` befüllen, `FIRMWARE_RESTART`, und in der Konsole:

```
NOZZLE_LOCATOR_READ DURATION=1.0
```

Expected: eine Frequenz um 3.1 MHz mit sd im Bereich 40–60 Hz und 0 Fehlern.
Danach die Hand über die Spule halten — die Frequenz muss messbar steigen.

**Vor jeder Drucker-Aktion prüfen, dass nicht gedruckt wird:**

```bash
curl -s http://192.168.178.60:7125/printer/objects/query?print_stats
```

- [ ] **Step 7: Commit**

```bash
git add klippy/extras/nozzle_locator.py configs/250/xy_probe.cfg* \
        configs/350/xy_probe.cfg* configs/250/printer.cfg \
        configs/350/printer.cfg install.sh tests/check_klipper_api.py
git commit -m "feat(xy-offset): nozzle_locator liest die Rohfrequenz der XY-Sonde"
```

---

## Task 3: Z-Anfahrt, Sweep und Ortung

Deliverable: `NOZZLE_LOCATE AXIS=X` findet eine Düse und meldet ihre Position.

**Files:**
- Modify: `klippy/extras/nozzle_locator.py`

**Interfaces:**
- Consumes: `nozzle_locator_fit.parabola_vertex`, `.bidirectional_center`, `.sweep_quality` (Task 1); `NozzleLocator.read_frequency` (Task 2)
- Produces:
  - `NozzleLocator.measure_baseline() -> float`
  - `NozzleLocator.approach_z(baseline, target_amplitude) -> float` (erreichtes Z)
  - `NozzleLocator.sweep(axis, center, span, step, descending) -> [(pos, freq), ...]`
  - `NozzleLocator.locate(axis, center, baseline) -> dict` mit `{'center', 'fwd', 'rev', 'spread', 'runs'}`
  - G-Code `NOZZLE_LOCATE AXIS=X|Y [REPEATS=n] [SPAN=mm] [STEP=mm]`

- [ ] **Step 1: Implementierung schreiben**

In `nozzle_locator.py` den Import ergänzen und die Methoden anfügen:

```python
from . import nozzle_locator_fit as fit
```

```python
    # ------------------------------------------------------------------
    # Bewegung
    # ------------------------------------------------------------------
    def _require_homed(self):
        toolhead = self.printer.lookup_object('toolhead')
        status = toolhead.get_status(
            self.printer.get_reactor().monotonic())
        if 'xyz' != ''.join(sorted(set(status['homed_axes']))):
            raise self.printer.command_error(
                "nozzle_locator: Achsen sind nicht gehomt. Erst homen, DANN "
                "die Halterung aufs Bett stellen -- ein Home mit Aufbau auf "
                "dem Bett ist ein Kollisionsrisiko.")

    def _move(self, coord, speed):
        """Absolute Bewegung; coord ist [x, y, z] mit None fuer 'unveraendert'."""
        toolhead = self.printer.lookup_object('toolhead')
        pos = toolhead.get_position()
        target = list(pos)
        for i in range(3):
            if coord[i] is not None:
                target[i] = coord[i]
        toolhead.manual_move(target[:3], speed)
        toolhead.wait_moves()

    def _z_floor(self):
        return self.holder_top_z + self.min_gap

    # ------------------------------------------------------------------
    # Messschritte
    # ------------------------------------------------------------------
    def measure_baseline(self):
        """Freiluft-Basislinie: Kopf steht weit weg von der Spule.

        Der Aufrufer ist dafuer verantwortlich, den Kopf vorher wegzufahren.
        Eine Basislinie mit der Duese in Reichweite ist der Fehler, der im
        Vorversuch schon einmal 12 kHz Versatz erzeugt hat.
        """
        self.state = 'baseline'
        mean, sd, n, errors = self.read_frequency(self.dwell_time * 2.0)
        if errors:
            raise self.printer.command_error(
                "nozzle_locator: %d Sensorfehler waehrend der Basislinie"
                % errors)
        return mean

    def approach_z(self, baseline, target_amplitude=None):
        """Senkt Z stufenweise, bis das Signal die Zielamplitude erreicht.

        Faehrt nie unter holder_top_z + min_gap. Rueckgabe: erreichtes Z.
        """
        if target_amplitude is None:
            target_amplitude = self.target_amplitude
        self.state = 'approaching'
        toolhead = self.printer.lookup_object('toolhead')
        floor = self._z_floor()
        z = toolhead.get_position()[2]
        step = 1.0
        while z > floor:
            z = max(floor, z - step)
            self._move([None, None, z], self.approach_speed)
            mean, sd, n, errors = self.read_frequency()
            if mean - baseline >= target_amplitude:
                return z
            # Naeher am Ziel feiner tasten
            if mean - baseline >= target_amplitude * 0.5:
                step = 0.25
        raise self.printer.command_error(
            "nozzle_locator: Zielamplitude bei Z=%.3f nicht erreicht "
            "(Signal %.0f Hz, noetig %.0f). Steht die Halterung an der "
            "erwarteten Stelle, und stimmt holder_top_z?"
            % (floor, self.last_freq - baseline, target_amplitude))

    def sweep(self, axis, center, span, step, descending=False):
        """Ein gerichteter Sweep. Rueckgabe: [(position, frequenz), ...].

        Wird immer aus derselben Richtung ANGEFAHREN (Vorlauf ausserhalb des
        Fensters), damit das Spiel der Achse nicht in die Messung geht.
        """
        self.state = 'sweeping'
        half = span / 2.0
        n_steps = int(round(span / step)) + 1
        positions = [center - half + i * step for i in range(n_steps)]
        if descending:
            positions = list(reversed(positions))
        # Vorlauf: 3 mm vor den ersten Punkt, gleiche Richtung
        lead = positions[0] - (step * 3.0 if not descending else -step * 3.0)
        idx = 0 if axis == 'X' else 1
        coord = [None, None, None]
        coord[idx] = lead
        self._move(coord, self.move_speed)

        points = []
        self.last_points = []
        for p in positions:
            coord = [None, None, None]
            coord[idx] = p
            self._move(coord, self.move_speed)
            mean, sd, n, errors = self.read_frequency()
            points.append((p, mean))
            self.last_points.append((round(p, 4), round(mean, 1)))
        return points

    def locate(self, axis, center, baseline, runs=None, span=None, step=None):
        """Bidirektionale Ortung. Rueckgabe: dict mit center/fwd/rev/spread.

        Jeder Lauf besteht aus Hin- UND Ruecksweep. Ein einzelner gerichteter
        Sweep ist nie ein Ergebnis: ein zeitlinearer Drift verschoebe seinen
        Scheitel um m/(2a), und weil alle Laeufe dieselbe Richtung haetten,
        wuerde die Streuung diesen Fehler nicht zeigen.
        """
        runs = self.runs if runs is None else runs
        span = self.sweep_span if span is None else span
        step = self.sweep_step if step is None else step

        centers, fwds, revs = [], [], []
        for _ in range(runs):
            fwd = self.sweep(axis, center, span, step, descending=False)
            good, reason = fit.sweep_quality(fwd, baseline, self.min_amplitude)
            if not good:
                raise self.printer.command_error(
                    "nozzle_locator %s-Hinsweep: %s" % (axis, reason))
            rev = self.sweep(axis, center, span, step, descending=True)
            good, reason = fit.sweep_quality(rev, baseline, self.min_amplitude)
            if not good:
                raise self.printer.command_error(
                    "nozzle_locator %s-Ruecksweep: %s" % (axis, reason))
            centers.append(fit.bidirectional_center(fwd, rev))
            fwds.append(fit.parabola_vertex(fwd))
            revs.append(fit.parabola_vertex(rev))

        spread = max(centers) - min(centers)
        if spread > self.runs_tolerance:
            raise self.printer.command_error(
                "nozzle_locator %s: Messung instabil, Spannweite %.1f um "
                "ueber %d Laeufe (erlaubt %.1f um). Einzelwerte: %s"
                % (axis, spread * 1000., runs, self.runs_tolerance * 1000.,
                   ", ".join("%.4f" % c for c in centers)))
        self.state = 'idle'
        return {
            'center': sum(centers) / len(centers),
            'runs': centers,
            'fwd': sum(fwds) / len(fwds),
            'rev': sum(revs) / len(revs),
            'spread': spread,
        }

    cmd_LOCATE_help = (
        "Locate a nozzle laterally over the XY probe coil. Parameters: "
        "AXIS (X or Y), REPEATS (runs, default from config), SPAN, STEP. "
        "Requires homed axes and the coil holder in place.")

    def cmd_LOCATE(self, gcmd):
        axis = gcmd.get('AXIS', 'X').upper()
        if axis not in ('X', 'Y'):
            raise gcmd.error("AXIS muss X oder Y sein")
        runs = gcmd.get_int('REPEATS', self.runs, minval=1)
        span = gcmd.get_float('SPAN', self.sweep_span, above=0.)
        step = gcmd.get_float('STEP', self.sweep_step, above=0.)
        self._require_homed()

        toolhead = self.printer.lookup_object('toolhead')
        center = toolhead.get_position()[0 if axis == 'X' else 1]
        # Basislinie: der Aufrufer steht schon ueber der Spule, also erst
        # wegfahren, messen, zurueck.
        here = toolhead.get_position()
        self._move([None, None, self.safe_z], self.move_speed)
        away = [None, None, None]
        away[0 if axis == 'X' else 1] = center + self.search_span
        self._move(away, self.move_speed)
        baseline = self.measure_baseline()
        self._move([here[0], here[1], None], self.move_speed)
        self._move([None, None, here[2]], self.approach_speed)

        result = self.locate(axis, center, baseline, runs=runs,
                             span=span, step=step)
        gcmd.respond_info(
            "nozzle_locator %s: %.4f mm (hin %.4f, rueck %.4f, "
            "Differenz %.1f um = gemessener Drift-Bias; Spannweite ueber "
            "%d Laeufe %.1f um)"
            % (axis, result['center'], result['fwd'], result['rev'],
               (result['fwd'] - result['rev']) * 1000., runs,
               result['spread'] * 1000.))
```

Und die Registrierung im Konstruktor ergänzen:

```python
        self.gcode.register_command(
            'NOZZLE_LOCATE', self.cmd_LOCATE, desc=self.cmd_LOCATE_help)
```

- [ ] **Step 2: Deployen und mit einem Tool von Hand prüfen**

```bash
ssh biqu@192.168.178.60 'cd ~/HappyToolchanger && git stash && git pull && ./install.sh'
ssh biqu@192.168.178.60 'sudo systemctl restart klipper'
```

In der Konsole, mit gehomten Achsen und aufgesetzter Halterung:

```
SET_IDLE_TIMEOUT TIMEOUT=3600
G1 X125 Y125 Z15 F3000
NOZZLE_LOCATE AXIS=X
NOZZLE_LOCATE AXIS=Y
SET_IDLE_TIMEOUT TIMEOUT=600
```

Expected: beide melden eine Position, die Differenz zwischen Hin- und
Rückwert liegt unter ~30 µm, die Spannweite über die Läufe unter
`runs_tolerance`.

- [ ] **Step 3: Wiederholbarkeit mit n=20 messen**

Das ist die Messung, die im Vorversuch fehlte (σ aus n=5 hat ein
Konfidenzintervall von 5,9–28,4 µm und entscheidet gar nichts):

```
NOZZLE_LOCATE AXIS=X REPEATS=20
```

Expected: die Meldung nennt die Spannweite über 20 Läufe. **Ergebnis im
Commit-Text festhalten** — es ist die erste belastbare Zahl zur
Wiederholbarkeit des Verfahrens.

- [ ] **Step 4: Commit**

```bash
git add klippy/extras/nozzle_locator.py
git commit -m "feat(xy-offset): bidirektionale Ortung mit Z-Anfahrt und Guards"
```

---

## Task 4: GESTRICHEN — die Extraktion war ein Denkfehler

**Diese Task wird nicht ausgeführt.** Ein Implementer hat sie mit BLOCKED
zurückgegeben, nachdem er beide Aufrufer gelesen hatte, und der Befund ist
stichhaltig.

Die Annahme des Plans war: `cmd_CALIBRATE_ALL_Z_OFFSETS` und
`cmd_CALIBRATE_PROBE_OFFSETS` lösen dieselbe Aufgabe doppelt. Das stimmt
nicht. Sie haben **drei verschiedene, jeweils absichtliche Auswahlpolitiken**,
die nur von weitem gleich aussehen:

| | `CALIBRATE_ALL_Z_OFFSETS` (`offset.py:474-514`) | `CALIBRATE_PROBE_OFFSETS` (`offset.py:696-916`) |
|---|---|---|
| unbekanntes Tool in `TOOLS` | wird still verworfen | harter Abbruch |
| Abbruchschwelle | nur wenn danach nichts übrig ist | schon bei einem einzigen |
| Duplikate | werden dedupliziert | — |
| Default ohne `TOOLS` | alle Tools | **nur Tools, die schon Z-Switch-Daten haben** |
| Referenztool | erzwungen enthalten, immer zuerst | **weder enthalten noch zuerst** — bewusst unabhängig (`offset.py:904-908` behandelt den Fall „Ref ist mitgemessen" explizit als Sonderfall mit Ergebnis ≈ 0) |

Der zweite Aufrufer wäre nicht in einem Randfall gebrochen, sondern im
**Normalfall**: sein Default hätte statt der Tools mit vorhandenen
Z-Switch-Daten plötzlich alle Tools gewählt, das Referenztool zwangsweise
mitgemessen und für alles ohne Daten mit „Missing Z-switch data" abgebrochen.
Auf der Hardware wären das echte zusätzliche Werkzeugwechsel und Antastungen.

Übrig bliebe als tatsächlich gemeinsamer Code ein Dreizeiler
(`if ref_tool not in available_tools: ref_tool = available_tools[0]`). Ein
Helfer mit genügend Schaltern, um alle drei Politiken abzubilden, wäre
schwerer zu lesen als die drei getrennten Stellen.

**Ruling:** Die Extraktion entfällt ersatzlos. Task 5 löst seine Tool-Auswahl
selbst, mit einer Politik, die zur XY-Messung passt (siehe dort). Die
Duplizierung, die dieser Plan vermeiden wollte, existierte nie — sie war eine
Formähnlichkeit, keine Logikgleichheit.

Der Befund ist trotzdem wertvoll und bleibt hier stehen: er dokumentiert die
Auswahlsemantik beider bestehender Kalibrierungen, die vorher nirgends
festgehalten war.

## Task 5: `CALIBRATE_XY_OFFSETS` — Orchestrierung

**Files:**
- Modify: `klippy/extras/offset.py`

**Interfaces:**
- Consumes: `nozzle_locator.locate/approach_z/measure_baseline` (Task 3)
- Produces zusätzlich: `Offset._xy_tool_run(gcmd) -> (ref_tool, ordered_tools)`

**Zur Tool-Auswahl:** Der Plan wollte diese Logik ursprünglich aus den beiden
bestehenden Kalibrierungen extrahieren. Das war ein Denkfehler (siehe Task 4)
— die drei Kommandos haben absichtlich verschiedene Auswahlpolitiken. Dieses
Kommando bekommt deshalb seine **eigene**, bewusst benannte Auflösung. Der
Name `_xy_tool_run` sagt, dass sie nur für diesen Zweck gilt; sie ist nicht
zum Teilen gedacht.

Die Politik für die XY-Messung:

- **Referenztool ist zwingend enthalten und steht zuerst.** Anders als bei den
  Probe-Offsets ist es hier keine optionale Vergleichsgröße: es legt die
  Messhöhe und das Grobsuchfenster für alle folgenden Tools fest. Ohne es gibt
  es keine Differenz, gegen die gerechnet werden kann.
- **Unbekannte Tools in `TOOLS` brechen ab**, statt still verworfen zu werden.
  Ein Tippfehler soll auffallen, bevor der Kopf über eine Halterung fährt.
- **Default ohne `TOOLS`:** alle konfigurierten Tools.
- Duplikate werden entfernt.

```python
    def _xy_tool_run(self, gcmd):
        """Referenztool und Reihenfolge fuer einen XY-Messlauf.

        Bewusst NICHT geteilt mit CALIBRATE_ALL_Z_OFFSETS oder
        CALIBRATE_PROBE_OFFSETS: die drei Kommandos haben absichtlich
        verschiedene Auswahlpolitiken. Siehe Task 4 im Plan.
        """
        available = sorted(self.toolchanger.tool_numbers)
        if not available:
            raise gcmd.error("Keine Tools konfiguriert")
        ref_tool = gcmd.get_int('REF_TOOL', self.default_ref_tool, minval=0)
        if ref_tool not in available:
            ref_tool = available[0]
        tools_param = gcmd.get('TOOLS', None)
        if tools_param:
            requested = []
            for token in tools_param.split(','):
                token = token.strip()
                if not token.isdigit():
                    raise gcmd.error(
                        "TOOLS erwartet Tool-Nummern, bekam '%s'" % token)
                requested.append(int(token))
            unknown = [t for t in requested if t not in available]
            if unknown:
                raise gcmd.error(
                    "Unbekannte Tools: %s"
                    % ", ".join("T%d" % t for t in unknown))
        else:
            requested = list(available)
        ordered, seen = [ref_tool], {ref_tool}
        for t in requested:
            if t not in seen:
                seen.add(t)
                ordered.append(t)
        self.last_ref_tool = ref_tool
        return ref_tool, ordered
- Produces:
  - G-Code `CALIBRATE_XY_OFFSETS [REF_TOOL=n] [TOOLS=0,1,2] [DRY_RUN=1] [TEMP=n]`
  - State-Datei `.offset_xy_results.json`
  - `get_status()['xy_results']` = `{'0': {'x': .., 'y': .., 'z_compare': .., 'x_fwd': .., 'x_rev': .., 'y_fwd': .., 'y_rev': .., 'spread_x': .., 'spread_y': ..}, ...}` plus `{'ref_tool': n, 'timestamp': ..}`

- [ ] **Step 1: Kommando implementieren**

```python
    cmd_CALIBRATE_XY_OFFSETS_help = (
        "Measure X/Y tool offsets with the XY probe coil. Parameters: "
        "REF_TOOL, TOOLS (subset), DRY_RUN (1 = travel only, never descend), "
        "TEMP (nozzle temperature, 0 = cold). Requires homed axes and the "
        "coil holder placed on the bed.")

    def cmd_CALIBRATE_XY_OFFSETS(self, gcmd):
        locator = self.printer.lookup_object('nozzle_locator', None)
        if locator is None:
            raise gcmd.error(
                "Keine XY-Sonde konfiguriert. In der Offset-Webapp ueber den "
                "Assistenten aktivieren -- xy_probe.cfg ist derzeit leer.")
        dry_run = gcmd.get_int('DRY_RUN', 0)
        temp = gcmd.get_float('TEMP', 0., minval=0.)
        ref_tool, ordered_tools = self._xy_tool_run(gcmd)
        self._require_leveled(gcmd)
        locator._require_homed()

        prev_timeout = self._current_idle_timeout()
        self.gcode.run_script_from_command("SET_IDLE_TIMEOUT TIMEOUT=3600")
        results = {}
        try:
            results = self._run_xy_calibration(
                gcmd, locator, ref_tool, ordered_tools, dry_run, temp)
        finally:
            self.gcode.run_script_from_command(
                "SET_IDLE_TIMEOUT TIMEOUT=%d" % prev_timeout)
            self._return_to_ref_tool(ref_tool, gcmd)

        if dry_run:
            gcmd.respond_info(
                "XY-Trockenlauf beendet: alle Wege abgefahren, nie abgesenkt. "
                "Wenn dabei nichts kollidiert ist, ist der Messlauf sicher.")
            return
        self.xy_results = results
        self._save_xy_results()
        self._report_xy_results(gcmd, results, ref_tool)

    def _run_xy_calibration(self, gcmd, locator, ref_tool, ordered_tools,
                            dry_run, temp):
        results = {}
        ref_pos = None
        measure_z = None
        baseline = None

        for tool_nr in ordered_tools:
            self.gcode.run_script_from_command(
                "SELECT_TOOL T=%d RESTORE_AXIS=XYZ" % tool_nr)
            if temp > 0:
                self.gcode.run_script_from_command(
                    "M109 S%.0f" % temp)
            # Immer erst auf sichere Hoehe, dann ueber die Spule.
            locator._move([None, None, locator.safe_z], locator.move_speed)
            locator._move([locator.search_x, locator.search_y, None],
                          locator.move_speed)
            if dry_run:
                gcmd.respond_info("T%d: Trockenlauf, Position erreicht"
                                  % tool_nr)
                continue

            if baseline is None:
                # Einmal pro Lauf, mit dem Referenztool und weit weg.
                locator._move([locator.search_x + locator.search_span,
                               locator.search_y, None], locator.move_speed)
                baseline = locator.measure_baseline()
                locator._move([locator.search_x, locator.search_y, None],
                              locator.move_speed)

            z_reached = locator.approach_z(baseline)
            if tool_nr == ref_tool:
                measure_z = z_reached
                cx, cy = locator.search_x, locator.search_y
                # Grobsuche nur einmal: sie legt das Fenster fuer alle fest.
                coarse_x = locator.locate(
                    'X', cx, baseline, runs=1,
                    span=locator.search_span, step=locator.sweep_step * 2.)
                cx = coarse_x['center']
                locator._move([cx, None, None], locator.move_speed)
                coarse_y = locator.locate(
                    'Y', cy, baseline, runs=1,
                    span=locator.search_span, step=locator.sweep_step * 2.)
                cy = coarse_y['center']
                locator._move([None, cy, None], locator.move_speed)
                self._xy_window = (cx, cy)
            cx, cy = self._xy_window
            locator._move([cx, cy, None], locator.move_speed)

            rx = locator.locate('X', cx, baseline)
            locator._move([rx['center'], None, None], locator.move_speed)
            ry = locator.locate('Y', cy, baseline)

            entry = {
                'x_peak': rx['center'], 'y_peak': ry['center'],
                'x_fwd': rx['fwd'], 'x_rev': rx['rev'],
                'y_fwd': ry['fwd'], 'y_rev': ry['rev'],
                'spread_x': rx['spread'], 'spread_y': ry['spread'],
                'z_reached': z_reached,
            }
            if tool_nr == ref_tool:
                ref_pos = (rx['center'], ry['center'], z_reached)
            results[str(tool_nr)] = entry
            locator._move([None, None, locator.safe_z], locator.move_speed)

        if dry_run or ref_pos is None:
            return results
        # Differenzen bilden -- hier kuerzt sich die Spulenposition weg.
        for key, entry in results.items():
            entry['x'] = entry['x_peak'] - ref_pos[0]
            entry['y'] = entry['y_peak'] - ref_pos[1]
            entry['z_compare'] = entry['z_reached'] - ref_pos[2]
            if (abs(entry['x']) > locator.max_offset
                    or abs(entry['y']) > locator.max_offset):
                raise gcmd.error(
                    "T%s: Offset %.3f/%.3f ueberschreitet max_offset %.1f mm "
                    "-- vermutlich wurde ein falscher Scheitel gefittet."
                    % (key, entry['x'], entry['y'], locator.max_offset))
        results['ref_tool'] = ref_tool
        return results
```

Dazu die Hilfsmethoden nach dem Muster von `_save_probe_results` /
`_load_probe_results`:

```python
    def _save_xy_results(self):
        self._save_json(self._get_state_file_path('.offset_xy_results.json'),
                        self.xy_results)

    def _load_xy_results(self):
        self.xy_results = self._load_json(
            self._get_state_file_path('.offset_xy_results.json'), {})

    def _current_idle_timeout(self):
        it = self.printer.lookup_object('idle_timeout', None)
        if it is None:
            return 600
        return int(getattr(it, 'idle_timeout', 600))

    def _report_xy_results(self, gcmd, results, ref_tool):
        gcmd.respond_info("XY-Offsets gegen T%d:" % ref_tool)
        for key in sorted(k for k in results if k.isdigit()):
            e = results[key]
            gcmd.respond_info(
                "  T%s  X=%+.4f  Y=%+.4f  (Z-Vergleich %+.3f, "
                "Drift-Bias X %+.1f um / Y %+.1f um)"
                % (key, e['x'], e['y'], e['z_compare'],
                   (e['x_fwd'] - e['x_rev']) * 1000.,
                   (e['y_fwd'] - e['y_rev']) * 1000.))
        gcmd.respond_info(
            "Uebernehmen mit APPLY XY OFFSETS in der Webapp -- nicht mit "
            "SAVE_CONFIG, das kann die included T<n>.cfg nicht schreiben.")
```

**Falls `_save_json`/`_load_json` in `offset.py` nicht als eigene Helfer
existieren:** die vorhandenen `_save_probe_results`/`_load_probe_results`
lesen und ihr Muster (json-Dump, Fehlerbehandlung) 1:1 übernehmen statt
eine neue Abstraktion zu erfinden.

- [ ] **Step 2: Registrierung und `get_status` ergänzen**

Im Konstruktor bei den anderen `register_command`-Zeilen:

```python
        self.gcode.register_command(
            'CALIBRATE_XY_OFFSETS', self.cmd_CALIBRATE_XY_OFFSETS,
            desc=self.cmd_CALIBRATE_XY_OFFSETS_help)
```

In `handle_connect` das Laden ergänzen (`self._load_xy_results()`), und in
`get_status` das Dict aufnehmen:

```python
            'xy_results': self.xy_results,
```

- [ ] **Step 3: Trockenlauf auf dem Drucker**

```bash
ssh biqu@192.168.178.60 'sudo systemctl restart klipper'
```

Halterung aufsetzen (**nach** dem Homen!), dann:

```
CALIBRATE_XY_OFFSETS DRY_RUN=1
```

Expected: jedes Tool wird gewechselt, jedes fährt über die Nominalposition,
nichts senkt sich ab, nichts kollidiert.

- [ ] **Step 4: Echter Lauf**

```
CALIBRATE_XY_OFFSETS
```

Expected: eine Tabelle mit X/Y je Tool, Z-Vergleich und dem Drift-Bias je
Achse. Der Drift-Bias sollte klein sein (< 30 µm); ist er groß, ist das die
Bestätigung, dass der bidirektionale Sweep gebraucht wird.

- [ ] **Step 5: Commit**

```bash
git add klippy/extras/offset.py
git commit -m "feat(offset): CALIBRATE_XY_OFFSETS misst Tool-Offsets per XY-Sonde"
```

---

## Task 6: Extraktion `updateConfigFile()` (reiner Refactor)

**Files:**
- Modify: `webapp/js/tools.js:474`, `:499`, `:627`, `:637`, `:1784`, `:1803`, `:2640`, `:2656`
- Modify: `tests/check_webapp_recovery.js`

**Interfaces:**
- Produces: `updateConfigFile(filePath, mutator) -> Promise` — liest die Datei ungecacht, ruft `mutator(inhalt)` auf, lädt das Ergebnis wieder hoch. Gibt `null` als Mutator-Rückgabe die Bedeutung „nichts zu ändern, nicht hochladen".

- [ ] **Step 1: Helfer schreiben**

```javascript
// Liest eine Config-Datei, laesst sie vom mutator veraendern und laedt sie
// wieder hoch. Ungecacht lesen ist Pflicht -- ein gecachter Lesevorgang hat
// hier schon einmal Uebernahmen verschluckt (e489c494).
// Liefert der mutator null, wird nicht hochgeladen.
function updateConfigFile(filePath, mutator) {
  return fetch(baseUrl + "/server/files/config/" + filePath, NO_CACHE)
    .then(function (r) {
      if (!r.ok) throw new Error("Config nicht lesbar: " + filePath);
      return r.text();
    })
    .then(function (content) {
      var updated = mutator(content);
      if (updated === null || updated === undefined) return null;
      var formData = new FormData();
      formData.append("file", new Blob([updated], { type: "text/plain" }),
                      filePath.split("/").pop());
      formData.append("root", "config");
      var dir = filePath.split("/").slice(0, -1).join("/");
      if (dir) formData.append("path", dir);
      return fetch(baseUrl + "/server/files/upload",
                   { method: 'POST', body: formData })
        .then(function (r) {
          if (!r.ok) throw new Error("Config nicht schreibbar: " + filePath);
          return r;
        });
    });
}
```

**Wichtig:** Die genauen `FormData`-Felder (`root`, `path`, Dateiname) aus
einem der vier bestehenden Aufrufer übernehmen, nicht aus diesem Entwurf
raten — sie müssen zu dem passen, was Moonraker hier tatsächlich erwartet.

- [ ] **Step 2: Alle vier Aufrufer umstellen**

Einer nach dem anderen, nach jedem `node tests/check_webapp_recovery.js`
laufen lassen. Die Umformung wandert **unverändert** in den Mutator — dies
ist ein reiner Refactor, es darf sich kein Verhalten ändern.

Beispiel für den Block bei `:474`/`:499` (die konkreten Variablennamen und
die Umformung aus der jeweiligen Fundstelle übernehmen):

```javascript
// vorher
return fetch(baseUrl + "/server/files/config/" + filePath, NO_CACHE)
  .then(function (r) { return r.text(); })
  .then(function (content) {
    content = replaceInConfigSection(content, section, key, value);
    var formData = new FormData();
    formData.append("file", new Blob([content], {type: "text/plain"}), name);
    formData.append("root", "config");
    return fetch(baseUrl + "/server/files/upload",
                 {method: 'POST', body: formData});
  });

// nachher
return updateConfigFile(filePath, function (content) {
  return replaceInConfigSection(content, section, key, value);
});
```

Unterscheidet sich einer der vier Blöcke in einem Detail (anderer
`root`, zusätzlicher `path`, abweichende Fehlerbehandlung), dann **nicht**
einebnen — stattdessen `updateConfigFile` um einen optionalen
Options-Parameter erweitern und das Detail dort abbilden. Ein Refactor, der
nebenbei Verhalten ändert, ist kein Refactor.

- [ ] **Step 3: Test laufen lassen**

Run: `node tests/check_webapp_recovery.js`
Expected: unverändert grün — dies ist ein reiner Refactor.

- [ ] **Step 4: Im Browser gegenprüfen**

Offset-UI öffnen, eine bestehende Übernahme (z.B. PID oder Dock) ausführen
und prüfen, dass der Wert wirklich in der Config landet. Expected: Wert
steht in der Datei.

- [ ] **Step 5: Commit**

```bash
git add webapp/js/tools.js tests/check_webapp_recovery.js
git commit -m "refactor(webapp): Config-Lesen und -Schreiben in updateConfigFile buendeln"
```

---

## Task 7: XY-Block in der Webapp

**Files:**
- Modify: `webapp/index.html`
- Modify: `webapp/js/tools.js`

**Interfaces:**
- Consumes: `updateConfigFile` (Task 6), `get_status()['xy_results']` (Task 5)
- Produces: `_xyResults`, `renderXyBlock()`, `applyXyOffset(toolNr, alsoWrite)`

- [ ] **Step 1: Zustandsvariablen und Polling ergänzen**

Bei den bestehenden Deklarationen (`tools.js:21-34`):

```javascript
let _xyResults = {};        // { "0": {x, y, z_compare, x_fwd, x_rev, ...} }
let _xyMethod = "eddy";     // "eddy" | "camera"
let _xyProbeActive = null;  // null = unbekannt, true/false = Config-Zustand
let _cameraPositions = {};  // { "0": {x, y} } aus "Position uebernehmen"
```

Dazu die Leseseite der Kameramethode. Sie gehört hierher und nicht in
Task 9, weil `renderXyBlock()` sie in Step 3 bereits aufruft — Task 9 fügt
nur das Erfassen und den Knopf hinzu. Solange niemand eine Position
festgehalten hat, liefert sie `null`, und die Tabelle zeigt „nicht
gemessen":

```javascript
// Offset der Kameramethode = Differenz zum Referenztool, genau wie beim
// Eddy-Verfahren. Nur so sind beide Verfahren vergleichbar.
function _cameraOffsetFor(toolNr) {
  var ref = _xyResults.ref_tool;
  if (ref === undefined) ref = 0;
  var here = _cameraPositions[String(toolNr)];
  var base = _cameraPositions[String(ref)];
  if (!here || !base) return null;
  return {x: here.x - base.x, y: here.y - base.y};
}
```

`xy_results` in die bestehende Status-Abfrage aufnehmen, dort wo
`dock_results` und `probe_results` schon geholt werden.

- [ ] **Step 2: Markup**

In `webapp/index.html`, als eigener Block bei den anderen Offset-Bereichen:

```html
<div class="card mb-3" id="xy-offset-card">
  <div class="card-header d-flex align-items-center justify-content-between">
    <span>XY-Offsets</span>
    <div class="d-flex align-items-center gap-2">
      <div class="btn-group btn-group-sm" role="group" id="xy-method">
        <input type="radio" class="btn-check" name="xy-method"
               id="xy-method-camera" value="camera">
        <label class="btn btn-outline-secondary" for="xy-method-camera">
          Kamera (manuell)</label>
        <input type="radio" class="btn-check" name="xy-method"
               id="xy-method-eddy" value="eddy" checked>
        <label class="btn btn-outline-secondary" for="xy-method-eddy">
          Eddy-Sweep</label>
      </div>
      <button class="btn btn-sm btn-primary" id="xy-wizard-btn">
        Assistent…</button>
    </div>
  </div>
  <div class="card-body" id="xy-offset-body"></div>
</div>
```

- [ ] **Step 3: Rendering**

```javascript
function renderXyBlock() {
  var body = document.getElementById('xy-offset-body');
  if (!body) return;
  var ref = _xyResults.ref_tool;
  var rows = Object.keys(_toolGcodeOffsets).sort(function (a, b) {
    return parseInt(a) - parseInt(b);
  }).map(function (t) {
    var cur = _toolGcodeOffsets[t] || {x: 0, y: 0};
    var res = (_xyMethod === 'eddy') ? _xyResults[t] : _cameraOffsetFor(t);
    var isRef = (String(ref) === String(t));
    if (isRef) {
      return '<tr><td>T' + t + '</td>' +
             '<td>' + cur.x.toFixed(3) + ' / ' + cur.y.toFixed(3) + '</td>' +
             '<td colspan="4" class="text-muted">Referenztool</td></tr>';
    }
    if (!res) {
      return '<tr><td>T' + t + '</td>' +
             '<td>' + cur.x.toFixed(3) + ' / ' + cur.y.toFixed(3) + '</td>' +
             '<td colspan="4" class="text-muted">nicht gemessen</td></tr>';
    }
    var dx = (res.x - cur.x) * 1000, dy = (res.y - cur.y) * 1000;
    var bias = (res.x_fwd !== undefined)
      ? ('<span title="Differenz Hin- gegen Ruecksweep = gemessener ' +
         'Drift-Bias">' + ((res.x_fwd - res.x_rev) * 1000).toFixed(1) +
         ' / ' + ((res.y_fwd - res.y_rev) * 1000).toFixed(1) + ' µm</span>')
      : '—';
    return '<tr><td>T' + t + '</td>' +
      '<td>' + cur.x.toFixed(3) + ' / ' + cur.y.toFixed(3) + '</td>' +
      '<td>' + res.x.toFixed(3) + ' / ' + res.y.toFixed(3) + '</td>' +
      '<td>' + dx.toFixed(0) + ' / ' + dy.toFixed(0) + ' µm</td>' +
      '<td>' + (res.z_compare !== undefined
                ? (res.z_compare * 1000).toFixed(0) + ' µm' : '—') + '</td>' +
      '<td>' + bias + '</td>' +
      '<td><button class="btn btn-sm btn-outline-primary" ' +
      'onclick="applyXyOffset(' + t + ', false)">Übernehmen</button> ' +
      '<button class="btn btn-sm btn-primary" ' +
      'onclick="applyXyOffset(' + t + ', true)">+ schreiben</button></td>' +
      '</tr>';
  }).join('');
  body.innerHTML =
    '<table class="table table-sm align-middle mb-2">' +
    '<thead><tr><th>Tool</th><th>aktuell X/Y</th><th>gemessen X/Y</th>' +
    '<th>Δ</th><th>Z-Vgl.</th><th>Drift-Bias</th><th></th></tr></thead>' +
    '<tbody>' + rows + '</tbody></table>' +
    '<button class="btn btn-sm btn-primary" onclick="applyAllXyOffsets()">' +
    'Alle übernehmen + schreiben</button>';
}
```

- [ ] **Step 4: Live-Kurve**

Der Locator führt die bisher gemessenen Punkte in `get_status()` mit
(Task 2/3, Schlüssel `points`). Die Webapp pollt den Status ohnehin — die
Kurve kostet damit keinen neuen Kanal. Inline-SVG, keine Bibliothek:

```javascript
// Zeichnet die laufende Glocke aus nozzle_locator.points. Zeigt sofort,
// ob das Ziel sauber im Fenster liegt oder ob die Halterung wackelt.
function renderXySparkline(status) {
  var el = document.getElementById('xy-sparkline');
  if (!el) return;
  var pts = (status && status.points) || [];
  if (pts.length < 2) {
    el.innerHTML = '<span class="text-muted">' +
      (status && status.state !== 'idle' ? status.state : '') + '</span>';
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
    '<small class="text-muted">' + (y1 - y0).toFixed(0) + ' Hz über ' +
    (x1 - x0).toFixed(1) + ' mm</small>';
}
```

Im Markup aus Step 2, im `card-body`, den Container ergänzen:

```html
<div id="xy-sparkline" class="mt-2"></div>
```

`renderXySparkline(status.nozzle_locator)` in derselben Poll-Schleife
aufrufen, die schon `xy_results` holt.

- [ ] **Step 5: Übernehmen**

```javascript
function applyXyOffset(toolNr, alsoWrite) {
  var res = (_xyMethod === 'eddy') ? _xyResults[String(toolNr)]
                                   : _cameraOffsetFor(String(toolNr));
  if (!res) { showToast("T" + toolNr + ": kein Messwert", "warning"); return; }
  var script = "SET_TOOL_GCODE_OFFSET T=" + toolNr +
               " X=" + res.x.toFixed(4) + " Y=" + res.y.toFixed(4);
  return sendGcodeWithRecovery(script, "XY-Offset T" + toolNr)
    .then(function () {
      if (!alsoWrite) return null;
      return updateConfigFile("toolchanger/tools/T" + toolNr + ".cfg",
        function (content) {
          content = replaceInConfigSection(
            content, "tool T" + toolNr, "gcode_x_offset", res.x.toFixed(4));
          return replaceInConfigSection(
            content, "tool T" + toolNr, "gcode_y_offset", res.y.toFixed(4));
        });
    });
}
```

- [ ] **Step 6: Im Browser prüfen**

Offset-UI öffnen, nach einem Messlauf muss die Tabelle die Werte zeigen.
„Übernehmen" prüfen (Wert steht danach in `printer.tool T1.gcode_x_offset`),
„+ schreiben" prüfen (Wert steht in `T1.cfg`). Während eines laufenden
`NOZZLE_LOCATE` muss die Sparkline mitwachsen.

- [ ] **Step 7: Commit**

```bash
git add webapp/index.html webapp/js/tools.js
git commit -m "feat(webapp): gemeinsamer XY-Offset-Block mit Live-Kurve"
```

---

## Task 8: Assistent und Rettungsnetz

**Files:**
- Modify: `webapp/js/tools.js`

**Interfaces:**
- Consumes: `updateConfigFile` (Task 6), `confirmDialog`/`alertDialog` (`tools.js:95-355`)
- Produces: `xyWizard()`, `xyProbeActivate()`, `xyProbeDeactivate()`, `checkXyProbeStranded()`

- [ ] **Step 1: Aktivieren und Deaktivieren**

```javascript
// Aktivieren = Inhalt aus der Vorlage nach xy_probe.cfg kopieren.
// Die Vorlage bleibt unangetastet, damit UUID und Halterungsmasse jeden
// Zyklus ueberleben.
function xyProbeActivate() {
  return fetch(baseUrl + "/server/files/config/xy_probe.cfg.disabled",
               NO_CACHE)
    .then(function (r) {
      if (!r.ok) throw new Error(
        "xy_probe.cfg.disabled fehlt -- dort muessen CAN-UUID und " +
        "Halterungsmasse einmalig eingetragen werden.");
      return r.text();
    })
    .then(function (template) {
      if (template.indexOf("HIER_EINTRAGEN") !== -1) throw new Error(
        "In xy_probe.cfg.disabled steht noch HIER_EINTRAGEN statt der " +
        "CAN-UUID der Sonde.");
      return updateConfigFile("xy_probe.cfg", function () { return template; });
    })
    .then(function () { return restartKlipperAndWait(); });
}

function xyProbeDeactivate() {
  return updateConfigFile("xy_probe.cfg", function () {
    return "# XY-Sonde deaktiviert.\n";
  }).then(function () { return restartKlipperAndWait(); });
}

// FIRMWARE_RESTART und warten, bis Klipper wieder 'ready' meldet.
// Ein Config-Include-Wechsel braucht nur FIRMWARE_RESTART -- der volle
// Service-Neustart ist nur noetig, wenn sich .py-Module geaendert haben
// (RESTART laedt sys.modules nicht neu).
function restartKlipperAndWait(timeoutMs) {
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
```

- [ ] **Step 2: Präsenzprüfung**

```javascript
// Ist der CAN-Knoten da? Fehlt der Endpunkt in dieser Moonraker-Version,
// faellt die Pruefung auf eine Rueckfrage zurueck statt zu scheitern.
function xyProbeOnBus(uuid) {
  return fetch(baseUrl + "/machine/peripherals/canbus?interface=can0",
               NO_CACHE)
    .then(function (r) {
      if (!r.ok) return null;               // Endpunkt gibt es nicht
      return r.json();
    })
    .then(function (j) {
      if (!j || !j.result || !j.result.can_uuids) return null;
      return j.result.can_uuids.some(function (d) { return d.uuid === uuid; });
    })
    .catch(function () { return null; });
}
```

Liefert die Funktion `null`, fragt der Assistent stattdessen per
`confirmDialog` nach („Ist die Sonde angesteckt und leuchtet?").

- [ ] **Step 3: Assistent**

Die Reihenfolge in Schritt 1 und 7 ist nicht kosmetisch — sie trägt die
Sicherheit. Homen **vor** dem Aufsetzen, Deaktivieren **vor** dem Abziehen.

```javascript
// Der Assistent fuehrt durch An- und Abstecken der XY-Sonde. Zwei
// Reihenfolgen sind zwingend:
//   Schritt 1: erst homen, DANN die Halterung aufsetzen -- ein G28 mit
//              Aufbau auf dem Bett hebt nur 10 mm und faehrt Y quer
//              ueber die Bettmitte (homing.cfg:35).
//   Schritt 7: erst deaktivieren, DANN abziehen -- sonst startet Klipper
//              beim naechsten Mal nicht mehr.
function xyWizard() {
  return confirmDialog({
    title: "XY-Sonde: Vorbereiten",
    body: "Zuerst werden die Achsen gehomt. Die Halterung kommt ERST " +
          "DANACH aufs Bett -- ein Homing mit Aufbau auf dem Bett ist ein " +
          "Kollisionsrisiko.",
    okLabel: "Homen"
  })
  .then(function () { return ensureHomed(); })
  .then(function () {
    return confirmDialog({
      title: "XY-Sonde: Aufsetzen",
      body: "Halterung jetzt auf das Bett stellen und die Sonde anstecken.",
      okLabel: "Ist erledigt"
    });
  })
  .then(function () { return xyProbeCheckPresent(); })
  .then(function () {
    showToast("Sonde wird aktiviert, Klipper startet neu…", "info");
    return xyProbeActivate();
  })
  .then(function () {
    return sendGcodeWithRecovery("NOZZLE_LOCATOR_READ DURATION=1.0",
                                 "Sonde prüfen");
  })
  .then(function () {
    return confirmDialog({
      title: "Trockenlauf",
      body: "Beim ersten Mal dringend empfohlen: alle Werkzeugwechsel und " +
            "Verfahrwege werden abgefahren, aber nie abgesenkt. Damit " +
            "siehst du gefahrlos, ob der Wechselweg über die Halterung führt.",
      okLabel: "Trockenlauf fahren",
      cancelLabel: "Überspringen"
    }).then(function (yes) {
      if (!yes) return null;
      return sendGcodeWithRecovery("CALIBRATE_XY_OFFSETS DRY_RUN=1",
                                   "Trockenlauf");
    });
  })
  .then(function () {
    return sendGcodeWithRecovery("CALIBRATE_XY_OFFSETS", "XY-Messlauf");
  })
  .then(function () { return refreshOffsetStatus(); })
  .then(function () {
    return confirmDialog({
      title: "Abschließen",
      body: "Die Sonde wird jetzt aus der Config entfernt und Klipper neu " +
            "gestartet. Erst DANACH abziehen.",
      okLabel: "Deaktivieren"
    });
  })
  .then(function () { return xyProbeDeactivate(); })
  .then(function () {
    return alertDialog("Fertig",
      "Sonde ist deaktiviert. Sie kann jetzt abgezogen und die Halterung " +
      "vom Bett genommen werden.");
  })
  .catch(function (err) {
    return alertDialog("XY-Assistent abgebrochen", gcodeErrorMessage(err), {
      extraLabel: "Sonde deaktivieren",
      extraAction: xyProbeDeactivate
    });
  });
}

// Praesenzpruefung mit Rueckfall: kennt diese Moonraker-Version den
// CAN-Endpunkt nicht, fragen wir den Nutzer statt zu scheitern.
function xyProbeCheckPresent() {
  return readXyProbeUuid()
    .then(function (uuid) { return xyProbeOnBus(uuid); })
    .then(function (onBus) {
      if (onBus === true) return true;
      if (onBus === false) throw new Error(
        "Die Sonde ist nicht auf dem CAN-Bus zu sehen. Steckt sie richtig?");
      return confirmDialog({
        title: "Sonde angesteckt?",
        body: "Diese Moonraker-Version kann den CAN-Bus nicht abfragen. " +
              "Bitte selbst prüfen: ist die Sonde angesteckt?",
        okLabel: "Ja, ist angesteckt"
      });
    });
}
```

`readXyProbeUuid()` liest die `canbus_uuid`-Zeile aus
`xy_probe.cfg.disabled`. `ensureHomed()` und `refreshOffsetStatus()` sind
die bereits vorhandenen Helfer der Offset-UI — deren tatsächliche Namen aus
`tools.js` übernehmen, nicht aus diesem Entwurf raten.

**Der `catch`-Zweig ist der wichtigste Teil.** Bricht irgendein Schritt
nach dem Aktivieren ab, muss der Nutzer die Sonde deaktivieren können, ohne
den Assistenten erneut zu durchlaufen — sonst bleibt sie aktiviert stehen
und der nächste Klipper-Start scheitert.

- [ ] **Step 4: Rettungsnetz**

```javascript
// Aktivierte Sonde + abgesteckter Knoten = Klipper startet nicht. Moonraker
// laeuft weiter, also koennen wir das genau hier noch reparieren.
function checkXyProbeStranded() {
  return fetch(baseUrl + "/printer/info", NO_CACHE)
    .then(function (r) { return r.json(); })
    .then(function (j) {
      var st = j && j.result && j.result.state;
      if (st !== 'error' && st !== 'shutdown') return;
      var msg = (j.result.state_message || '');
      if (msg.indexOf('xyprobe') === -1 && msg.indexOf('mcu') === -1) return;
      return alertDialog(
        "XY-Sonde blockiert den Start",
        "Klipper startet nicht, und in der Config steht noch die XY-Sonde. " +
        "Wurde sie abgezogen, ohne sie vorher zu deaktivieren?",
        {extraLabel: "Sonde deaktivieren und neu starten",
         extraAction: xyProbeDeactivate});
    });
}
```

Beim Laden der Offset-UI aufrufen.

- [ ] **Step 5: Durchspielen**

Den ganzen Assistenten einmal von vorn bis hinten durchlaufen. Danach
**bewusst** den Fehlerfall provozieren: aktivieren, Sonde abziehen,
Klipper neu starten, und prüfen, dass das Rettungsnetz greift.

- [ ] **Step 6: Commit**

```bash
git add webapp/js/tools.js
git commit -m "feat(webapp): Assistent fuer die XY-Sonde inkl. Rettungsnetz"
```

---

## Task 9: Kamera „Position übernehmen"

Macht beide Verfahren vergleichbar, indem auch die Kameramethode eine
Position je Tool liefert statt einer Zahl im Kopf des Nutzers.

**Files:**
- Modify: `webapp/js/camera.js`
- Modify: `webapp/js/tools.js`
- Modify: `webapp/index.html`

**Interfaces:**
- Produces: `captureCameraPosition(toolNr)`, `_cameraOffsetFor(toolNr)`

- [ ] **Step 1: Erfassen**

```javascript
// Haelt bei zentriertem Fadenkreuz die aktuelle Kopfposition fest. Der
// Offset ist spaeter die Differenz zum Referenztool -- genau wie beim
// Eddy-Verfahren, damit beide vergleichbar bleiben.
function captureCameraPosition(toolNr) {
  return fetch(baseUrl + "/printer/objects/query?gcode_move", NO_CACHE)
    .then(function (r) { return r.json(); })
    .then(function (j) {
      var p = j.result.status.gcode_move.gcode_position;
      _cameraPositions[String(toolNr)] = {x: p[0], y: p[1]};
      renderXyBlock();
      showToast("T" + toolNr + ": Position festgehalten (" +
                p[0].toFixed(3) + " / " + p[1].toFixed(3) + ")", "info");
    });
}

```

`_cameraOffsetFor()` gibt es bereits aus Task 7 — hier kommt nur das
Erfassen dazu.

- [ ] **Step 2: Knopf im Kamerabereich**

In `webapp/index.html`, im Kamera-Panel neben dem Zoom-Regler
(`zoom-range`, vgl. `camera.js:27`):

```html
<button class="btn btn-sm btn-outline-primary" id="camera-capture-btn"
        title="Hält die aktuelle Kopfposition fest, während die Düse auf
               dem Fadenkreuz steht. Der Offset ist später die Differenz
               zum Referenztool.">
  Position übernehmen
</button>
```

und in `camera.js` bei der bestehenden Verdrahtung der Zoom-Steuerung
(`setupCameraControls`, `camera.js:44-48`):

```javascript
  var captureBtn = document.getElementById('camera-capture-btn');
  if (captureBtn) {
    captureBtn.addEventListener('click', function () {
      captureCameraPosition(currentToolNumber());
    });
  }
```

`currentToolNumber()` ist der vorhandene Helfer der Offset-UI — den
tatsächlichen Namen aus `tools.js` übernehmen.

- [ ] **Step 3: Im Browser prüfen**

Ein Tool mit der Kamera zentrieren, „Position übernehmen", auf ein zweites
Tool wechseln, dasselbe. Expected: der XY-Block zeigt bei Methode „Kamera"
die Differenz als Offset.

- [ ] **Step 4: Commit**

```bash
git add webapp/js/camera.js webapp/js/tools.js webapp/index.html
git commit -m "feat(webapp): Kameramethode liefert eine vergleichbare Position je Tool"
```

---

## Abschluss: die eigentliche Frage beantworten

Nach Task 9 steht das Werkzeug — die Frage, ob das Verfahren der Kamera
überlegen ist, ist damit **noch nicht** beantwortet. Sie wird es erst durch
einen Vergleichslauf, und den kann jetzt das Werkzeug selbst fahren:

- [ ] Alle Tools per Kamera vermessen, Werte festhalten
- [ ] Dieselben Tools per Eddy vermessen
- [ ] Differenz je Tool bilden

Auswertung:

| Befund | Bedeutung |
|---|---|
| Übereinstimmung < ~20 µm je Tool | Der Heizblock-Effekt (Risiko R1) ist klein, dem Eddy kann man trauen |
| **systematische** Abweichung je Tool, aber konstant | Genau der Blockversatz — der Eddy taugt dann nicht als Absolutverfahren, wohl aber zur Drift-Überwachung gegen eine per Kamera gesetzte Referenz |
| unsystematische Streuung | Das Problem sitzt im Dock, und dann hilft keins von beiden Verfahren |

Zusätzlich `NOZZLE_LOCATE AXIS=X REPEATS=20` gegen die Kamera-Wiederholbarkeit
(8–10 unabhängige Zentrierungen, einmal mit und einmal ohne Werkzeugwechsel
dazwischen). Erst diese Zahlen erlauben die Aussage „besser als die Kamera".

Ergebnisse in `memory/eddy-xy-offset-spike.md` nachtragen.
