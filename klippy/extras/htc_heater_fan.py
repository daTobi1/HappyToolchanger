# Hotend-Luefter mit einstellbarer Drehzahl je Zustand und Gehaeuse-Anhebung.
#
# Klippers [heater_fan] kennt genau eine Drehzahl (fan_speed) und kein
# Kommando, sie zur Laufzeit zu aendern. Dieses Modul verhaelt sich wie
# [heater_fan] (haengt sich an die Heizung, Sicherheitswert bei Shutdown,
# Kick-Start aus fan.Fan), erlaubt aber:
#
#   * eigene Drehzahl fuer aktives Tool, geparktes Tool und Abkuehlen
#   * lineare Anhebung mit der Gehaeusetemperatur (gegen Heat-Creep)
#   * Ueberschreiben und Umstellen zur Laufzeit (SET_HEATER_FAN)
#
# Bewusst KEINE Kennlinie nach Hotend-Temperatur: die Heizung wird schon
# per PID geregelt, ein zweiter Regler auf derselben Groesse koennte
# schwingen. Die Zustaende sind diskret, die Gehaeuse-Anhebung ist langsam
# und monoton.
#
# Die Entscheidungslogik (classify_state, target_speed, needs_update) ist
# reines Python ohne Klipper und wird von tests/check_htc_heater_fan.py
# gepruft.
#
# This file may be distributed under the terms of the GNU GPLv3 license.
import inspect

try:
    from . import fan
except ImportError:  # ausserhalb von Klipper (Tests)
    fan = None

PIN_MIN_TIME = 0.100
UPDATE_DEADBAND = 0.02   # kleinere Aenderungen der Anhebung nicht senden

STATE_OFF = 'off'
STATE_ACTIVE = 'active'
STATE_PARKED = 'parked'
STATE_COOLDOWN = 'cooldown'


# --- reine Logik -----------------------------------------------------------

def classify_state(heater_temps, heater_temp, tool_active):
    """Zustand aus den Heizungen ableiten.

    heater_temps: Liste (ist, soll) je Heizung.
    Wie [heater_fan]: an, sobald ein Sollwert gesetzt ist ODER die Heizung
    noch ueber heater_temp liegt.
    """
    any_target = any(target for _, target in heater_temps)
    any_hot = any(current > heater_temp for current, _ in heater_temps)
    if not any_target and not any_hot:
        return STATE_OFF
    if not any_target:
        return STATE_COOLDOWN
    return STATE_ACTIVE if tool_active else STATE_PARKED


def chamber_boost(base, chamber_temp, temp_start, temp_full, max_speed):
    """Drehzahl linear von base (bei temp_start) auf max_speed (bei temp_full)
    anheben. Senkt nie unter base."""
    if chamber_temp is None or temp_full <= temp_start:
        return base
    top = max(max_speed, base)
    frac = (chamber_temp - temp_start) / (temp_full - temp_start)
    frac = min(1., max(0., frac))
    return base + (top - base) * frac


def target_speed(state, speeds, override, min_speed,
                 chamber_temp=None, chamber=None):
    """Solldrehzahl fuer einen Zustand.

    speeds:   {'active': x, 'parked': y, 'cooldown': z}
    override: feste Grunddrehzahl statt speeds[state], oder None
    min_speed: Untergrenze, solange der Luefter ueberhaupt laeuft
    chamber:  (temp_start, temp_full, max_speed) oder None
    """
    if state == STATE_OFF:
        return 0.
    base = speeds[state] if override is None else override
    speed = base
    if chamber is not None:
        speed = chamber_boost(base, chamber_temp, *chamber)
    speed = max(speed, min_speed)
    return round(min(1., max(0., speed)), 3)


def needs_update(last_speed, new_speed, state_changed):
    """Senden nur bei Zustandswechsel, Ein/Aus oder Aenderung ueber dem
    Totband -- die Gehaeuse-Anhebung soll nicht jede Sekunde nachstellen."""
    if new_speed == last_speed:
        return False
    if state_changed or new_speed == 0. or last_speed == 0.:
        return True
    return abs(new_speed - last_speed) >= UPDATE_DEADBAND


# --- Klipper-Anbindung -----------------------------------------------------

