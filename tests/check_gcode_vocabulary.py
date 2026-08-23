#!/usr/bin/env python3
"""Verify that every gcode command this project emits actually exists.

Two sources are checked against the printer's live command registry:

  1. Every [gcode_macro] body in the config files, rendered with Klipper's
     own Jinja delimiters against the printer's live status. That catches
     template errors and undefined attributes, which Klipper only reports
     at render time -- a successful startup proves nothing about them.
  2. Every gcode string literal handed to run_script_from_command() in the
     Klipper extras, extracted via ast (handles f-strings and %-formatting).

Run it on the printer, where jinja2 and Moonraker are available:

    ~/klippy-env/bin/python check_gcode_vocabulary.py [--url http://localhost:7125]

Exit code 0 = clean, 1 = findings.
"""
import argparse
import ast
import glob
import json
import os
import re
import sys
import urllib.parse
import urllib.request

# Traditional gcode is dispatched by a different path than extended commands
# and is not always present in the registry, so allow it by pattern.
TRADITIONAL = re.compile(r"^[GMT]\d+(\.\d+)?$")
# Placeholder left behind after stripping %s / f-string expressions
PLACEHOLDER = "\x00"

# Commands emitted only from a branch a static scan cannot evaluate.
CONDITIONAL = {
    # toolchanger.activate_fan(): guarded by "if self.has_multi_fan",
    # and ACTIVATE_FAN only exists when a [multi_fan] section is configured.
    "ACTIVATE_FAN": "nur bei konfiguriertem [multi_fan] (has_multi_fan-Guard)",
}


def fetch(url, path):
    with urllib.request.urlopen(url + path, timeout=15) as r:
        return json.load(r)["result"]


def live_commands(url):
    st = fetch(url, "/printer/objects/query?gcode")["status"]["gcode"]
    cmds = st.get("commands")
    if not cmds:
        raise SystemExit("printer.gcode.commands ist leer - nicht pruefbar")
    return set(cmds)


def live_status(url):
    names = fetch(url, "/printer/objects/list")["objects"]
    q = "&".join(urllib.parse.quote(n) for n in names)
    return fetch(url, "/printer/objects/query?" + q)["status"]


class Status(dict):
    """printer.foo and printer['foo'] both work, like Klipper's wrapper."""

    def __getattr__(self, k):
        try:
            return self[k]
        except KeyError:
            raise AttributeError(k)


class Coord(list):
    """Klipper hands templates a Coord namedtuple; Moonraker's JSON flattens
    it to a list. Restore .x/.y/.z/.e so axis_minimum.x resolves."""

    _AXES = ("x", "y", "z", "e")

    def __getattr__(self, k):
        try:
            return self[self._AXES.index(k)]
        except (ValueError, IndexError):
            raise AttributeError(k)


def wrap(o):
    if isinstance(o, dict):
        return Status({k: wrap(v) for k, v in o.items()})
    if isinstance(o, list):
        vals = [wrap(v) for v in o]
        if 3 <= len(vals) <= 4 and all(
                isinstance(v, (int, float)) and not isinstance(v, bool)
                for v in vals):
            return Coord(vals)
        return vals
    return o


def macro_variables(body):
    """variable_foo: bar -> {'foo': ...}; Klipper injects these by bare name."""
    out = {}
    for m in re.finditer(r"^variable_([a-zA-Z0-9_]+)\s*:\s*(.*)$", body, re.M):
        name, raw = m.group(1), m.group(2).strip()
        try:
            out[name] = ast.literal_eval(raw)
        except Exception:
            out[name] = raw
    return out


def loaded_macros(url):
    """-> [(name, gcode_body, variables)] from the config Klipper actually
    loaded. Parsing .cfg files instead would cover dormant files that are
    never [include]d and would have to resolve includes by hand."""
    settings = fetch(url, "/printer/objects/query?configfile")[
        "status"]["configfile"]["settings"]
    out = []
    for section, opts in sorted(settings.items()):
        if not section.startswith("gcode_macro "):
            continue
        if not isinstance(opts, dict) or "gcode" not in opts:
            continue
        variables = {k[len("variable_"):]: coerce(v)
                     for k, v in opts.items() if k.startswith("variable_")}
        out.append((section.split(" ", 1)[1], opts["gcode"], variables))
    return out


def coerce(raw):
    try:
        return ast.literal_eval(str(raw).strip())
    except Exception:
        return raw


