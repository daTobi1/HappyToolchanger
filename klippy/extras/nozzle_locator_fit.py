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
    # Randabstand ueber die POSITION, nicht den Index: ein Scan hat ~600
    # Punkte, da ist "nicht der erste Punkt" kein Kriterium. Messtag
    # 2026-09-04: Maximum einen Korb neben dem Rand, Fit auf der Flanke,
    # Scheitel 17 mm daneben. Mindestens 12,5 % der Fensterbreite Abstand
    # von beiden Raendern (1 mm bei 8 mm).
    positions = [p for p, _ in points]
    lo, hi = min(positions), max(positions)
    margin = (hi - lo) * 0.125
    peak_pos = points[peak_idx][0]
    if peak_pos - lo < margin - 1e-9 or hi - peak_pos < margin - 1e-9:
        return False, ("Scheitel liegt am Rand des Fensters (%.3f, Fenster "
                       "%.3f..%.3f) -- Bereich verfehlt, erst Grobsuche"
                       % (peak_pos, lo, hi))
    return True, ""


def bin_points(points, lo, hi, step):
    """Koerbe: fasst dicht liegende Samples eines Scans zu Gitterpunkten
    zusammen. -> [(mittlere_position, mittelwert), ...] in Sweep-Reihenfolge.

    Ein kontinuierlicher Scan liefert ~80 Samples je mm. Die Grobsuche
    (local_peak) braucht Nachbarn mit festem Abstand, und der Status soll
    kompakt bleiben. Die Koerbe liegen GANZ im Fenster: [lo, lo+step),
    [lo+step, lo+2step), ..., Mitten bei lo+step/2, ..., hi-step/2. Ein
    halber Randkorb waere schief: auf der Flanke der Glocke zieht der
    Steigungsterm den Mittelwert um ~200 Hz vom Wert in der Korbmitte weg.
    Jeder Korb meldet die MITTLERE Position seiner Samples, nicht die
    geometrische Korbmitte: liegen die Samples im Korb nicht symmetrisch,
    verschoebe die Korbmitte den Fit (6 um Schwerpunktversatz gaben im Test
    4 um am Scheitel). Leere Koerbe fehlen, statt mit 0 gefuellt zu werden;
    Samples ausserhalb [lo, hi] zaehlen nicht. Die Reihenfolge ist die des ersten Samples je
    Korb, damit ein Ruecksweep absteigend bleibt (sweep_quality prueft die
    Listenenden).
    """
    n_bins = int(round((hi - lo) / step))
    if n_bins < 1:
        raise ValueError("Fenster %.3f..%.3f kleiner als ein Korb (%.3f)"
                         % (lo, hi, step))
    sums = {}
    order = []
    for pos, val in points:
        if pos < lo - 1e-9 or pos > hi + 1e-9:
            continue
        k = int((pos - lo) / step)
        if k >= n_bins:           # pos == hi gehoert in den letzten Korb
            k = n_bins - 1
        if k not in sums:
            sums[k] = [0.0, 0.0, 0]
            order.append(k)
        sums[k][0] += pos
        sums[k][1] += val
        sums[k][2] += 1
    return [(sums[k][0] / sums[k][2], sums[k][1] / sums[k][2])
            for k in order]


def samples_to_track(samples, lookup, origin, direction, lo, hi,
                     latency=0.0):
    """Ordnet Sensor-Samples ihrer Bahnposition zu.
    -> [(bogenlaenge, wert), ...] in Zeitreihenfolge.

    samples: [(print_time, wert), ...]
    lookup:  print_time -> ((x, y, z), geschwindigkeit) oder (None, None)
    origin/direction: Bahn s = <(x, y) - origin, direction>; direction ist
             ein Einheitsvektor, fuer achsparallele Sweeps (1,0)/(0,1) mit
             origin (0,0), fuer Diagonalen (1, +-1)/sqrt2 ab dem Zentrum.
    lo/hi:   nur Samples mit lo <= s <= hi bleiben.
    latency: Sekunden, um die der Sensor VOR seinem Zeitstempel misst;
             das Sample gehoert zur Position bei t - latency.

    Samples im Stillstand (geschwindigkeit <= 0) fallen weg: Vor- und
    Nachlauf, Verweilzeiten. Zeiten ohne bekannte Bewegung ebenfalls.
    """
    track = []
    dx, dy = direction
    ox, oy = origin
    for t, val in samples:
        pos, vel = lookup(t - latency)
        if pos is None or vel is None or vel <= 0.0:
            continue
        s = (pos[0] - ox) * dx + (pos[1] - oy) * dy
        if s < lo or s > hi:
            continue
        track.append((s, val))
    return track


