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


def load_config(config):
    return NozzleLocator(config)
