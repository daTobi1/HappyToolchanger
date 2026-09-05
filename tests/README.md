# Tests

`check_webapp_recovery.js` laeuft **lokal** (`node tests/check_webapp_recovery.js`),
die beiden Python-Tests laufen **auf dem Drucker** (sie brauchen `jinja2` aus
Klippers venv und einen laufenden Moonraker). Keiner davon bewegt etwas.

```bash
scp tests/*.py biqu@<IP>:/tmp/
ssh biqu@<IP> '~/klippy-env/bin/python /tmp/check_gcode_vocabulary.py'
ssh biqu@<IP> '~/klippy-env/bin/python /tmp/check_klipper_api.py'
ssh biqu@<IP> '~/klippy-env/bin/python /tmp/check_qgl_probe_choice.py'
ssh biqu@<IP> '~/klippy-env/bin/python /tmp/check_homing_rebound.py'
ssh biqu@<IP> '~/klippy-env/bin/python /tmp/check_tool_extruders.py'
ssh biqu@<IP> 'python3 /tmp/check_htc_heater_fan.py'   # braucht htc_heater_fan.py daneben
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

## `check_xy_offset_ui.js`

Prüft die Entscheidungslogik des XY-Offset-Blocks und des XY-Assistenten
aus der Offset-Webapp — ohne Browser, ohne Drucker. Wie
`check_webapp_recovery.js` werden einzelne Funktionen als Text aus
`webapp/js/tools.js` herausgeschnitten und gegen gestubbte globale
Zustandsvariablen, `fetch`, `$.get` und `confirmDialog` laufen gelassen.

```bash
node tests/check_xy_offset_ui.js
```

63 Zusicherungen in fünf Teilen; die Teile überschreiben nacheinander
dieselben Globals, die Reihenfolge in der Promise-Kette am Dateiende ist
deshalb Teil des Aufbaus.

**Teil 1 — Referenztool und Messwert-Erkennung**

Der Offset der Kameramethode ist, wie beim Eddy-Sweep, die Differenz zur
zuletzt festgehaltenen Position des Referenztools — nur so sind beide
Verfahren vergleichbar.

| Zusicherung | warum sie zählt |
|---|---|
| Referenz- und Zieltool erfasst → Offset ist die Differenz | Grundfunktion |
| Ziel- bzw. Referenztool nicht erfasst → `null` | die Tabelle zeigt „nicht gemessen" statt `NaN` |
| keine Position erfasst → `null` für jedes Tool | Zustand vor der ersten Messung |
| `ref_tool` explizit gesetzt → wird respektiert, auch gegen die UI-Auswahl | hat Klipper gemessen, gilt sein Bezugspunkt |
| kein `ref_tool`, kein T0 in der Config → kleinstes Tool ist Referenz | ein hart verdrahtetes `0` machte die Kameramethode auf fremden Configs ohne T0 unbenutzbar (Generality-Requirement) |
| kein `ref_tool` → UI-Auswahl aus dem Kalibrier-Abschnitt gilt | wer T2 als Referenz anhakt, bekam trotzdem T0 beschriftet |
| `xyMeasured()`: Eintrag ohne `x` bzw. `y` → kein Messwert | ein Eintrag ohne Fit-Ergebnis ließ `res.x.toFixed(3)` in der ungefangenen Poll-Kette werfen — das fror Dock-, PID- und Probe-Tabelle gleich mit ein, alle 2 s, sichtbar nur in der Konsole |
| `x = 0` zählt trotzdem als Messwert | 0 ist ein gültiger Offset, keine Abwesenheit |

**Teil 2 — `parseXyProbeSerial()`, `xyStepOk()`, `xyIsHomed()`, `writeXyConfigs()`**

| Zusicherung | warum sie zählt |
|---|---|
| Platzhalter `HIER_EINTRAGEN` bzw. fehlende `serial:`-Zeile → Fehler | sonst wird ein `undefined`-Pfad in die scharfe Config geschrieben |
| `xyStepOk({transport:true})` → **false** | `/printer/gcode/script` bleibt offen, bis das Skript fertig ist: Verbindungsabbruch heißt „läuft noch", nicht „fertig". PID- und Z-Lauf halten darauf an; nur der Assistent hatte daran maschinenbewegende Folgeschritte gekettet |
| `xyIsHomed("xy")` / `""` / `undefined` → nicht gehomt | die Prüfung, die `ensureHomedAfterActivate()` nach dem `G28` **wiederholt** |
| fehlender Config-Schlüssel → `false` **und** kein Upload | vorher meldete derselbe Vorgang gleichzeitig Erfolg und „nicht geschrieben" |

**Teil 3 — Assistenten-Kontrollfluss**

| Zusicherung | warum sie zählt |
|---|---|
| Abbruch bei „Aufsetzen" ruft `xyProbeDeactivate()` | die Sonde ist zu dem Zeitpunkt schon aktiviert; ein stiller Abbruch ließe Klipper beim nächsten Start scheitern |
| nach dem Trockenlauf wird nachgefragt, bevor der Messlauf startet | der Trockenlauf existiert genau dafür, dass man abbrechen kann — vorher schob ein Verbindungsabbruch den absenkenden Messlauf sofort hinterher |
| „Nein" nach dem Trockenlauf → `CALIBRATE_XY_OFFSETS` wird nicht gesendet | — |
| Drucker nach dem Messlauf nicht idle → kein „Abschließen", kein Deaktivieren | `xyProbeDeactivate()` macht `FIRMWARE_RESTART`; der fiele sonst mitten in die Fahrt |
| Regelfall (`transport`, danach idle) → vollständige Dialogfolge, genau ein Deaktivieren | — |
| `ensureHomedAfterActivate()`: `transport` **oder** `{ok:true}` allein → Abbruch; erst `homed_axes` mit x/y/z zählt | der nächste Dialog bittet darum, die Halterung **auf das Bett** zu stellen — das darf erst kommen, wenn der Drucker steht |

**Teil 4/5 — Kamera-„Position übernehmen"**

| Zusicherung | warum sie zählt |
|---|---|
| erfasst wird das **montierte** Tool, nicht das angehakte Referenztool | der Nutzer zentriert die Düse des Tools, das er in der Hand hatte |
| `tool_number < 0` oder kein `toolchanger`-Objekt → Dialog statt Capture | fremde Configs müssen ohne Toolchanger-Modul durchlaufen |
| abgelehnte `toolchanger`- bzw. `gcode_move`-Abfrage → Dialog statt Stille | ein verpuffter Klick sieht aus wie ein erfasster Wert |

**Deckt nicht ab:**

* **`renderXyBlock()`, `applyXyOffset()` und `applyAllXyOffsets()` — die drei
  Funktionen, die tatsächlich Maschinen-Offsets schreiben — haben keine
  einzige Zusicherung.** Geprüft ist nur die Hilfsfunktion `xyMeasured()`,
  die sie benutzen, und `writeXyConfigs()` als deren letzte Stufe. Ob der
  Bestätigungsdialog die richtigen Vorher-/Nachher-Werte zeigt, ob das
  Referenztool übersprungen wird und ob `SET_TOOL_GCODE_OFFSET` mit den
  Werten rausgeht, die in der Tabelle stehen, ist unverifiziert.
* **Nichts davon wurde je in einem Browser gesehen.** Der gesamte Block ist
  bisher ausschließlich gegen diesen Node-Harness gelaufen. Tabellenaufbau,
  Methodenwechsel (Kamera/Eddy-Sweep), Sparkline und die `onclick`-Handler
  der Tabelle sind ungeprüft — dieselbe Lücke, die bei
  `check_webapp_recovery.js` schon einmal einen grünen Test neben einem
  unsichtbaren Dialog stehen ließ.
* Die Klipper-Seite. `nozzle_locator`, `CALIBRATE_XY_OFFSETS` und
  `xy_results` existieren noch nicht; alle Assistenten-Tests laufen gegen
  gestubbte Sender. Wie sich der echte Messlauf verhält, sagt kein Test hier.

## `check_nozzle_locator_fit.py`

Prüft die Fit-Mathematik der XY-Offset-Ortung. Braucht **keine
Druckerhardware und kein Klipper** — nur einen Python-Interpreter:

```bash
python3 tests/check_nozzle_locator_fit.py
```

Auf dem Windows-Entwicklungsrechner ist keiner installiert, dort also über
den Pi (der Drucker bewegt sich dabei nicht):

```bash
scp tests/check_nozzle_locator_fit.py klippy/extras/nozzle_locator_fit.py biqu@<IP>:/tmp/
ssh biqu@<IP> 'cd /tmp && python3 check_nozzle_locator_fit.py'
```

Seit dem Scanmodus (2026-09-04) prüft er außerdem die Bausteine des
kontinuierlichen Sweeps: `bin_points` (Körbe ganz im Fenster, mittlere
Sample-Position statt Korbmitte, leere Körbe fehlen) und `samples_to_track`
(Zeitstempel → Bahnposition, Stillstand und Fensterfremdes fallen weg,
Diagonale als Bogenlänge, Latenz wird vom Zeitstempel abgezogen).

Der Anlass ist ein Denkfehler, der beinahe ins Verfahren eingebaut worden
wäre. Der ursprüngliche Schluss lautete: ein Peak-Fit ist gegen additive
Ablagen invariant, also stört der Temperaturgang der Basislinie nicht. Das
gilt aber nur für eine *konstante* Ablage. Der Sweep läuft monoton in x —
ein zeitlinearer Drift wird dadurch zu einem linearen Term in x und
verschiebt den Scheitel um `m/(2a)`, bei den gemessenen Werten des 250ers
rund 19 µm pro Kelvin.

Zwei Eigenschaften machen das gefährlich: die Verschiebung ist ein **Bias,
keine Streuung** (alle Läufe fahren dieselbe Richtung, also sieht σ sie
nicht), und sie kürzt sich **nicht** zwischen den Tools weg (das
Referenztool wird mit kalter Spule gemessen, spätere Tools mit warmer).

| Zusicherung | warum sie zählt |
|---|---|
| Fit ohne Drift trifft das bekannte Zentrum | Grundfunktion |
| Einzelsweep verschiebt sich um exakt `m/(2a)` | nagelt den Fehlerbetrag fest, statt ihn zu behaupten |
| bidirektionaler Mittelwert hebt ihn auf | der eigentliche Fix |
| konstante Ablage verschiebt nichts | die Hälfte der Ursprungsannahme, die stimmt |
| `sweep_quality` lehnt zu schwaches Signal ab | ein Ad-hoc-Skript ohne diesen Guard hat im Vorversuch fünf wertlose Läufe produziert |
| `sweep_quality` lehnt Scheitel am Fensterrand ab | halb erfasstes Ziel liefert einen plausibel aussehenden Unsinnswert |
| Tal statt Berg wird abgewiesen | bei gehärtetem Stahl kann das Vorzeichen drehen |

**Deckt nicht ab:** ob die reale Glocke überhaupt einer Parabel ähnelt. Der
Vorversuch zeigt eine leichte Schiefe, die in X stärker ist als in Y — der
Fit über einen *festen* Punktesatz ist deshalb Pflicht, ein
schwellwertabhängiges Fenster verschob den Scheitel schon um 47 µm.

## `check_qgl_probe_choice.py`

Prüft, welche Probe `QUAD_GANTRY_LEVEL` wählt — ohne den Drucker zu bewegen.

Der Anlass: T0 mit Eddy war montiert, QGL nahm trotzdem den mechanischen Tap
und heizte dafür die Düse auf. Die Auswahl-Logik war nicht falsch, der
*Zeitpunkt* war es. Klipper rendert ein `gcode_macro` komplett, bevor die
erste Zeile läuft — `INITIALIZE_TOOLCHANGER` und `DETECT_ACTIVE_TOOL_PROBE`
standen im selben Makro und kamen damit zu spät. Im Log an der Reihenfolge
ablesbar:

```
02:48:00  // QGL mit T0: coarse=eddy, fine=eddy
02:48:02  // toolchanger initialized, active tool T0
```

Nach einem QGL-Abbruch steht `tool_number` auf `-1`, `_T-1_QGL` gibt es nicht,
also griff der Default `"tap"` — still.

Der Test rendert `_QGL_FOR_ACTIVE_TOOL` gegen den Live-Status, einmal je
Szenario, und liest aus `M117 QGL coarse (..)` ab, welche Probe gewählt würde:

Der zweite Fall kam später dazu und war ein Eigentor. Weil der Eddy nur bis
rund 2.5 mm über dem Bett liest, fiel der grobe Durchgang auf den Tap zurück,
solange `quad_gantry_level.applied` noch `false` war. Nur: `applied` ist nach
**jedem** Klipper-Neustart `false`, und genau dann fährt man QGL. Die Regel hat
`_T0_QGL` mit `coarse_probe: "eddy"` damit praktisch immer überstimmt — im Log
sichtbar als

```
// QGL: Gantry noch nicht geleveled - grober Durchgang mit Tap, ...
// QGL mit T0: coarse=tap, fine=eddy
```

Das Sicherheitsnetz ist deshalb jetzt ein Schalter (`eddy_needs_level`) und
steht auf **aus**: es gilt die Probe aus `_Tn_QGL`. Einschalten lässt es sich
global in `[gcode_macro QUAD_GANTRY_LEVEL]`, pro Tool mit demselben
`variable_eddy_needs_level` in `_Tn_QGL`, oder pro Lauf mit `NEEDS_LEVEL=1`.

| Szenario | erwartet (coarse/fine) |
|---|---|
| Eddy-Tool, Gantry geleveled | `eddy/eddy` |
| **Eddy-Tool, Toolchanger auf `-1`** | `eddy/eddy` — der Fall aus dem Fehlerbericht |
| **Eddy-Tool, Gantry NICHT geleveled** | `eddy/eddy` — die Tool-Einstellung gilt |
| dito, `NEEDS_LEVEL=1` | `tap/eddy` — Netz an, Eddy wäre außer Reichweite |
| dito, `NEEDS_LEVEL=1 COARSE_PROBE=eddy` | `eddy/eddy` — explizite Angabe hat Vorrang |
| Netz an, Gantry geleveled | `eddy/eddy` — Netz greift nur ungelevelt |
| **Netz per `_Tn_QGL` an, nicht geleveled** | `tap/eddy` — pro Tool schaltbar |
| dito, aber `NEEDS_LEVEL=0` | `eddy/eddy` — Aufruf schlägt Tool-Einstellung |
| Tap-Tool montiert | `tap/tap` |
| Tap-Tool, Gantry nicht geleveled | `tap/tap` |
| kein Tool erkennbar | Abbruch mit Fehler, **nicht** stiller Rückfall auf `tap` |

Gegen die alte Fassung gerechnet fällt der Test durch — er prüft also wirklich
den Fehler und nicht nur sich selbst.

**Deckt nicht ab:** ob die gewählte Probe brauchbare Werte liefert. Steht das
Gantry mehrere Millimeter schief, liest der Eddy an der tiefen Ecke Unsinn und
QGL bricht ab (`required adjustment 640.7 > max_adjust 60`). Dann einmal
`QUAD_GANTRY_LEVEL COARSE_PROBE=tap` fahren — oder `NEEDS_LEVEL=1` setzen,
wenn das regelmäßig vorkommt.

## `check_tool_extruders.py`

Prüft, dass jedes Tool seinen eigenen Extruder und Lüfter hat.

Der Anlass: `[tool T5]` stand auf `extruder: extruder` statt `extruder5` —
ein Tippfehler in einer Zeile. Aufgefallen ist er erst bei der PID-Übernahme,
weil die Webapp daraufhin in `T5.cfg` nach einer Sektion `[extruder]` suchte,
die dort nicht existiert. Die eigentliche Folge wäre schlimmer gewesen:
`SELECT_TOOL` hätte bei T5 den Extruder von **T0** aktiviert — also mit dem
falschen Motor extrudiert und die falsche Düse geheizt. Alle anderen sechs
Verweise in derselben Datei waren korrekt, nur dieser eine nicht.

Der Test prüft nicht die Dateistruktur, sondern die Bedeutung: auf einem
Toolchanger hat jedes Tool eigene Hardware. Zwei Tools, die auf denselben
Extruder oder Lüfter zeigen, sind ein Fehler — unabhängig davon, wie die
Configs auf Dateien verteilt sind. Das bleibt auch auf fremden
Konfigurationen gültig.

| Zusicherung | warum sie zählt |
|---|---|
| jedes Tool hat `extruder:` gesetzt | ohne fällt Klipper still auf `extruder` zurück |
| der genannte Extruder existiert | Tippfehler im Namen fliegen sonst erst beim Wechsel auf |
| kein Extruder wird von zwei Tools benutzt | genau der Fall T0/T5 |
| kein Lüfter wird von zwei Tools benutzt | dieselbe Copy-Paste-Klasse |

Gegen den kaputten Zustand gerechnet meldete er `T0 und T5 teilen sich
extruder 'extruder'`.

**Deckt nicht ab:** ob die Zuordnung *physisch* stimmt. Dass T3 auf
`extruder3` zeigt heißt nicht, dass an T3 auch das Kabel von `extruder3`
steckt.

## `check_htc_heater_fan.py`

Prüft die Entscheidungslogik von `klippy/extras/htc_heater_fan.py` — den
Hotend-Lüfter mit Drehzahl je Zustand. Braucht **kein Klipper und keinen
Drucker**, nur Python 3 (auf dem Windows-Rechner also über den Pi):

```bash
scp tests/check_htc_heater_fan.py klippy/extras/htc_heater_fan.py biqu@<IP>:/tmp/
ssh biqu@<IP> 'cd /tmp && python3 check_htc_heater_fan.py'
```

Der Anlass: Klippers `[heater_fan]` kennt eine feste Drehzahl und kein
Kommando dagegen. Die Lüfter an den Hotends sind zu stark, und bei warmem
Gehäuse sollen sie mehr Luft geben. Bewusst **keine** Kennlinie nach
Hotend-Temperatur — die wird schon per PID geregelt, ein zweiter Regler auf
derselben Größe könnte schwingen.

| Zusicherung | warum sie zählt |
|---|---|
| Sollwert gesetzt → an, auch wenn noch kalt | wie `[heater_fan]`: Luft, sobald geheizt wird |
| Sollwert 0, noch über `heater_temp` → `cooldown` | Abkühlen braucht Luft, egal ob montiert |
| genau `heater_temp` ist noch nicht „über" | Klipper prüft `>`; ein `>=` hielte den Lüfter einen Tick länger an |
| `min_speed` hebt an, schaltet aber kalt nicht ein | Untergrenze gegen Heat-Creep, kein Dauerläufer |
| `SPEED=` ersetzt nur die Zustandsdrehzahl, `min_speed` bleibt | ein Tippfehler im Override darf die Düse nicht verstopfen |
| Gehäuse-Anhebung senkt nie unter die Zustandsdrehzahl | `chamber_max_speed` unter `fan_speed` darf nicht drosseln |
| Sensorausfall (`None`) → Zustandsdrehzahl, nicht 0 | ein kaputter Gehäusesensor darf den Hotend-Lüfter nicht stoppen |
| `temp_full <= temp_start` → keine Anhebung | statt Division durch 0 |
| Totband 2 % für die Anhebung, Zustandswechsel und Ein/Aus immer sofort | die Anhebung soll nicht jede Sekunde ein PWM-Update schicken |

**Deckt nicht ab:** die Klipper-Anbindung — Tool-Zuordnung über den
Extruder, Timer, `SET_HEATER_FAN`. Das sichert `check_klipper_api.py`
(Signaturen von `fan.Fan`, `heater.get_temp`, `tool.extruder_name`) und ein
Blick auf `printer["htc_heater_fan Tn_hotend_fan"].state` am laufenden
Drucker.

## `check_nozzle_map.js`

Prüft die reinen Funktionen des Raster-Viewers `webapp/js/map.js`
(C-Scan aus `NOZZLE_LOCATOR_MAP`): Basislinien-Abzug, Differenzbild nur auf
gleichem Gitter, `null`-Zellen, Wertebereich, vorzeichenbehaftete Log-Skala,
Farbrampe. Läuft lokal ohne DOM:

```bash
node tests/check_nozzle_map.js
```

Seit der Live-3D-Ansicht prüft `check_nozzle_map.js` zusätzlich `mapToSurface`
aus `webapp/js/map3d.js` (Basislinie, `null`-Zellen, Fortschritt, Titel), und
`check_xy_offset_ui.js` das Kommando-Bauen `xyMapCommand()` des Raster-Panels
(Grenzen, Label-Zeichen, Unsinn statt Zahl).
Seit dem 2026-09-04 (abends) auch `imageToMap()` -- das Messbild aus
`printer.offset.xy_results` im Dateiformat von `NOZZLE_LOCATOR_MAP`, für
`map.html?src=xy&t=…&i=…`.

## `check_nozzle_overlay.js`

Prüft die reinen Funktionen des Überlagerungs-Editors `webapp/js/overlay.js`
(zwei Messbilder übereinander legen): Ebene aus einem Raster-Messbild,
Ausrichten von B auf den gemessenen Scheitel von A, Verschieben, Normieren
auf Spitze 1, Höhenlinien per Marching Squares (Kreis um einen Gauss-Buckel,
geschlossen, `null`-Zellen), Plotly-Spuren für 2D (Linien + Scheitelkreuz) und
3D (zwei Flächen) sowie die Auswahlliste aus `xy_results`:

```bash
node tests/check_nozzle_overlay.js
```

Dazu in `check_xy_offset_ui.js`: `xyOverlayLayers()` (Felder des Editors ->
Ebenen samt Verschiebung), `xyProgressHtml()`/`xyProgressDone()` des
Fortschrittsdialogs beim Messlauf und der Messbild-Dialog mit Log-Umschalter,
2D-Link und Überlagern-Knopf; im Assistenten-Test, dass der Fortschrittsdialog
VOR dem Senden des Messlaufs aufgeht.

## `check_fit_radius.js`

Prüft die Fit-Radius-Ermittlung `webapp/js/fitradius.js`: den Paraboloid-Fit
(Kleinstequadrate wie `nozzle_locator_fit.paraboloid_fit`, exakte Parabel bei
jedem Radius, Sattel und zu wenige Punkte werfen), `imagePoints` (Basislinie,
`null`), `radiusSweep` und den Vorschlag `suggestFitRadius` (Plateau-Kriterium:
größter Radius, bei dem der Scheitel aller Tools innerhalb 25 µm um seinen
Median bleibt; ein einseitiger Ausläufer wirft die großen Radien raus):

```bash
node tests/check_fit_radius.js
```

Dazu in `check_xy_offset_ui.js`: Knopf „ermitteln" am Feld, Tabelle
`xyFitRadiusTableHtml()`, Übernahme des Vorschlags als Default.
