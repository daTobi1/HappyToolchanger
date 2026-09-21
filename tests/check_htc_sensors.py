#!/usr/bin/env python3
"""Prueft die Zustandslogik der HTC-Filamentsensoren
(klippy/extras/happy_toolchanger/sensor_manager.py).

Braucht kein Klipper und keinen Drucker -- nur Python 3:

    python3 tests/check_htc_sensors.py

Exit-Code 0 = sauber, 1 = Befunde.
"""
import importlib.util
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
CANDIDATES = [
    os.path.join(HERE, "..", "klippy", "extras", "happy_toolchanger",
                 "sensor_manager.py"),
    os.path.join(HERE, "sensor_manager.py"),
]

FINDINGS = []
CHECKS = [0]


def ok(cond, what, detail=""):
    CHECKS[0] += 1
    if not cond:
        FINDINGS.append("%s%s" % (what, (" -- " + detail) if detail else ""))


def load_module():
    for path in CANDIDATES:
        if os.path.exists(path):
            spec = importlib.util.spec_from_file_location("sensor_manager", path)
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            return mod
    raise SystemExit("sensor_manager.py nicht gefunden (%s)" % CANDIDATES)


class FakeReactor:
    NEVER = 9e99

    def __init__(self):
        self.timers = []

    def register_timer(self, callback, waketime):
        timer = [callback, waketime]
        self.timers.append(timer)
        return timer

    def unregister_timer(self, timer):
        self.timers.remove(timer)

    def advance(self, now):
        for timer in list(self.timers):
            if timer in self.timers and timer[1] <= now:
                timer[1] = timer[0](now)


class FakePrinter:
    def __init__(self):
        self.reactor = FakeReactor()

    def get_reactor(self):
        return self.reactor


class FakeSwitch:
    def __init__(self, present):
        self.filament_present = present


def make(m, present_at_boot):
    printer = FakePrinter()
    events = []
    mgr = m.SensorManager(printer, 4, 1.0,
                          lambda g: events.append(("runout", g)),
                          lambda g: events.append(("insert", g)))
    for gate, present in enumerate(present_at_boot):
        mgr.register_sensor(gate, FakeSwitch(present))
    return mgr, printer.reactor, events


def main():
    m = load_module()
    DET, EMPTY, OFF = m.SENSOR_DETECTED, m.SENSOR_EMPTY, m.SENSOR_DISABLED

    # --- Mainsail prueft `state === 1` ---
    ok(DET == 1 and EMPTY == 0 and OFF == -1,
       "Sensorzustaende muessen 1/0/-1 sein",
       "HtcSensorStatus.vue vergleicht mit der Zahl 1")

    # --- Startzustand kommt vom Sensor ---
    mgr, reactor, events = make(m, [True, False])
    ok(mgr.get_status()['sensor_states'] == [DET, EMPTY, OFF, OFF],
       "beim Booten leerer Sensor muss 'empty' sein, Gates ohne Sensor 'disabled'",
       str(mgr.get_status()['sensor_states']))
    ok(mgr.get_status()['num_sensors'] == 2, "num_sensors zaehlt registrierte")
    ok(events == [], "Registrieren darf weder Runout noch Insert ausloesen")
    ok(mgr.is_present(0) and not mgr.is_present(1), "is_present folgt dem Zustand")

    # --- Sensor ausserhalb num_tools wird ignoriert statt IndexError ---
    try:
        mgr.register_sensor(7, FakeSwitch(True))
        ok(7 not in mgr.sensors, "Gate >= num_tools darf nicht registriert werden")
    except IndexError:
        ok(False, "Gate >= num_tools wirft IndexError")

    # --- Runout erst nach der Entprellzeit ---
    mgr, reactor, events = make(m, [True])
    mgr.note_filament_present(0, False, 10.0)
    reactor.advance(10.5)
    ok(events == [] and mgr.is_present(0),
       "vor Ablauf der Entprellzeit kein Runout")
    reactor.advance(11.0)
    ok(events == [("runout", 0)], "nach der Entprellzeit genau ein Runout",
       str(events))
    ok(mgr.get_status()['sensor_states'][0] == EMPTY, "danach 'empty'")
    ok(reactor.timers == [], "der Runout-Timer wird wieder abgemeldet")

    # --- Wackler innerhalb der Entprellzeit ---
    mgr, reactor, events = make(m, [True])
    mgr.note_filament_present(0, False, 10.0)
    mgr.note_filament_present(0, True, 10.3)
    reactor.advance(12.0)
    ok(events == [], "Filament kommt rechtzeitig zurueck -> kein Runout, kein Insert",
       str(events))

    # --- Nachlegen meldet Insert, genau einmal ---
    mgr, reactor, events = make(m, [False])
    mgr.note_filament_present(0, True, 5.0)
    mgr.note_filament_present(0, True, 5.1)
    ok(events == [("insert", 0)], "leer -> vorhanden meldet genau ein Insert",
       str(events))
    ok(mgr.is_present(0), "danach 'detected'")

    # --- schon leer: kein zweiter Runout ---
    mgr, reactor, events = make(m, [False])
    mgr.note_filament_present(0, False, 5.0)
    reactor.advance(10.0)
    ok(events == [], "ein schon leerer Sensor loest keinen Runout aus", str(events))

    # --- Events von nicht registrierten Gates ---
    mgr, reactor, events = make(m, [True])
    mgr.note_filament_present(2, False, 1.0)
    reactor.advance(5.0)
    ok(events == [], "Gate ohne Sensor wird ignoriert")

    print("%d Pruefungen, %d Befunde" % (CHECKS[0], len(FINDINGS)))
    for f in FINDINGS:
        print("  BEFUND: " + f)
    return 1 if FINDINGS else 0


if __name__ == "__main__":
    sys.exit(main())
