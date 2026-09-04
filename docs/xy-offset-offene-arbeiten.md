# XY-Offset-Kalibrierung: offene Arbeiten und Befunde der ersten Messläufe

Die XY-Offset-Kalibrierung per Eddy-Spule ist entworfen, gebaut und am 250er einmal komplett gelaufen (Stand 2026-09-04, Abschnitt 8; Scanmodus und Bewegungsformen in Abschnitt 9). Dieses Issue hält fest, was noch zu tun ist, was beim Bauen zu beachten war, welche Stellen ohne Hardware entstanden sind, und was die ersten echten Messläufe gelehrt haben. Die Abschnitte 1–7 sind die Vorgeschichte, Abschnitt 8 der aktuelle Stand.

**Design:** `docs/superpowers/specs/2026-08-31-eddy-xy-offset-design.md`
**Plan:** `docs/superpowers/plans/2026-08-31-eddy-xy-offset.md`

---

## 1. Stand

| Task | Zustand |
|---|---|
| 1 — Fit-Mathematik (`nozzle_locator_fit.py`) | **fertig**, 80 Zusicherungen, getestet (Scan-, Raster- und Klemm-Bausteine seit 2026-09-04, Abschnitt 9) |
| 2 — Sensoranbindung (`nozzle_locator.py`) | **fertig**, am 250er verifiziert (2026-09-03), siehe 2.1 |
| 3 — Z-Anfahrt, Sweep, Ortung | **fertig**, am 250er verifiziert (2026-09-04), siehe Abschnitt 8 |
| 4 — Extraktion `_resolve_tool_run()` | **gestrichen** (siehe unten) |
| 5 — `CALIBRATE_XY_OFFSETS` | **fertig**, ein kompletter Lauf über T0–T3 am 250er (2026-09-04), siehe Abschnitt 8 |
| 6 — Extraktion `updateConfigFile()` | **fertig**, Test grün |
| 7 — XY-Block in der Webapp | **vorgebaut ohne Hardware**, Node-Tests gruen, siehe Abschnitt 4 |
| 8–9 — Assistent, Kamera-Position | **vorgebaut ohne Hardware**, siehe Abschnitt 4 |

Die Webapp-Tests umfassen 63 Zusicherungen (`tests/check_xy_offset_ui.js`), der Fit 80 (`tests/check_nozzle_locator_fit.py`). Ein Abschluss-Review über den gesamten Umfang ist gelaufen; sein einziger blockierender Befund (`transport` als Erfolg behandelt) ist behoben, siehe 4.5.

**Task 4 wurde bewusst gestrichen.** Die Annahme, `CALIBRATE_ALL_Z_OFFSETS` und `CALIBRATE_PROBE_OFFSETS` lösten dieselbe Tool-Auswahl doppelt, war falsch — sie haben absichtlich verschiedene Politiken (u. a. wählt das zweite ohne `TOOLS` nur Tools mit vorhandenen Z-Switch-Daten und erzwingt das Referenztool weder in die Liste noch an deren Anfang). Ein gemeinsamer Helfer hätte das zweite Kommando im Normalfall verändert. Details in Task 4 des Plans.

---

## 2. Hardware — vor dem Bestellen lesen

### 2.1 KRITISCH: Firmware

**Die neue Spule braucht die *normale* Klipper-Firmware, NICHT die von eddy-ng.**

Grund: eddy-ng bringt einen eigenen MCU-Befehlssatz mit (`config_ldc1612_ng`, `ldc1612_ng_start_stop`, `ldc1612_ng_setup_home`, …, siehe `eddy-ng/ldc1612_ng.py`) und dafür eigene Firmware unter `eddy-ng/src/eddy_ng/`. Das neue Modul `nozzle_locator.py` benutzt bewusst Klippers **eingebauten** `ldc1612`-Treiber, und der spricht den Standard-Befehlssatz (`config_ldc1612`, `query_ldc1612_status`). Die beiden sind nicht kompatibel.

Das ist kein Nachteil, sondern Absicht: beide Sensoren bleiben vollständig unabhängig, verschiedene Firmware, keine Kollision von Kommandos oder Config-Sektionen. Der Grund für diese Entscheidung war ohnehin ein anderer — eine zweite `[probe_eddy_ng]`-Sektion lässt Klipper gar nicht erst starten, weil `eddy-ng/probe_eddy_ng/probe.py:186` seine Kommandos global und nicht instanz-skopiert registriert.

**Erledigt am 2026-09-03, und so lief es tatsächlich (BTT Eddy Duo, per USB am 250er):**

- **Die Werksfirmware spricht kein USB.** Rote LED an, blaue blinkt, USB/CAN-Schalter auf USB — und der Kernel sieht beim Einstecken *nichts*, nicht einmal einen fehlgeschlagenen Verbindungsversuch. Das sieht aus wie ein Ladekabel und ist keins. Welche Firmware ab Werk drauf ist, dokumentiert BTT nirgends.
- **Der Beweis, dass Kabel und Port taugen, ist der BOOT-Taster:** halten, einstecken, loslassen → der feste RP2040-Bootloader meldet sich als `RP2 Boot` (2e8a:0003) mit einem Laufwerk `RPI-RP2`. Das verändert nichts und trennt Kabelproblem von Firmwareproblem in einem Schritt.
- **Geflasht wurde Standard-Klipper** aus einem sauberen Worktree des Host-Klippers (`~/klipper-xyprobe` auf dem 250er, gleiche Version wie der Host): `MACH_RP2040`, `FLASH_START_0100`, `RPXXXX_USB`, `WANT_LDC1612`, und **`RP2040_FLASH_GENERIC_03` (CLKDIV 4)** — BTTs README warnt, dass die Sonde mit der CLKDIV-2-Vorgabe „nur sporadisch beim Einschalten startet". Laufwerk mounten, `klipper.uf2` kopieren, fertig; die Sonde meldet sich als `usb-Klipper_rp2040_504434041088E21C-if00`.
- **Späteres Neuflashen ohne Taster:** die Klipper-Firmware geht per 1200-Baud-Request in den Bootloader (`serial.Serial(pfad, 1200)`), braucht dafür aber ein paar Sekunden mehr, als man erwartet — dann wieder mounten und kopieren.
- **Config nach der offiziellen BTT-Vorlage** (`sample-bigtreetech-eddy.cfg`): `i2c_bus: i2c0f`, `restart_method: command`, dazu die beiden Temperatursensoren (MCU und Spulen-NTC an `gpio26`). Die Spulentemperatur gehört zu jeder Messung — der Drift-Bias hängt an ihr.
- **Ergebnis:** `NOZZLE_LOCATOR_READ DURATION=1.0` → 3.132.430 Hz, sd 21 Hz, 480 Samples, 0 Fehler; Samplezahl skaliert mit der Dauer, der Sensor stoppt nach jeder Messung sauber (`LDC1612 finished` im Log). Spule 34 °C, MCU 39 °C. Eine von Hand darüber gehaltene Düse: ~3.142.000 Hz, also **+9.700 Hz**, sd ~200 Hz (Handzittern).

**Beim Bau gefallen, gegen Klippers Quelle geprüft:** `BatchBulkHelper._proc_batch` entfernt nur den Client, dessen *eigener* Callback `False` liefert — der Plan wollte per zweitem Lambda abmelden, das hätte den ersten Client nie losgelassen (Risiko 4.6, bestätigt). Und `errors` im Batch ist `last_error_count`, kumuliert seit Messstart, nicht pro Batch. Beides ist in `nozzle_locator.py` berücksichtigt und in `check_klipper_api.py` als Zusicherung festgenagelt (65 Zusicherungen, grün).

### 2.2 Sensor

- LDC1612-basiert (BTT Eddy oder baugleich), Spule **nach oben** gerichtet
- **Anbindung per USB** (Entscheidung vom 31.08.), Kabel lang genug für die Bettmitte
- Es wird **keine** Frequenz→Höhe-Kalibrierung gebraucht und sie ist auch nicht möglich — ein aufwärts gerichteter Sensor lässt sich nicht gegen ein Bett kalibrieren. Nur die Rohfrequenz zählt.

**Warum USB hier die bessere Wahl ist**, über die Bequemlichkeit hinaus: die Präsenzerkennung wird dadurch eindeutig. Ich habe beide Moonraker-Endpunkte am 250er nachgemessen:

```
/machine/peripherals/serial  → serial_devices[] mit path_by_id   ✓ brauchbar
/machine/peripherals/canbus  → can_uuids: []                     ✗ leer,
                                obwohl zwei CAN-MCUs laufen
```

Der canbus-Endpunkt sieht nur Knoten, die **kein laufender Klipper beansprucht** — im Normalbetrieb also nichts. Eine CAN-basierte „steckt die Sonde?"-Prüfung wäre damit bestenfalls zweideutig gewesen. Über USB fragt der Assistent `path_by_id` ab und bekommt eine klare Antwort.

**Den `serial`-Pfad einmal ermitteln**, während die Sonde steckt, und in `xy_probe.cfg.disabled` eintragen:

```bash
ls /dev/serial/by-id/
```

Immer den `by-id`-Pfad nehmen, nie `/dev/ttyACM0` — der wandert, sobald ein anderes USB-Gerät früher erkannt wird.

### 2.3 Halterung

| Anforderung | Warum |
|---|---|
| **bekannte, feste Bauhöhe** | geht als `holder_top_z` in die Config; daraus wird der harte Z-Boden `holder_top_z + min_gap` berechnet, unter den nichts fährt. Muss unter `park_z` (Default 60) liegen, sonst lehnt das Modul die Config ab |
| ~~reproduzierbarer Sitz auf dem Bett~~ **grob mittig unter die stehende Düse stellbar** | seit R-B' (2026-09-03) gibt der Kopf die Position vor: `NOZZLE_LOCATOR_PARK` fährt auf die Anfahrposition, danach kommt die Sonde darunter. Laut Vorversuch stehen 5–8 mm seitlich daneben noch +3.513 Hz an, „grob mittig" ist also großzügig; `search_span` fängt den Rest |
| **kein Metall in Spulennähe** | verfälscht die Basislinie |
| **hitzefest, falls je heiß gemessen wird** | Default ist kalt messen; heiß ist optional und dann liegt eine 200-°C-Düse ~1 mm über der Halterung |
| **niedrig genug für den Werkzeugwechselweg** | oder der Wechselweg führt nicht darüber — das prüft der Trockenlauf, siehe 5.2 |

Die absolute Position der Halterung ist **egal**. Gemessen wird pro Tool der Scheitel in Maschinenkoordinaten, der Offset ist die Differenz zum Referenztool — die Spulenposition kürzt sich exakt weg. Genau deshalb darf die Halterung abnehmbar sein.

**Bettmitte als Default:** nicht aus den Achsgrenzen — beim 250er reichen sie wegen der Docks bis Y −85, die „Mitte" läge bei Y 72. Das Modul nimmt `mesh_min`/`mesh_max` aus `[bed_mesh]` (250er: X 125 / Y 130) und fällt nur ohne Bettmesh auf die Achsgrenzen zurück. Der Assistent zeigt die Werte vor dem Anfahren, sie sind editierbar und werden in beide Sonden-Dateien zurückgeschrieben.

---

## 3. Was noch zu bauen ist

Alles im Plan ausgeschrieben, inklusive Code.

