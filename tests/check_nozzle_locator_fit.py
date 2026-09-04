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

    # --- 13: Bahn eines Scans -- Start und Ende in Bettkoordinaten ---
    # scan_line(origin, direction, lo, hi, lead, through) liefert
    # (start_xy, end_xy). Die Bahn laeuft in `direction` und geht durch
    # `through` -- die Senkrechtkomponente kommt also von `through`, nicht
    # von origin. Genau das war der Fehler des ersten Scan-Entwurfs: ein
    # X-Sweep mit origin (0,0) haette Y auf 0 gefahren.
    start, end = fit.scan_line((0.0, 0.0), (1.0, 0.0), 120.0, 128.0, 3.0,
                               through=(124.0, 130.0))
    close(start[0], 117.0, 1e-9, "X-Scan startet nicht bei lo - lead")
    close(end[0], 131.0, 1e-9, "X-Scan endet nicht bei hi + lead")
    close(start[1], 130.0, 1e-9, "X-Scan verlaesst die aktuelle Y-Position")
    close(end[1], 130.0, 1e-9, "X-Scan verlaesst die aktuelle Y-Position")
    # Rueckwaerts (lo > hi): Vorlauf auf der anderen Seite
    start, end = fit.scan_line((0.0, 0.0), (0.0, 1.0), 134.0, 126.0, 3.0,
                               through=(124.0, 130.0))
    close(start[1], 137.0, 1e-9, "Y-Ruecksweep startet nicht bei hi + lead")
    close(end[1], 123.0, 1e-9, "Y-Ruecksweep endet nicht bei lo - lead")
    close(start[0], 124.0, 1e-9, "Y-Scan verlaesst die aktuelle X-Position")
    # Diagonale durch das Zentrum: through == origin, Bogenlaenge ab dort
    start, end = fit.scan_line((124.0, 130.0), (1.0 / root2, -1.0 / root2),
                               -4.0, 4.0, 3.0, through=(124.0, 130.0))
    close(start[0], 124.0 - 7.0 / root2, 1e-9, "Diagonale: Start x falsch")
    close(start[1], 130.0 + 7.0 / root2, 1e-9, "Diagonale: Start y falsch")
    close(end[0], 124.0 + 7.0 / root2, 1e-9, "Diagonale: Ende x falsch")
    close(end[1], 130.0 - 7.0 / root2, 1e-9, "Diagonale: Ende y falsch")
    # Liegt `through` neben der Linie durch origin, bleibt der Abstand
    # senkrecht zur Richtung erhalten, die Bogenlaenge zaehlt ab origin.
    start, end = fit.scan_line((124.0, 130.0), (1.0, 0.0), -4.0, 4.0, 0.0,
                               through=(200.0, 131.0))
    close(start[0], 120.0, 1e-9, "Bogenlaenge zaehlt nicht ab origin")
    close(start[1], 131.0, 1e-9, "Senkrechtabstand von through geht verloren")

    # --- 14: Raster -> Gitter (C-Scan) ---
    # Zeilen sind Scans mit dichten Samples; das Gitter fasst jede Zeile in
    # Spalten der Breite pitch zusammen (bin_points), Zeilen nach y sortiert.
    rows = [
        (131.0, [(120.0 + 0.1 * i, 3.0 + i * 0.01) for i in range(81)]),
        (129.0, [(128.0 - 0.1 * i, 1.0) for i in range(81)]),   # rueckwaerts
        (130.0, [(120.0 + 0.1 * i, 2.0) for i in range(81)]),
    ]
    grid = fit.raster_grid(rows, 120.0, 128.0, 2.0)
    ok(grid['ys'] == [129.0, 130.0, 131.0], "raster_grid sortiert die Zeilen "
       "nicht nach y", str(grid['ys']))
    ok(len(grid['xs']) == 4 and all(abs(x - (121.0 + 2.0 * i)) < 0.05
                                    for i, x in enumerate(grid['xs'])),
       "raster_grid legt die Spalten nicht auf pitch-Mitten",
       str(grid['xs']))
    ok(len(grid['values']) == 3 and all(len(r) == 4 for r in grid['values']),
       "raster_grid hat nicht 3x4 Werte")
    ok(all(v == 1.0 for v in grid['values'][0]),
       "raster_grid: Rueckwaerts-Zeile falsch einsortiert",
       str(grid['values'][0]))
    ok(all(v == 2.0 for v in grid['values'][1]),
       "raster_grid: mittlere Zeile falsch", str(grid['values'][1]))
    ok(grid['values'][2][0] < grid['values'][2][3],
       "raster_grid: Steigung der obersten Zeile verloren")
    # Fehlende Spalte -> None, nicht 0 und kein Abbruch
    sparse = [(130.0, [(120.5, 5.0), (127.5, 6.0)])]
    g2 = fit.raster_grid(sparse, 120.0, 128.0, 2.0)
    ok(g2['values'][0][0] == 5.0 and g2['values'][0][1] is None
       and g2['values'][0][3] == 6.0,
       "raster_grid fuellt fehlende Spalten nicht mit None",
       str(g2['values']))

    # --- 15: Geschwindigkeitsklemme -- Mindestzahl Samples im Fenster ---
    # 400 Samples/s ueber 8 mm: bei 5 mm/s 640 Samples, bei 40 mm/s nur 80.
    # Statt abzubrechen wird die Geschwindigkeit so weit gesenkt, dass
    # min_samples im Fenster liegen (EddySeek macht es genauso).
    close(fit.clamp_scan_speed(5.0, 8.0, 400.0, 200), 5.0, 1e-9,
          "clamp_scan_speed bremst, obwohl genug Samples da sind")
    close(fit.clamp_scan_speed(40.0, 8.0, 400.0, 200), 16.0, 1e-9,
          "clamp_scan_speed klemmt nicht auf rate*span/min_samples")
    close(fit.clamp_scan_speed(16.0, 8.0, 400.0, 200), 16.0, 1e-9,
          "clamp_scan_speed veraendert die Grenzgeschwindigkeit")
    # Grobsuche ueber 30 mm darf schneller: 30 mm * 400 / 200 = 60 mm/s
    close(fit.clamp_scan_speed(40.0, 30.0, 400.0, 200), 40.0, 1e-9,
          "clamp_scan_speed bremst die Grobsuche unnoetig")
    try:
        fit.clamp_scan_speed(5.0, 8.0, 400.0, 0)
        ok(False, "clamp_scan_speed akzeptiert min_samples 0")
    except ValueError:
        ok(True, "")

    # --- 16: Basislinie neben der Spule -- Ausweichrichtung in X ---
    # Auf park_z steht die Duese 7 mm ueber der Spule und hebt die
    # Basislinie um ~1.400 Hz (8.6). Also seitlich weg: baseline_side
    # liefert das Ziel-x, das um `offset` von x entfernt liegt und in den
    # Achsgrenzen bleibt; bevorzugt +X, sonst -X, sonst Fehler.
    close(fit.baseline_side(125.0, 40.0, 0.0, 250.0), 165.0, 1e-9,
          "baseline_side geht nicht nach +X")
    close(fit.baseline_side(230.0, 40.0, 0.0, 250.0), 190.0, 1e-9,
          "baseline_side weicht nicht nach -X aus, wenn +X nicht passt")
    try:
        fit.baseline_side(20.0, 40.0, 0.0, 50.0)
        ok(False, "baseline_side erfindet ein Ziel ausserhalb der Achse")
    except ValueError:
        ok(True, "")
    # Genau auf der Grenze ist erlaubt
    close(fit.baseline_side(210.0, 40.0, 0.0, 250.0), 250.0, 1e-9,
          "baseline_side lehnt ein Ziel genau auf der Achsgrenze ab")

    # --- 17: sweep_quality mit Randabstand ---
    # Messtag 2026-09-04: der Y-Scheitel lag am unteren Fensterrand, das
    # Maximum einen Korb daneben (126.5 statt 126.0) -- die Randpruefung
    # sah ein inneres Maximum, der Parabelfit auf der Flanke lieferte
    # Y 109 statt 126. Jetzt muss das Maximum mindestens 12,5 % der
    # Fensterbreite von beiden Raendern entfernt liegen.
    FLANK = [(126.0, 9291.), (126.5, 9305.), (127.0, 9189.), (127.5, 8936.),
             (128.0, 8530.), (128.5, 8030.), (129.0, 7452.), (129.5, 6825.),
             (130.0, 6154.), (130.5, 5485.), (131.0, 4839.), (131.5, 4229.),
             (132.0, 3676.), (132.5, 3193.), (133.0, 2760.), (133.5, 2384.),
             (134.0, 2133.)]
    good, reason = fit.sweep_quality(FLANK, 0.0, 2000.0)
    ok(not good, "sweep_quality laesst einen Scheitel am Fensterrand durch "
       "(Messtag-Flanke)")
    ok("Rand" in reason, "Begruendung nennt den Rand nicht", reason)
    # Gespiegelt (Scheitel am oberen Rand) genauso
    good, reason = fit.sweep_quality([(260.0 - x, v) for x, v in FLANK],
                                     0.0, 2000.0)
    ok(not good, "sweep_quality laesst einen Scheitel am oberen Rand durch")
    # Ein Scheitel 1,5 mm vom Rand (von 8 mm) liegt ausserhalb der 12,5 % und
    # ist erlaubt; genau bei 1,0 mm (= 12,5 %) auch noch.
    ok(fit.sweep_quality(bell(121.5, AMPL, CURV, POSITIONS), 0.0, 2000.0)[0],
       "sweep_quality lehnt einen Scheitel 1,5 mm vom Rand ab")
    ok(fit.sweep_quality(bell(121.0, AMPL, CURV, POSITIONS), 0.0, 2000.0)[0],
       "sweep_quality lehnt einen Scheitel genau auf der 12,5-%-Grenze ab")
    ok(not fit.sweep_quality(bell(120.5, AMPL, CURV, POSITIONS),
                             0.0, 2000.0)[0],
       "sweep_quality laesst einen Scheitel 0,5 mm vom Rand durch")
    # Dichte Scan-Punkte: dasselbe Kriterium ueber die Position, nicht den
    # Index
    dense = bell(120.6, AMPL, CURV, [120.0 + 0.0125 * i for i in range(641)])
    ok(not fit.sweep_quality(dense, 0.0, 2000.0)[0],
       "sweep_quality bewertet den Rand bei dichten Punkten nach Index statt "
       "Position")

    # --- 18: Spitzen-Extrapolation -- Scheitel bei zwei Spalten auf Spalt 0 ---
    # Messtag 2026-09-04: T0s Y-Scheitel wandert 0,6 mm je mm Spalt, weil
    # die Duesenspitze nicht auf der Blockachse sitzt. Die Spule liefert bei
    # Spalt -> 0 die Spitze; zwei Messungen bei kleinen Spalten und eine
    # Gerade dorthin.
    # T0-Zahlen: Spalt 0,465 -> 5,2143, Spalt 1,0 -> 5,5264 -> Spalt 0 ~ 4,94
    tip = fit.tip_extrapolate(5.2143, 0.465, 5.5264, 1.0)
    close(tip, 5.2143 - (5.5264 - 5.2143) * 0.465 / 0.535, 1e-9,
          "tip_extrapolate rechnet nicht linear auf Spalt 0")
    ok(4.9 < tip < 5.0, "tip_extrapolate liefert fuer T0 nicht ~4,94",
       "%.4f" % tip)
    # Ohne Spaltabhaengigkeit bleibt der Wert
    close(fit.tip_extrapolate(1.0, 0.3, 1.0, 0.8), 1.0, 1e-12,
          "tip_extrapolate veraendert einen spaltunabhaengigen Scheitel")
    # Reihenfolge der Spalte egal
    close(fit.tip_extrapolate(5.5264, 1.0, 5.2143, 0.465), tip, 1e-9,
          "tip_extrapolate haengt von der Reihenfolge ab")
    # Gleiche Spalte -> keine Gerade -> Fehler statt Division durch 0
    try:
        fit.tip_extrapolate(1.0, 0.5, 1.1, 0.5)
        ok(False, "tip_extrapolate akzeptiert gleiche Spalte")
    except ValueError:
        ok(True, "")
    # Die Steigung (mm je mm Spalt) ist der Exzentrizitaets-Indikator
    close(fit.tip_slope(5.2143, 0.465, 5.5264, 1.0),
          (5.5264 - 5.2143) / 0.535, 1e-9, "tip_slope falsch")

    # --- 19: 2D-Paraboloid-Fit ueber ein kleines Raster ---
    # z = c + gx x + gy y + axx x^2 + ayy y^2 + axy xy um (cx, cy);
    # Scheitel aus grad z = 0. Nutzt alle Rasterpunkte und den Kreuzterm
    # zugleich -- zwei Linien sehen den Kreuzterm nicht (6.4).
    def bell2d(x0, y0, a, b, c_xy, pts):
        return [(x, y, 9000.0 - a * (x - x0) ** 2 - b * (y - y0) ** 2
                 - c_xy * (x - x0) * (y - y0)) for x, y in pts]
    grid = [(120.0 + 0.5 * i, 128.0 + 0.5 * j)
            for i in range(13) for j in range(13)]   # 6 x 6 mm, 0,5 mm
    r = fit.paraboloid_fit(bell2d(123.2, 130.7, 130.0, 240.0, 40.0, grid),
                           123.0, 131.0, 2.0)
    close(r['x'], 123.2, 1e-6, "paraboloid_fit trifft x0 nicht")
    close(r['y'], 130.7, 1e-6, "paraboloid_fit trifft y0 nicht")
    close(r['axx'], 130.0, 1e-6, "paraboloid_fit: Kruemmung x falsch")
    close(r['ayy'], 240.0, 1e-6, "paraboloid_fit: Kruemmung y falsch")
    close(r['axy'], 40.0, 1e-6, "paraboloid_fit: Kreuzterm falsch")
    close(r['rho'], 40.0 / (2.0 * (130.0 * 240.0) ** 0.5), 1e-9,
          "paraboloid_fit: rho nicht c/(2 sqrt(ab))")
    ok(r['n'] > 40, "paraboloid_fit nimmt zu wenige Punkte im Radius",
       str(r['n']))
    # Nur Punkte im Radius zaehlen: ein Ausreisser weit draussen stoert nicht
    far = bell2d(123.2, 130.7, 130.0, 240.0, 0.0, grid) + [(127.0, 135.0, 0.0)]
    close(fit.paraboloid_fit(far, 123.0, 131.0, 2.0)['x'], 123.2, 1e-6,
          "paraboloid_fit laesst Punkte ausserhalb des Radius einfliessen")
    # Konstante Ablage und lineare Drift ueber die Sweep-Reihenfolge: die
    # Serpentine wechselt je Zeile die Richtung, ein Zeitdrift kippt also
    # die Zeilen abwechselnd -- das Paraboloid mittelt das. Hier nur die
    # Ablage: sie darf nichts aendern.
    shifted = [(x, y, v + 50000.0)
               for x, y, v in bell2d(123.2, 130.7, 130.0, 240.0, 0.0, grid)]
    close(fit.paraboloid_fit(shifted, 123.0, 131.0, 2.0)['y'], 130.7, 1e-6,
          "paraboloid_fit: konstante Ablage verschiebt den Scheitel")
    # Sattel oder Tal -> Fehler, zu wenige Punkte -> Fehler
    try:
        fit.paraboloid_fit(bell2d(123.2, 130.7, -130.0, 240.0, 0.0, grid),
                           123.0, 131.0, 2.0)
        ok(False, "paraboloid_fit akzeptiert einen Sattel")
    except ValueError:
        ok(True, "")
    try:
        fit.paraboloid_fit(bell2d(123.2, 130.7, 130.0, 240.0, 0.0, grid[:5]),
                           120.0, 128.0, 2.0)
        ok(False, "paraboloid_fit akzeptiert 5 Punkte")
    except ValueError:
        ok(True, "")
    # Scheitel ausserhalb des Radius -> Fehler statt Extrapolation
    try:
        fit.paraboloid_fit(bell2d(126.0, 133.0, 130.0, 240.0, 0.0, grid),
                           123.0, 131.0, 2.0)
        ok(False, "paraboloid_fit extrapoliert einen Scheitel ausserhalb")
    except ValueError:
        ok(True, "")

    # --- 20: quadratische Spitzen-Extrapolation mit drei Stuetzstellen ---
    # T0 (10.5/10.7): die Kurve Scheitel ueber Spalt wird zum kleinen Spalt
    # hin steiler, linear ueber 0,75 mm hochgerechnet bleibt ein Rest.
    # Parabel p(g) = p0 + a g + b g^2 durch drei Spalte, Spitze = p0.
    P0, A_, B_ = 123.95, 0.15, 0.12          # ~0.6 mm/mm bei g = 1.9
    gs = [0.75, 1.15, 1.55]
    ps = [P0 + A_ * g + B_ * g * g for g in gs]
    close(fit.tip_extrapolate_quadratic(ps, gs), P0, 1e-9,
          "tip_extrapolate_quadratic trifft p0 nicht")
    # Auf einer Geraden stimmt sie mit der linearen ueberein
    lin = [5.0 + 0.3 * g for g in gs]
    close(fit.tip_extrapolate_quadratic(lin, gs),
          fit.tip_extrapolate(lin[0], gs[0], lin[1], gs[1]), 1e-9,
          "tip_extrapolate_quadratic weicht auf einer Geraden von der "
          "linearen ab")
    # Vier Stuetzstellen: Kleinste Quadrate, exakt bei exakter Parabel
    gs4 = [0.5, 0.9, 1.3, 1.7]
    close(fit.tip_extrapolate_quadratic([P0 + A_ * g + B_ * g * g
                                         for g in gs4], gs4), P0, 1e-9,
          "tip_extrapolate_quadratic mit 4 Punkten falsch")
    # Weniger als drei Punkte oder gleiche Spalte -> Fehler
    for bad_p, bad_g in (([1.0, 2.0], [0.5, 1.0]),
                         ([1.0, 2.0, 3.0], [0.5, 0.5, 1.0])):
        try:
            fit.tip_extrapolate_quadratic(bad_p, bad_g)
            ok(False, "tip_extrapolate_quadratic akzeptiert %s" % (bad_g,))
        except ValueError:
            ok(True, "")

    # --- 21: Basislinie am Druckerrand ---
    # Tobi (2026-09-04): fuer die Basislinie die Duese sicherheitshalber
    # kurz vor den Rand fahren, damit wirklich nichts ueber der Spule
    # steht. baseline_edge liefert das Ziel-x: die von x weiter entfernte
    # Achsgrenze, um `margin` nach innen.
    close(fit.baseline_edge(125.0, 0.0, 250.0, 10.0), 240.0, 1e-9,
          "baseline_edge geht nicht zur weiter entfernten Grenze (+X)")
    close(fit.baseline_edge(200.0, 0.0, 250.0, 10.0), 10.0, 1e-9,
          "baseline_edge geht nicht zur weiter entfernten Grenze (-X)")
    close(fit.baseline_edge(125.0, -20.0, 270.0, 10.0), 260.0, 1e-9,
          "baseline_edge rechnet mit falschen Grenzen")
    try:
        fit.baseline_edge(125.0, 120.0, 130.0, 10.0)
        ok(False, "baseline_edge erfindet ein Ziel bei zu enger Achse")
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