def scan_line(origin, direction, lo, hi, lead, through):
    """Start- und Endpunkt eines Scans in Bettkoordinaten.
    -> ((x_start, y_start), (x_end, y_end))

    Die Bahn laeuft in `direction` (Einheitsvektor) und geht durch den
    Punkt `through`; die Bogenlaenge s zaehlt ab `origin` (wie in
    samples_to_track). Start bei lo - lead, Ende bei hi + lead; fuer
    lo > hi laeuft der Scan rueckwaerts, der Vorlauf liegt dann auf der
    anderen Seite.

    Die Senkrechtkomponente kommt von `through`, nie von origin: ein
    X-Sweep mit origin (0, 0) muss auf seiner aktuellen Y-Position
    bleiben. Der erste Scan-Entwurf haette Y auf 0 gefahren.
    """
    dx, dy = direction
    ox, oy = origin
    tx, ty = through
    # Fusspunkt von `through` auf der Linie durch origin, plus dessen
    # Senkrechtabstand -> Basispunkt fuer s = 0.
    s_through = (tx - ox) * dx + (ty - oy) * dy
    base_x = tx - dx * s_through
    base_y = ty - dy * s_through
    sign = 1.0 if hi >= lo else -1.0
    s_start = lo - sign * lead
    s_end = hi + sign * lead
    return ((base_x + dx * s_start, base_y + dy * s_start),
            (base_x + dx * s_end, base_y + dy * s_end))


def raster_grid(rows, x_lo, x_hi, pitch):
    """C-Scan: Zeilen eines Rasters zu einem Gitter zusammenfassen.

    rows: [(y, [(x, wert), ...]), ...] -- je Zeile die Samples eines Scans,
          Reihenfolge beliebig (Serpentine).
    -> {'xs': Spaltenmitten, 'ys': aufsteigend, 'values': [[...], ...]}
       values[j][i] gehoert zu (xs[i], ys[j]); fehlende Spalten sind None.

    Jede Zeile wird mit bin_points in Spalten der Breite pitch gemittelt;
    die Spaltenmitte ist die geometrische Mitte (nicht die mittlere
    Sample-Position wie bei bin_points), damit alle Zeilen dasselbe
    x-Gitter teilen.
    """
    n_cols = int(round((x_hi - x_lo) / pitch))
    if n_cols < 1:
        raise ValueError("Raster schmaler als eine Spalte")
    xs = [x_lo + (i + 0.5) * pitch for i in range(n_cols)]
    ys = []
    values = []
    for y, samples in sorted(rows, key=lambda r: r[0]):
        row = [None] * n_cols
        for pos, val in bin_points(samples, x_lo, x_hi, pitch):
            i = int((pos - x_lo) / pitch)
            if i >= n_cols:
                i = n_cols - 1
            row[i] = val
        ys.append(y)
        values.append(row)
    return {'xs': xs, 'ys': ys, 'values': values}


def clamp_scan_speed(speed, span, rate, min_samples):
    """Begrenzt die Scan-Geschwindigkeit so, dass mindestens min_samples
    Samples im Fenster liegen. -> mm/s

    Samples im Fenster = rate * span / speed. Statt bei zu wenigen Samples
    abzubrechen, wird gebremst -- so macht es auch EddySeek. Bei 400 Hz und
    8 mm Fenster sind 200 Samples ab 16 mm/s unterschritten.
    """
    if min_samples <= 0:
        raise ValueError("min_samples muss positiv sein")
    limit = rate * span / float(min_samples)
    return min(speed, limit)