- **Task 2** — `nozzle_locator.py`: Sensorbesitz über Klippers `ldc1612`, `read_frequency()`, `NOZZLE_LOCATOR_READ`, die Config-Dateien beider Drucker, Erweiterung von `check_klipper_api.py`.
- **Task 3** — `approach_z()`, `sweep()`, `locate()`, `NOZZLE_LOCATE`. Enthält den bidirektionalen Sweep, der nicht optional ist, sowie `AXIS=DIAG` zur einmaligen Bestimmung der Kreuzkopplung (siehe 6.4).
- **Task 5** — `CALIBRATE_XY_OFFSETS`: Tool-Durchlauf, Differenzbildung, Persistenz, Trockenlauf, `XY_ITERATIONS` gegen die Kreuzkopplung (siehe 6.4).

### 3.1 Eine Änderung gegenüber dem ursprünglichen Plan

Task 5 bekommt eine **eigene** Tool-Auflösung `_xy_tool_run()` statt der gestrichenen gemeinsamen. Politik: Referenztool zwingend enthalten und zuerst (es legt Messhöhe und Grobsuchfenster für alle folgenden Tools fest), unbekannte Tools in `TOOLS` brechen ab. Code steht im Plan bei Task 5.

---

## 4. Worauf zu achten ist, weil ohne Sonde vorgebaut wurde

Das ist der eigentliche Zweck dieses Issues. Die folgenden Stellen sind geschrieben, aber **nie gegen echte Hardware oder echte Daten gelaufen**.

### 4.1 Die Datenform zwischen Task 5 und der UI

Die UI liest `printer.offset.xy_results` in einer Form, die bisher nur auf dem Papier existiert:

```
{ "0": {x, y, z_compare, x_peak, y_peak, x_fwd, x_rev, y_fwd, y_rev,
        spread_x, spread_y, z_reached}, ..., "ref_tool": 0 }
```

**Stand 2026-09-04:** Task 5 liefert genau diese Schlüssel und zusätzlich je Tool `pred_x`, `pred_y` (Vorhersage aus den Config-Offsets), `amplitude` (Hz über der Basislinie auf der Messhöhe), `coil_temp`, `z_mode`; auf oberster Ebene `timestamp`, `z_mode` und `zswitch_run_id` (Kennung des Z-Switch-Laufs, auf dem der Spalt beruht). Die Tabelle wurde **noch nicht im Browser** gegen diese Daten gesehen — die Daten liegen jetzt aber real in `printer.offset.xy_results` am 250er, der Check ist also möglich. Offen: `amplitude` und `zswitch_run_id` werden noch nicht angezeigt; letzteres sollte eine Warnung auslösen, wenn `probe_results[..].run_id` inzwischen neuer ist.

### 4.2 Der einzige bisher erreichbare Renderpfad ist der leere

Ohne Messdaten konnte nur der Leerzustand geprüft werden. Ungeprüft sind: gefüllte Tabelle, Spaltenformatierung, Vorzeichen der Δ-Anzeige, die Live-Kurve (Sparkline) mit echten Punkten, und ob die Sparkline bei laufendem Sweep überhaupt mitwächst.

### 4.3 Geratene Helfernamen — aufgelöst, mit einem Sicherheitsfund

Der Plan rief drei Helfer auf, deren Namen geraten waren. Inzwischen gegen `tools.js` geprüft:

| geraten | tatsächlich |
|---|---|
| `refreshOffsetStatus()` | `getOffsetSnapshot()` (`tools.js:1039`), periodisch über `updateAllProbeResults()` (`:1065`, alle 2 s via `_probeInterval` `:1167`) |
| `currentToolNumber()` | Referenztool: `getSelectedReferenceTool()` (`:721`). Montiertes Tool: `toolchanger.tool_number` im Druckerstatus (am 250er verifiziert) |
| `ensureHomed()` | **existiert nicht** — und die naheliegende Ersatzlösung wäre gefährlich gewesen |

**Der Sicherheitsfund:** `recoveryFor(detail)` (`tools.js:262`) fängt „Must home first" ab und fährt `G28` → `QUAD_GANTRY_LEVEL` → `G28 Z`. Für jede andere Kalibrierung genau richtig. Hier nicht: die Recovery greift erst, wenn der Messlauf schon läuft — also wenn die Halterung längst auf dem Bett steht. Zusammen mit `homing.cfg:35` (`SET_KINEMATIC_POSITION Z=0`, nur 10 mm anheben, dann Y quer über die Bettmitte) ist das genau der Crash, den die Schrittreihenfolge in Abschnitt 5.1 verhindern soll.

Der Assistent homet deshalb in Schritt 1 **selbst und vorher**, über eine eigene kleine Funktion `ensureHomedBeforeSetup()`, und verlässt sich ausdrücklich nicht auf die Recovery. Steht so im Plan.

`readXyProbeSerial()` ist weiterhin selbst zu schreiben (liest die `serial`-Zeile aus `xy_probe.cfg.disabled`). Code steht im Plan.

### 4.4 Präsenzerkennung — erledigt