class HtcHeaterFan:
    def __init__(self, config):
        self.printer = config.get_printer()
        self.name = config.get_name()
        self.short_name = self.name.split()[-1]
        self.printer.load_object(config, 'heaters')
        self.heater_names = config.getlist('heater', ('extruder',))
        self.heater_temp = config.getfloat('heater_temp', 50.0)
        self.heaters = []
        self.fan = fan.Fan(config, default_shutdown_speed=1.)
        # Klipper >= 2024: set_speed(value, print_time=None);
        # aeltere: set_speed(print_time, value)
        params = list(inspect.signature(self.fan.set_speed).parameters)
        self._legacy_set_speed = params[:1] == ['print_time']
        # Drehzahlen je Zustand
        fan_speed = config.getfloat('fan_speed', 1., minval=0., maxval=1.)
        self.speeds = {
            STATE_ACTIVE: fan_speed,
            STATE_PARKED: config.getfloat('parked_speed', fan_speed,
                                          minval=0., maxval=1.),
            STATE_COOLDOWN: config.getfloat('cooldown_speed', fan_speed,
                                            minval=0., maxval=1.),
        }
        self.min_speed = config.getfloat('min_speed', 0., minval=0., maxval=1.)
        # Gehaeuse-Anhebung (optional)
        self.chamber_sensor_name = config.get('chamber_sensor', None)
        self.chamber_sensor = None
        self.chamber = None
        if self.chamber_sensor_name:
            temp_start = config.getfloat('chamber_temp_start', 40.)
            temp_full = config.getfloat('chamber_temp_full', 60.,
                                        above=temp_start)
            max_speed = config.getfloat('chamber_max_speed', 1.,
                                        minval=0., maxval=1.)
            self.chamber = (temp_start, temp_full, max_speed)
        # Tool-Zuordnung: explizit oder ueber den Extruder des Tools
        self.tool_name = config.get('tool', None)
        self.tool = None
        self.toolchanger = None
        # Laufzeit
        self.override = None
        self.state = STATE_OFF
        self.last_speed = 0.
        self.last_chamber_temp = None
        self.printer.register_event_handler('klippy:ready', self.handle_ready)
        gcode = self.printer.lookup_object('gcode')
        for key in (self.short_name, self.name):
            gcode.register_mux_command('SET_HEATER_FAN', 'FAN', key,
                                       self.cmd_SET_HEATER_FAN,
                                       desc=self.cmd_SET_HEATER_FAN_help)

    def handle_ready(self):
        pheaters = self.printer.lookup_object('heaters')
        self.heaters = [pheaters.lookup_heater(n) for n in self.heater_names]
        if self.chamber_sensor_name:
            self.chamber_sensor = self.printer.lookup_object(
                self.chamber_sensor_name)
        self.toolchanger = self.printer.lookup_object('toolchanger', None)
        if self.toolchanger is not None:
            self.tool = self._find_tool()
        reactor = self.printer.get_reactor()
        reactor.register_timer(self.callback,
                               reactor.monotonic() + PIN_MIN_TIME)

    def _find_tool(self):
        if self.tool_name:
            return self.printer.lookup_object('tool ' + self.tool_name)
        for _, tool in self.printer.lookup_objects(module='tool'):
            if getattr(tool, 'extruder_name', None) in self.heater_names:
                return tool
        return None

    def _tool_active(self):
        if self.tool is None or self.toolchanger is None:
            return True
        return self.toolchanger.get_selected_tool() is self.tool

    def _chamber_temp(self, eventtime):
        if self.chamber_sensor is None:
            return None
        try:
            temp, _ = self.chamber_sensor.get_temp(eventtime)
        except Exception:
            return None
        return temp

    def _set_speed(self, speed):
        if self._legacy_set_speed:
            curtime = self.printer.get_reactor().monotonic()
            print_time = self.fan.get_mcu().estimated_print_time(curtime)
            self.fan.set_speed(print_time + PIN_MIN_TIME, speed)
        else:
            self.fan.set_speed(speed)

    def _update(self, eventtime, force=False):
        temps = [h.get_temp(eventtime) for h in self.heaters]
        state = classify_state(temps, self.heater_temp, self._tool_active())
        chamber_temp = self._chamber_temp(eventtime)
        speed = target_speed(state, self.speeds, self.override,
                             self.min_speed, chamber_temp, self.chamber)
        state_changed = state != self.state or force
        self.state = state
        self.last_chamber_temp = chamber_temp
        if needs_update(self.last_speed, speed, state_changed):
            self.last_speed = speed
            self._set_speed(speed)

    def callback(self, eventtime):
        self._update(eventtime)
        return eventtime + 1.

    def get_status(self, eventtime):
        status = dict(self.fan.get_status(eventtime))
        status.update({
            'state': self.state,
            'target_speed': self.last_speed,
            'override': self.override,
            'fan_speed': self.speeds[STATE_ACTIVE],
            'parked_speed': self.speeds[STATE_PARKED],
            'cooldown_speed': self.speeds[STATE_COOLDOWN],
            'chamber_temp': self.last_chamber_temp,
            'tool': getattr(self.tool, 'name', None),
        })
        return status

    cmd_SET_HEATER_FAN_help = (
        "Hotend-Luefter einstellen: SPEED=x (fest, bis RESET=1), "
        "FAN_SPEED= / PARKED_SPEED= / COOLDOWN_SPEED= je Zustand")

    def cmd_SET_HEATER_FAN(self, gcmd):
        for key, state in (('FAN_SPEED', STATE_ACTIVE),
                           ('PARKED_SPEED', STATE_PARKED),
                           ('COOLDOWN_SPEED', STATE_COOLDOWN)):
            val = gcmd.get_float(key, None, minval=0., maxval=1.)
            if val is not None:
                self.speeds[state] = val
        min_speed = gcmd.get_float('MIN_SPEED', None, minval=0., maxval=1.)
        if min_speed is not None:
            self.min_speed = min_speed
        if gcmd.get_int('RESET', 0):
            self.override = None
        speed = gcmd.get_float('SPEED', None, minval=0., maxval=1.)
        if speed is not None:
            self.override = speed
        eventtime = self.printer.get_reactor().monotonic()
        self._update(eventtime, force=True)
        gcmd.respond_info(
            "%s: %s, %.0f%% (aktiv %.0f%% / geparkt %.0f%% / abkuehlen %.0f%%%s)"
            % (self.short_name, self.state, self.last_speed * 100.,
               self.speeds[STATE_ACTIVE] * 100.,
               self.speeds[STATE_PARKED] * 100.,
               self.speeds[STATE_COOLDOWN] * 100.,
               ", fest %.0f%%" % (self.override * 100.)
               if self.override is not None else ""))


def load_config_prefix(config):
    return HtcHeaterFan(config)
