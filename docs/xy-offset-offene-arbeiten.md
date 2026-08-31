# XY-Offset-Kalibrierung: offene Arbeiten, sobald die zweite Eddy-Spule da ist

Die XY-Offset-Kalibrierung per Eddy-Spule ist entworfen, geplant und zur Hälfte gebaut. Was fehlt, hängt an der zweiten Spule, die es noch nicht gibt. Dieses Issue hält fest, was noch zu tun ist, was beim Bestellen und Bauen zu beachten ist, und welche Stellen nachgeprüft werden müssen, weil sie ohne echte Hardware entstanden sind.

**Design:** `docs/superpowers/specs/2026-08-31-eddy-xy-offset-design.md`
**Plan:** `docs/superpowers/plans/2026-08-31-eddy-xy-offset.md`

---

## 1. Stand

| Task | Zustand |
|---|---|
| 1 — Fit-Mathematik (`nozzle_locator_fit.py`) | **fertig**, 16 Zusicherungen, getestet |
| 2 — Sensoranbindung (`nozzle_locator.py`) | offen, **braucht Hardware** |
| 3 — Z-Anfahrt, Sweep, Ortung | offen, **braucht Hardware** |
| 4 — Extraktion `_resolve_tool_run()` | **gestrichen** (siehe unten) |
| 5 — `CALIBRATE_XY_OFFSETS` | offen, hängt an Task 3 |
| 6 — Extraktion `updateConfigFile()` | **fertig**, Test grün |
| 7 — XY-Block in der Webapp | **vorgebaut ohne Hardware**, Node-Tests gruen, siehe Abschnitt 4 |
| 8–9 — Assistent, Kamera-Position | vorgebaut ohne Hardware, siehe Abschnitt 4 |

**Task 4 wurde bewusst gestrichen.** Die Annahme, `CALIBRATE_ALL_Z_OFFSETS` und `CALIBRATE_PROBE_OFFSETS` lösten dieselbe Tool-Auswahl doppelt, war falsch — sie haben absichtlich verschiedene Politiken (u. a. wählt das zweite ohne `TOOLS` nur Tools mit vorhandenen Z-Switch-Daten und erzwingt das Referenztool weder in die Liste noch an deren Anfang). Ein gemeinsamer Helfer hätte das zweite Kommando im Normalfall verändert. Details in Task 4 des Plans.

---

## 2. Hardware — vor dem Bestellen lesen

### 2.1 KRITISCH: Firmware

**Die neue Spule braucht die *normale* Klipper-Firmware, NICHT die von eddy-ng.**

Grund: eddy-ng bringt einen eigenen MCU-Befehlssatz mit (`config_ldc1612_ng`, `ldc1612_ng_start_stop`, `ldc1612_ng_setup_home`, …, siehe `eddy-ng/ldc1612_ng.py`) und dafür eigene Firmware unter `eddy-ng/src/eddy_ng/`. Das neue Modul `nozzle_locator.py` benutzt bewusst Klippers **eingebauten** `ldc1612`-Treiber, und der spricht den Standard-Befehlssatz (`config_ldc1612`, `query_ldc1612_status`). Die beiden sind nicht kompatibel.

Das ist kein Nachteil, sondern Absicht: beide Sensoren bleiben vollständig unabhängig, verschiedene Firmware, keine Kollision von Kommandos oder Config-Sektionen. Der Grund für diese Entscheidung war ohnehin ein anderer — eine zweite `[probe_eddy_ng]`-Sektion lässt Klipper gar nicht erst starten, weil `eddy-ng/probe_eddy_ng/probe.py:186` seine Kommandos global und nicht instanz-skopiert registriert.

**Zu tun:** neue Spule mit Standard-Klipper flashen (USB-Variante, siehe 2.2).

**Früh verifizieren, bevor Zeit in Task 2 fließt:** dass Klippers `ldc1612` die neue Spule wirklich anspricht. Ein einzelnes `NOZZLE_LOCATOR_READ` mit plausibler Frequenz und ohne Fehlerflags reicht als Beweis.

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
| **bekannte, feste Bauhöhe** | geht als `holder_top_z` in die Config; daraus wird der harte Z-Boden `holder_top_z + min_gap` berechnet, unter den nichts fährt |
| **reproduzierbarer Sitz auf dem Bett, ±3 mm reicht** | die Grobsuche tastet sich an der Nominalposition nach unten; laut Vorversuch stehen 5–8 mm seitlich daneben noch +3.513 Hz an, also ist das großzügig |
| **kein Metall in Spulennähe** | verfälscht die Basislinie |
| **hitzefest, falls je heiß gemessen wird** | Default ist kalt messen; heiß ist optional und dann liegt eine 200-°C-Düse ~1 mm über der Halterung |
| **niedrig genug für den Werkzeugwechselweg** | oder der Wechselweg führt nicht darüber — das prüft der Trockenlauf, siehe 5.2 |

Die absolute Position der Halterung ist **egal**. Gemessen wird pro Tool der Scheitel in Maschinenkoordinaten, der Offset ist die Differenz zum Referenztool — die Spulenposition kürzt sich exakt weg. Genau deshalb darf die Halterung abnehmbar sein.

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

**Wenn Task 5 davon abweicht, zeigt die Tabelle stillschweigend „nicht gemessen" statt zu krachen.** Das ist als Verhalten richtig, macht den Fehler aber unsichtbar. Beim ersten echten Lauf also nicht nur schauen, ob Zahlen erscheinen, sondern ob **alle** Spalten gefüllt sind — besonders Z-Vergleich und Drift-Bias.

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

### 4.5 Das Abmelden vom Sensor-Datenstrom

`read_frequency()` meldet sich per `sensor.add_client(cb)` an und beendet die Session, indem der Callback `False` liefert. **Ob Klippers `bulk_sensor.BatchBulkHelper` Clients wirklich so verwirft, ist an der Quelle zu bestätigen** — im Plan steht ein Hinweis dazu bei Task 2 Step 3. Wenn nicht, muss der Callback selbst ein Flag setzen, statt einen zweiten Callback anzumelden. Symptom bei Fehler: der Sensor läuft nach der ersten Messung endlos weiter oder die Sample-Zahl wächst über Messungen hinweg.

### 4.6 Kein einziger Browser-Check

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

### 6.1 Heizblock statt Düsenspitze (das größte)

Der Vorversuch maß eine **nackte Düse** in einem Halter. Real nähert sich der komplette Hotend inklusive Heizblock, und die Spule ortet den Metallschwerpunkt in ihrem Feld, nicht die Spitze. Liegt der Schwerpunkt pro Tool anders — Einschraubtiefe, Blockverdrehung, Fertigungstoleranz — kürzt sich der Fehler **nicht** in der Differenz weg.

Das lässt sich vorab nicht ausräumen. Der gemeinsame XY-Block macht die erste Version selbst zum Messinstrument dafür: Eddy gegen Kamera, pro Tool.

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
