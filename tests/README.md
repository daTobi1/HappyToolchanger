# Tests

`check_webapp_recovery.js` laeuft **lokal** (`node tests/check_webapp_recovery.js`),
die beiden Python-Tests laufen **auf dem Drucker** (sie brauchen `jinja2` aus
Klippers venv und einen laufenden Moonraker). Keiner davon bewegt etwas.

```bash
scp tests/*.py biqu@<IP>:/tmp/
ssh biqu@<IP> '~/klippy-env/bin/python /tmp/check_gcode_vocabulary.py'
ssh biqu@<IP> '~/klippy-env/bin/python /tmp/check_klipper_api.py'
```

Exit-Code 0 = sauber, 1 = Befunde.

## `check_gcode_vocabulary.py`

Prüft, dass jedes GCode-Kommando, das dieses Projekt absetzt, auch existiert.

Ein erfolgreicher Klipper-Start beweist das **nicht**: Klipper kompiliert die
Jinja-Templates beim Laden, rendert sie aber erst beim Aufruf. Tippfehler in
Kommandonamen und undefinierte Attribute fliegen also erst mitten im Druck auf.

Zwei Quellen:

1. **Jedes geladene `[gcode_macro]`**, gerendert mit Klippers eigenen
   Jinja-Delimitern (`{% %}` / `{ }`) gegen den **Live-Status** des Druckers.
   Die Makros kommen aus `printer.configfile.settings`, also aus der Config,
   die Klipper tatsächlich geladen hat — nicht aus den `.cfg`-Dateien. Sonst
   würden nie `[include]`-te Dateien mitgeprüft (z.B. `print_area_bed_mesh.cfg`
   liegt im Config-Ordner, ist aber nicht eingebunden).
2. **Jedes GCode-Stringliteral** aus `run_script_from_command()` in
   `klippy/extras/*.py`, extrahiert per `ast` (behandelt f-Strings und
   `%`-Formatierung).

Aus beiden wird das erste Token jeder Zeile gegen `printer.gcode.commands`
geprüft, Klippers Registry aller registrierten Kommandos.

**Deckt nicht ab:** Parameternamen und -werte, nur Kommandonamen. Makros, die
ohne Argumente nicht renderbar sind, werden als solche ausgewiesen statt als
Fehler. Kommandos hinter einem Laufzeit-Guard stehen in `CONDITIONAL` (mit
Begründung), weil ein statischer Scan die Bedingung nicht auswerten kann.

## `check_klipper_api.py`

Unsere Extras sind keine Plugins gegen eine stabile API — sie erben von
Klipper-Interna und rufen sie direkt auf. Ein Klipper-Update kann jedes davon
ohne Vorwarnung verschieben, und der Ausfall zeigt sich erst mitten im Druck.

Der Test prüft die genutzte Oberfläche vorab, damit ein Update **den Test**
bricht statt den Drucker. Abgedeckt sind u.a.:

| Zusicherung | warum sie zählt |
|---|---|
| `manual_probe.create_probe_result` subtrahiert `z_offset` | `bed_z = trigger_z − z_offset` ist die Grundlage **aller** Offset-Formeln |
| `homing.Homing._probing_home` macht `curpos[2] -= ppos.bed_z` | nur dadurch trifft `G28 Z` den Nozzle-Kontakt |
| `homing.Homing._create_probe_gcmd` übergibt `PROBE_SPEED` | überschreibt `speed:` aus `[tool_probe]` → Tap-Überfahrt, siehe `homing_retract_dist` |
| `homing.Homing.home_rails` sendet **kein** `home_rails_end` im Probe-Pfad | Begründung für den `G28`-Wrapper; fällt das weg, geht es per Event eleganter |
| `gcode._get_extended_params` nutzt `shlex` | sonst zerbricht `PROBE="probe_eddy_ng my_eddy"` am Leerzeichen |
| `probe.ProbeEndstopWrapper.__init__(config, probe_offsets, param_helper)` | `tool_probe.py` konstruiert das direkt |
| `gcode_move`: `reset_last_position`, `cmd_G1`, `homing_position`, `last_position` | direkte Toolhead-Moves umgehen den Cache und müssen resynct werden |

**Deckt nicht ab:** Verhalten. Dass die Interna existieren heißt nicht, dass
sie dasselbe *tun*. Ein echter Homing-Lauf bleibt der einzige Beweis.

## `check_webapp_recovery.js`

Prüft die Fehlerbehandlung der Offset-Webapp — ohne Browser, ohne Drucker.
Die Funktionen werden aus `webapp/js/tools.js` herausgeschnitten und gegen
gestubbte `$.get`/`confirmDialog` laufen gelassen.

```bash
node tests/check_webapp_recovery.js
```

Der Anlass: ein Abbruch wegen fehlendem Homing/QGL erschien nur als Toast und
sah aus wie „nichts passiert". Jetzt kommt ein Popup mit OK **und** einem
Button, der die Vorbereitung nachholt und den Lauf fortsetzt. Was der Test
festnagelt:

| Zusicherung | warum sie zählt |
|---|---|
| `Must home first` → `G28` → `QUAD_GANTRY_LEVEL` → `G28 Z` | QGL kippt das Gantry, ohne das abschließende `G28 Z` startet der Lauf mit falschem Z |
| nur QGL fehlt → **kein** unnötiges `G28` vorweg | ein Re-Home kostet Minuten, wenn der Drucker längst gehomed ist |
| Vorbereitung + Lauf gehen als **ein** Request raus | zwischen zwei Requests könnte etwas anderes dazwischenlaufen |
| zweiter Fehlschlag zeigt nur noch die Meldung | sonst bietet der Dialog endlos „nochmal" an |
| Transportfehler öffnet **keinen** Dialog | da läuft der Job weiter, das ist kein Fehler |
| unbekannter Fehler → Dialog **ohne** Recovery-Button | nur beheben, was sich wirklich beheben lässt |

**Deckt nicht ab:** das DOM — und genau dort saß der eigentliche Fehler.
Der Node-Test war grün, während im Browser gar kein Popup erschien: Bootstrap
verwirft `show()`, solange das Modal noch aus der vorigen Nutzung ausblendet,
und meldet das nicht. Der Bestätigungsdialog fuhr gerade zu, der HTTP-400 kam
~50 ms später zurück — Inhalt wurde gesetzt, sichtbar wurde nichts.

Diese Sequenz lässt sich nur im Browser prüfen. Offset-UI öffnen
(`http://<IP>:3000/`), DevTools-Konsole, und einfügen:

```js
const el = document.getElementById('confirmModal');
const wait = ms => new Promise(r => setTimeout(r, ms));
const pm = document.getElementById('printerModal');
if (pm.classList.contains('show')) bootstrap.Modal.getOrCreateInstance(pm).hide();
await wait(600);
const p1 = confirmDialog({title:'TEST-1', body:'x', okLabel:'OK'});
await wait(600);
document.getElementById('confirmModalOk').click();
await p1;
await wait(50);                       // Fehler kommt mitten in der Transition
alertDialog('TEST-2 FEHLER', 'Must home first', {extraLabel:'Home + QGL'});
await wait(1200);
console.log('sichtbar:', el.classList.contains('show'));   // muss true sein
```

`sichtbar: false` heißt: der Fehlerdialog wird wieder verschluckt.
