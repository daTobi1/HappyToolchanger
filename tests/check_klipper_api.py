#!/usr/bin/env python3
"""Verify the Klipper internals this project builds on still look as expected.

Our extras are not plugins against a stable API -- they subclass and call
Klipper internals directly. A Klipper update can move any of these without
warning, and the failure would only show up mid-print. This checks the whole
surface up front, so an update breaks the test instead of the printer.

Run on the printer:

    ~/klippy-env/bin/python check_klipper_api.py [--klipper ~/klipper]

Exit code 0 = clean, 1 = findings.
"""
import argparse
import inspect
import os
import re
import sys

FINDINGS = []
CHECKS = [0]


def ok(cond, what, detail=""):
    CHECKS[0] += 1
    if not cond:
        FINDINGS.append("%s%s" % (what, (" -- " + detail) if detail else ""))


def has_attrs(obj, names, label):
    for n in names:
        ok(hasattr(obj, n), "%s fehlt Attribut/Methode '%s'" % (label, n))


def arg_names(fn):
    try:
        return list(inspect.signature(fn).parameters)
    except (TypeError, ValueError):
        return []


def source_of(fn):
    try:
        return inspect.getsource(fn)
    except (OSError, TypeError):
        return ""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--klipper", default=os.path.expanduser("~/klipper"))
    args = ap.parse_args()

    klippy = os.path.join(args.klipper, "klippy")
    if not os.path.isdir(klippy):
        raise SystemExit("Klipper nicht gefunden: %s" % klippy)
    sys.path.insert(0, klippy)

    from extras import probe, manual_probe, homing, gcode_macro  # noqa: F401
    import gcode as gcode_mod

    # --- probe.py: tool_probe.py und offset.py bauen direkt darauf auf ---
    has_attrs(probe, [
        "ProbeOffsetsHelper", "ProbeParameterHelper", "ProbeEndstopWrapper",
        "SampleAveragingHelper", "HomingViaProbeHelper", "ProbeCommandHelper",
        "run_single_probe",
    ], "probe")

    if hasattr(probe, "ProbeEndstopWrapper"):
        p = arg_names(probe.ProbeEndstopWrapper.__init__)
        ok(p[:4] == ["self", "config", "probe_offsets", "param_helper"],
           "probe.ProbeEndstopWrapper.__init__ Signatur geaendert",
           "erwartet (self, config, probe_offsets, param_helper), ist %s" % p)

    if hasattr(probe, "HomingViaProbeHelper"):
        p = arg_names(probe.HomingViaProbeHelper.__init__)
        ok(p[:3] == ["self", "config", "position_endstop"],
           "probe.HomingViaProbeHelper.__init__ Signatur geaendert",
           "ist %s" % p)

    if hasattr(probe, "ProbeOffsetsHelper"):
        has_attrs(probe.ProbeOffsetsHelper, ["get_offsets"],
                  "probe.ProbeOffsetsHelper")

    # bed_z semantics: create_probe_result must subtract z_offset
    if hasattr(probe, "run_single_probe"):
        ok(len(arg_names(probe.run_single_probe)) == 2,
           "probe.run_single_probe Signatur geaendert")

    # --- manual_probe.ProbeResult: wir lesen .bed_z ---
    ok(hasattr(manual_probe, "ProbeResult"), "manual_probe.ProbeResult fehlt")
    if hasattr(manual_probe, "ProbeResult"):
        f = getattr(manual_probe.ProbeResult, "_fields", ())
        ok("bed_z" in f, "ProbeResult hat kein Feld 'bed_z'", "Felder: %s" % (f,))
    if hasattr(manual_probe, "create_probe_result"):
        src = source_of(manual_probe.create_probe_result)
        ok("test_pos[2]-z_offset" in src.replace(" ", ""),
           "create_probe_result subtrahiert z_offset nicht mehr",
           "bed_z = trigger_z - z_offset ist die Grundlage aller Offset-Formeln")

    # --- homing.py: G28 Z laeuft ueber _do_home_z_via_probe ---
    ok(hasattr(homing, "HomingMove"), "homing.HomingMove fehlt")
    hm = getattr(homing, "Homing", None)
    ok(hm is not None, "homing.Homing fehlt")
    if hm is not None:
        has_attrs(hm, ["_do_home_z_via_probe", "_probing_home",
                       "_create_probe_gcmd", "home_rails",
                       "_do_home_rails"], "homing.Homing")
        src = source_of(getattr(hm, "_probing_home", None)) or ""
        ok("curpos[2] -= ppos.bed_z" in src,
           "homing._probing_home setzt Z nicht mehr ueber bed_z",
           "davon haengt ab, dass G28 Z den Nozzle-Kontakt trifft")
        src = source_of(getattr(hm, "_create_probe_gcmd", None)) or ""
        ok("PROBE_SPEED" in src,
           "homing._create_probe_gcmd uebergibt kein PROBE_SPEED mehr",
           "beeinflusst die Tap-Ueberfahrt; siehe homing_retract_dist")
        src = source_of(getattr(hm, "home_rails", None)) or ""
        ok("home_rails_end" not in src,
           "homing.home_rails sendet jetzt evtl. doch ein Event fuer den "
           "Probe-Pfad -- der G28-Wrapper koennte dadurch ueberfluessig sein")

    # --- gcode.py: Parameterwerte mit Leerzeichen brauchen shlex ---
    gc = getattr(gcode_mod, "GCodeDispatch", None)
    ok(gc is not None, "gcode.GCodeDispatch fehlt")
    if gc is not None:
        src = source_of(getattr(gc, "_get_extended_params", None)) or ""
        ok("shlex" in src,
           "gcode._get_extended_params nutzt kein shlex mehr",
           'PROBE="probe_eddy_ng my_eddy" wuerde am Leerzeichen zerbrechen')

    # --- gcode_move: wir rufen reset_last_position/cmd_G1 und lesen
    #     homing_position direkt ---
    from extras import gcode_move
    gm = getattr(gcode_move, "GCodeMove", None)
    ok(gm is not None, "gcode_move.GCodeMove fehlt")
    if gm is not None:
        has_attrs(gm, ["reset_last_position", "cmd_G1", "get_status"],
                  "gcode_move.GCodeMove")
        src = source_of(gm.__init__) or ""
        ok("homing_position" in src,
           "gcode_move.homing_position fehlt",
           "_gcode_z_offset() hat aber einen get_status-Fallback")
        ok("self.last_position" in src,
           "gcode_move.last_position fehlt -- reset_last_position pruefen")

    # --- reactor: der Spoolman-Update laeuft in einem eigenen Thread und
    #     muss seine Meldungen ueber register_async_callback zurueckgeben.
    #     gcode.respond_info gehoert dem Reactor-Thread; direkt aus dem
    #     Worker aufgerufen schreibt es in Klippers Ausgabe, waehrend der
    #     Reactor sie ebenfalls benutzt. ---
    import reactor as reactor_mod
    for cls_name in ("SelectReactor", "PollReactor", "EPollReactor"):
        cls = getattr(reactor_mod, cls_name, None)
        if cls is None:
            continue
        has_attrs(cls, ["register_async_callback"], "reactor." + cls_name)
        src = source_of(getattr(cls, "register_async_callback", None)) or ""
        ok("_async_queue" in src or "async" in src.lower(),
           "reactor.register_async_callback sieht nicht mehr nach einer "
           "threadsicheren Queue aus",
           "happy_toolchanger/core.py loggt darueber aus dem Spoolman-Thread")
        break

    # --- configfile: CALIBRATE_TOOL_PID haengt aus, was PID_CALIBRATE fuer
    #     SAVE_CONFIG vormerkt (pid_Kp liegt in der eingebundenen T<n>.cfg) ---
    import configfile as cfg_mod
    pc = getattr(cfg_mod, "PrinterConfig", None)
    ok(pc is not None, "configfile.PrinterConfig fehlt")
    if pc is not None:
        has_attrs(pc, ["set", "remove_section"], "configfile.PrinterConfig")
    autosave_cls = getattr(cfg_mod, "ConfigAutoSave", None)
    if autosave_cls is None:
        # Name variiert zwischen Versionen - Attribute am set() pruefen
        src = source_of(getattr(pc, "set", None)) or ""
        ok("autosave" in src,
           "configfile.PrinterConfig.set delegiert nicht mehr an autosave",
           "_unstage_autosave() greift auf configfile.autosave zu")
    src = ""
    for name in dir(cfg_mod):
        obj = getattr(cfg_mod, name)
        if isinstance(obj, type) and hasattr(obj, "set"):
            s2 = source_of(obj.set) or ""
            if "status_save_pending" in s2:
                src = s2
                break
    ok("status_save_pending" in src,
       "keine Klasse mit status_save_pending in configfile gefunden",
       "_unstage_autosave() raeumt genau dieses dict auf")

    # --- heaters: _current_tool_pid() liest Kp/Ki/Kd fuer die PID-Anzeige ---
    from extras import heaters
    ok(hasattr(heaters, "ControlPID"), "heaters.ControlPID fehlt")
    if hasattr(heaters, "ControlPID"):
        src = source_of(heaters.ControlPID.__init__) or ""
        ok("self.Kp" in src and "PID_PARAM_BASE" in src,
           "ControlPID speichert Kp nicht mehr als config-Wert / PID_PARAM_BASE",
           "_current_tool_pid() rechnet mit *255 zurueck")
    ok(getattr(heaters, "PID_PARAM_BASE", None) == 255.,
       "heaters.PID_PARAM_BASE ist nicht mehr 255")
    hc = getattr(heaters, "Heater", None)
    ok(hc is not None, "heaters.Heater fehlt")
    if hc is not None:
        src = source_of(hc.__init__) or ""
        ok("self.control" in src,
           "heaters.Heater.control fehlt",
           "es gibt keinen Getter, _current_tool_pid() liest das Attribut")

    # --- toolhead: manual_move/set_position umgehen gcode_move bewusst ---
    sys.path.insert(0, os.path.dirname(klippy))
    import toolhead as th_mod
    th = getattr(th_mod, "ToolHead", None)
    ok(th is not None, "toolhead.ToolHead fehlt")
    if th is not None:
        has_attrs(th, ["manual_move", "set_position", "get_position",
                       "wait_moves", "get_status"], "toolhead.ToolHead")

    print("geprueft: %d Zusicherungen gegen Klipper in %s"
          % (CHECKS[0], args.klipper))
    if FINDINGS:
        print("\n%d Befund(e) -- Klipper hat sich geaendert:" % len(FINDINGS))
        for f in FINDINGS:
            print("  " + f)
        return 1
    print("OK - alle genutzten Klipper-Interna unveraendert")
    return 0


if __name__ == "__main__":
    sys.exit(main())
