import logging


class HtcFilamentSwitch:
    def __init__(self, config, gate, pin_name):
        self.printer = printer = config.get_printer()
        self.gate = gate
        # False wie in Klippers filament_switch_sensor: buttons melden beim
        # Start nur Pins, die logisch 1 sind -- "vorhanden" kommt als Event,
        # "leer" kommt nie.
        self.filament_present = False

        # load_object braucht die Config-Sektion, nicht das configfile-Objekt:
        # ist buttons noch nicht geladen, ruft Klipper config.getsection() auf.
        buttons = printer.load_object(config, 'buttons')
        # register_buttons will die Pin-Strings und parst sie selbst
        # (mit can_invert/can_pullup) -- keine vorgeparsten pin_params.
        buttons.register_buttons([pin_name], self._button_handler)

        printer.register_event_handler('klippy:ready', self._handle_ready)

    def _handle_ready(self):
        htc = self.printer.lookup_object('happy_toolchanger', None)
        if htc and hasattr(htc, 'sensor_manager'):
            htc.sensor_manager.register_sensor(self.gate, self)
            htc.sync_gates_from_sensors()

    def _button_handler(self, eventtime, state):
        self.filament_present = bool(state)
        htc = self.printer.lookup_object('happy_toolchanger', None)
        if htc and hasattr(htc, 'sensor_manager'):
            htc.sensor_manager.note_filament_present(
                self.gate, self.filament_present, eventtime)


class HtcSensors:
    def __init__(self, config):
        self.printer = config.get_printer()
        self.switches = []

        for i in range(16):
            pin = config.get('sensor_pin_%d' % i, None)
            if pin:
                switch = HtcFilamentSwitch(config, i, pin)
                self.switches.append(switch)
                logging.info("HTC: Sensor registered for gate %d" % i)

    def get_status(self, eventtime=None):
        return {
            'num_sensors': len(self.switches),
        }


def load_config(config):
    return HtcSensors(config)
