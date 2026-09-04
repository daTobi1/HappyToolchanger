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
import json
import logging
import os
import time

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
        # Spalt der Feinmessung ueber der Halterungsoberkante, wenn die
        # Duesenlaengen aus den Z-Switch-Daten bekannt sind. Klein, weil der
        # Heizblock den Scheitel um ~240 um je mm Spalt verzieht; setzt
        # eine auf 0,2 mm genau gemessene holder_top_z voraus.
        self.fine_gap = config.getfloat('fine_gap', 0.75, above=0.)

        # Messparameter
        self.sweep_span = config.getfloat('sweep_span', 8.0, above=0.)
        self.sweep_step = config.getfloat('sweep_step', 1.0, above=0.)
        self.dwell_time = config.getfloat('dwell_time', 0.5, above=0.)
        # Kontinuierlicher Scan: der Kopf faehrt mit scan_speed durch das
        # Fenster, der Sensor laeuft mit (~400 Samples/s), jedes Sample wird
        # ueber seinen Zeitstempel der Bahnposition zugeordnet. Bei 5 mm/s
        # sind das ~80 Samples je mm statt einem Punkt je sweep_step, und
        # ein Sweep dauert 2 s statt 8. scan_speed: 0 schaltet auf den
        # alten Punktmodus (anfahren, verweilen, mitteln) zurueck.
        self.scan_speed = config.getfloat('scan_speed', 5.0, minval=0.)
        # Der LDC1612 integriert VOR seinem Zeitstempel (Wandlung 2,5 ms
        # bei 400 Hz). Im Hinsweep erscheint der Scheitel dadurch um
        # v*latency zu weit vorn, im Ruecksweep zu weit hinten -- der
        # bidirektionale Mittelwert hebt das auf. Wer die Latenz kennt
        # (Hin-Rueck-Differenz bei zwei Geschwindigkeiten), traegt sie hier
        # ein; dann stimmen auch die Einzelsweeps.
        self.sample_latency = config.getfloat('sample_latency', 0.0,
                                              minval=0.)
        # Mindestzahl Samples je Scan-Fenster. Liegt scan_speed darueber,
        # wird gebremst statt abgebrochen (fit.clamp_scan_speed).
        self.min_samples = config.getint('min_samples', 200, minval=3)
        # Seitlicher Versatz in X fuer die Basislinie: auf park_z steht die
        # Duese noch 7 mm ueber der Spule und hebt die Basislinie um
        # ~1.400 Hz. Neben der Spule ist sie wirklich leer.
        self.baseline_offset = config.getfloat('baseline_offset', 40.0,
                                               minval=0.)
        # Aufwaermzeit des Sensors vor der ersten Messung eines
        # Kalibrierlaufs: der erste Lauf nach dem Sensorstart lag am Messtag
        # 80 um daneben (Spule kalt, Kruemmung 15 % kleiner); nach ~1 min
        # Dauerbetrieb stabil. CALIBRATE_XY_OFFSETS haelt den Sensor ueber
        # den ganzen Lauf und wartet beim ersten Halten so lange.
        self.warmup_time = config.getfloat('warmup_time', 60.0, minval=0.)
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
        self.max_offset = config.getfloat('max_offset', 8.0, above=0.)
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
        # Rohdaten der letzten Sweeps fuer NOZZLE_LOCATOR_DUMP: jeder
        # Eintrag ein Sweep mit allen Samples. Bewusst nicht im Status --
        # 600 Samples je Sweep haben in Moonrakers Polling nichts verloren.
        self.sweep_log = []
        self.sweep_log_limit = 64
        # Sensor-Haltung im Scanmodus (siehe _hold_sensor)
        self._hold_count = 0
        self._hold_flag = None
        # Dateiname des letzten Rasters (NOZZLE_LOCATOR_MAP), fuer den Viewer
        self.last_map_file = None
        self.last_baseline = None
        # Laufendes Raster fuer die Live-3D-Ansicht der Webapp
        self.live_map = None
        # Zuletzt per NOZZLE_LOCATOR_PARK angefahrene Position. Der
        # Messlauf nimmt sie, damit eine im Assistenten geaenderte Position
        # ohne FIRMWARE_RESTART gilt.
        self.parked = None

        self.gcode.register_command(
            'NOZZLE_LOCATOR_READ', self.cmd_READ, desc=self.cmd_READ_help)
        self.gcode.register_command(
            'NOZZLE_LOCATOR_DUMP', self.cmd_DUMP, desc=self.cmd_DUMP_help)
        self.gcode.register_command(
            'NOZZLE_LOCATOR_MAP', self.cmd_MAP, desc=self.cmd_MAP_help)
        self.gcode.register_command(
            'NOZZLE_LOCATOR_CALIBRATE_DRIVE', self.cmd_CALIBRATE_DRIVE,
            desc=self.cmd_CALIBRATE_DRIVE_help)
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
            'scan_speed': self.scan_speed,
            'sweeps_logged': len(self.sweep_log),
            'last_map_file': self.last_map_file,
            'last_baseline': self.last_baseline,
            'drive_current': self.sensor.dccal.get_drive_current(),
            'map': self.live_map,
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

    cmd_DUMP_help = ("Write the raw samples of the last sweeps (up to 64) "
                     "as JSON into the log directory and clear the buffer. "
                     "Parameters: FILE (name, default nozzle_locator_"
                     "<timestamp>.json), KEEP=1 keeps the buffer")

    def cmd_DUMP(self, gcmd):
        if not self.sweep_log:
            gcmd.respond_info("nozzle_locator: keine Sweeps im Puffer")
            return
        path = self._write_json(gcmd, gcmd.get('FILE', None), "nozzle_locator",
                                {'sweeps': self.sweep_log,
                                 'scan_speed': self.scan_speed,
                                 'sweep_step': self.sweep_step,
                                 'holder_top_z': self.holder_top_z})
        n = len(self.sweep_log)
        if not gcmd.get_int('KEEP', 0):
            del self.sweep_log[:]
        gcmd.respond_info("nozzle_locator: %d Sweeps nach %s geschrieben"
                          % (n, path))

    def _write_json(self, gcmd, name, prefix, data):
        """Schreibt data als JSON ins Log-Verzeichnis (Moonraker liefert es
        unter /server/files/logs/<name> aus). Rueckgabe: voller Pfad."""
        if not name:
            name = "%s_%s.json" % (prefix, time.strftime("%Y%m%d_%H%M%S"))
        log_file = self.printer.get_start_args().get('log_file')
        log_dir = os.path.dirname(log_file) if log_file else "/tmp"
        path = os.path.join(log_dir, os.path.basename(name))
        try:
            with open(path, "w") as f:
                json.dump(data, f)
        except (IOError, OSError) as e:
            raise gcmd.error("nozzle_locator: Schreiben nach %s "
                             "fehlgeschlagen: %s" % (path, e))
        return path

    cmd_MAP_help = (
        "Raster scan (C-scan) of the metal above the coil, centred on the "
        "current position. Parameters: WIDTH, HEIGHT (mm, default 20), "
        "PITCH (line spacing, default 1), SPEED (mm/s, default 2x "
        "scan_speed), X, Y (centre, default current), BASELINE=0 skips the "
        "free-air reading at park_z, LABEL (e.g. T0), FILE (name in the log "
        "directory). Writes JSON for webapp/map.html. Requires homed axes "
        "and the nozzle already at measuring height.")

    def cmd_MAP(self, gcmd):
        if not self._scanning():
            raise gcmd.error("NOZZLE_LOCATOR_MAP braucht den Scanmodus "
                             "(scan_speed > 0)")
        width = gcmd.get_float('WIDTH', 20., above=0.)
        height = gcmd.get_float('HEIGHT', 20., above=0.)
        pitch = gcmd.get_float('PITCH', 1.0, above=0.)
        speed = gcmd.get_float('SPEED', self.scan_speed * 2.0, above=0.)
        label = gcmd.get('LABEL', '')
        name = gcmd.get('FILE', None)
        self._require_homed()
        toolhead = self.printer.lookup_object('toolhead')
        here = toolhead.get_position()
        cx = gcmd.get_float('X', here[0])
        cy = gcmd.get_float('Y', here[1])
        z = here[2]
        if z < self._z_floor():
            raise gcmd.error(
                "nozzle_locator: Z %.3f liegt unter dem Z-Boden %.3f"
                % (z, self._z_floor()))
        baseline = None
        if gcmd.get_int('BASELINE', 1):
            self._move([None, None, self.park_z], self.move_speed)
            baseline = self.measure_baseline()
            self._move([cx, cy, None], self.move_speed)
            self._move([None, None, z], self.approach_speed)
        else:
            self._move([cx, cy, None], self.move_speed)

        n_rows = int(round(height / pitch)) + 1
        x_lo, x_hi = cx - width / 2.0, cx + width / 2.0
        y_lo = cy - height / 2.0
        lead = 3.0 * self.sweep_step
        rows = []
        t_start = time.time()
        # Live-Bild fuer die Webapp: nach jeder Zeile das Gitter bis hierhin
        # in den Status legen (printer.nozzle_locator.map). 30 x 20 Zellen
        # sind fuer Moonrakers Polling unkritisch.
        self.live_map = {
            'label': label, 'x': cx, 'y': cy, 'z': z,
            'gap': z - self.holder_top_z, 'pitch': pitch,
            'width': width, 'height': height, 'baseline': baseline,
            'rows_total': n_rows, 'rows_done': 0, 'done': False,
            'file': None, 'xs': [], 'ys': [], 'values': [],
        }
        self._hold_sensor()
        try:
            for j in range(n_rows):
                y = y_lo + j * pitch
                # Serpentine: gerade Zeilen +X, ungerade -X. Der Vorlauf der
                # naechsten Zeile ist dann nur ein Y-Schritt entfernt.
                lo, hi = (x_lo, x_hi) if j % 2 == 0 else (x_hi, x_lo)
                track = self._scan("Raster y=%.2f" % y, (0.0, 0.0),
                                   (1.0, 0.0), lo, hi, lead, speed,
                                   through=(cx, y), log=False)
                rows.append((y, track))
                try:
                    part = fit.raster_grid(rows, x_lo, x_hi, pitch)
                    self.live_map = dict(self.live_map, rows_done=j + 1,
                                         xs=part['xs'], ys=part['ys'],
                                         values=part['values'])
                except ValueError:
                    pass
        finally:
            self._release_sensor()
            self.state = 'idle'
        self._move([cx, cy, None], self.move_speed)
        duration = time.time() - t_start

        try:
            grid = fit.raster_grid(rows, x_lo, x_hi, pitch)
        except ValueError as e:
            raise gcmd.error("nozzle_locator Raster: %s" % e)
        peak = None
        for j, row in enumerate(grid['values']):
            for i, v in enumerate(row):
                if v is not None and (peak is None or v > peak[0]):
                    peak = (v, grid['xs'][i], grid['ys'][j])
        n_samples = sum(len(t) for _, t in rows)
        data = {
            'kind': 'nozzle_locator_map',
            'label': label, 'time': time.time(),
            'x': cx, 'y': cy, 'z': z, 'gap': z - self.holder_top_z,
            'width': width, 'height': height, 'pitch': pitch,
            'speed': speed, 'baseline': baseline,
            'holder_top_z': self.holder_top_z,
            'coil_temp': self._coil_temp(),
            'grid': grid,
            'rows': [{'y': y, 'samples': [(round(x, 4), round(v, 1))
                                          for x, v in t]}
                     for y, t in rows],
        }
        path = self._write_json(gcmd, name, "nozzle_locator_map", data)
        self.last_map_file = os.path.basename(path)
        self.live_map = dict(self.live_map, done=True, file=self.last_map_file,
                             xs=grid['xs'], ys=grid['ys'],
                             values=grid['values'])
        gcmd.respond_info(
            "nozzle_locator Raster %s: %d Zeilen x %.0f mm, %d Samples in "
            "%.0f s, Spalt %.2f mm%s. Maximum %+.0f Hz bei X%.1f Y%.1f. "
            "Datei %s -- ansehen in webapp/map.html"
            % (label or "", n_rows, width, n_samples, duration,
               z - self.holder_top_z,
               (", Basislinie %.0f Hz" % baseline) if baseline else "",
               peak[0] - (baseline or 0.0), peak[1], peak[2],
               self.last_map_file))

    cmd_CALIBRATE_DRIVE_help = (
        "Calibrate the LDC1612 drive current (reg_drive_current) with the "
        "nozzle at measuring height above the coil. The chip needs a coil "
        "amplitude of 1.2-1.8 V; below that the conversion noise rises. "
        "The new value takes effect at the next sensor start (no restart "
        "needed) and is staged for SAVE_CONFIG. Run SAVE_CONFIG only after "
        "the holder is off the bed -- it restarts Klipper and clears homing.")

    def cmd_CALIBRATE_DRIVE(self, gcmd):
        """Wie Klippers LDC_CALIBRATE_DRIVE_CURRENT, aber mit Hoehenpruefung
        und sofort wirksam: der Chip bestimmt im Auto-Amplituden-Modus den
        Strom selbst, wir lesen das Register aus und setzen dccal.drive_cur,
        das _start_measurements beim naechsten Sensorstart schreibt.
        """
        self._require_homed()
        toolhead = self.printer.lookup_object('toolhead')
        here = toolhead.get_position()
        z_lo = self._z_floor()
        z_hi = self.holder_top_z + 3.0
        if not (z_lo <= here[2] <= z_hi):
            raise gcmd.error(
                "nozzle_locator: Z %.3f muss auf Messhoehe liegen (%.2f bis "
                "%.2f, Duese ueber der Spule) -- der Drive-Current haengt "
                "vom Ziel im Feld ab" % (here[2], z_lo, z_hi))
        dccal = self.sensor.dccal
        old = dccal.get_drive_current()
        sensor = self.sensor
        active = [True]
        sensor.add_client(lambda msg: active[0])
        try:
            toolhead.dwell(0.100)
            toolhead.wait_moves()
            old_config = sensor.read_reg(ldc1612.REG_CONFIG)
            # Auto-Amplituden-Modus (Bit 9 gesetzt, Bit 12 geloescht): der
            # Chip regelt den Strom auf 1,2-1,8 V ein -- so macht es
            # Klippers cmd_LDC_CALIBRATE.
            sensor.set_reg(ldc1612.REG_CONFIG, 0x001 | (1 << 9))
            toolhead.wait_moves()
            toolhead.dwell(0.100)
            toolhead.wait_moves()
            reg = sensor.read_reg(ldc1612.REG_DRIVE_CURRENT0)
            sensor.set_reg(ldc1612.REG_CONFIG, old_config)
        finally:
            active[0] = False
        new = (reg >> 6) & 0x1f
        dccal.drive_cur = new
        configfile = self.printer.lookup_object('configfile')
        configfile.set(self.name, 'reg_drive_current', "%d" % new)
        # Wirkung sofort pruefen: BatchBulkHelper stoppt den Sensor erst,
        # wenn der Kalibrier-Client beim naechsten Batch False liefert --
        # kurz warten, damit der naechste Start den neuen Strom schreibt.
        reactor = self.printer.get_reactor()
        reactor.pause(reactor.monotonic() + 0.4)
        mean, sd, n, errors = self.read_frequency(1.0)
        gcmd.respond_info(
            "nozzle_locator: reg_drive_current %d -> %d (gilt ab jetzt; "
            "%.0f Hz, sd %.1f Hz ueber %d Samples). SAVE_CONFIG traegt den "
            "Wert dauerhaft ein -- erst wenn die Halterung vom Bett ist, "
            "der Neustart loescht das Homing."
            % (old, new, mean, sd, n))

    def _coil_temp(self):
        """Spulentemperatur, wenn ein [temperature_sensor xyprobe_coil]
        existiert (Vorlage xy_probe.cfg); sonst None."""
        sensor = self.printer.lookup_object(
            'temperature_sensor xyprobe_coil', None)
        if sensor is None:
            return None
        try:
            now = self.printer.get_reactor().monotonic()
            return sensor.get_status(now).get('temperature')
        except Exception:
            return None

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
    def measure_baseline(self, aside=True):
        """Freiluft-Basislinie. Der Aufrufer steht auf park_z ueber der
        Spule. Dort ist die Duese aber noch 7 mm ueber der Spule und hebt
        die Basislinie um ~1.400 Hz (offene Arbeiten 8.6) -- deshalb faehrt
        der Kopf um baseline_offset seitlich weg, liest, und kommt zurueck.
        park_z ist per Definition die freie Fahrhoehe, der Weg ist sicher.
        aside=False liest an Ort und Stelle (Rueckfall, wenn seitlich kein
        Platz ist). Eine Basislinie mit der Duese in Reichweite ist der
        Fehler, der im Vorversuch 12 kHz Versatz erzeugt hat.
        """
        self.state = 'baseline'
        toolhead = self.printer.lookup_object('toolhead')
        here = toolhead.get_position()
        moved = False
        if aside and self.baseline_offset > 0.:
            now = self.printer.get_reactor().monotonic()
            status = toolhead.get_status(now)
            x_min = status['axis_minimum'][0]
            x_max = status['axis_maximum'][0]
            try:
                x_aside = fit.baseline_side(here[0], self.baseline_offset,
                                            x_min, x_max)
            except ValueError as e:
                logging.info("nozzle_locator: Basislinie an Ort und Stelle "
                             "-- %s", e)
                x_aside = None
            if x_aside is not None:
                self._move([x_aside, None, None], self.move_speed)
                moved = True
        try:
            mean, sd, n, errors = self.read_frequency(self.dwell_time * 2.0)
        finally:
            if moved:
                self._move([here[0], None, None], self.move_speed)
        if errors:
            raise self.printer.command_error(
                "nozzle_locator: %d Sensorfehler waehrend der Basislinie"
                % errors)
        self.last_baseline = mean
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
        while True:
            # Erst lesen, dann senken: wer schon auf Zielamplitude steht
            # (zweite Anfahrt ueber dem Grobscheitel), bleibt dort. Vorher
            # ging jede Anfahrt blind einen Schritt tiefer und landete beim
            # zweiten Aufruf direkt am Boden (250er, 2026-09-03).
            mean, sd, n, errors = self.read_frequency()
            if mean - baseline >= target_amplitude:
                return z
            if z <= floor:
                break
            # Naeher am Ziel feiner tasten. Die letzte Stufe 0,05 mm, weil
            # ein Tool bei 0,25-mm-Schritten bis zu 0,25 mm ueber dem
            # Ziel stehen bleibt -- bei 0,6 mm Scheitelwanderung je mm
            # Spalt (T0, Messtag) sind das 150 um.
            if mean - baseline >= target_amplitude * 0.85:
                step = 0.05
            elif mean - baseline >= target_amplitude * 0.5:
                step = 0.25
            if z - 5.0 > coarse_until:
                z = z - 5.0
                self._move([None, None, z], self.move_speed)
            else:
                z = max(floor, z - step)
                self._move([None, None, z], self.approach_speed)
        raise self.printer.command_error(
            "nozzle_locator: Zielamplitude bei Z=%.3f nicht erreicht "
            "(Signal %.0f Hz, noetig %.0f). Steht die Sonde unter der "
            "Duese, und stimmt holder_top_z?"
            % (floor, self.last_freq - baseline, target_amplitude))

    def _hold_sensor(self, speed=None, warmup=None):
        """Haelt den Sensor ueber mehrere Sweeps hinweg am Laufen.

        Klippers FixedFreqReader setzt seine Zeitstempel-Regression bei
        jedem Sensorstart zurueck und braucht ~20 Batches (2 s), bis die
        Zuordnung Sample -> print_time stabil ist. Ohne Haltung stoppt
        BatchBulkHelper den Sensor, sobald der letzte Client weg ist --
        also nach jedem Sweep -- und der naechste Scan begaenne mit frischer,
        noch ungenauer Zeitbasis (5 mm/s: 5 um je ms). Der Halte-Client
        bleibt, bis _release_sensor den Zaehler auf 0 bringt; beim ersten
        Halten wartet der Kopf 1 s, damit die Regression einschwingt.
        Nur im Scanmodus noetig; der Punktmodus mittelt ohne Zeitstempel.
        """
        if not self._scanning(speed):
            return
        self._hold_count += 1
        if self._hold_count > 1:
            return
        flag = [True]
        self._hold_flag = flag
        self.sensor.add_client(lambda msg: flag[0])
        toolhead = self.printer.lookup_object('toolhead')
        # Einschwingen der Zeitbasis (1 s) oder Aufwaermen der Spule
        # (warmup, Kalibrierlauf) -- je nachdem, wer haelt.
        dwell = max(1.0, warmup or 0.0)
        if dwell > 5.0:
            self.gcode.respond_info(
                "nozzle_locator: Sensor waermt %.0f s auf" % dwell)
        toolhead.dwell(dwell)
        toolhead.wait_moves()

    def _release_sensor(self, speed=None):
        if not self._scanning(speed):
            return
        self._hold_count = max(0, self._hold_count - 1)
        if self._hold_count == 0 and self._hold_flag is not None:
            self._hold_flag[0] = False
            self._hold_flag = None

    def _scan(self, label, origin, direction, lo, hi, lead, speed=None,
              through=None, log=True):
        """Ein kontinuierlicher Scan entlang einer Bahn.
        Rueckgabe: [(bogenlaenge, frequenz), ...] in Fahrreihenfolge.

        Bahn: s = <(x, y) - origin, direction>, direction Einheitsvektor;
        die Linie geht durch `through` (Default: aktuelle Position -- ein
        X-Sweep bleibt so auf seinem Y). Faehrt erst auf s = lo - lead
        (Vorlauf, gleiche Richtung wie der Scan, damit das Achsspiel
        draussen bleibt), dann in EINEM Zug mit `speed` bis hi + lead;
        Beschleunigen und Bremsen liegen so ausserhalb des Fensters.
        Waehrenddessen laeuft der Sensor; danach bekommt jedes Sample ueber
        seinen Zeitstempel die Sollposition aus der Bewegungswarteschlange
        (motion_report, wie Klippers eigener Eddy-Scan) und wird auf die
        Bahn projiziert. Fuer lo > hi laeuft der Scan rueckwaerts.
        log=False haelt den Sweep aus dem Puffer fuer NOZZLE_LOCATOR_DUMP
        heraus (Raster schreiben ihre eigene Datei).
        """
        if speed is None:
            speed = self.scan_speed
        # Geschwindigkeitsklemme: lieber langsamer als zu duenn abgetastet.
        rate = self.sensor.get_samples_per_second()
        clamped = fit.clamp_scan_speed(speed, abs(hi - lo), rate,
                                       self.min_samples)
        if clamped < speed:
            logging.info("nozzle_locator %s: Scan auf %.1f mm/s gebremst "
                         "(%d Samples ueber %.1f mm bei %d Hz)",
                         label, clamped, self.min_samples, abs(hi - lo),
                         rate)
            speed = clamped
        self.state = 'sweeping'
        toolhead = self.printer.lookup_object('toolhead')
        motion = self.printer.lookup_object('motion_report')
        dtrapq = motion.dtrapqs.get('toolhead')
        if dtrapq is None:
            raise self.printer.command_error(
                "nozzle_locator: motion_report kennt keine toolhead-"
                "Bewegungswarteschlange -- Scan nicht moeglich, "
                "scan_speed: 0 setzen")
        ox, oy = origin
        dx, dy = direction
        if through is None:
            cur = toolhead.get_position()
            through = (cur[0], cur[1])
        start, end = fit.scan_line(origin, direction, lo, hi, lead, through)
        self._move([start[0], start[1], None], self.move_speed)

        collected = []
        errors = [0]
        active = [True]

        def handle_batch(msg):
            if not active[0]:
                return False
            errors[0] = max(errors[0], msg.get('errors', 0))
            for sample in msg['data']:
                collected.append((sample[0], sample[1]))
            return True

        self.sensor.add_client(handle_batch)
        try:
            pos = toolhead.get_position()
            target = [end[0], end[1], pos[2]]
            toolhead.manual_move(target, speed)
            t_end = toolhead.get_last_move_time()
            toolhead.wait_moves()
            # Die letzten Samples kommen batchweise nach; warten, bis der
            # Sensor die Bewegung ganz abgedeckt hat.
            reactor = self.printer.get_reactor()
            deadline = reactor.monotonic() + 2.0
            while not collected or collected[-1][0] < t_end:
                if reactor.monotonic() > deadline:
                    raise self.printer.command_error(
                        "nozzle_locator: Sensor liefert keine Samples bis "
                        "zum Ende des Scans (%d empfangen) -- steckt die "
                        "Sonde?" % len(collected))
                reactor.pause(reactor.monotonic() + 0.05)
        finally:
            active[0] = False
        self.last_errors = errors[0]

        def lookup(print_time):
            return dtrapq.get_trapq_position(print_time)

        s_lo, s_hi = min(lo, hi), max(lo, hi)
        track = fit.samples_to_track(collected, lookup, origin, direction,
                                     s_lo, s_hi, latency=self.sample_latency)
        if len(track) < 3:
            raise self.printer.command_error(
                "nozzle_locator %s: nur %d Samples im Fenster (%d insgesamt) "
                "-- Zeitstempel und Bewegung passen nicht zusammen"
                % (label, len(track), len(collected)))
        if track:
            self.last_freq = track[-1][1]
        # Kompakt fuer Status und Webapp: Koerbe in sweep_step-Breite.
        try:
            self.last_points = [(round(p, 4), round(v, 1)) for p, v in
                                fit.bin_points(track, s_lo, s_hi,
                                               self.sweep_step)]
        except ValueError:
            self.last_points = [(round(p, 4), round(v, 1))
                                for p, v in track]
        if log:
            self.sweep_log.append({
                'label': label, 'x': round(pos[0], 3),
                'y': round(pos[1], 3), 'z': round(pos[2], 3),
                'origin': [ox, oy], 'direction': [dx, dy], 'speed': speed,
                'latency': self.sample_latency, 'errors': errors[0],
                'time': time.time(),
                'samples': [(round(s, 5), round(v, 1)) for s, v in track],
            })
            del self.sweep_log[:-self.sweep_log_limit]
        return track

    def sweep(self, axis, center, span, step, descending=False, speed=None):
        """Ein gerichteter Sweep. Rueckgabe: [(position, frequenz), ...].

        Wird immer aus derselben Richtung ANGEFAHREN (Vorlauf ausserhalb des
        Fensters), damit das Spiel der Achse nicht in die Messung geht.

        Mit scan_speed > 0 ein kontinuierlicher Scan (siehe _scan), sonst
        Punkt fuer Punkt anfahren, verweilen, mitteln. Die Punktliste ist
        im Scanmodus dicht (~80 je mm) -- wer Gitterpunkte braucht
        (Grobsuche), legt fit.bin_points darueber.
        """
        half = span / 2.0
        lo, hi = center - half, center + half
        idx = 0 if axis == 'X' else 1
        use_scan = self.scan_speed > 0. if speed is None else speed > 0.
        if use_scan:
            direction = (1.0, 0.0) if idx == 0 else (0.0, 1.0)
            label = "%s %s" % (axis, "rueck" if descending else "hin")
            if descending:
                return self._scan(label, (0.0, 0.0), direction, hi, lo,
                                  step * 3.0, speed)
            return self._scan(label, (0.0, 0.0), direction, lo, hi,
                              step * 3.0, speed)

        self.state = 'sweeping'
        n_steps = int(round(span / step)) + 1
        positions = [lo + i * step for i in range(n_steps)]
        if descending:
            positions = list(reversed(positions))
        # Vorlauf: 3 Schritte vor den ersten Punkt, gleiche Richtung
        lead = positions[0] - (step * 3.0 if not descending else -step * 3.0)
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

    def locate(self, axis, center, baseline, runs=None, span=None, step=None,
               speed=None):
        """Bidirektionale Ortung. Rueckgabe: dict mit center/fwd/rev/spread.

        Jeder Lauf besteht aus Hin- UND Ruecksweep. Ein einzelner gerichteter
        Sweep ist nie ein Ergebnis: ein zeitlinearer Drift verschoebe seinen
        Scheitel um m/(2a), und weil alle Laeufe dieselbe Richtung haetten,
        wuerde die Streuung diesen Fehler nicht zeigen. Im Scanmodus kommt
        die Sensorlatenz als zweiter gerichteter Anteil dazu (v*latency);
        auch sie faellt im Mittel heraus, die Hin-Rueck-Differenz zeigt sie.
        """
        runs = self.runs if runs is None else runs
        span = self.sweep_span if span is None else span
        step = self.sweep_step if step is None else step

        centers, fwds, revs, curvs = [], [], [], []
        self._hold_sensor(speed)
        try:
            for _ in range(runs):
                fwd = self.sweep(axis, center, span, step, descending=False,
                                 speed=speed)
                good, reason = fit.sweep_quality(fwd, baseline,
                                                 self.min_amplitude)
                if not good:
                    raise self.printer.command_error(
                        "nozzle_locator %s-Hinsweep: %s" % (axis, reason))
                rev = self.sweep(axis, center, span, step, descending=True,
                                 speed=speed)
                good, reason = fit.sweep_quality(rev, baseline,
                                                 self.min_amplitude)
                if not good:
                    raise self.printer.command_error(
                        "nozzle_locator %s-Ruecksweep: %s" % (axis, reason))
                try:
                    v_fwd, k_fwd = fit.parabola_fit(fwd)
                    v_rev, k_rev = fit.parabola_fit(rev)
                except ValueError as e:
                    raise self.printer.command_error(
                        "nozzle_locator %s: Fit fehlgeschlagen: %s"
                        % (axis, e))
                centers.append((v_fwd + v_rev) / 2.0)
                fwds.append(v_fwd)
                revs.append(v_rev)
                curvs.append((k_fwd + k_rev) / 2.0)
        finally:
            self._release_sensor(speed)

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

    def coarse_locate(self, axis, center, baseline):
        """Grobsuche: ein Sweep ueber search_span in doppelter Schrittweite,
        Ergebnis ist der lokale Buckel, der `center` am naechsten liegt.

        Kein Parabelfit ueber das ganze Fenster und kein globales Maximum:
        das Fenster streift den Heizblock, und der liefert ein Vielfaches
        der Duese (siehe nozzle_locator_fit.local_peak). Kein Ruecksweep --
        der Drift-Bias ist hier egal, die Feinmessung folgt ohnehin.
        Rueckgabe: (position, amplitude).
        """
        step = self.sweep_step * 2.0
        self._hold_sensor()
        try:
            pts = self.sweep(axis, center, self.search_span, step,
                             descending=False)
        finally:
            self._release_sensor()
        if self.scan_speed > 0.:
            # Scan liefert ~80 Samples je mm; local_peak braucht Nachbarn
            # mit festem Abstand -> Koerbe in doppelter Schrittweite.
            half = self.search_span / 2.0
            try:
                pts = fit.bin_points(pts, center - half, center + half, step)
            except ValueError as e:
                raise self.printer.command_error(
                    "nozzle_locator Grobsuche %s: %s" % (axis, e))
        try:
            pos, amp = fit.local_peak(pts, center, baseline,
                                      self.min_amplitude)
        except ValueError as e:
            raise self.printer.command_error(
                "nozzle_locator Grobsuche %s: %s" % (axis, e))
        return pos, amp

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
        self._hold_sensor()
        try:
            rx = self.locate('X', center_x, baseline, runs=runs)
            self._move([rx['center'], None, None], self.move_speed)
            ry = self.locate('Y', center_y, baseline, runs=runs)
            self._move([None, ry['center'], None], self.move_speed)
            a, b = rx['curvature'], ry['curvature']
            k45 = self._diagonal_curvature(rx['center'], ry['center'],
                                           baseline, +1)
            k135 = self._diagonal_curvature(rx['center'], ry['center'],
                                            baseline, -1)
        finally:
            self._release_sensor()
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
        half = self.sweep_span / 2.0
        root2 = 2.0 ** 0.5
        direction = (1.0 / root2, sign / root2)
        if self.scan_speed > 0.:
            pts = self._scan("Diagonale %s45" % ("+" if sign > 0 else "-"),
                             (cx, cy), direction, -half, half,
                             3.0 * self.sweep_step, through=(cx, cy))
        else:
            self.state = 'sweeping'
            n_steps = int(round(self.sweep_span / self.sweep_step)) + 1
            # Vorlauf in Sweeprichtung, wie bei den achsparallelen Sweeps
            s0 = -half - 3.0 * self.sweep_step
            self._move([cx + s0 * direction[0], cy + s0 * direction[1],
                        None], self.move_speed)
            pts = []
            self.last_points = []
            for i in range(n_steps):
                s = -half + i * self.sweep_step          # Bogenlaenge
                self._move([cx + s * direction[0], cy + s * direction[1],
                            None], self.move_speed)
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
        "STEP, SPEED (scan speed mm/s, 0 = point mode), GAPS (comma-"
        "separated gaps above holder_top_z: repeats the X/Y locate at each "
        "height and reports the peak per gap -- height series diagnostic). "
        "AXIS=DIAG runs both diagonals and reports the cross-term of "
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
        speed = gcmd.get_float('SPEED', None, minval=0.)
        # COARSE=1: erst Grobsuche ueber search_span um die aktuelle
        # Position, dann fein. Ohne Grobsuche faellt der Fit auf eine
        # Flanke herein, wenn der Scheitel nicht im 8-mm-Fenster liegt
        # (Messtag 2026-09-04) -- sweep_quality faengt das jetzt, aber die
        # Grobsuche ist der Weg dorthin.
        coarse = gcmd.get_int('COARSE', 0)
        gaps_raw = gcmd.get('GAPS', None)
        gaps = None
        if gaps_raw:
            try:
                gaps = [float(g) for g in gaps_raw.split(',') if g.strip()]
            except ValueError:
                raise gcmd.error("GAPS muss eine Liste von Zahlen sein")
            if axis == 'DIAG':
                raise gcmd.error("GAPS geht nur mit AXIS=X oder Y")
            for g in gaps:
                if self.holder_top_z + g < self._z_floor():
                    raise gcmd.error(
                        "GAPS: Spalt %.2f liegt unter min_gap %.2f"
                        % (g, self.min_gap))
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
            if gaps:
                self._height_series(gcmd, axis, here, baseline, gaps, runs,
                                    span, step, speed)
                return
            if axis == 'DIAG':
                r = self.measure_coupling(here[0], here[1], baseline,
                                          runs=runs)
                gcmd.respond_info(
                    "nozzle_locator Kopplung: a=%.1f b=%.1f c=%.1f Hz/mm^2, "
                    "rho=%.3f" % (r['a'], r['b'], r['c'], r['rho']))
                gcmd.respond_info(self._coupling_advice(r['rho']))
                return
            center = here[0 if axis == 'X' else 1]
            if coarse:
                center, amp = self.coarse_locate(axis, center, baseline)
                gcmd.respond_info("nozzle_locator Grobsuche %s: %.2f "
                                  "(%+.0f Hz)" % (axis, center, amp))
                coord = [None, None, None]
                coord[0 if axis == 'X' else 1] = center
                self._move(coord, self.move_speed)
            result = self.locate(axis, center, baseline, runs=runs,
                                 span=span, step=step, speed=speed)
            gcmd.respond_info(
                "nozzle_locator %s: %.4f mm (hin %.4f, rueck %.4f, "
                "Differenz %.1f um = gemessener Drift-Bias%s; Spannweite "
                "ueber %d Laeufe %.1f um, Kruemmung %.0f Hz/mm^2)"
                % (axis, result['center'], result['fwd'], result['rev'],
                   (result['fwd'] - result['rev']) * 1000.,
                   " + 2*v*Latenz" if self._scanning(speed) else "",
                   runs, result['spread'] * 1000., result['curvature']))
            coord = [None, None, None]
            coord[0 if axis == 'X' else 1] = result['center']
            self._move(coord, self.move_speed)
        finally:
            self.state = 'idle'

    def _scanning(self, speed=None):
        return self.scan_speed > 0. if speed is None else speed > 0.

    def _height_series(self, gcmd, axis, here, baseline, gaps, runs, span,
                       step, speed):
        """Hoehenserie: dieselbe Achse bei mehreren Spalten ueber der
        Halterung orten. Zeigt, wie der Scheitel mit dem Spalt wandert
        (Heizblock-Anteil, ~240 um/mm am 250er) und ob er zum kleinen
        Spalt hin konvergiert. Die Rohdaten jedes Sweeps liegen danach im
        Puffer fuer NOZZLE_LOCATOR_DUMP. Faehrt am Ende auf die
        Ausgangshoehe zurueck; das Fenster folgt dem jeweils letzten
        Scheitel.
        """
        idx = 0 if axis == 'X' else 1
        center = here[idx]
        rows = []
        gcmd.respond_info(
            "nozzle_locator Hoehenserie %s ueber %d Spalte (Halterung %.2f, "
            "Basislinie %.0f Hz)" % (axis, len(gaps), self.holder_top_z,
                                     baseline))
        try:
            for gap in gaps:
                z = self.holder_top_z + gap
                self._move([None, None, z], self.approach_speed)
                r = self.locate(axis, center, baseline, runs=runs,
                                span=span, step=step, speed=speed)
                amp = max(v for _, v in self.last_points) - baseline
                rows.append((gap, z, r))
                gcmd.respond_info(
                    "  Spalt %.2f (Z %.3f): %s %.4f  hin-rueck %+.1f um  "
                    "Spannweite %.1f um  Kruemmung %.0f Hz/mm^2  "
                    "Amplitude %+.0f Hz"
                    % (gap, z, axis, r['center'],
                       (r['fwd'] - r['rev']) * 1000., r['spread'] * 1000.,
                       r['curvature'], amp))
                center = r['center']
        finally:
            # Zurueck auf die Ausgangshoehe UND auf den letzten Scheitel:
            # ein Sweep endet am Fensterrand, und ein Folgekommando fiel
            # am Messtag genau darauf herein (7 mm neben dem Scheitel,
            # Amplitude zu klein).
            self._move([None, None, here[2]], self.approach_speed)
            coord = [None, None, None]
            coord[idx] = center
            self._move(coord, self.move_speed)
        if len(rows) >= 2:
            g0, _, r0 = rows[0]
            g1, _, r1 = rows[-1]
            if abs(g1 - g0) > 1e-6:
                slope = (r1['center'] - r0['center']) / (g1 - g0)
                gcmd.respond_info(
                    "  Scheitel wandert %.0f um je mm Spalt (von %.2f nach "
                    "%.2f); Extrapolation auf Spalt 0: %.4f"
                    % (slope * 1000., g0, g1,
                       r1['center'] - slope * g1))


def load_config(config):
    return NozzleLocator(config)
