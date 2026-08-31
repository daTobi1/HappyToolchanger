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
