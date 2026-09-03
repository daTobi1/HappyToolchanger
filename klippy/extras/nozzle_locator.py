# Laterale Ortung einer Duese ueber einer LDC1612-Spule, die der Nutzer
# unter die stehende Duese stellt.
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
# Die Spule muss dafuer die Standard-Klipper-Firmware tragen, nicht die von
# eddy-ng (anderer MCU-Befehlssatz).
#
# Positionierung (Spec R-B'): nicht die Halterung hat eine feste Position,
# sondern der Kopf faehrt auf eine einstellbare Anfahrposition (park_x/y/z,
# Default Bettmitte und Z 60), und der Nutzer stellt die Sonde samt
# Halterung danach grob mittig darunter. park_z ist Freihoehe zum
# Unterschieben und zugleich die Fahrhoehe aller Verfahrwege.
#
# Copyright (C) 2026  HappyToolchanger
# This file may be distributed under the terms of the GNU GPLv3 license.
import logging

from . import ldc1612
from . import nozzle_locator_fit as fit


class NozzleLocator:
    def __init__(self, config):
        self.printer = config.get_printer()
        self.gcode = self.printer.lookup_object('gcode')
        self.name = config.get_name()

        # Kein calibration-Argument: es gibt keine Hoehenkarte.
        self.sensor = ldc1612.LDC1612(config)

        # Anfahrposition. X/Y optional -- fehlen sie, Bettmitte aus den
        # Achsgrenzen (siehe park_position). Ein eigenes safe_z gibt es
        # nicht: park_z ist die Fahrhoehe.
        self.park_x = config.getfloat('park_x', None)
        self.park_y = config.getfloat('park_y', None)
        self.park_z = config.getfloat('park_z', 60.0, above=0.)
        self.search_span = config.getfloat('search_span', 30.0, above=0.)
        self.holder_top_z = config.getfloat('holder_top_z', 0.0, minval=0.)
        self.min_gap = config.getfloat('min_gap', 0.5, above=0.)

        # Messparameter
        self.sweep_span = config.getfloat('sweep_span', 8.0, above=0.)
        self.sweep_step = config.getfloat('sweep_step', 1.0, above=0.)
        self.dwell_time = config.getfloat('dwell_time', 0.5, above=0.)
        self.runs = config.getint('runs', 3, minval=1)
        self.runs_tolerance = config.getfloat('runs_tolerance', 0.05, above=0.)
        # X->Y-Runden gegen die Kreuzkopplung der 2D-Glocke. 1 genuegt, wenn
        # die Glocke achsparallel liegt; ob 2 noetig ist, misst
        # NOZZLE_LOCATE AXIS=DIAG einmalig.
        self.xy_iterations = config.getint('xy_iterations', 1,
                                           minval=1, maxval=4)
        self.min_amplitude = config.getfloat('min_amplitude', 2000., above=0.)
        self.target_amplitude = config.getfloat('target_amplitude', 6000.,
                                                above=0.)
        self.max_offset = config.getfloat('max_offset', 5.0, above=0.)
        self.move_speed = config.getfloat('move_speed', 60., above=0.)
        self.approach_speed = config.getfloat('approach_speed', 5., above=0.)

        if self.park_z <= self._z_floor():
            raise config.error(
                "nozzle_locator: park_z (%.1f) muss ueber holder_top_z + "
                "min_gap (%.1f) liegen" % (self.park_z, self._z_floor()))

        # Bettmitte fuer den Default von park_x/park_y: die Achsgrenzen
        # taugen dafuer nicht, bei Toolchangern liegen die Docks weit
        # ausserhalb des Betts (250er: Y bis -85). Das Bettmesh kennt das
        # Bett; die Achsgrenzen bleiben Rueckfall.
        self.bed_center = None
        if config.has_section('bed_mesh'):
            bm = config.getsection('bed_mesh')
            try:
                mn = bm.getfloatlist('mesh_min', count=2)
                mx = bm.getfloatlist('mesh_max', count=2)
                self.bed_center = ((mn[0] + mx[0]) / 2., (mn[1] + mx[1]) / 2.)
            except Exception:
                logging.info("nozzle_locator: bed_mesh ohne mesh_min/max, "
                             "Bettmitte aus den Achsgrenzen")

        # Laufzeitzustand, ueber get_status sichtbar
        self.state = 'idle'
        self.last_freq = 0.0
        self.last_errors = 0
        self.last_points = []
        # Zuletzt per NOZZLE_LOCATOR_PARK angefahrene Position. Der
        # Messlauf nimmt sie, damit eine im Assistenten geaenderte Position
        # ohne FIRMWARE_RESTART gilt.
        self.parked = None

        self.gcode.register_command(
            'NOZZLE_LOCATOR_READ', self.cmd_READ, desc=self.cmd_READ_help)
        self.gcode.register_command(
            'NOZZLE_LOCATOR_PARK', self.cmd_PARK, desc=self.cmd_PARK_help)
        self.gcode.register_command(
            'NOZZLE_LOCATE', self.cmd_LOCATE, desc=self.cmd_LOCATE_help)

    def get_status(self, eventtime):
        # 'park' ist die Vorbelegung fuer den Assistenten. Solange die
        # Kinematik noch nicht steht, bleibt X/Y das, was die Config sagt.
        try:
            park = list(self.park_position())
        except Exception:
            park = [self.park_x, self.park_y, self.park_z]
        return {
            'state': self.state,
            'last_freq': self.last_freq,
            'errors': self.last_errors,
            'points': list(self.last_points),
            'park': park,
            'parked': list(self.parked) if self.parked else None,
            'min_amplitude': self.min_amplitude,
            'target_amplitude': self.target_amplitude,
        }

    # ------------------------------------------------------------------
    # Bewegung
    # ------------------------------------------------------------------
    def _require_homed(self):
        toolhead = self.printer.lookup_object('toolhead')
        now = self.printer.get_reactor().monotonic()
        homed = toolhead.get_status(now).get('homed_axes', '')
        missing = [a for a in 'xyz' if a not in homed]
        if missing:
            raise self.printer.command_error(
                "nozzle_locator: Achse(n) %s nicht gehomt -- dieses Modul "
                "homt nie selbst (Halterung koennte auf dem Bett stehen)"
                % ''.join(missing).upper())

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
    # Anfahrposition
    # ------------------------------------------------------------------
    def park_position(self):
        """(x, y, z) der Anfahrposition; X/Y aus der Config, sonst Bettmitte."""
        x, y = self.park_x, self.park_y
        if x is None or y is None:
            if self.bed_center is not None:
                cx, cy = self.bed_center
            else:
                toolhead = self.printer.lookup_object('toolhead')
                now = self.printer.get_reactor().monotonic()
                status = toolhead.get_status(now)
                mn, mx = status['axis_minimum'], status['axis_maximum']
                cx, cy = (mn[0] + mx[0]) / 2., (mn[1] + mx[1]) / 2.
            if x is None:
                x = cx
            if y is None:
                y = cy
        return x, y, self.park_z

    def park(self, x, y, z):
        """Faehrt das montierte Tool auf die Anfahrposition. Homt nie."""
        self._require_homed()
        if z <= self._z_floor():
            raise self.printer.command_error(
                "nozzle_locator: Z %.1f liegt nicht ueber dem Z-Boden %.1f "
                "(holder_top_z + min_gap)" % (z, self._z_floor()))
        # Erst hoch, dann seitlich -- nie diagonal durch etwas hindurch.
        self._move([None, None, z], self.move_speed)
        self._move([x, y, None], self.move_speed)
        self.parked = (x, y, z)

    cmd_PARK_help = ("Move the mounted tool to the XY probe park position "
                     "without measuring. Parameters: X, Y, Z (default: "
                     "config, else bed centre and park_z)")

    def cmd_PARK(self, gcmd):
        px, py, pz = self.park_position()
        x = gcmd.get_float('X', px)
        y = gcmd.get_float('Y', py)
        z = gcmd.get_float('Z', pz, above=0.)
        self.park(x, y, z)
        gcmd.respond_info("nozzle_locator: Anfahrposition X%.1f Y%.1f Z%.1f "
                          "erreicht -- Sonde jetzt grob mittig unter die "
                          "Duese stellen" % (x, y, z))

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
        # Klippers ldc1612 meldet 'errors' als kumulierten Zaehler seit
        # Messstart, nicht pro Batch -- also den letzten Wert nehmen.
        errors = [0]
        # Abmelden: BatchBulkHelper entfernt genau den Callback, der False
        # liefert. Ein zweiter Callback wuerde den ersten nicht loswerden,
        # der Sensor liefe endlos weiter. Deshalb ein Flag im selben
        # Callback -- so macht es auch Klippers LDC_CALIBRATE_DRIVE_CURRENT.
        active = [True]

        def handle_batch(msg):
            if not active[0]:
                return False
            errors[0] = max(errors[0], msg.get('errors', 0))
            # Klippers ldc1612 liefert (print_time, freq, dummy_height).
            for sample in msg['data']:
                collected.append(sample[1])
            return True

        self.sensor.add_client(handle_batch)
        try:
            toolhead = self.printer.lookup_object('toolhead')
            toolhead.dwell(duration)
            toolhead.wait_moves()
        finally:
            active[0] = False

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

    # ------------------------------------------------------------------
    # Messschritte
    # ------------------------------------------------------------------
    def measure_baseline(self):
        """Freiluft-Basislinie. Der Aufrufer stellt sicher, dass die Duese
        weit von der Spule weg ist -- auf park_z (60 mm) ist sie praktisch
        unsichtbar. Eine Basislinie mit der Duese in Reichweite ist der
        Fehler, der im Vorversuch 12 kHz Versatz erzeugt hat.
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

        Faehrt nie unter holder_top_z + min_gap. Von park_z herab ist der
        Weg lang: bis 5 mm ueber die Halterung in 5-mm-Schritten, dann
        tasten. Rueckgabe: erreichtes Z.
        """
        if target_amplitude is None:
            target_amplitude = self.target_amplitude
        self.state = 'approaching'
        toolhead = self.printer.lookup_object('toolhead')
        floor = self._z_floor()
        z = toolhead.get_position()[2]
        coarse_until = self.holder_top_z + 5.0
        step = 1.0
        while z > floor:
            if z - 5.0 > coarse_until:
                z = z - 5.0
                self._move([None, None, z], self.move_speed)
            else:
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
            "(Signal %.0f Hz, noetig %.0f). Steht die Sonde unter der "
            "Duese, und stimmt holder_top_z?"
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
        # Vorlauf: 3 Schritte vor den ersten Punkt, gleiche Richtung
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

        centers, fwds, revs, curvs = [], [], [], []
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
            try:
                v_fwd, k_fwd = fit.parabola_fit(fwd)
                v_rev, k_rev = fit.parabola_fit(rev)
            except ValueError as e:
                raise self.printer.command_error(
                    "nozzle_locator %s: Fit fehlgeschlagen: %s" % (axis, e))
            centers.append((v_fwd + v_rev) / 2.0)
            fwds.append(v_fwd)
            revs.append(v_rev)
            curvs.append((k_fwd + k_rev) / 2.0)

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
            'curvature': sum(curvs) / len(curvs),
        }

    def measure_coupling(self, center_x, center_y, baseline, runs=None):
        """Misst den Kreuzterm der 2D-Quadrik ueber zwei Diagonal-Sweeps.

        Achsparallele Sweeps liefern nur a und b. Der Kreuzterm c ist fuer
        sie unsichtbar, verschiebt ihren Scheitel aber um (c/a)*(y1-y0).
        Ueber die Diagonalen faellt er heraus:
            Kruemmung( 45 Grad) = (a + b + 2c)/2
            Kruemmung(135 Grad) = (a + b - 2c)/2
        Rueckgabe: {'a','b','c','rho'} mit rho = c/sqrt(a*b).

        Diagnose, kein Teil der Messroutine: einmal fahren, rho ansehen,
        danach entscheiden ob die Routine aufwendiger werden muss.
        """
        rx = self.locate('X', center_x, baseline, runs=runs)
        self._move([rx['center'], None, None], self.move_speed)
        ry = self.locate('Y', center_y, baseline, runs=runs)
        self._move([None, ry['center'], None], self.move_speed)
        a, b = rx['curvature'], ry['curvature']
        k45 = self._diagonal_curvature(rx['center'], ry['center'],
                                       baseline, +1)
        k135 = self._diagonal_curvature(rx['center'], ry['center'],
                                        baseline, -1)
        c = (k45 - k135) / 2.0   # (a+b+2c)/2 - (a+b-2c)/2 = 2c
        denom = (a * b) ** 0.5 if a > 0 and b > 0 else 0.0
        rho = c / denom if denom > 0 else 0.0
        self._move([rx['center'], ry['center'], None], self.move_speed)
        return {'a': a, 'b': b, 'c': c, 'rho': rho}

    def _diagonal_curvature(self, cx, cy, baseline, sign):
        """Ein Sweep entlang (1, sign)/sqrt(2) durch (cx, cy).

        Bewegt X und Y gemeinsam. Die Positionsachse des Fits ist die
        Bogenlaenge entlang der Diagonalen, nicht x oder y allein -- sonst
        stimmt die Kruemmung um Faktor 2 nicht.
        """
        self.state = 'sweeping'
        half = self.sweep_span / 2.0
        n_steps = int(round(self.sweep_span / self.sweep_step)) + 1
        root2 = 2.0 ** 0.5
        # Vorlauf in Sweeprichtung, wie bei den achsparallelen Sweeps
        s0 = -half - 3.0 * self.sweep_step
        self._move([cx + s0 / root2, cy + sign * s0 / root2, None],
                   self.move_speed)
        pts = []
        self.last_points = []
        for i in range(n_steps):
            s = -half + i * self.sweep_step          # Bogenlaenge
            dx = s / root2
            dy = sign * s / root2
            self._move([cx + dx, cy + dy, None], self.move_speed)
            mean, sd, n, errors = self.read_frequency()
            pts.append((s, mean))
            self.last_points.append((round(s, 4), round(mean, 1)))
        good, reason = fit.sweep_quality(pts, baseline, self.min_amplitude)
        if not good:
            raise self.printer.command_error(
                "nozzle_locator Diagonale (%s45): %s"
                % ("+" if sign > 0 else "-", reason))
        try:
            return fit.parabola_fit(pts)[1]
        except ValueError as e:
            raise self.printer.command_error(
                "nozzle_locator Diagonale: Fit fehlgeschlagen: %s" % e)

    def _coupling_advice(self, rho):
        """Sagt, was der gemessene rho-Wert fuer die Messroutine bedeutet.

        Der Restfehler einer X->Y-Sequenz schrumpft pro voller Runde um
        rho^2. Die ZUERST gemessene Achse erbt dagegen rho*sqrt(b/a) mal den
        Fehler der Grobsuche -- sie ist also um etwa 1/rho schlechter als die
        zweite. Genau das behebt XY_ITERATIONS=2.
        """
        r = abs(rho)
        if r < 0.1:
            return ("Kopplung vernachlaessigbar. Eine X->Y-Sequenz genuegt, "
                    "xy_iterations: 1 ist richtig.")
        if r < 0.35:
            return ("Kopplung merklich. Die zuerst gemessene Achse ist rund "
                    "%.0fx schlechter als die zweite -- xy_iterations: 2 "
                    "verwenden, das kostet einen Sweep und gleicht beide an."
                    % (1.0 / r))
        return ("Kopplung stark (rho=%.2f). Die Glocke ist deutlich gegen "
                "die Achsen verkippt. xy_iterations: 2 reicht hier "
                "moeglicherweise nicht; ein 2D-Gitterfit waere der saubere "
                "Weg. Ergebnis in docs/xy-offset-offene-arbeiten.md "
                "festhalten." % r)

    cmd_LOCATE_help = (
        "Locate a nozzle laterally over the XY probe coil. Parameters: "
        "AXIS (X, Y or DIAG), REPEATS (runs, default from config), SPAN, "
        "STEP. AXIS=DIAG runs both diagonals and reports the cross-term of "
        "the 2D peak (diagnostic only). Requires homed axes, the coil "
        "placed under the nozzle and the nozzle already lowered to the "
        "measuring height (see approach_z / CALIBRATE_XY_OFFSETS).")

    def cmd_LOCATE(self, gcmd):
        axis = gcmd.get('AXIS', 'X').upper()
        if axis not in ('X', 'Y', 'DIAG'):
            raise gcmd.error("AXIS muss X, Y oder DIAG sein")
        runs = gcmd.get_int('REPEATS', self.runs, minval=1)
        span = gcmd.get_float('SPAN', self.sweep_span, above=0.)
        step = gcmd.get_float('STEP', self.sweep_step, above=0.)
        self._require_homed()

        toolhead = self.printer.lookup_object('toolhead')
        here = toolhead.get_position()
        if here[2] < self._z_floor():
            raise gcmd.error(
                "nozzle_locator: Z %.3f liegt unter dem Z-Boden %.3f"
                % (here[2], self._z_floor()))
        # Basislinie: der Aufrufer steht schon ueber der Spule. Auf park_z
        # ist die Spule praktisch unsichtbar -- also hoch, messen, wieder
        # herunter. Kein seitliches Wegfahren noetig.
        self._move([None, None, self.park_z], self.move_speed)
        baseline = self.measure_baseline()
        self._move([None, None, here[2]], self.approach_speed)
        try:
            if axis == 'DIAG':
                r = self.measure_coupling(here[0], here[1], baseline,
                                          runs=runs)
                gcmd.respond_info(
                    "nozzle_locator Kopplung: a=%.1f b=%.1f c=%.1f Hz/mm^2, "
                    "rho=%.3f" % (r['a'], r['b'], r['c'], r['rho']))
                gcmd.respond_info(self._coupling_advice(r['rho']))
                return
            center = here[0 if axis == 'X' else 1]
            result = self.locate(axis, center, baseline, runs=runs,
                                 span=span, step=step)
            gcmd.respond_info(
                "nozzle_locator %s: %.4f mm (hin %.4f, rueck %.4f, "
                "Differenz %.1f um = gemessener Drift-Bias; Spannweite "
                "ueber %d Laeufe %.1f um)"
                % (axis, result['center'], result['fwd'], result['rev'],
                   (result['fwd'] - result['rev']) * 1000., runs,
                   result['spread'] * 1000.))
            coord = [None, None, None]
            coord[0 if axis == 'X' else 1] = result['center']
            self._move(coord, self.move_speed)
        finally:
            self.state = 'idle'


def load_config(config):
    return NozzleLocator(config)