def baseline_side(x, offset, x_min, x_max):
    """Ziel-x fuer die Basislinie neben der Spule: um `offset` von x weg,
    bevorzugt +X, sonst -X, innerhalb [x_min, x_max]. Wirft ValueError,
    wenn beides ausserhalb liegt.

    Auf park_z steht die Duese noch 7 mm ueber der Spule und hebt die
    Basislinie um ~1.400 Hz (offene Arbeiten 8.6). Seitlich versetzt ist
    die Spule wirklich leer -- und park_z ist per Definition die freie
    Fahrhoehe, also ist der Weg dorthin sicher.
    """
    eps = 1e-9
    if x + offset <= x_max + eps:
        return x + offset
    if x - offset >= x_min - eps:
        return x - offset
    raise ValueError(
        "Kein Platz fuer die Basislinie: x=%.1f +- %.1f liegt ausserhalb "
        "von %.1f..%.1f" % (x, offset, x_min, x_max))


def tip_slope(p1, g1, p2, g2):
    """Steigung des Scheitels ueber dem Spalt, mm je mm. Gross heisst: die
    Duesenspitze sitzt nicht auf der Achse des Blocks (T0 am Messtag
    2026-09-04: 0,6 mm/mm), klein heisst zentriert (T1: -0,05)."""
    if abs(g2 - g1) < 1e-6:
        raise ValueError("Spitzen-Extrapolation braucht zwei verschiedene "
                         "Spalte, bekam %.3f und %.3f" % (g1, g2))
    return (p2 - p1) / (g2 - g1)


def tip_extrapolate(p1, g1, p2, g2):
    """Scheitel bei Spalt 0 aus zwei Messungen (p1 bei Spalt g1, p2 bei g2).

    Bei grossem Spalt misst die Spule den Heizblock, bei kleinem die
    Spitze; die Gerade durch beide Messungen trifft bei Spalt 0 die
    Spitze -- die auch die Kamera sieht. Nebenbei faellt jede additive
    Stoerung heraus, die mit dem Spalt schwaecher wird. Linear ist eine
    Naeherung: zum kleinen Spalt hin wird die Kurve steiler (offene
    Arbeiten 10.5), also beide Spalte so klein wie moeglich waehlen.
    """
    return p1 - tip_slope(p1, g1, p2, g2) * g1


def _solve(matrix, rhs):
    """Gauss mit Spaltenpivot fuer kleine, dichte Systeme (6x6)."""
    n = len(rhs)
    m = [list(row) + [rhs[i]] for i, row in enumerate(matrix)]
    for col in range(n):
        piv = max(range(col, n), key=lambda r: abs(m[r][col]))
        if abs(m[piv][col]) < 1e-12:
            raise ValueError("Fit nicht loesbar (entartete Punktlage)")
        m[col], m[piv] = m[piv], m[col]
        for r in range(n):
            if r == col:
                continue
            f = m[r][col] / m[col][col]
            if f:
                for k in range(col, n + 1):
                    m[r][k] -= f * m[col][k]
    return [m[i][n] / m[i][i] for i in range(n)]


def paraboloid_fit(points, cx, cy, radius):
    """2D-Scheitel aus einem kleinen Raster: Kleinstequadrate-Paraboloid
    z = c + gx x + gy y + axx x^2 + ayy y^2 + axy xy (x, y relativ zu
    cx, cy) ueber alle Punkte im Umkreis `radius`.
    -> {'x', 'y', 'axx', 'ayy', 'axy', 'rho', 'n'}; Kruemmungen positiv
    fuer einen Hochpunkt, rho = axy / (2 sqrt(axx ayy)) der Kreuzterm
    (6.4), n die Zahl der benutzten Punkte.

    Nutzt alle Rasterpunkte und den Kreuzterm zugleich -- zwei
    achsparallele Linien sehen den Kreuzterm nicht und messen bei einer
    verkippten Glocke systematisch daneben. Der Radius haelt den Fit auf
    dem Buckel (der ist keine Parabel, die Flanken kippen den Scheitel).
    Wirft ValueError bei zu wenigen Punkten, einem Sattel oder Tal, oder
    wenn der Scheitel ausserhalb des Radius liegt (dann war die
    Vorhersage zu weit weg: Grobsuche wiederholen statt extrapolieren).
    """
    rows = []
    for x0, y0, z in points:
        x, y = x0 - cx, y0 - cy
        if x * x + y * y > radius * radius:
            continue
        rows.append(([1.0, x, y, x * x, y * y, x * y], z))
    n = len(rows)
    if n < 6:
        raise ValueError("2D-Fit braucht mindestens 6 Punkte im Radius, "
                         "hat %d" % n)
    S = [[0.0] * 6 for _ in range(6)]
    t = [0.0] * 6
    for f, z in rows:
        for a in range(6):
            t[a] += f[a] * z
            fa = f[a]
            Sa = S[a]
            for b in range(6):
                Sa[b] += fa * f[b]
    c, gx, gy, axx, ayy, axy = _solve(S, t)
    if axx >= 0.0 or ayy >= 0.0:
        raise ValueError("2D-Fit hat keinen Hochpunkt (a_xx=%.4g, a_yy=%.4g)"
                         % (axx, ayy))
    det = 4.0 * axx * ayy - axy * axy
    if det <= 0.0:
        raise ValueError("2D-Fit ist ein Sattel (Kreuzterm zu gross)")
    # grad = 0: [2axx axy; axy 2ayy] [vx; vy] = -[gx; gy]
    vx = (-gx * 2.0 * ayy + gy * axy) / det
    vy = (-gy * 2.0 * axx + gx * axy) / det
    if vx * vx + vy * vy > radius * radius:
        raise ValueError("2D-Scheitel liegt ausserhalb des Radius (%.2f mm "
                         "von der Rastermitte) -- Grobsuche wiederholen"
                         % ((vx * vx + vy * vy) ** 0.5))
    a, b = -axx, -ayy
    return {'x': cx + vx, 'y': cy + vy, 'axx': a, 'ayy': b, 'axy': -axy,
            'rho': -axy / (2.0 * (a * b) ** 0.5), 'n': n}


