# Fit-Helfer fuer die laterale Ortung einer Duese ueber einer Eddy-Spule.
#
# Bewusst ohne Klipper-Importe und ohne numpy: das macht diesen Teil ohne
# Drucker testbar, und er ist die einzige Stelle, an der die Drift-Korrektur
# lebt.
#
# Copyright (C) 2026  HappyToolchanger
# This file may be distributed under the terms of the GNU GPLv3 license.


def parabola_fit(points):
    """Scheitel UND Kruemmung der Kleinstequadrate-Parabel durch
    [(pos, wert), ...]. Rueckgabe: (scheitel, kruemmung), kruemmung positiv
    fuer einen Hochpunkt (also -a des gefitteten y = a x^2 + b x + c).

    Die Kruemmung braucht der Diagonal-Sweep: aus den Kruemmungen bei 45
    und 135 Grad faellt der Kreuzterm der 2D-Quadrik heraus, den
    achsparallele Sweeps prinzipiell nicht sehen koennen.

    Invariant gegen eine *konstante* Ablage: die verschiebt nur c, nicht
    -b/(2a). Ein Drift, der *linear in x* ist, verschiebt den Scheitel
    dagegen um m/(2a) -- siehe bidirectional_center.

    Die x-Werte werden um ihren Mittelwert zentriert, bevor gefixt wird.
    Bei realen Bettkoordinaten (120-300 mm, weit vom Ursprung) verbessert
    das die Kondition der Normalgleichungsmatrix erheblich.
    """
    n = len(points)
    if n < 3:
        raise ValueError("Fit braucht mindestens 3 Punkte, hat %d" % n)
    # x um seinen Mittelwert zentrieren fuer bessere Kondition.
    xbar = sum(x for x, _ in points) / n
    sx = sx2 = sx3 = sx4 = sy = sxy = sx2y = 0.0
    for x, y in points:
        xc = x - xbar
        xc2 = xc * xc
        sx += xc
        sx2 += xc2
        sx3 += xc2 * xc
        sx4 += xc2 * xc2
        sy += y
        sxy += xc * y
        sx2y += xc2 * y
    # Normalgleichungen fuer y = a xc^2 + b xc + c, geloest per Cramer.
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
    return xbar - b / (2.0 * a), -a


def parabola_vertex(points):
    """Nur der Scheitel. Duenner Wrapper um parabola_fit."""
    return parabola_fit(points)[0]


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


def local_peak(points, near, baseline, min_amplitude):
    """Grobsuche: der lokale Buckel, der `near` am naechsten liegt.

    Das globale Maximum taugt hier nicht. Am 250er (2026-09-03) lag die
    Duese bei Y 130 als Buckel von +6.000 Hz -- und 8 mm weiter vorn stieg
    das Signal auf +100.000 Hz, weil dort der Heizblock ueber die Spule
    kam. Ein Sweepfenster, das den Block streift, hat sein Maximum am Rand
    und trotzdem einen brauchbaren Duesen-Buckel in der Mitte.

    Kandidaten sind innere Punkte, die groesser als beide Nachbarn sind
    (Plateau auf einer Seite erlaubt) und mindestens min_amplitude ueber
    der Basislinie liegen. Gewaehlt wird der Kandidat mit dem kleinsten
    Abstand zu `near`; sein Scheitel wird ueber die drei Punkte um ihn
    herum parabolisch verfeinert. Rueckgabe: (position, amplitude).
    Wirft ValueError, wenn es keinen Buckel gibt.
    """
    n = len(points)
    if n < 3:
        raise ValueError("Grobsuche braucht mindestens 3 Punkte, hat %d" % n)
    amps = [v - baseline for _, v in points]
    best = None
    for i in range(1, n - 1):
        a = amps[i]
        if a < min_amplitude:
            continue
        left, right = amps[i - 1], amps[i + 1]
        if not ((a > left and a >= right) or (a >= left and a > right)):
            continue
        dist = abs(points[i][0] - near)
        if best is None or dist < best[0]:
            best = (dist, i)
    if best is None:
        raise ValueError(
            "Kein lokaler Scheitel ueber %.0f Hz im Fenster -- steht die "
            "Sonde unter der Duese?" % min_amplitude)
    i = best[1]
    trio = points[i - 1:i + 2]
    try:
        pos = parabola_vertex(trio)
    except ValueError:
        pos = points[i][0]
    # Der Scheitel muss zwischen den Nachbarn bleiben; sonst ist das Trio
    # kein Buckel, sondern eine Kante, und der Rohpunkt ist ehrlicher.
    lo, hi = min(trio[0][0], trio[2][0]), max(trio[0][0], trio[2][0])
    if not (lo <= pos <= hi):
        pos = points[i][0]
    return pos, amps[i]


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