War als Risiko notiert („existiert der Endpunkt überhaupt?"), ist inzwischen am 250er nachgemessen: `/machine/peripherals/serial` liefert `serial_devices[]` mit `path_by_id`, der Assistent kann also zuverlässig prüfen, ob die USB-Sonde steckt. Details und der Grund, warum die CAN-Variante untauglich gewesen wäre, in Abschnitt 2.2.

Der Rückfall auf eine manuelle Rückfrage bleibt trotzdem im Code, falls jemand das Ganze auf einer älteren Moonraker-Version betreibt.

### 4.5 Eine Falle, die schon einmal zugeschnappt ist: `transport` heißt „läuft noch"

Das gehört gelesen, bevor jemand Task 2, 3 oder 5 baut oder den Assistenten anfasst.

`sendGcodeWithRecovery` liefert drei Ergebnisse: `{ok}`, `{handled}` und **`{transport}`**. Der eigene Kommentar der Funktion (`tools.js:244`) erklärt, was das dritte bedeutet: `/printer/gcode/script` bleibt offen, bis das Skript fertig ist — **ein Verbindungsabriss heißt also, dass der Drucker weiterarbeitet.** Nicht, dass etwas schiefging.

Der XY-Assistent hat das anfangs als Erfolg behandelt und den nächsten maschinenbewegenden Schritt drangehängt. Konkret hätte das bedeutet: „Halterung jetzt aufs Bett stellen", während ein `G28` noch fährt. Der Fehler ist behoben, aber die Lehre bleibt:

> **Bei einem `transport`-Ergebnis darf nie ein Folgeschritt anlaufen, der die Maschine bewegt oder den Nutzer an die Maschine schickt** — erst muss positiv belegt sein, dass der Drucker steht. Der Assistent tut das über `waitForPrinterIdle()`, und Homing wird über eine erneute `homed_axes`-Abfrage nachgewiesen statt aus dem Sendeergebnis geschlossen.

Bei `CALIBRATE_XY_OFFSETS` über sechs Tools mit bidirektionalen Sweeps ist `transport` **der Normalfall, nicht der Randfall** — der Lauf dauert länger als der PID-Lauf, den dieses Repo bereits als „überlebt seinen HTTP-Request" dokumentiert. Wer die Klipper-Seite baut, sollte damit rechnen.

Die PID- und Z-Abläufe (`tools.js:2249` und `:1502`) machen es richtig vor: Meldung „Verbindung verloren, läuft weiter" — und dann **Stopp**.

### 4.5a Drei kleine Nacharbeiten, wenn die Sonde da ist

Aus dem Abschluss-Review, bewusst zurückgestellt, weil sie ohne Hardware nichts bewirken:

- **`_xyLocatorProbe` beim Aktivieren zurücksetzen.** Der Sparkline-Poll merkt sich beim Laden der Seite, dass es kein `nozzle_locator`-Objekt gibt. Der Assistent erzeugt es später per `FIRMWARE_RESTART` — die Merkung steht dann aber schon, und die Live-Kurve bleibt genau während des Messlaufs leer. Einzeiler: den Cache in `xyProbeActivate`/`xyProbeDeactivate` verwerfen.
- **`console.error` im `catch` des Status-Polls.** Er loggt derzeit nur über `OffsetDebug`, das ohne `?debug=1` stumm ist. Ein künftiger Render-Fehler hinterlässt damit keine sichtbare Spur mehr.
- **Zwei Stellen sind menschliche statt maschineller Sperren:** die Rückfrage „Trockenlauf beendet?" und der „Sonde deaktivieren"-Knopf im Abbruchdialog warnen bei laufendem Drucker, hindern aber niemanden am zu frühen Klick. Kein Verletzungspfad (der Trockenlauf senkt nichts ab), aber wer will, kann sich um die Kollisionsprüfung bringen.

### 4.6 Das Abmelden vom Sensor-Datenstrom

`read_frequency()` meldet sich per `sensor.add_client(cb)` an und beendet die Session, indem der Callback `False` liefert. **Ob Klippers `bulk_sensor.BatchBulkHelper` Clients wirklich so verwirft, ist an der Quelle zu bestätigen** — im Plan steht ein Hinweis dazu bei Task 2 Step 3. Wenn nicht, muss der Callback selbst ein Flag setzen, statt einen zweiten Callback anzumelden. Symptom bei Fehler: der Sensor läuft nach der ersten Messung endlos weiter oder die Sample-Zahl wächst über Messungen hinweg.

### 4.7 Kein einziger Browser-Check

Weder Task 6 noch 7–9 wurden je im Browser gesehen. Die Node-Tests decken Fehlerdialoge und die Offset-Rechnung der Kameramethode ab, **nicht das DOM** — und genau dort saß schon einmal ein Fehler, den der grüne Node-Test nicht gefunden hat (siehe `tests/README.md`, Abschnitt zu `check_webapp_recovery.js`: Bootstrap verwirft `show()` während einer laufenden Transition, ohne das zu melden). Beim ersten Öffnen also gezielt die Dialogketten des Assistenten durchklicken, nicht nur die Tabelle ansehen.

---

## 5. Der erste echte Lauf

### 5.1 Reihenfolge, die nicht verhandelbar ist

1. **Erst homen, DANN die Halterung aufs Bett stellen.** `homing.cfg:35` setzt bei unhomed Z ein `SET_KINEMATIC_POSITION Z=0`, hebt nur 10 mm an und fährt danach Y quer über die Bettmitte. Mit Aufbau auf dem Bett ist das ein Crash.
2. **Erst deaktivieren, DANN abstecken.** Steht die Sonde in der Config und ist sie abgezogen, startet Klipper nicht mehr — USB aendert daran nichts, Klipper braucht jeden MCU beim Start. Die UI hat dafür ein Rettungsnetz (Moonraker läuft weiter, die Config lässt sich auch bei totem Klipper zurückschreiben) — aber es ist unangenehm.

### 5.2 Trockenlauf zuerst

`CALIBRATE_XY_OFFSETS DRY_RUN=1` fährt alle Werkzeugwechsel und Verfahrwege auf `safe_z` ab, ohne je abzusenken. Das ist die Kollisionsprüfung für den Wechselweg über die Halterung — die lässt sich im Code nicht allgemein prüfen, nur abfahren.

### 5.3 Idle-Timeout

Der Messlauf setzt ihn selbst auf 3600 s und stellt ihn danach zurück. Bei Handmessungen daran denken: der Default von 600 s fällt in Sprechpausen, schaltet die Motoren ab, die Achsen gelten als unhomed — und dann steht ein hoher Aufbau auf dem Bett.

---

## 6. Inhaltliche Risiken, die noch offen sind

### 6.1 Heizblock statt Düsenspitze (das größte) — **eingetreten, gemessen**

Der Vorversuch maß eine **nackte Düse** in einem Halter. Real nähert sich der komplette Hotend inklusive Heizblock, und die Spule ortet den Metallschwerpunkt in ihrem Feld, nicht die Spitze. Liegt der Schwerpunkt pro Tool anders — Einschraubtiefe, Blockverdrehung, Fertigungstoleranz — kürzt sich der Fehler **nicht** in der Differenz weg.

**2026-09-04 am 250er bestätigt, Zahlen in Abschnitt 8.** Der Y-Scheitel von T0 wandert um **~240 µm je mm Spalt** in Richtung des Heizblocks (+Y); X kaum. Gegen die Kamera-Offsets liegt die Sonde in Y bei allen drei Tools rund 0,5 mm daneben, in dieselbe Richtung. Zwei Gegenmaßnahmen sind gebaut (gleicher Spalt aus den Z-Switch-Daten, kleiner Feinspalt), eine Ursache bleibt: die Eddy-NG-Sonde an T0, 16 mm in +Y neben der Düse.

### 6.2 Der bidirektionale Fix ist hergeleitet, nicht gemessen

Dass ein zeitlinearer Drift den Scheitel um `m/(2a)` verschiebt (~19 µm/K bei den gemessenen Werten) und dass Hin- plus Rücksweep das aufhebt, ist analytisch hergeleitet und in `tests/check_nozzle_locator_fit.py` als Zusicherung festgenagelt — aber **nie an echter Hardware bestätigt**. Deshalb speichert und zeigt die UI Hin- und Rückwert einzeln: ihre Differenz *ist* der gemessene Drift-Bias. Beim ersten Lauf darauf schauen.

### 6.3 Gehärteter Stahl

Bei ferromagnetischen Düsen kann das Vorzeichen drehen oder die Amplitude einbrechen. Der Grobsweep bestimmt das Vorzeichen, und bei zu schwachem Signal greift ein Abbruch statt eines erfundenen Scheitels — getestet ist das aber nur synthetisch.

### 6.4 Kreuzkopplung: der Sweep misst nicht ganz das, was er zu messen glaubt

Nahe am Scheitel ist die Glocke eine 2D-Quadrik:

```
f(x,y) ≈ f₀ − ½[ a(x−x₀)² + 2c(x−x₀)(y−y₀) + b(y−y₀)² ]
```

Ein X-Sweep bei festem `y = y₁` liefert deshalb **nicht** `x₀`, sondern

```
x_peak = x₀ − (c/a)·(y₁ − y₀)
```

Ist der Kreuzterm `c ≠ 0` — die Glocke also eine gegen die Achsen verkippte Ellipse —, misst jeder achsparallele Sweep systematisch daneben, proportional dazu, wie weit seine Linie am wahren Zentrum vorbeiführt.

**Daraus folgt eine Schieflage, die dem Ergebnis nicht anzusehen ist.** Mit ρ = c/√(ab) als Kopplungsmaß und `ey₀` als Restfehler der Grobsuche:

| Schritt | Restfehler |
|---|---|
| Fein-X bei der Grob-Y | **ρ·√(b/a)·ey₀** |
| Fein-Y bei der Fein-X | **ρ²·ey₀** |

Die **zuerst** gemessene Achse ist also um rund 1/ρ schlechter als die zweite. Bei ρ = 0,2 und 75 µm Grobfehler: X ≈ 15 µm, Y ≈ 3 µm. Das liegt in derselben Größenordnung wie der Drift-Bias.

**Zwei Gegenmittel, beide jetzt im Plan:**

1. **`XY_ITERATIONS=2`** — die Sequenz X→Y→X. Eine Fixpunktiteration; der Restfehler schrumpft pro voller Runde um ρ² und beide Achsen landen auf demselben Niveau. Kostet einen Sweep. Config-Default ist `xy_iterations: 1`.
2. **`NOZZLE_LOCATE AXIS=DIAG`** — Diagnose, kein Teil der Messroutine. X- und Y-Sweeps liefern nur `a` und `b`; `c` ist für sie prinzipiell unsichtbar. Die Diagonalen zeigen ihn:

```
Krümmung bei  45° = (a + b + 2c)/2
Krümmung bei 135° = (a + b − 2c)/2
        Differenz  = 2c
```

**Vorgehen, sobald die Sonde da ist:** einmal `AXIS=DIAG` fahren. Das Kommando gibt ρ aus und sagt direkt, was daraus folgt — unter 0,1 genügt eine Runde, darüber lohnt `XY_ITERATIONS=2`, und bei starker Kopplung wäre ein 2D-Gitterfit der saubere Weg (25 Punkte statt 2×9, liefert x₀, y₀, a, b, c gleichzeitig und ohne Richtungsbias; der bidirektionale Drift-Trick müsste dann boustrophedon laufen).

**Nicht überbewerten:** dieser Fehler ist real, aber kleiner als Risiko 6.1. Reihenfolge deshalb: erst den Vergleichslauf gegen die Kamera (klärt den Heizblock), dann ρ messen, und erst danach entscheiden, ob die Messroutine überhaupt aufwendiger werden muss.

### 6.5 Die eigentliche Frage ist weiterhin unbeantwortet

**Ob das Verfahren genauer ist als die Kamera von Hand, weiß niemand.** Der Vorversuch lieferte σ = 9,87 µm aus n=5 — das 95-%-Konfidenzintervall reicht von 5,9 bis 28,4 µm, der Test kann „exzellent" und „unbrauchbar" statistisch nicht unterscheiden. Und die Wiederholbarkeit des Kameraverfahrens wurde nie gemessen.

Der Vergleichslauf am Ende des Plans beantwortet beides:

- Übereinstimmung < ~20 µm je Tool → Heizblock-Effekt klein, dem Eddy kann man trauen
- systematische, aber konstante Abweichung je Tool → das ist der Blockversatz; taugt dann nicht als Absolutverfahren, wohl aber zur Drift-Überwachung gegen eine per Kamera gesetzte Referenz
- unsystematische Streuung → das Problem sitzt im Dock, dann hilft keins von beiden

Dazu `NOZZLE_LOCATE AXIS=X REPEATS=20` gegen 8–10 Kamerazentrierungen, einmal mit und einmal ohne Werkzeugwechsel dazwischen.

---

## 7. Nebenbefund, unabhängig von diesem Feature

Beim Refactor in Task 6 ist aufgefallen: **alle vier Stellen, die Config-Dateien schreiben, ignorieren den HTTP-Status** von Lesen und Schreiben. Ein fehlgeschlagener Moonraker-Write — Rechteproblem, 500, Verbindungsabbruch — wird von der Offset-UI **still als Erfolg angezeigt**. Das ist vorbestehend und wurde bewusst nicht mitgefixt, weil Task 6 ein reiner Refactor war. Es sitzt jetzt aber an *einer* Stelle (`updateConfigFile` in `webapp/js/tools.js`) statt an vieren und ließe sich dort in einem Zug beheben — sinnvollerweise so, dass ein Fehlschlag pro Tool gemeldet wird, ohne die Schleife über die restlichen Tools abzubrechen.

Verdient eine eigene Task.

## 8. Die ersten echten Messläufe am 250er (2026-09-03/04)

Halterung 53 mm hoch (`holder_top_z: 53`), `park_z: 60`, T0 als Referenz, Sonde von Hand grob mittig unter die Düse gestellt. Vier Läufe waren nötig, bis einer über alle vier Tools durchlief; jeder Abbruch hat etwas gelehrt, und jede Lehre ist inzwischen Code.

### 8.1 Was die Abbrüche gelehrt haben

| Lauf | Abbruch | Ursache | Fix |
|---|---|---|---|
| 1 | Y-Grobsuche: „Scheitel am Rand des Fensters (115)" | Ab 8 mm nach vorn kommt der **Heizblock** über die Spule: +100.000 Hz gegen +6.000 Hz für die Düse. Die Grobsuche nahm das globale Maximum. | `nozzle_locator_fit.local_peak`: der lokale Buckel, der der Vorhersage am nächsten liegt; die echte Y-Kurve ist als Testfall festgenagelt |
| 2 | T1: „Zielamplitude bei Z 53,5 nicht erreicht" | Zwei Fehler auf einmal. `approach_z` senkte erst und las dann, deshalb landete T0 bei der zweiten Anfahrt blind am Boden. Und T1 wurde im Fenster von T0 gesucht — **T1–T3 liegen ~5 mm in Y neben T0**, das Feinfenster von 8 mm sieht sie nie. | Erst lesen, dann senken. **Grobsuche je Tool**, zentriert auf Referenzscheitel plus Config-Offset-Differenz; Suchhöhe bis zur Mindestamplitude, Zielamplitude erst über dem Grobscheitel |
| 3 | T1-Grobsuche: „Kein lokaler Scheitel über 2000 Hz" | Suchhöhe genau bis 2.000 Hz angefahren, die 2-mm-Sweeppunkte lagen dann bei 1.987 und 1.982 Hz. | Suchhöhe bis zum **Doppelten** der Mindestamplitude |
| 4 | — | lief durch | — |

Dazu aus Lauf 1 und 2: **jeder G-Code-Fehler setzt den Toolchanger auf `uninitialized`** (`toolchanger._handle_command_error`), das zuletzt montierte Tool ist danach im Status weg, und `T<n>` geht erst wieder nach `INITIALIZE_TOOLCHANGER` — oder nach einem Homing, das sich mit Halterung auf dem Bett verbietet. `CALIBRATE_XY_OFFSETS` prüft den Status deshalb vorher und wechselt bei Abbruch **selbst zurück auf das Referenztool**, solange der Fehler das Kommando noch nicht verlassen hat (Tobis Wunsch). Der Kopf geht dabei erst auf `park_z`; er kann 0,5 mm über der Halterung stehen.

Und: die Plausibilitätsgrenze `max_offset` misst jetzt die **Abweichung von der Vorhersage**, nicht vom Referenztool — 5 mm Offset sind hier normal.

### 8.2 Ergebnis von Lauf 4 (gleiche Amplitude, 6.000 Hz)

| Tool | Sonde X | Kamera X | ΔX | Sonde Y | Kamera Y | ΔY | Messhöhe | Spannweite X/Y |
|---|---|---|---|---|---|---|---|---|
| T1 | +0,382 | +0,330 | +52 µm | −5,693 | −5,050 | **−643 µm** | 54,00 | 3,5 / 1,5 µm |
| T2 | +0,590 | +0,440 | +150 µm | −5,015 | −4,560 | **−455 µm** | 54,50 | 2,0 / 2,6 µm |
| T3 | +0,142 | −0,180 | +322 µm | −6,444 | −5,840 | **−604 µm** | 54,00 | 0,8 / 2,0 µm |

Drift-Bias 16–24 µm in X, 7–11 µm in Y — der bidirektionale Sweep ist nötig und reicht. Die Kamera-Offsets sind die, mit denen Tobi druckt; sie gelten als richtig.

**Wiederholbarkeit exzellent, Richtigkeit nicht.** Spannweite über drei Läufe 1–4 µm, aber ein systematischer Fehler von ~0,5 mm in Y bei allen Tools in dieselbe Richtung, und in X bis 0,3 mm.

### 8.3 Der Höhentest, der die Ursache zeigt

T0 dreimal geortet, nur die Höhe verändert:

| Spalt Düse–Spule | Y-Scheitel | X-Scheitel |
|---|---|---|
| 2,5 mm | 129,070 | 124,151 |
| 1,5 mm | 128,902 | 124,146 |
| 0,75 mm | 128,652 | 124,111 |

**Der Y-Scheitel wandert um ~240 µm je mm Spalt** zum Heizblock hin (+Y), X kaum. Die Spule misst den Metallschwerpunkt; je größer der Spalt, desto mehr zählt der Block.

Daraus erklärt sich die Tabelle in 8.2 fast vollständig:

- **Gleiche Amplitude war nicht gleicher Spalt.** T1 erreichte 6.000 Hz erst 0,5 mm tiefer als T0, obwohl der Z-Switch sagt, dass T1s Düse 0,31 mm *weiter* herausragt. T1s Düse gibt bei gleichem Spalt also deutlich weniger Signal — anderes Material oder andere Bauform (Risiko 6.3, real). T1 maß bei 0,7 mm Spalt, T0 bei 1,5 mm → ~0,2 mm des Y-Fehlers; T3 analog ~0,07 mm.
- **Der Rest von ~0,45 mm ist bei allen drei gleich** und passt zu Metall, das nur T0 hat: die **Eddy-NG-Sonde, 16 mm in +Y neben der Düse**, wenige Millimeter über der Spule. Sie zieht T0s Scheitel nach +Y, alle anderen Tools erscheinen relativ dazu nach −Y verschoben. Nicht bewiesen — plausibelste Erklärung.
- Nur T1–T3 untereinander verglichen: Y-Abweichung 40–190 µm, X 100–270 µm. **T3 in X (+0,3 mm) ist unerklärt.**

### 8.4 Was daraus gebaut wurde (noch ohne Messlauf dahinter)

- **`Z_MODE=switch` (Default): gleicher Spalt aus den Z-Switch-Daten.** Das Referenztool legt den Spalt fest, jedes weitere Tool bekommt `ref_z` plus Differenz der Auslösepunkte (`z_trigger`). Die Amplitude wird gemessen, gemeldet und gespeichert, nicht erzwungen. Ohne vollständige Z-Daten Rückfall auf `Z_MODE=amplitude` mit Hinweis.
- **`fine_gap` (Default 0,75 mm):** im Spaltmodus misst das Referenztool auf `holder_top_z + fine_gap` statt auf Zielamplitude; liegt das kürzeste Tool damit unter dem Boden, werden alle gemeinsam angehoben. Setzt eine auf ~0,2 mm genau gemessene Halterungshöhe voraus. Für Tobis Tools heißt das Z 53,75 / 54,06 / 53,79 / **53,54** — T3 nur 0,04 mm über dem Boden.
- **Bootstrap für frische Configs:** ohne XY-Offsets trifft der Z-Switch nicht (5 mm daneben). `CALIBRATE_XY_OFFSETS` erkennt fehlende Z-Daten und läuft dann in drei Phasen mit stehender Halterung: XY grob (Amplitude) → `CALIBRATE_ALL_Z_OFFSETS XY_SOURCE=eddy` (Schalter mit den vorläufigen Sonden-Werten anfahren, Config unberührt) → XY fein (Spalt). Der Schalter sitzt am 250er bei X 30 / Y 1, weit weg von der Halterung, Anfahrt auf der Freihöhe.
- Das Ergebnis trägt `zswitch_run_id`. Werden Düsen gewechselt und Z neu kalibriert, ist ein alter XY-Lauf bei falschem Spalt entstanden — die Webapp sollte das anzeigen (offen, siehe 4.1).

**Der nächste Lauf ist genau dieser:** Spaltmodus mit `fine_gap` 0,75. Erwartung: der spaltbedingte Anteil verschwindet, der T0-Sonden-Anteil (~0,45 mm in Y) bleibt. Bleibt er, gibt es zwei Wege — ein anderes Tool als Referenz nehmen, oder T0s Anteil einmal per Kamera bestimmen und fest verrechnen.

### 8.5 Was das Verfahren heute kann und was nicht

- **Als Driftwächter gegen eine per Kamera gesetzte Referenz: sofort brauchbar.** 1–4 µm Wiederholbarkeit; eine Verschiebung von 50 µm nach einem Crash oder Düsenwechsel fällt sicher auf. Das ist der Fall „systematische, aber konstante Abweichung je Tool" aus 6.5.
- **Als Absolutverfahren:** in Y derzeit nicht, in X grenzwertig. Ob Spaltmodus plus kleiner Spalt das ändert, entscheidet der nächste Lauf.
- Die Frage aus 6.5 („genauer als die Kamera?") ist damit **halb** beantwortet: wiederholbarer ja, richtiger nein — noch nicht.

### 8.6 Praktische Lehren für den Betrieb

- Jede Code-Änderung an einem Klipper-Modul kostet einen Zyklus: Halterung runter, Service-Neustart, Homen, QGL, `G28 Z`, `T0`, `NOZZLE_LOCATOR_PARK`, Halterung wieder unter die Düse. Vier Läufe = vier Zyklen. Deshalb vor jedem Neustart alles zusammen fixen, was absehbar ist.
- Der Messlauf über vier Tools dauert ~15–19 Minuten. Die HTTP-Verbindung reißt dabei sicher ab (`{transport}`), deshalb per `gcode_store` und `idle_timeout.state` beobachten, nie auf die Antwort warten.
- Die Basislinie auf `park_z` liegt ~1.400 Hz über der echten Freiluft — die Düse ist 7 mm über der Spule schon schwach sichtbar. Für Amplituden-Schwellen unerheblich, für absolute Vergleiche mit dem Vorversuch nicht vergessen.
- `_return_to_ref_tool` läuft bei den Z-Kalibrierungen bewusst *nicht* bei Abbruch (Tool zum Nachsehen montiert lassen). Der XY-Lauf macht es anders, weil bei ihm eine Halterung auf dem Bett steht und ein unbekanntes Tool im Kopf gefährlicher ist als ein verlorener Befund.


---

## 9. Scanmodus und die Frage nach der Bewegungsform (2026-09-04, abends)

Gebaut nach dem ersten Durchlauf, **noch nicht gefahren**. Anlass war Tobis Frage, ob eine geschickte Bewegungsform den Heizblock aus der Messung herausbekommt.

### 9.1 Warum die naheliegenden Bewegungsformen nichts bringen

Die Spule misst die Summe aus Düsenbuckel und Block-Hintergrund. Der Block liegt weit weg und ist im Messfenster nur eine schiefe Ebene, also ein linearer Anstieg nach +Y. Buckel plus Gerade ist wieder ein Buckel, nur verschoben — aus einer einzelnen Y-Kurve kann kein Parabelfit die Verschiebung vom wahren Scheitel trennen. Deshalb helfen nicht:

- **Kreisbahn um die Düse** — der Gradient erzeugt exakt dieselbe Grundschwingung wie ein versetzter Kreismittelpunkt.
- **Flankenmitte statt Scheitel** (Halbwertspunkte mitteln) — beide Flanken wandern in dieselbe Richtung, die Mitte ist sogar doppelt so empfindlich wie der Scheitel (nachgerechnet für eine Gaußglocke: Scheitel `b·σ²/H`, Flankenmitte `2·b·σ²/H`).
- **2D-Raster mit X-Krümmung als Referenz** — X liefert die Krümmung, aber die Y-Gerade bleibt mit dem Scheitel entartet.

Was Düse und Block physikalisch unterscheidet, ist ihr **Abstand zur Spule**, und 8.3 beweist, dass die beiden Anteile mit Z verschieden skalieren. Zwei Wege bleiben:

1. **Zwei-Höhen-Differenz:** denselben Sweep auf dem Feinspalt und 1–2 mm höher fahren, den Fit auf die *Differenz* legen. Düse ändert sich stark, Block schwach, die T0-Sonde in 16 mm Entfernung fast gar nicht. Zeitdrift kürzt sich weg. **Noch nicht gebaut** — erst die Rohdaten ansehen (9.4).
2. **Aufgelöste Buckelform** statt Parabel: mit ~600 Punkten je Sweep lässt sich ein Peak mit linearem Untergrund fitten; die Entartung gilt nur für die Parabel. Braucht den Scanmodus (9.2).

Die drei Höhenpunkte aus 8.3 sind **nicht linear**: 168 µm/mm zwischen 2,5 und 1,5 mm, 333 µm/mm zwischen 1,5 und 0,75 mm — die Kurve wird zum kleinen Spalt hin *steiler*, physikalisch wäre das Gegenteil zu erwarten. Entweder ist die Form nichtlinear, oder der Parabelfit über 8 mm verzerrt bei kleinem Spalt, weil der Buckel dann schmaler als das Fenster ist. Beides klärt die Höhenserie (9.4).

### 9.2 Kontinuierlicher Scan statt Punkt für Punkt

Jedes LDC1612-Sample trägt einen `print_time`; Klippers `motion_report` liefert zu jedem Zeitstempel die Sollposition aus der Bewegungswarteschlange (so macht es auch Klippers eigener Eddy-Scan). Der Sweep ist jetzt **ein Zug** durch das Fenster mit `scan_speed` (Default 5 mm/s), Vor- und Nachlauf `3·sweep_step` außerhalb, damit Beschleunigen und Bremsen nicht ins Fenster fallen. `scan_speed: 0` schaltet auf den alten Punktmodus zurück.

| | Punktmodus | Scan (5 mm/s) |
|---|---|---|
| Punkte je Richtung | 9 | ~600 |
| Dauer je Richtung | ~8 s | ~2 s |
| Feinmessung je Tool, beide Achsen, 3 Läufe | ~100 s | ~25 s |

Gebaut (`nozzle_locator.py`, `nozzle_locator_fit.py`):

- `_scan()` als Primitiv für achsparallele Sweeps **und die Diagonalen** (`AXIS=DIAG`): Bahn `s = ⟨(x,y) − origin, direction⟩`, Fit über die Bogenlänge.
- `fit.samples_to_track()` — Zeitstempel → Bahnposition, Stillstand und Fensterfremdes fallen weg, optional `sample_latency`.
- `fit.bin_points()` — Körbe in `sweep_step`-Breite für die Grobsuche (`local_peak` braucht Nachbarn) und den Status. Jeder Korb liegt ganz im Fenster (halbe Randkörbe mitteln auf der Flanke schief) und meldet die **mittlere Sample-Position**, nicht die geometrische Mitte (6 µm Schwerpunktversatz gaben im Test 4 µm am Scheitel).
- **Sensor-Haltung:** Klippers `FixedFreqReader` setzt seine Zeitstempel-Regression bei jedem Sensorstart zurück und braucht ~2 s, bis sie stabil ist. Ohne Gegenmaßnahme stoppt der Sensor nach jedem Sweep. `_hold_sensor()` hält ihn über die ganze Ortung am Laufen, mit 1 s Einschwingzeit beim ersten Halten.
- **Latenz:** der Sensor integriert vor seinem Zeitstempel (Wandlung 2,5 ms bei 400 Hz). Bei 5 mm/s verschiebt das den Hinsweep um `v·Δt` nach vorn, den Rücksweep nach hinten; der bidirektionale Mittelwert hebt es auf, die Hin-Rück-Differenz zeigt es. `NOZZLE_LOCATE SPEED=` bei zwei Geschwindigkeiten bestimmt Δt, `sample_latency` in der Config verrechnet es.
- `NOZZLE_LOCATOR_DUMP [FILE=] [KEEP=1]` schreibt die Rohsamples der letzten 64 Sweeps als JSON ins Log-Verzeichnis. Der Status trägt nur die Körbe (`points`) und `sweeps_logged`.
- `NOZZLE_LOCATE AXIS=Y GAPS=3,2,1.5,1,0.75,0.5` — Höhenserie: dieselbe Achse bei mehreren Spalten über der Halterung, je Zeile Scheitel, Hin-Rück-Differenz, Spannweite, Krümmung, Amplitude; am Ende Steigung und Extrapolation auf Spalt 0. Fährt auf die Ausgangshöhe zurück.

Tests: `check_nozzle_locator_fit.py` jetzt 51 Zusicherungen (Körbe, Projektion, Diagonale, Latenz, Stillstand); `check_klipper_api.py` 71 (dazu `motion_report.DumpTrapQ.get_trapq_position`, `dtrapqs['toolhead']`, `print_time` an Index 0).

### 9.3 Was am Drucker zu prüfen ist (ohne Hardware entstanden)

- Ob `get_trapq_position` zu den Sample-Zeiten wirklich Bewegung liefert (`vel > 0`). Fehlermeldung „nur N Samples im Fenster" heißt: Zeitstempel und Bewegung passen nicht zusammen — dann `scan_speed: 0` als Rückfall.
- Die Hin-Rück-Differenz im Scanmodus: erwartet `2·v·Δt` plus Drift, also bei 5 mm/s etwa 15–30 µm, gleiches Vorzeichen bei X und Y. Deutlich mehr → Zeitbasis wackelt (Haltung greift nicht).
- Ob die Sparkline der Webapp mit den Körben (8 Punkte je Feinsweep, 15 je Grobsuche) noch etwas Sinnvolles zeigt.

### 9.4 Reihenfolge am Drucker

1. `NOZZLE_LOCATE AXIS=X` einmal — Scan geht durch, Differenz plausibel.
2. `NOZZLE_LOCATE AXIS=X SPEED=10` — Differenz sollte sich verdoppeln; daraus Δt.
3. **Höhenserie** `AXIS=Y GAPS=3,2,1.5,1,0.75,0.5`, dann `NOZZLE_LOCATOR_DUMP`. Die Rohdaten beantworten offline: konvergiert der Feinspalt, taugt die Extrapolation, wie gut wird die Zwei-Höhen-Differenz.
4. `AXIS=DIAG` einmal (Kreuzkopplung, jetzt billig).
5. Erst dann der Spaltmodus-Lauf `CALIBRATE_XY_OFFSETS` aus 8.4.

### 9.5 Raster (C-Scan): `NOZZLE_LOCATOR_MAP` und `webapp/map.html`

Tobis Einwurf: mit einem Wirbelstromsensor lässt sich per Raster ein Bild des Metalls erzeugen (C-Scan). Mit dem Scanmodus ist das billig — ein Raster sind viele Scan-Züge in Y-Abständen, Serpentine, 20 × 20 mm in ~1 min bei 10 mm/s.

**Was es uns bringt, in dieser Reihenfolge:**

1. **T0 minus T1 zeigt die Eddy-NG-Sonde direkt.** Zwei Raster beim gleichen Spalt, voneinander abgezogen — übrig bleibt das Metall, das nur T0 trägt. Die 0,45-mm-Hypothese aus 8.3 wird damit gemessen statt vermutet, und die Differenzkarte liefert den Korrekturterm.
2. **Der Block-Hintergrund wird modellierbar.** Im 2D-Bild ist die Block-Ebene nicht mehr mit dem Scheitel entartet: Plateau mit Kante gegen rotationssymmetrischen Buckel. Ein Fit „Buckel plus Kante" trennt beide. Noch nicht gebaut — erst Bilder ansehen.
3. **Diagnose-Ansicht:** sitzt die Spule mittig, wo liegt der Block, ist anderes Metall im Feld.

**Grenze:** die Auflösung ist die Feldbreite der Spule (~10 mm). Die Düse erscheint als weicher Fleck, die Blockkante als Rampe — das Bild ist eine Faltung mit der Spulenantwort. Für Schwerpunkte und Kanten reicht das, für Konturen nicht.

**Gebaut:**

- `NOZZLE_LOCATOR_MAP [WIDTH=20] [HEIGHT=20] [PITCH=1] [SPEED=2·scan_speed] [X= Y=] [BASELINE=1] [LABEL=T0] [FILE=]` — Raster um die aktuelle Position auf der aktuellen Höhe. Basislinie wie bei `NOZZLE_LOCATE` (hoch auf `park_z`, lesen, zurück). Schreibt `nozzle_locator_map_<Zeit>.json` ins Log-Verzeichnis (Moonraker: `/server/files/logs/…`): Gitter (`fit.raster_grid`, Spaltenmitten fest, fehlende Zellen `null`), dazu die Rohzeilen, Spalt, Geschwindigkeit, Basislinie, Spulentemperatur. Meldet Maximum und Dateiname; `printer.nozzle_locator.last_map_file` trägt den Namen.
- `webapp/map.html` (+ `js/map.js`): lädt ein oder zwei Raster (Datei oder direkt vom Drucker, Liste per Knopf), Heatmap mit Achsen in Bettkoordinaten, Kreuz auf der Rastermitte, Kreis auf dem Maximum, Hover-Werte, **A − B** auf gleichem Gitter (divergierende Farbskala), lineare oder vorzeichenbehaftet-logarithmische Skala (der Block bei +100.000 Hz überstrahlt die Düse bei +6.000 sonst). Aufruf auch mit `map.html?ip=…&a=…&b=…`.
- `fit.scan_line()` — **behebt einen Fehler des Scan-Entwurfs vom Nachmittag:** die Bahn eines X-Sweeps mit origin (0,0) hätte Y auf 0 gefahren. Die Linie geht jetzt durch `through` (Default: aktuelle Position), die Bogenlänge zählt ab origin. Getestet, nie gefahren, deshalb ist Schritt 1 in 9.4 (`NOZZLE_LOCATE AXIS=X`) jetzt noch wichtiger.

Tests: Fit 71 Zusicherungen (Bahn, Raster-Gitter), `tests/check_nozzle_map.js` 17 (Viewer-Logik ohne DOM).

**Reihenfolge am Drucker, ergänzt:** nach dem Scan-Test (9.4 Schritt 1–2) auf Messhöhe `NOZZLE_LOCATOR_MAP LABEL=T0`, dann `T1`, `NOZZLE_LOCATOR_PARK`-Höhe, gleicher Spalt (Z aus `z_trigger`-Differenz), `NOZZLE_LOCATOR_MAP LABEL=T1`, und beide in `map.html` als A − B ansehen. Erst danach die Höhenserie.

### 9.6 Recherche: was Literatur und Vergleichsprojekte zur Genauigkeit sagen (2026-09-04)

Quellen: TI-Applikationsbericht SNOA931 „LDC1612/LDC1614 Linear Position Sensing", LDC1612-Datenblatt, Mook/Simonin „Eddy current imaging using array probes" (ECNDT 2014), EddySeek (charliemayall, Klipper-Add-on für Düsenausrichtung per LDC1612), BTT-Eddy-Doku.

**Was sich bestätigt hat**

- **Kleiner Spalt ist richtig.** TI: Zielabstand unter einem Spulendurchmesser halten, beste Auflösung beim kleinsten Abstand (Tabelle 1 in SNOA931: 43 Codes/µm bei 1 mm gegen 16 bei 3 mm). Deckt sich mit 8.3 — der Block-Anteil sinkt mit dem Spalt ebenfalls. `fine_gap` 0,75 bleibt; der eigentliche Hebel ist eine **genau bekannte Halterungshöhe** (`holder_top_z`), damit `min_gap` kleiner werden darf.
- **Die Zeitdrift ist der Referenzoszillator des LDC1612, nicht die Spule.** Datenblatt: interner Referenztakt −13 ppm/K. Bei 3,13 MHz sind das 41 Hz/K — der Vorversuch maß 39,8 Hz/K. Der bidirektionale Sweep bleibt die richtige Antwort; ein Aufwärmen der Sonde vor dem Lauf verkleinert den Rest, und die Spulentemperatur gehört weiter zu jeder Messung.
- **Sample-Rauschen ist nicht der Engpass.** TI Tabelle 2: σ 0,36 µm bei 26 ms Wandlung, 1,3 µm bei 1,6 ms, 17 µm bei 0,1 ms (ihre Geometrie). Klipper fährt 2,5 ms (400 Hz); unsere 21 Hz σ je Sample ergeben bei ~600 Samples je Sweep und 115 Hz/mm² Krümmung rund 1–2 µm Scheitelrauschen — genau die beobachtete Spannweite. Eine andere Abtastrate (RCOUNT) bringt nichts: weniger Rauschen je Sample gegen weniger Samples, das Produkt bleibt. Klippers `ldc1612` legt 400 Hz ohnehin fest.
- **EddySeek macht dasselbe, und wir sind wiederholbarer.** Spule auf dem Bett, Düse darüber, kontinuierliche X/Y-Sweeps (20 mm/s grob, 10 mm/s fein), Zeitstempel → Schrittmotorposition wie bei uns, frequenzgewichteter 2D-Schwerpunkt statt Parabel, Standard-Messhöhe **5 mm** über der Spule. Ergebnis laut ihrer Doku: σ 21 µm (X) / 14 µm (Y), maximale Streuung 47 µm, 7,3 s je Suche. Unser Verfahren: 1–4 µm Spannweite bei 0,75–1,5 mm Spalt. Das Heizblock-Problem taucht bei EddySeek nicht auf — es misst wie wir nur relativ zum Referenztool, und mit identischen Tools kürzt sich der Block heraus. Unser Sonderfall ist die Eddy-NG-Sonde an T0.

**Was wir übernehmen sollten**

1. **Drive-Current einmal kalibrieren.** LDC1612 will 1,2–1,8 V Spulenamplitude, darunter steigt das Wandlungsrauschen. `xy_probe.cfg` setzt keinen `reg_drive_current`, Klipper nimmt 15. Einmal `LDC_CALIBRATE_DRIVE_CURRENT CHIP=nozzle_locator` mit der Düse auf Messhöhe fahren und den Wert eintragen. (Achtung TI: ein geänderter Drive-Current verschiebt den Ausgabe-Offset — für Scheitel egal, für Amplitudenschwellen neu prüfen.)
2. **Geschwindigkeit an die Sampledichte klemmen** wie EddySeek (`min_sweep_samples`): statt bei zu wenigen Samples abzubrechen, die Geschwindigkeit so weit senken, dass mindestens N Samples im Fenster liegen. Bei uns: `scan_speed ≤ 400 · span / N`. Für 8 mm und N = 200: 16 mm/s. Kleine Änderung in `_scan`.
3. **Gestaffelte Parallel-Sweeps** (EddySeek: 3 Linien im Abstand 0,3 mm in der Grobphase) sind ihr Mittel gegen die Kreuzkopplung. Bei uns misst `AXIS=DIAG` den Kreuzterm einmal; ist ρ klein, brauchen wir das nicht.

**Rasterabstand und Rastergröße**

- Die Spulenantwort auf ein kleines Ziel (PSF) ist bei axialen Spulen ein einzelner Buckel von der Breite etwa des Spulendurchmessers (Mook: Auflösung ≈ Spulenmaß; nicht-axiale Sonden haben eine „Mexican-hat"-PSF mit dunklem Halo, das betrifft uns nicht). Die Breite messen wir mit dem ersten Raster selbst (Halbwertsbreite des Düsenflecks).
- **Nyquist:** Zeilenabstand ≤ halbe Halbwertsbreite. Bei ~10 mm PSF ist 1 mm reichlich, 2 mm noch sauber. Für die **Zeilen** ist der Abstand egal (12 µm Samples). Für **Differenzbilder und Block-Fit** 0,5 mm nur, wenn die Halbwertsbreite unter 4 mm liegt — sonst kostet es Zeit ohne Information.
- **Mook „Micro-Scanning"** (Bild mehrfach um Bruchteile des Sensorabstands versetzt aufnehmen und gewichtet überlagern) gewinnt nur bei Sensor-Arrays; ein Einzelsensor mit feinem Raster hat das schon.
- **Rastergröße:** der Block liegt ~8 mm in +Y, die T0-Sonde 16 mm in +Y. Für das Differenzbild T0 − T1 deshalb `HEIGHT=30` (±15 mm, 30 Zeilen, ~1,5 min) oder `Y=` um +5 mm versetzt. Für die reine Düse reichen `WIDTH=12 HEIGHT=12 PITCH=0.5`.
- **Entfaltung** (Wiener, PSF-Deconvolution aus der ECT-Literatur) verschiebt einen symmetrischen Scheitel nicht und bringt für die Ortung nichts; für den Block-Hintergrund ist ein Modellfit („Buckel + Kante") der sauberere Weg als Entfaltung.

**Was nicht hilft**

- Höhere oder niedrigere Abtastrate (siehe oben).
- Schnellere Scans als ~10 mm/s für die Feinmessung: die Latenz-Verschiebung `v·Δt` wächst, der Gewinn an Zeit ist bei 2 s je Sweep bedeutungslos.
- Ein größerer Spalt zugunsten von „mehr Fläche im Bild": TI und 8.3 zeigen in dieselbe Richtung, klein bleiben.

**Umgesetzt (2026-09-04, spät):**

- **Geschwindigkeitsklemme** `min_samples` (Default 200): `_scan` bremst auf `rate · span / min_samples`, statt bei zu wenigen Samples abzubrechen (`fit.clamp_scan_speed`). Für 8 mm bei 400 Hz sind das höchstens 16 mm/s, die Grobsuche über 30 mm darf 60.
- **`NOZZLE_LOCATOR_CALIBRATE_DRIVE`**: wie Klippers `LDC_CALIBRATE_DRIVE_CURRENT`, aber mit Höhenprüfung (Düse muss auf Messhöhe stehen, der Strom hängt vom Ziel im Feld ab) und **sofort wirksam** — setzt `dccal.drive_cur`, das der nächste Sensorstart schreibt, und legt den Wert für `SAVE_CONFIG` bereit. Meldet altes und neues Register und das Rauschen danach. `SAVE_CONFIG` erst bei leerem Bett (Neustart löscht das Homing). Status: `printer.nozzle_locator.drive_current`.
- **Basislinie neben der Spule** (Tobis Einwand): auf `park_z` steht die Düse noch 7 mm über der Spule und hebt die Basislinie um ~1.400 Hz (8.6). `measure_baseline()` fährt jetzt um `baseline_offset` (Default 40 mm) in X zur Seite (bevorzugt +X, sonst −X, innerhalb der Achsgrenzen, `fit.baseline_side`), liest, und kommt zurück; `park_z` ist per Definition die freie Fahrhöhe. Gilt für `NOZZLE_LOCATE`, `NOZZLE_LOCATOR_MAP` und `CALIBRATE_XY_OFFSETS`. Folge: die Amplituden (`min_amplitude`, `target_amplitude`, Raster-Werte) sind ab jetzt ~1.400 Hz größer als bisher gemeldet — Schwellen sind davon nicht betroffen (Düse bei 6.000 Hz bleibt weit darüber), aber alte und neue Amplituden nicht direkt vergleichen. Status: `last_baseline`.

Tests: Fit 80 Zusicherungen, Klipper-API-Wächter um die `ldc1612`-Register und `DriveCurrentCalibrate` erweitert.

**Drive-Current und die Eddy-NG-Sonde an T0 (Tobis Frage, 2026-09-04):** geprüft an Quelle und Drucker, kein Konflikt auf Software-Ebene.

| Ebene | XY-Spule | T0 Eddy-NG | Befund |
|---|---|---|---|
| Chip | LDC1612 auf `xyprobe` (USB) | LDC1612 auf `eddy` (CAN) | getrennte Chips, das Register ist je Chip |
| Kommando | `NOZZLE_LOCATOR_CALIBRATE_DRIVE`; Klippers `LDC_CALIBRATE_DRIVE_CURRENT CHIP=nozzle_locator` (Mux, nur mit CHIP) | `PROBE_EDDY_NG_*`, `EDDYNG_*`; kein `LDC_CALIBRATE_DRIVE_CURRENT` | keine Namensüberschneidung |
| Config | `[nozzle_locator] reg_drive_current` (Autosave) | `[probe_eddy_ng my_eddy] reg_drive_current = 16`, `tap_drive_current = 16`, `calibrated_drive_currents` | getrennte Sektionen; `SAVE_CONFIG` schreibt beide Blöcke neu, mischt sie aber nicht. `reg_drive_current` in `xy_probe.cfg` **auskommentiert lassen**, den Wert verwaltet der Autosave-Block |

**Physik, die bleibt** (siehe 10.1: die Kupferplatine der Sonde ist als Ziel 16,7 mm neben T0s Duese mit 136 kHz sichtbar): beide Schwingkreise liegen im selben Band (Eddy-NG Freiluft 3,15–3,23 MHz laut Kalibrierung, XY-Spule 3,13 MHz) und stehen bei T0 16 mm auseinander. Solange die Eddy-NG-Sonde **nicht angesteuert** wird, ist ihre Spule ein passiver Resonator im Feld — ein konstanter Anteil des T0-Bias, den das Differenzbild ohnehin zeigt. Werden **beide gleichzeitig getrieben**, ist Frequenzziehen möglich. Eddy-NG startet seinen Sensor nur bei Probe, Tap oder `EDDYNG_START_STREAM_EXPERIMENTAL`; die XY-Kommandos rufen nichts davon auf, und mit Halterung auf dem Bett ist Probing ohnehin verboten. Regel: **während XY-Läufen kein Eddy-NG-Stream und kein Probing.** Der Drive-Current der XY-Spule wird mit T0 auf Messhöhe kalibriert (Referenztool, Sonde passiv im Feld); der Auto-Amplituden-Bereich 1,2–1,8 V ist breit genug für die anderen Tools ohne Sonde.

**Live-3D-Ansicht in der Offset-UI (Tobis Wunsch, 2026-09-04, spät):** `cmd_MAP` legt nach jeder Zeile das Gitter bis dahin in den Status (`printer.nozzle_locator.map`: `xs`, `ys`, `values`, `rows_done`/`rows_total`, `done`, `file`, Basislinie, Spalt). Der XY-Block der Webapp hat ein aufklappbares Panel „Raster (C-Scan) mit Live-3D-Ansicht": Felder Breite/Höhe/Raster/Label, Knopf „Raster starten" (baut das Kommando über `xyMapCommand()` mit Validierung, sendet über `xySendMounted`, kein Recovery-Knopf, weil die Halterung auf dem Bett steht), und die Fläche wird im 2-s-Poll der Sparkline aus dem Status nachgezeichnet — man sieht das Bild Zeile für Zeile wachsen. Zeichnen übernimmt `js/map3d.js` mit plotly.js (gl3d-Bundle vom CDN, erst beim ersten Bild geladen; ohne Netz sagt das Panel das, die 2D-Heatmap in `map.html` braucht kein CDN). Nach der letzten Zeile: Knopf wieder frei, Link zu `map.html` mit der Datei. `map.html` hat denselben Renderer als „3D-Ansicht"-Knopf, auch für A − B. Tests: `check_nozzle_map.js` (mapToSurface), `check_xy_offset_ui.js` (xyMapCommand). Nie im Browser gesehen.

---

## 10. Messtag am 250er mit Scanmodus und Raster (2026-09-04, Mittag)

Alles aus Abschnitt 9 ist gefahren. Die wichtigsten Befunde stellen Abschnitt 8.3 und Teile von 9.1/9.5 auf den Kopf; **was dort über „den Heizblock" steht, ist falsch gedeutet** und bleibt nur als Protokoll stehen.

### 10.1 Was die Raster zeigen

Raster (`NOZZLE_LOCATOR_MAP`) mit T0, T1, T2 bei ~3 mm Spalt, 30–40 mm hoch, und ein Feinraster T2 bei 0,9 mm:

| Tool | schwacher Buckel (Düse + Block) | starkes Signal 16,7 mm daneben |
|---|---|---|
| T0 | 2,6 kHz bei 3 mm, 9,3 kHz bei 1 mm, Scheitel (123,45 / 126,44) | **77 kHz bei 3 mm, 136 kHz bei 0,75 mm**, Scheitel (123,9 / 109,7) |
| T1 | 2,5 kHz, Scheitel ≈ (123,9 / 120,9) | nichts |
| T2 | 2,6 kHz, Scheitel ≈ (124,1 / 121,7) | nichts |

Tobis Auskunft: alle Tools tragen dasselbe Hotend (Bambu-Bauart, Stahlblock direkt über der Düse), T1/T2 um 180° um die Hochachse gedreht. Daraus folgt:

- **Der schwache Buckel ist die Düse mit Block.** Der Block ist ferromagnetischer Stahl, dessen Permeabilität den Wirbelstromeffekt bei 3 MHz weitgehend aufhebt — darum nur ~9 kHz bei 1 mm statt der 100 kHz, die ein Kupferblock gäbe. Auf allen Tools gleich stark. **Alle bisherigen Kalibrierläufe haben also die Düse gemessen, wie beabsichtigt.**
- **Das starke Signal an T0 ist die Kupferplatine der Eddy-NG-Sonde**, 16,7 mm neben der Düse (Config `y_offset: 16.0`; das Vorzeichen in Rohkoordinaten ist **−Y**, das wäre für die Bettmesh-Anfahrt einmal zu prüfen). Eine große Kupferfläche 3,5 mm über der Spule, mit sehr sauberem, spaltunabhängigem Scheitel (Höhenserie 3 → 0,75 mm: 4 µm/mm, Spannweite ≤ 1,5 µm) — sie ist ein hervorragendes Ziel, aber das falsche.
- **Der „Heizblock-Effekt" aus 8.3 (240 µm je mm Spalt) ist die Platine.** Sie zieht T0s Düsenscheitel spaltabhängig; T1–T3 haben das nicht. Deshalb: **T0 taugt nicht als Referenztool**, solange die Sonde dran ist. Tobis Vorschlag, nur T1–T3 zu messen, war richtig.

### 10.2 Ergebnis des Laufs mit T1 als Referenz (Scanmodus, Spaltmodus)

`CALIBRATE_XY_OFFSETS REF_TOOL=1 TOOLS=1,2,3`, 298 s statt 15–19 min, Feinspalt um 0,275 mm angehoben (T3 am Boden), Spule 37 °C:

| Tool | Sonde (Differenz zu T1) | Kamera (Differenz zu T1) | Abweichung | Spannweite X/Y | Amplitude |
|---|---|---|---|---|---|
| T2 | +0,2096 / +0,7352 | +0,110 / +0,490 | **+100 / +245 µm** | 0,8 / 1,5 µm | 9,9 kHz |
| T3 | −0,2415 / −0,7054 | −0,510 / −0,790 | **+269 / +85 µm** | 3,4 / 1,1 µm | 9,2 kHz |

Wiederholbarkeit weiter 1–3 µm. Die Abweichungen zur Kamera liegen bei 0,1–0,27 mm — deutlich unter den 0,45–0,64 mm mit T0 als Referenz, aber nicht bei null. Kandidaten: (a) die Kamera-Offsets selbst (nie auf Wiederholbarkeit geprüft, 6.5), (b) der Schwerpunkt von Düse + Block liegt nicht exakt auf der Düsenachse (Heizpatronen-/Thermistorbohrung), und **bei um 180° gedrehten Hotends kippt dieser Versatz das Vorzeichen** — Tobi weiß, welche Tools wie herum sitzen; die Feinraster der Buckel (12 × 12 mm, 0,5 mm) würden die Asymmetrie zeigen. Offen.

### 10.3 Weitere Befunde des Tages

- **Scanmodus läuft**, Bahnfehler (`fit.scan_line`) real bestätigt behoben, Y blieb während X-Scans stehen.
- **Aufwärmen:** der erste Lauf nach dem Sensorstart lag 80 µm daneben und hatte 15 % weniger Krümmung; nach ~1 min Dauerbetrieb stabil auf 5 µm. Die Haltung muss vor der ersten Messung eines Laufs eine Aufwärmzeit bekommen (offen, Code).
- **Hin-Rück-Differenz** ist keine Sensorlatenz (wächst mit der Sweep*dauer*, nicht mit der Geschwindigkeit; Latenz ≈ 0): eine Zeitrampe von 12–29 Hz/s nur *während* der Fahrt, zwischen den Sweeps kein Niveauunterschied. Ursache unklar; kürzt sich im bidirektionalen Mittel weg. Rohdaten: `scan_x_speeds.json` (18 Sweeps bei 2,5/5/10 mm/s). 5 mm/s bleibt die beste Wahl (Spannweite 2 µm).
- **Drive-Current** kalibriert: bleibt 15.
- **`NOZZLE_LOCATE` ohne Grobsuche fällt auf Flanken herein:** Y-Scheitel am Fensterrand → Fit auf der Flanke lieferte Y 109 statt 126, und `sweep_quality` fing es nicht (Maximum einen Korb neben dem Rand). Offen: Randabstand in `sweep_quality`, Grobsuche in `NOZZLE_LOCATE`.
- **Jeder Abbruch setzt den Toolchanger auf `uninitialized`** (8.1) — heute dreimal; `INITIALIZE_TOOLCHANGER` reicht, kein Homing nötig.
- **`G1` mit T1/T2 rechnet den Tool-Offset ein**, die Locator-Kommandos arbeiten in Maschinenkoordinaten. Manuelle Anfahrten dazwischen sind um den Offset verschoben — `NOZZLE_LOCATOR_PARK` und die Locator-Kommandos nehmen.
- Krümmung des Düsenbuckels: Y ~250 Hz/mm² gegen X ~100 — der Buckel ist in X deutlich breiter (Block länger in X?). `AXIS=DIAG` steht noch aus.
- Live-3D-Panel und Viewer funktionieren; die Z-Achse muss logarithmisch sein, sonst überstrahlt die Platine alles (behoben).

### 10.4 Umgesetzt nach dem Messtag (Tobi: T0 bleibt drin, Spalt so klein wie möglich)

- **`CALIBRATE_XY_OFFSETS FINE_GAP= MIN_GAP= WARMUP=`**: Spalt und Z-Boden je Lauf ohne Config-Änderung (MIN_GAP ≥ 0,15, weil `holder_top_z` nur auf 0,1–0,2 mm bekannt ist). Ziel: T0 mit kleinstem Spalt messen, damit die Kupferplatine der Sonde so wenig wie möglich zieht. Erwartung aus der Höhenserie (8.3, Steigung zum kleinen Spalt hin *steiler*): bei 0,4 mm bleiben vermutlich ~0,1 mm; deshalb zusätzlich T0 bei zwei Spalten messen und auf Spalt 0 hochrechnen (`NOZZLE_LOCATE GAPS=`).
- **Sensor-Haltung über den ganzen Lauf mit Aufwärmzeit** (`warmup_time`, Default 60 s): der erste Lauf nach dem Sensorstart lag 80 µm daneben.
- **`sweep_quality` mit Randabstand** (12,5 % der Fensterbreite, über die Position): die Messtag-Flanke ist als Testfall festgenagelt.
- **`NOZZLE_LOCATE COARSE=1`**: Grobsuche über `search_span` vor der Feinmessung.
- Fit 87 Zusicherungen.

### 10.5 Zweiter Lauf mit T0 bis T3, Feinspalt, Höhenserien, Platinen-Abzug (2026-09-04, 13–14 Uhr)

`CALIBRATE_XY_OFFSETS REF_TOOL=1 TOOLS=0,1,2,3 FINE_GAP=0.4 MIN_GAP=0.25` (Feinspalt vom Boden auf 0,78 angehoben, T3 begrenzt), 428 s:

| Tool | Sonde, Differenz zu T1 | Kamera | Abweichung | Amplitude | Messhöhe Z |
|---|---|---|---|---|---|
| T0 | −0,4995 / +5,2143 | −0,330 / +5,050 | −170 / +164 µm | 15,7 kHz | 53,465 |
| T2 | +0,2128 / +0,7297 | +0,110 / +0,490 | +103 / +240 µm | 11,7 kHz | 53,509 |
| T3 | −0,2470 / −0,7196 | −0,510 / −0,790 | +263 / +70 µm | 10,8 kHz | 53,250 |
| T1 | Referenz | | | 8,1 kHz | 53,775 |

T2/T3 reproduzieren den ersten Lauf (10.2) auf 15 µm — bei anderer Halterungsposition und anderem Spalt. Die Sonde ist also stabil; die Abweichung zur Kamera ist systematisch.

**Höhenserien** (`NOZZLE_LOCATE GAPS=`, „Spalt" = Z − 53):

- T1 Y: 2,0 → 0,6: 118,642 → 118,710, **−48 µm/mm**, X flach (−2 µm/mm). Auch ohne Platine wandert der Y-Scheitel mit dem Spalt — Block-Geometrie.
- T0 Y: 2,31 → 1,12: 124,550 → 124,250, **+250 µm/mm und zum kleinen Spalt hin steiler** (146 → 308 → 384 µm/mm je Stufe); im Lauf bei Z 53,465: 123,93. Die Extrapolation auf Spalt 0 ist damit nicht belastbar. X: 19 µm/mm.

**Platinen-Abzug aus zwei Feinrastern** (14 × 14 mm, 0,5 mm, T0 bei Z 54,12, T1 bei Z 53,76; T1-Buckel auf den T0-Buckel geschoben und abgezogen): das Residuum am Buckel ist nur +100 Hz Niveau mit **~10 Hz/mm Gefälle → ≤ 20 µm Scheitelverschiebung**. Die direkte, additive Wirkung der Platine am Düsenort ist also klein. Die starke Spaltabhängigkeit von T0 kommt woanders her.

**Der eigentliche Befund: die Spalte waren im Lauf nicht gleich.** Amplituden bei „gleichem Spalt": T1 8,1 kHz, T2 11,7, T3 10,8, T0 15,7. Und die Feinraster: T1 bei Z 53,76 hat dieselbe Amplitude (8,47 kHz) wie T0 bei Z 54,12 (8,58 kHz) — T1s Spitze steht also bei gleichem Z rund 0,36 mm **höher** als T0s, T1 ist kürzer. Der Spaltmodus rechnet mit dem Gegenteil (z_trigger T1 1,337 > T0 1,026 → „T1 länger") und hat T0 auf ~0,1 mm und T3 auf wenige Hundertstel über die Halterung gesetzt (kein Schaden, T1-Kontrolle vor/nach dem Lauf stabil). Entweder ist die Vorzeichen-Deutung von `z_trigger` im Spaltmodus falsch, oder T0s Z-Daten sind veraltet (Hotend gewechselt?). **Solange das offen ist, ist `Z_MODE=amplitude` mit identischen Hotends der ehrlichere Gleichspalt** — die Spule selbst setzt den Spalt, unabhängig von Z-Daten; die Platine addiert am Buckel nur ~1 %.

Weitere Code-Punkte: die Höhenserie lässt den Kopf am Sweep-Ende stehen statt auf dem Scheitel (Folgekommando fiel darauf herein); `target_amplitude` als Laufparameter (`TARGET_AMPLITUDE=`), damit der Amplitudenmodus einen kleinen Spalt fahren kann.

### 10.6 Dritter Lauf: Amplitudenmodus, T0 bis T3 (2026-09-04, 14:30)

`CALIBRATE_XY_OFFSETS REF_TOOL=1 TOOLS=0,1,2,3 Z_MODE=amplitude TARGET_AMPLITUDE=8000 MIN_GAP=0.25`, 456 s. Amplituden jetzt 8,5–9,4 kHz für alle (Spaltmodus davor: 8,1–15,7). Messhöhen: T1 53,75, T0 54,00, T2 54,00, T3 53,50 — **T0 steht bei gleicher Amplitude 0,25 mm höher als T1, ist also länger; der Spaltmodus hatte ihn 0,31 tiefer gesetzt.** T3 passt zum Spaltmodus (kürzer). Also sind T0s Z-Switch-Daten falsch oder veraltet, nicht das Vorzeichen im Code.

| Tool | Sonde (zu T1) | Kamera (zu T1) | Abweichung | Spaltmodus-Lauf (10.5) |
|---|---|---|---|---|
| T0 | −0,3601 / +5,5264 | −0,330 / +5,050 | −30 / **+476 µm** | −170 / +164 (bei Z 53,465) |
| T2 | +0,2156 / +0,7149 | +0,110 / +0,490 | +106 / +225 µm | +103 / +240 |
| T3 | −0,2414 / −0,7231 | −0,510 / −0,790 | +269 / +67 µm | +263 / +70 |

T2 und T3 sind über drei Läufe, zwei Halterungspositionen und zwei Spaltmodi auf ~15 µm stabil. **T0s Y hängt massiv vom Spalt ab:** Spalt ~0,47 → +5,21, Spalt ~1,0 → +5,53, Serie bis 2,3 mm → weiter steigend; Extrapolation auf Spalt 0 ≈ +4,9…5,0, Kamera +5,05.

**Deutung, die alle Befunde zusammenbringt:** der Platinen-Abzug (10.5) zeigte nur ~20 µm additive Wirkung am Buckel. Eine Spaltabhängigkeit von 0,6 mm/mm entsteht aber, wenn **die Düsenspitze nicht auf der Blockachse sitzt** (Gewindespiel, Düse schief eingeschraubt, Spitze beschädigt): bei großem Spalt misst die Spule den Block, bei kleinem die Spitze, und der Scheitel wandert zwischen beiden. T1 zeigt −48 µm/mm (fast zentriert), T0 +600 µm/mm (Spitze ~0,5 mm neben der Blockachse, in −Y). Die Kamera sieht die Bohrung, also die Spitze. **Prüfbar mit der Kamera von unten: liegt T0s Bohrung mittig im Blockumriss?**

**Konsequenz für das Verfahren:** die Spule liefert bei Spalt → 0 die Spitze. Zwei Messungen je Tool bei zwei kleinen Spalten und lineare Extrapolation auf 0 („Spitzen-Extrapolation") machen das Ergebnis unabhängig von Block-Exzentrizität — und nebenbei von jeder additiven Störung, die mit dem Spalt schwächer wird (Platine). Kosten: eine zweite Feinmessung je Tool, ~30 s. Offen: nichtlinear zum kleinen Spalt hin (10.5), also Spalte so klein wie möglich (0,25/0,6) und ggf. drei Stützstellen. Und im Amplitudenmodus die Z-Schritte von 0,25 auf 0,05 mm verfeinern, sonst streut der Spalt je Tool um bis zu 0,25 mm.

### 10.7 Läufe mit Spitzen-Extrapolation, T0 als Referenz (2026-09-04, 15–16 Uhr)

Amplitudenmodus, feine Z-Tastung (0,05 mm), zwei Spalte je Tool, Gerade auf Spalt 0. Halterung unverändert zwischen den Läufen (T0-Grobsuche beide Male 121,04 / 119,73).

| Lauf | Ziel-Amplitude | Spalte T0 | T1 (Kamera +0,330 / −5,050) | T2 (+0,440 / −4,560) | T3 (−0,180 / −5,840) |
|---|---|---|---|---|---|
| 4 | 8.000 Hz | 1,20 / 1,70 | +0,2866 / −5,2168 → **−43 / −167 µm** | +0,4903 / −4,5236 → **+50 / +36** | +0,0207 / −5,9651 → **+201 / −125** |
| 5 | 12.000 Hz | 0,70 / 1,10 | abgebrochen: T1 erreicht am Boden (Z 53,25) nur 11.578 Hz | | |
| 6 | 11.000 Hz | 0,75 / 1,15 | +0,2765 / −5,1665 → **−54 / −117 µm** | +0,4842 / −4,4640 → **+44 / +96** | +0,0149 / −5,8964 → **+195 / −56** |

Steigungen je Tool (mm je mm Spalt), stabil über die Läufe: **T0 Y +0,26…+0,29** („Düse sitzt schief im Block"), X −0,02…−0,06; T1 Y −0,04/−0,05; T2 Y −0,03; T3 Y −0,03/−0,04. Spannweiten 1,6–4,9 µm.

**Befunde:**

- Zwischen Lauf 4 und 6 wandern alle drei Offsets um ~+60 µm in Y gemeinsam: das ist T0s Spitzen-Extrapolation, die von 1,2 mm auf 0,75 mm Spalt noch nicht konvergiert ist (Kurve zum kleinen Spalt hin steiler, 10.5). Kleinere Spalte für T0 gehen nicht, weil T1 und T3 rund 0,5 mm kürzer sind und am Boden (0,2 mm) die Amplitude begrenzen — im Amplitudenmodus haben alle Tools denselben Spalt.
- **Amplituden-Z gegen Z-Switch:** Z-Vergleich (Amplitude) T1 −0,45, T2 −0,15, T3 −0,55 gegen Z-Switch-Differenzen +0,31 / +0,04 / −0,21. Die Abweichung ist je Tool verschieden (0,76 / 0,19 / 0,34 mm), also nicht nur ein veralteter T0-Wert. Amplitude als Z-Quelle ist damit vorerst vom Tisch (siehe Diskussion 15 Uhr: Spitze/Block-Bild ≠ Kontakt); eher sind Z-Switch-Daten mehrerer Tools veraltet oder die Amplitudenkurve ist doch nicht für alle gleich.
- **Restabweichung zur Kamera 40–200 µm**, T3 in X mit +195/+201 über alle Läufe konstant. Ob Kamera oder Sonde recht hat, ist ohne die Kamera-Wiederholbarkeit nicht zu entscheiden (6.5, weiterhin offen).

**Nächste Schritte (Vorschlag):** (1) Kamera-Wiederholbarkeit messen: 8–10 Zentrierungen eines Tools. (2) T0 mit der Kamera ansehen: liegt die Bohrung mittig im Blockumriss? (3) 2D-Paraboloid-Fit über ein kleines Raster statt zweier Linien, gleiches Fenster für alle Tools. (4) Für T0 eine dritte Stützstelle und quadratische Extrapolation, oder T0 bei kleinem Spalt gegen ein Tool ohne Exzentrizität messen.

### 10.8 Lauf 7: 2D-Fit plus quadratische Extrapolation (2026-09-04, 19 Uhr)

`CALIBRATE_XY_OFFSETS REF_TOOL=0 Z_MODE=amplitude TARGET_AMPLITUDE=11000 MIN_GAP=0.2 EXTRAPOLATE_DZ=0.4 FIT2D=1`, 682 s, Halterung neu gesetzt (T0-Grobsuche 120,55 / 115,42).

| Tool | Steigung X / Y (mm/mm) | Spitze (Methode) | Offset zu T0 | Kamera | Abweichung |
|---|---|---|---|---|---|
| T0 | **+0,141 / +0,505** | quadratisch (linear hätte X +0,12 / Y +0,13 anders ergeben) | Referenz | | |
| T1 | +0,017 / −0,069 | linear | +0,6524 / −4,6879 | +0,330 / −5,050 | **+322 / +362 µm** |
| T2 | +0,014 / −0,043 | linear | +0,8658 / −3,9525 | +0,440 / −4,560 | **+426 / +608 µm** |
| T3 | +0,014 / −0,050 | linear | +0,4200 / −5,4337 | −0,180 / −5,840 | **+600 / +406 µm** |

**Der 2D-Fit selbst funktioniert:** T2−T1 = (+0,213 / +0,735) und T3−T1 = (−0,232 / −0,746) stimmen mit allen bisherigen Läufen (1D-Linien, beide Spaltmodi, drei Halterungspositionen) auf 30–40 µm überein. Die Steigungen von T1–T3 bleiben klein.

**T0 ist damit endgültig das Problem, nicht das Verfahren.** Mit dem 2-mm-Radius des 2D-Fits verdoppelt sich T0s Steigung gegenüber der 8-mm-Parabel (Y 0,29 → 0,50, X −0,04 → +0,14): der lokale Scheitel folgt bei kleinem Spalt der Spitze, bei großem dem Block, und beide liegen bei T0 offenbar ~0,5 mm auseinander. Die quadratische Hochrechnung über 0,8 mm verschiebt T0s „Spitze" gegenüber der linearen um 0,12/0,13 mm und gegenüber dem 1D-Verfahren um ~0,35/0,45 mm — alle Offsets gegen T0 wandern gemeinsam mit. **Solange T0s Düse so sitzt, ist ihre Spitze per Spule nicht besser als ±0,3 mm zu bestimmen**, egal mit welcher Extrapolation; die Kamera-Abweichungen von 300–600 µm in diesem Lauf sind genau dieser Fehler.

Konsequenz: (a) T0s Düse unter der Kamera prüfen und ggf. tauschen/neu setzen (10.7, Weg 3); (b) bis dahin T1 als Referenz und T0s Offset aus der Kamera; (c) `FIT2D=1` und die Extrapolation bleiben für Tools mit Steigung < 0,1 die bessere Methode (gleiches Fenster, Kreuzterm), `QUAD_SLOPE` als Warnschwelle. Der Nebeneffekt der 2D-Messung: `spread` und Drift-Bias sind dort 0 (ein Raster, kein Hin/Rück) — die Webapp zeigt das entsprechend.

### 10.9 Entscheidung nach Lauf 7 (Tobi, 2026-09-04, Abend)

Die Ungenauigkeit durch T0s Düse wird vorerst akzeptiert, T0 bleibt Referenz. Zwei Änderungen dazu:

- **Basislinie am Druckerrand:** `measure_baseline()` fährt in X zur weiter entfernten Achsgrenze minus `baseline_edge_margin` (Default 10 mm) statt 40 mm zur Seite — damit steht bei der Basislinie wirklich nichts über der Spule. `baseline_offset` ist ersetzt.
- **Extrapolation immer über drei Spalte:** `QUAD_SLOPE` Default 0, also bekommt jedes Tool die dritte Stützstelle und die Parabel, auch mit gerader Düse. Gleiche Behandlung für alle; die Steigung bleibt als Warnung im Ergebnis. Kostet ~25 s je Tool.

Nie gefahren (Restart nötig).

### 10.10 Lauf 8: Endlauf nach den Entscheidungen aus 10.9 (2026-09-04, spät)

Nach `sudo systemctl restart klipper`, Bett leer, `G28`, QGL, `G28 Z`, `NOZZLE_LOCATOR_PARK`, Halterung drauf. Vorher geprüft: alle `gcode_move`-Offsets 0/0/0 (die Zeile „gcode-Offset aktiv" steht jetzt je Tool im Protokoll), `xy_results` leer, Config-Offset von T0 0/0 → keine Aufaddierung möglich, der Lauf schreibt nichts. Der Trockenlauf ist aus dem Assistenten raus; „Aufsetzen" startet direkt `CALIBRATE_XY_OFFSETS`.

```
CALIBRATE_XY_OFFSETS REF_TOOL=0 Z_MODE=amplitude TARGET_AMPLITUDE=11000 MIN_GAP=0.2 EXTRAPOLATE_DZ=0.4 FIT2D=1
```

803 s, Basislinie 3 133 679 Hz am Druckerrand, je Tool drei Raster (Spalt 0,8 / 1,2 / 1,6 mm, 13 × 12 Zellen) mit 2D-Fit und quadratischer Extrapolation auf Spalt 0.

| Tool | Eddy X / Y | Kamera X / Y | Δ X / Y (µm) | Steigung X / Y (µm je mm Spalt) | Messhöhe Z |
|---|---|---|---|---|---|
| T0 | 0 / 0 (Referenz) | 0 / 0 | – | +166 / +556 | 53,80 |
| T1 | +0,6670 / −4,5829 | +0,330 / −5,050 | +337 / +467 | +23 / −51 | 53,30 |
| T2 | +0,9009 / −3,8635 | +0,440 / −4,560 | +461 / +696 | +24 / −30 | 53,60 |
| T3 | +0,5008 / −5,3286 | −0,180 / −5,840 | +681 / +511 | +13 / −34 | 53,20 |

T1–T3 untereinander bleiben in den 30–40 µm der früheren Läufe (T2−T1 +234/+719, Kamera +110/+490; T3−T1 −166/−746, Kamera −510/−790 — hier ist die Abweichung zur Kamera wieder 0,04–0,35 mm, wie in §10.2/10.8). Der gemeinsame Versatz aller Tools gegen die Kamera (~+0,35…+0,68 mm in X, +0,47…+0,70 in Y) ist T0s eigene Spitze: Steigung 0,17/0,56 mm je mm Spalt, die Parabel über drei Spalte setzt den Scheitel bei Spalt 0 auf X 120,70 / Y 110,98, linear wären es 120,87 / 111,16. Das ist die akzeptierte Ungenauigkeit aus 10.9.

**Webapp im Browser gesehen (erstmals):** Verbindung über „Change Printer / Camera" (IP, Kamera „Gantry"), Akkordeon „XY-Offsets: Eddy vs. Kamera", Umschalter „Eddy-Sweep". Klick auf „T1" in der Tabelle öffnet den Dialog „Messbild T1" mit Kopfzeile (Offset, Amplitude, Steigung, Extrapolation, ρ) und den drei Rastern als plotly-3D-Flächen; keine Konsolenfehler. Die Ergebnisse stehen im Block, aber sind **nicht übernommen** — die Kamera-Werte bleiben in den `T<n>.cfg`.

**Zustand beim Verlassen:** gehomt, T0 montiert, Kopf X 121,4 / Y 111,5 / Z 60,2 über der Sonde, Halterung und Sonde auf dem Bett, Idle-Timeout 3600 s. `printer.offset.xy_results` hat `ref_tool` 0 mit `images` (drei Raster je Tool, 45 KB), Datei `.offset_xy_results.json`.

**Was jetzt noch offen ist**
- Übernahme in die Config: erst entscheiden, ob Kamera oder Sonde für T0 recht hat. Nächster Schritt dafür: T0s Düse unter der Kamera (Bohrung) gegen den Sonden-Scheitel legen; oder T0-Düse tauschen und Lauf 8 wiederholen.
- Wenn T0 gerade sitzt (Steigung < 0,05), sollte der Lauf die Kamera-Werte auf ~0,1 mm treffen (Referenz: T1-Läufe §10.2, §10.8).