def tip_extrapolate_quadratic(positions, gaps):
    """Spitze aus drei oder mehr Messungen: Kleinstequadrate-Parabel
    p(g) = p0 + a g + b g^2 ueber den Spalt, Rueckgabe p0 (Spalt 0).

    Fuer Tools, deren Scheitel stark und nicht linear mit dem Spalt
    wandert (T0 am Messtag 2026-09-04: zum kleinen Spalt hin steiler).
    Drei Stuetzstellen legen die Parabel exakt; mehr glaetten. Die
    Hochrechnung verstaerkt Rauschen -- Spalte so klein und Stuetzstellen
    so weit auseinander wie moeglich.
    """
    n = len(positions)
    if n < 3 or len(gaps) != n:
        raise ValueError("Quadratische Extrapolation braucht mindestens "
                         "drei Spalte, hat %d" % n)
    if max(gaps) - min(gaps) < 1e-6 or len(set(round(g, 6)
                                                for g in gaps)) < 3:
        raise ValueError("Quadratische Extrapolation braucht drei "
                         "verschiedene Spalte, bekam %s" % (gaps,))
    S = [[0.0] * 3 for _ in range(3)]
    t = [0.0] * 3
    for p, g in zip(positions, gaps):
        f = [1.0, g, g * g]
        for a in range(3):
            t[a] += f[a] * p
            for b in range(3):
                S[a][b] += f[a] * f[b]
    return _solve(S, t)[0]


def recenter_target(vertex, centre, tol):
    """Erst zentrieren, dann messen (Tobi, 2026-09-04): liegt der
    gefittete Scheitel weiter als `tol` von der Rastermitte entfernt,
    ist das Fenster schief ueber dem Buckel gestanden -- bei einem
    asymmetrischen Buckel (T0: Platine und schiefe Duese) verschiebt
    das den Fit. Dann wird das Raster auf den Scheitel nachzentriert.
    -> (x, y) der neuen Mitte oder None, wenn es passt."""
    dx = vertex[0] - centre[0]
    dy = vertex[1] - centre[1]
    if (dx * dx + dy * dy) ** 0.5 <= tol:
        return None
    return (vertex[0], vertex[1])


def baseline_edge(x, x_min, x_max, margin):
    """Ziel-x fuer die Basislinie am Druckerrand: die von x weiter
    entfernte Achsgrenze, um `margin` nach innen. Tobi (2026-09-04):
    sicherheitshalber kurz vor den Rand, damit wirklich nichts ueber der
    Spule steht. Wirft ValueError, wenn die Achse dafuer zu eng ist."""
    lo, hi = x_min + margin, x_max - margin
    if hi <= lo:
        raise ValueError("Achse zu eng fuer eine Basislinie am Rand "
                         "(%.1f..%.1f, Rand %.1f)" % (x_min, x_max, margin))
    return hi if (hi - x) >= (x - lo) else lo
