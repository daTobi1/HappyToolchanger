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
# Die Spule muss dafuer die Standard-Klipper-Firmware tragen, nicht die von
# eddy-ng (anderer MCU-Befehlssatz).
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
