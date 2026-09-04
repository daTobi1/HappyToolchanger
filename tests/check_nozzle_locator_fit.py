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

    # --- 4a: der Fit traegt auch bei realen Bettkoordinaten weit vom Ursprung ---
    # Der Fit zentriert intern um den Mittelwert der x-Werte; ohne das
    # liefert die schlecht konditionierte Normalgleichungsmatrix Unsinn bei
    # x ~ 300. Hier ist Kondition das Motiv, nicht Drift.
    CENTER_FAR = 300.0
    POSITIONS_FAR = [296.0, 297.0, 298.0, 299.0, 300.0, 301.0, 302.0, 303.0, 304.0]
    pts_far = bell(CENTER_FAR, AMPL, CURV, POSITIONS_FAR)
    close(fit.parabola_vertex(pts_far), CENTER_FAR, 1e-6,
          "Fit bei Bettkoordinaten (x~300) trifft das Zentrum nicht")
    fwd_far = bell(CENTER_FAR, AMPL, CURV, POSITIONS_FAR, DRIFT_PER_STEP)
    close(fit.parabola_vertex(fwd_far), CENTER_FAR + expected_shift, 1e-6,
          "Einzelsweep bei Bettkoordinaten verschiebt sich nicht um m/(2a)")
    n_far = len(POSITIONS_FAR)
    rev_far = [(x, AMPL - CURV * (x - CENTER_FAR) ** 2 + DRIFT_PER_STEP * (n_far - 1 - i))
               for i, x in enumerate(POSITIONS_FAR)]
    close(fit.bidirectional_center(fwd_far, rev_far), CENTER_FAR, 1e-9,
          "bidirektionaler Mittelwert bei Bettkoordinaten hebt den Drift nicht auf")

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

    # --- 9: parabola_fit liefert auch die Kruemmung (fuer AXIS=DIAG) ---
    pts = bell(CENTER, AMPL, CURV, POSITIONS)
    v, k = fit.parabola_fit(pts)
    close(k, CURV, 1e-6, "parabola_fit liefert nicht die eingesetzte Kruemmung")
    ok(v == fit.parabola_vertex(pts),
       "parabola_vertex und parabola_fit()[0] weichen voneinander ab")

    # --- 10: Grobsuche waehlt den Duesen-Buckel, nicht den Heizblock ---
    # Echte Y-Grobsuche vom 250er (2026-09-03, Basislinie abgezogen): die
    # Duese bei ~130 als Buckel von +6.000 Hz, ab Y 123 nach vorn der
    # Heizblock mit bis zu +100.000 Hz am Fensterrand.
    REAL_Y = [(115, 102596), (117, 74712), (119, 45577), (121, 25193),
              (123, 6598), (125, 2523), (127, 4422), (129, 5928),
              (131, 5972), (133, 4667), (135, 2859), (137, 1454),
              (139, 626), (141, 172), (143, -90), (145, -245)]
    pos, amp = fit.local_peak(REAL_Y, 130.0, 0.0, 2000.0)
    ok(129.0 <= pos <= 131.5,
       "Grobsuche trifft den Duesen-Buckel nicht", "pos=%.3f" % pos)
    ok(pos < 120 or pos > 125,
       "Grobsuche ist auf den Heizblock am Rand gesprungen")
    ok(5000 <= amp <= 7000,
       "Grobsuche meldet nicht die Amplitude des Buckels", "amp=%.0f" % amp)
    # Liegt die Anfahrposition naeher am Blockrand, gilt trotzdem nur ein
    # echter Buckel -- die Kante bei 115 ist keiner.
    pos2, _ = fit.local_peak(REAL_Y, 118.0, 0.0, 2000.0)
    ok(129.0 <= pos2 <= 131.5,
       "Kante am Fensterrand wurde als Scheitel genommen", "pos=%.3f" % pos2)
    # Reine Flanke ohne Buckel -> Fehler statt erfundenem Scheitel
    try:
        fit.local_peak([(x, 1000.0 * x) for x in range(10)], 5.0, 0.0, 2000.0)
        ok(False, "local_peak akzeptiert eine monotone Flanke")
    except ValueError:
        ok(True, "")
    # Synthetischer Buckel: Scheitel wird parabolisch verfeinert
    pts = bell(124.3, AMPL, CURV, [118.0 + 2.0 * i for i in range(11)])
    pos3, _ = fit.local_peak(pts, 125.0, 0.0, 2000.0)
    close(pos3, 124.3, 0.05, "Grobsuche verfeinert den Scheitel nicht")

    # --- 11: Koerbe -- ein kontinuierlicher Scan wird zu Gitterpunkten ---
    # Der Scan liefert ~80 Samples je mm. Die Grobsuche (local_peak) braucht
    # Nachbarn mit festem Abstand; bin_points mittelt je Korb. Die Koerbe
    # liegen ganz im Fenster (ein halber Randkorb mittelt auf der Flanke
    # schief), die Mitten also bei lo+step/2 ... hi-step/2.
    fine_pos = [120.0 + 0.0125 * i for i in range(641)]      # 120..128
    raw = bell(CENTER, AMPL, CURV, fine_pos)
    bins = fit.bin_points(raw, 120.0, 128.0, 1.0)
    CENTERS = [120.5 + i for i in range(8)]
    # Je Korb die mittlere Sample-Position (hier 6 um unter der Korbmitte,
    # weil die Samples bei 120.0 beginnen), nicht die geometrische Mitte.
    ok(len(bins) == 8 and all(abs(p - c) < 0.01
                              for (p, _), c in zip(bins, CENTERS)),
       "bin_points legt die Koerbe nicht ins Fenster",
       str([p for p, _ in bins]))
    # Der Mittelwert einer Parabel ueber einen Korb liegt curv*step^2/12
    # unter dem Wert in der Korbmitte -- das ist die erlaubte Abweichung.
    for p, v in bins:
        close(v, AMPL - CURV * (p - CENTER) ** 2, CURV / 12.0 + 1.0,
              "Korbmittel weicht vom Glockenwert ab", "bei %.1f" % p)
    close(fit.parabola_vertex(bins), CENTER, 1e-3,
          "Fit ueber die Koerbe trifft das Zentrum nicht")
    pos_b, _ = fit.local_peak(bins, 125.0, 0.0, 2000.0)
    close(pos_b, CENTER, 0.05, "Grobsuche auf Koerben verfehlt den Buckel")
    # Rueckwaerts gescannt -> Koerbe in Sweep-Reihenfolge (absteigend), damit
    # sweep_quality den Rand weiter am Listenende erkennt.
    bins_rev = fit.bin_points(list(reversed(raw)), 120.0, 128.0, 1.0)
    ok(len(bins_rev) == 8 and all(
        abs(p - q) < 1e-9
        for (p, _), (q, _) in zip(bins_rev, reversed(bins))),
       "bin_points haelt die Sweep-Reihenfolge nicht ein")
    # Leere Koerbe werden ausgelassen, nicht mit 0 gefuellt.
    gappy = [(p, v) for p, v in raw if not (123.0 <= p < 124.0)]
    bins_gap = fit.bin_points(gappy, 120.0, 128.0, 1.0)
    ok(not any(123.0 <= p < 124.0 for p, _ in bins_gap),
       "bin_points fuellt einen leeren Korb")
    ok(len(bins_gap) == 7, "bin_points verliert mehr als den leeren Korb",
       str(len(bins_gap)))
    # Samples ausserhalb des Fensters gehoeren in keinen Korb.
    wide = bell(CENTER, AMPL, CURV, [118.0 + 0.0125 * i for i in range(961)])
    bins_wide = fit.bin_points(wide, 120.0, 128.0, 1.0)
    ok([p for p, _ in bins_wide] == [p for p, _ in bins],
       "bin_points nimmt Samples ausserhalb des Fensters mit")
    # Ein Sample genau auf hi landet im letzten Korb, nicht in einem neuen.
    edge = fit.bin_points([(128.0, 1.0), (127.9, 3.0)], 120.0, 128.0, 1.0)
    ok(len(edge) == 1 and abs(edge[0][0] - 127.95) < 1e-9
       and edge[0][1] == 2.0, "Sample auf hi wird falsch einsortiert",
       str(edge))

    # --- 12: Zeitstempel -> Bahnposition entlang einer Richtung ---
    # Ein Scan liefert (print_time, frequenz). Die Position kommt aus der
    # Bewegungswarteschlange: lookup(t) -> ((x, y, z), geschwindigkeit) oder
    # (None, None), wenn keine Bewegung bekannt ist. samples_to_track
    # projiziert auf die Bahn (Bogenlaenge ab origin in Richtung direction)
    # und behaelt nur Samples in Bewegung innerhalb des Fensters [lo, hi].
    V = 5.0                       # mm/s
    T0 = 100.0                    # Bewegungsbeginn
    X0, X1 = 117.0, 131.0         # Vorlauf 3 mm vor 120, Nachlauf bis 131

    def lookup_x(t):
        if t < T0 - 1.0:
            return None, None
        if t < T0:
            return (X0, 50.0, 10.0), 0.0
        if t > T0 + (X1 - X0) / V:
            return (X1, 50.0, 10.0), 0.0
        return (X0 + V * (t - T0), 50.0, 10.0), V

    samples = [(T0 - 1.5 + 0.0025 * i, 1000.0) for i in range(2000)]
    track = fit.samples_to_track(samples, lookup_x, (0.0, 0.0), (1.0, 0.0),
                                 120.0, 128.0)
    ok(len(track) > 0, "samples_to_track liefert nichts")
    ok(all(120.0 <= s <= 128.0 for s, _ in track),
       "samples_to_track laesst Positionen ausserhalb des Fensters durch")
    ok(all(v == 1000.0 for _, v in track),
       "samples_to_track veraendert die Frequenzwerte")
    # Stillstand (Vorlaufposition vor T0) darf nicht auftauchen, auch wenn
    # sie ausserhalb des Fensters liegt -- und auch nicht, wenn sie drin
    # laege: geprueft ueber einen Stillstand mitten im Fenster.
    def lookup_stop(t):
        return (124.0, 50.0, 10.0), 0.0
    ok(fit.samples_to_track(samples, lookup_stop, (0.0, 0.0), (1.0, 0.0),
                            120.0, 128.0) == [],
       "samples_to_track nimmt Samples im Stillstand")
    # Die Bahnposition ist die Projektion: x bei Richtung (1,0)
    s_first = track[0][0]
    t_first = [t for t, _ in samples
               if (lookup_x(t)[1] or 0) > 0 and 120.0 <= lookup_x(t)[0][0]][0]
    close(s_first, X0 + V * (t_first - T0), 1e-9,
          "samples_to_track projiziert nicht auf x")
    # Reihenfolge = Zeitreihenfolge = Sweep-Reihenfolge
    ok(all(track[i][0] <= track[i + 1][0] for i in range(len(track) - 1)),
       "samples_to_track haelt die Sweep-Reihenfolge nicht ein")

    # Diagonale: Richtung (1, -1)/sqrt2 ab origin (cx, cy). Ein Punkt 2 mm
    # weiter in x und 2 mm weniger in y hat Bogenlaenge 2*sqrt2.
    root2 = 2.0 ** 0.5
    def lookup_diag(t):
        return (124.0 + 2.0, 130.0 - 2.0, 10.0), V
    track_d = fit.samples_to_track([(T0, 1.0)], lookup_diag, (124.0, 130.0),
                                   (1.0 / root2, -1.0 / root2), -4.0, 4.0)
    ok(len(track_d) == 1, "Diagonal-Sample verloren")
    close(track_d[0][0], 2.0 * root2, 1e-9,
          "Bogenlaenge auf der Diagonalen falsch")

    # Latenz: der Sensor integriert VOR dem Zeitstempel. Mit latency=L wird
    # das Sample der Position zur Zeit t-L zugeordnet -- also weiter hinten
    # auf der Bahn (kleineres x im Hinsweep).
    track_lat = fit.samples_to_track(samples, lookup_x, (0.0, 0.0),
                                     (1.0, 0.0), 120.0, 128.0, latency=0.010)
    t_l = [t for t, _ in samples
           if (lookup_x(t - 0.010)[1] or 0) > 0
           and 120.0 <= lookup_x(t - 0.010)[0][0]][0]
    close(track_lat[0][0], X0 + V * (t_l - 0.010 - T0), 1e-9,
          "Latenz wird nicht vom Zeitstempel abgezogen")
    ok(track_lat[0][0] < track[0][0] + 1e-9 and len(track_lat) == len(track),
       "Latenz verschiebt die Bahn nicht wie erwartet")

    # Unbekannte Zeiten (lookup -> (None, None)) werden still uebergangen.
    ok(fit.samples_to_track([(T0 - 5.0, 1.0)], lookup_x, (0.0, 0.0),
                            (1.0, 0.0), 120.0, 128.0) == [],
       "samples_to_track stolpert ueber unbekannte Zeiten")

    print("%d Zusicherungen geprueft" % CHECKS[0])
    if FINDINGS:
        for f in FINDINGS:
            print("BEFUND: %s" % f)
        return 1
    print("sauber")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