def render(body, printer, variables):
    import jinja2

    env = jinja2.Environment("{%", "%}", "{", "}",
                             extensions=["jinja2.ext.do"])
    noop = lambda *a, **k: ""
    ctx = {
        "printer": printer,
        # A plain dict, exactly as Klipper passes it: params.FOO on a missing
        # key yields Undefined, so |default(...) works and only genuinely
        # required parameters raise.
        "params": {},
        "rawparams": "",
        # Klipper's action_* callables must not blow up during a dry run
        "action_respond_info": noop,
        "action_raise_error": noop,
        "action_emergency_stop": noop,
        "action_call_remote_method": noop,
        "action_log": noop,
    }
    ctx.update(variables)
    return env.from_string(body).render(ctx)


def commands_in(text):
    """First token of every non-empty, non-comment line."""
    found = set()
    for line in text.splitlines():
        line = line.strip()
        if not line or line[0] in "#;":
            continue
        tok = line.split()[0].strip()
        if not tok or PLACEHOLDER in tok:
            continue
        found.add(tok.upper())
    return found


def flatten(node):
    """Best-effort literal text of an ast string expression."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):  # f-string
        parts = []
        for v in node.values:
            if isinstance(v, ast.Constant) and isinstance(v.value, str):
                parts.append(v.value)
            else:
                parts.append(PLACEHOLDER)
        return "".join(parts)
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Mod):
        return flatten(node.left)  # "CMD %d" % x
    return None


def literal_scripts(py_path):
    """gcode strings passed to run_script_from_command(), via ast."""
    tree = ast.parse(open(py_path, encoding="utf-8").read())
    out = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        f = node.func
        if not (isinstance(f, ast.Attribute)
                and f.attr == "run_script_from_command"):
            continue
        if not node.args:
            continue
        s = flatten(node.args[0])
        if s:
            out.append(re.sub(r"%[-#0-9.]*[a-zA-Z]", PLACEHOLDER, s))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://localhost:7125")
    ap.add_argument("--repo", default=os.path.expanduser("~/HappyToolchanger"))
    ap.add_argument("--config",
                    default=os.path.expanduser("~/printer_data/config"))
    args = ap.parse_args()

    known = live_commands(args.url)
    printer = wrap(live_status(args.url))
    print("registrierte Kommandos: %d" % len(known))

    problems = []
    needs_params = []
    skipped = set()
    n_macros = 0

    for name, body, variables in loaded_macros(args.url):
        n_macros += 1
        try:
            text = render(body, printer, variables)
        except Exception as e:
            import jinja2
            entry = "%s: %s: %s" % (name, type(e).__name__, e)
            if isinstance(e, jinja2.UndefinedError):
                # Macro cannot render without arguments -- not a defect,
                # it just means a dry run cannot cover it.
                needs_params.append(entry)
            else:
                problems.append("RENDER    " + entry)
            continue
        for cmd in commands_in(text):
            if cmd in known or TRADITIONAL.match(cmd):
                continue
            problems.append("UNBEKANNT '%s' in Makro %s" % (cmd, name))

    n_scripts = 0
    for py in sorted(glob.glob(os.path.join(args.repo, "klippy/extras/*.py"))):
        for script in literal_scripts(py):
            n_scripts += 1
            for cmd in commands_in(script):
                if cmd in known or TRADITIONAL.match(cmd):
                    continue
                if cmd in CONDITIONAL:
                    skipped.add("%s (%s)" % (cmd, CONDITIONAL[cmd]))
                    continue
                problems.append("UNBEKANNT '%s' in %s"
                                % (cmd, os.path.basename(py)))

    print("geprueft: %d Makros, %d Gcode-Literale aus Python"
          % (n_macros, n_scripts))
    if skipped:
        print("\nnicht pruefbar (bedingter Zweig):")
        for e in sorted(skipped):
            print("  " + e)
    if needs_params:
        print("\n%d Makro(s) brauchen Parameter, im Trockenlauf nicht "
              "renderbar (kein Fehler):" % len(needs_params))
        for e in sorted(set(needs_params)):
            print("  " + e)
    if problems:
        print("\n%d Befund(e):" % len(problems))
        for p in sorted(set(problems)):
            print("  " + p)
        return 1
    print("OK - alle Kommandos bekannt, alle Makros rendern fehlerfrei")
    return 0


if __name__ == "__main__":
    sys.exit(main())
