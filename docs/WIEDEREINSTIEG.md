# Wiedereinstieg: XY-Offset-Kalibrierung per Eddy-Spule

**Das hier zuerst lesen.** Danach weißt du, wo du stehst und was als Nächstes
zu tun ist. Die Details stehen woanders — dieses Dokument sagt nur, wo.

Stand: 2026-09-03, alles auf `main` und gepusht. Die Spule ist da, haengt per USB am 250er und liefert Rohfrequenz.

---

## 1. Wo du stehst

| Task | Zustand |
|---|---|
| 1 — Fit-Mathematik `nozzle_locator_fit.py` | **fertig**, 18 Zusicherungen |
| 2 — Sensoranbindung `nozzle_locator.py` | **fertig**, am 250er verifiziert (3,13 MHz, sd 21 Hz, 0 Fehler) |
| 3 — Z-Anfahrt, Sweep, Ortung | **fertig**, `NOZZLE_LOCATE X/Y/DIAG`, am 250er verifiziert |
| 4 — Extraktion `_resolve_tool_run()` | **gestrichen**, siehe §4 |
| 5 — `CALIBRATE_XY_OFFSETS` | **fertig**, ein kompletter Lauf über T0–T3 am 2026-09-04 |
| 6 — Extraktion `updateConfigFile()` | **fertig** |
| 7–9 — Webapp (Block, Assistent, Kamera) | **fertig gebaut, nie im Browser gesehen** |

Tests heute: 18 (Fit, Python) + 63 (Webapp, node) + 19 (Recovery, node).
Ein Abschluss-Review über den gesamten Umfang ist gelaufen und sauber.

**Blockiert ist Task 3 jetzt nur noch an der Halterung** (bekannte Bauhoehe, siehe
`xy-offset-offene-arbeiten.md` §2.3). Die Spule selbst ist erledigt, siehe §2.**

---

## 2. Was mit der Spule passiert ist (2026-09-03)

Die Spule ist ein BTT Eddy Duo, hängt per USB am 250er und ist erledigt.
Was dabei zu lernen war, steht in `xy-offset-offene-arbeiten.md` §2.1 —
kurz: **die Werksfirmware spricht kein USB**, der Kernel sieht beim
Einstecken gar nichts, auch keinen Fehlversuch. Geflasht wurde
Standard-Klipper per BOOT-Taster; der Pfad steht in
`configs/250/xy_probe.cfg.disabled`.

`NOZZLE_LOCATOR_READ DURATION=1.0` am 250er:

| Wert | Ergebnis |
|---|---|
| Frequenz | 3.132.430 Hz (Freiluft, Bettmitte) |
| Streuung | 21 Hz |
| Samples | 480 in 1 s (400 Hz Rate, skaliert sauber mit der Dauer) |
| Fehler | 0 |

Metalltest: eine von Hand darüber gehaltene Düse hebt die Frequenz um ~9.700 Hz
(sd ~200 Hz durch das Zittern der Hand). Damit trägt der Ansatz.

**Positionierung ist umgedreht (Spec R-B', 2026-09-03):** die Sonde ist
portabel. Der Kopf fährt mit dem Referenztool auf eine im Assistenten
einstellbare Anfahrposition (`park_x/park_y/park_z`, Default Bettmitte aus
dem Bettmesh und Z 60), **danach** stellt der Nutzer Sonde samt Halterung
grob mittig darunter. `park_z` ist Freihöhe und Fahrhöhe zugleich, ein
`safe_z` gibt es nicht mehr; `holder_top_z` bleibt als harter Z-Boden.
Kommando: `NOZZLE_LOCATOR_PARK [X= Y= Z=]`, gebaut und im Assistenten als
Schritt „Anfahren" verdrahtet (Node-Tests: 80). Der Messlauf nimmt später
`locator.parked`, die tatsächlich angefahrene Position. Am 250er
verifiziert: ungehomt wird verweigert, nach `G28` steht der Kopf auf
X 125 / Y 130 / Z 60 (Bettmesh-Mitte), Z unter dem Boden wird abgelehnt.

**Stand 2026-09-04: alles gebaut, ein kompletter Messlauf ist durch.**
Halterung 53 mm, vier Läufe bis zum ersten Durchlauf, Ergebnis und
Bewertung in `xy-offset-offene-arbeiten.md` §8. Kurz: Wiederholbarkeit
1–4 µm, aber ~0,5 mm systematisch daneben in Y, weil die Spule den
Heizblock mitsieht (240 µm je mm Spalt) und T0 die Eddy-NG-Sonde trägt.
Gegenmaßnahmen gebaut, aber **noch nicht gefahren**: gleicher Spalt aus
den Z-Switch-Daten (`Z_MODE=switch`) und kleiner Feinspalt (`fine_gap`).

**Zustand des 250ers beim Verlassen am 2026-09-04:** Klipper läuft auf
Commit `7d5f5c5` (Spaltmodus, Bootstrap, `fine_gap` drin, nie gefahren).
**Ungehomt**, weil der Service für den Deploy neu gestartet wurde.
**Halterung und Sonde stehen noch auf dem Bett** — vor dem Homen runter.
Sonde ist in `xy_probe.cfg` aktiv und steckt; vor dem Abstecken über den
Assistenten deaktivieren. Die Ergebnisse des Amplituden-Laufs liegen in
`printer.offset.xy_results` und `.offset_xy_results.json`.

**Nächste Schritte in dieser Reihenfolge:**

1. **Den Spaltmodus-Lauf fahren.** Klipper läuft schon auf dem Stand.
   Ablauf: Bett leer → `G28`, `QUAD_GANTRY_LEVEL`, `G28 Z`,
   `SET_IDLE_TIMEOUT TIMEOUT=3600`, `T0`, `NOZZLE_LOCATOR_PARK` → Sonde
   unter die Düse → `CALIBRATE_XY_OFFSETS`. Erwartung: Y-Fehler schrumpft
   auf den T0-Sonden-Anteil (~0,45 mm). Beobachten per `gcode_store`,
   nicht auf die HTTP-Antwort warten.
2. Bleibt der T0-Anteil: Lauf mit `REF_TOOL=1` und die Differenzen
   T2−T1, T3−T1 gegen die Kamera stellen. Stimmen die, ist T0 der
   Störer, nicht das Verfahren.
3. **Webapp im Browser öffnen** — jetzt liegen echte Daten in
   `printer.offset.xy_results`. Tabelle, Δ-Spalte, Sparkline prüfen;
   `amplitude` und `zswitch_run_id` fehlen dort noch (§8.4).
4. `NOZZLE_LOCATE AXIS=DIAG` einmal fahren (Kreuzkopplung, ρ).

---

## 3. Die drei Dinge, die am wahrscheinlichsten beißen

Alle drei sind bekannt und dokumentiert — sie stehen hier, damit du sie
wiedererkennst statt sie zu debuggen.

**a) `{transport}` heißt „läuft noch", nicht „fertig".**
Der wichtigste Fund der letzten Sitzung. `sendGcodeWithRecovery` liefert das,
wenn die Verbindung abreißt — und Moonraker hält sie offen, bis das Skript
durch ist. Bei einem Messlauf über sechs Tools ist das der **Normalfall**.
Nie einen maschinenbewegenden Folgeschritt daran hängen.
→ `xy-offset-offene-arbeiten.md` §4.5

**b) Die Datenform zwischen Task 5 und der UI ist nur auf dem Papier
abgestimmt.** Weicht Task 5 ab, zeigt die Tabelle stillschweigend „nicht
gemessen" statt zu krachen. Beim ersten Lauf also nicht nur prüfen, *ob*
Zahlen kommen, sondern ob **alle Spalten** gefüllt sind — besonders
Z-Vergleich und Drift-Bias.
→ `xy-offset-offene-arbeiten.md` §4.1

**c) Homing und Halterung dürfen sich nie überschneiden.**
`FIRMWARE_RESTART` löscht das Homing. Der Assistent ist deshalb so gebaut,
dass jedes Homing bei leerem Bett passiert. Wer die Reihenfolge anfasst,
muss diese Eigenschaft erhalten.
→ `xy-offset-offene-arbeiten.md` §5.1

**d) Klippers `bulk_sensor` und `ldc1612` verhalten sich anders, als der
Plan annahm.** Beim Bau von Task 2 an der Quelle nachgelesen: ein Client
wird nur abgemeldet, wenn **sein eigener** Callback `False` liefert — ein
zweiter Lambda-Client meldet den ersten nicht ab, der Sensor liefe endlos.
Und `errors` im Batch ist ein **kumulierter** Zähler seit Messstart, kein
Wert pro Batch. `nozzle_locator.read_frequency()` macht beides richtig
(Flag im selben Callback, letzter statt summierter Fehlerwert); der Code
für Task 3 im Plan ruft nur `read_frequency()` auf und ist davon nicht
betroffen — aber wer dort direkt an den Sensor geht, muss es wissen.
Der Wächtertest `check_klipper_api.py` nagelt beides fest.

**e) Jeder G-Code-Fehler setzt den Toolchanger auf `uninitialized`.**
Danach ist kein Tool mehr im Status, `T<n>` geht erst nach
`INITIALIZE_TOOLCHANGER` (erkennt das Tool, fährt nicht) — oder nach
einem Homing, das sich mit Halterung auf dem Bett verbietet. Der
XY-Lauf prüft den Status vorher und wechselt bei Abbruch selbst zurück
auf das Referenztool. → `xy-offset-offene-arbeiten.md` §8.1

**f) Die Spule misst den Metallschwerpunkt, nicht die Spitze.** Der
Heizblock liegt in +Y hinter der Düse und zieht den Y-Scheitel um
~240 µm je mm Spalt; T0 trägt zusätzlich die Eddy-NG-Sonde. Deshalb
gleicher Spalt aus den Z-Switch-Daten und ein kleiner Feinspalt — und
deshalb ist die Grobsuche ein *lokaler* Buckel, nie das globale Maximum.
→ `xy-offset-offene-arbeiten.md` §8.3

---

## 4. Entscheidungen, die schon getroffen sind

Damit die nächste Sitzung sie nicht neu aufmacht.

| Entschieden | Warum |
|---|---|
| **USB statt CAN** | Präsenzprüfung wird eindeutig; Moonrakers canbus-Endpunkt sieht nur unbeanspruchte Knoten und ist im Betrieb leer |
| **Klippers `ldc1612` statt eddy-ng** | zweite `[probe_eddy_ng]`-Sektion lässt Klipper nicht starten (globale Kommandos); außerdem brauchen wir keine Höhenkalibrierung |
| **Task 4 gestrichen** | die beiden Kommandos haben absichtlich verschiedene Tool-Auswahlpolitiken — ein gemeinsamer Helfer hätte `CALIBRATE_PROBE_OFFSETS` im Normalfall verändert |
| **Bidirektionaler Sweep ist Pflicht** | Zeitdrift verschiebt den Scheitel um ~19 µm/K; der Fehler ist ein Bias, keine Streuung, und kürzt sich nicht zwischen den Tools weg |
| **Z nur als Vergleichswert** | die Z-Anfahrt ist ohnehin Pflicht, aber der Z-Switch misst die Düsenspitze und ist damit prinzipiell richtiger |
| **XY-Default kalt messen** | der XY-Offset ist weitgehend temperaturunabhängig; der Heizblock dehnt sich nach unten |
| **Direkt auf `main`** | Tobis Entscheidung, entspricht der übrigen Historie |
| **Grobsuche je Tool, Vorhersage aus Config-Offsets** | T1–T3 liegen ~5 mm neben T0; ein gemeinsames Fenster findet sie nicht |
| **Gleicher Spalt aus Z-Switch-Daten, nicht gleiche Amplitude** | Amplitude hängt vom Düsenmaterial ab (T1 gibt bei gleichem Spalt weniger Signal); der Block verzieht den Scheitel je nach Spalt |
| **Bei Abbruch zurück auf das Referenztool** | Tobis Wunsch; mit Halterung auf dem Bett ist ein unbekanntes Tool im Kopf gefährlicher als ein verlorener Befund |
| **Bootstrap XY grob → Z-Switch → XY fein** | frische Config hat weder XY noch Z; der Schalter braucht XY, der Spalt braucht Z |

---

## 5. Welches Dokument welche Frage beantwortet

| Frage | Dokument |
|---|---|
| Was ist offen, was ist zu beachten, was ist ungeprüft? | `docs/xy-offset-offene-arbeiten.md` ← **die Hauptquelle** |
| Wie baue ich Task 2/3/5? (vollständiger Code) | `docs/superpowers/plans/2026-08-31-eddy-xy-offset.md` |
| Warum ist das so entworfen? | `docs/superpowers/specs/2026-08-31-eddy-xy-offset-design.md` |
| Was hat der Vorversuch gemessen? | Memory: `eddy-xy-offset-spike.md` |
| Wo genau stand die Messposition? | Memory: `eddy-xy-messposition.md` |

---

## 6. Die Frage, die am Ende zählt

Sie ist **halb beantwortet** (2026-09-04, Details §8 der offenen
Arbeiten):

> Ist das Eddy-Verfahren genauer als die Kamera von Hand?

**Wiederholbarer: ja, mit Abstand** — 1–4 µm Spannweite über drei Läufe.
**Richtiger: noch nicht** — ~0,5 mm systematisch in Y, bis 0,3 mm in X,
Ursache Heizblock und T0-Sonde. Als Driftwächter gegen eine per Kamera
gesetzte Referenz ist es damit heute schon brauchbar; ob es die Kamera
ersetzt, entscheidet der Spaltmodus-Lauf. Die ursprüngliche Planung:

Der Vorversuch lieferte σ = 9,87 µm aus n = 5 — Konfidenzintervall 5,9 bis
28,4 µm. Der Test kann „exzellent" und „unbrauchbar" statistisch nicht
unterscheiden. Und die Wiederholbarkeit des Kameraverfahrens wurde nie
gemessen.

Der Vergleichslauf am Ende des Plans beantwortet beides. Reihenfolge, wenn
die Hardware läuft:

1. Beide Verfahren auf denselben Tools, Differenz je Tool bilden
   → klärt das größte Risiko (misst die Spule den Heizblock statt die Düse?)
2. `NOZZLE_LOCATE AXIS=X REPEATS=20` gegen 8–10 Kamerazentrierungen,
   einmal mit und einmal ohne Werkzeugwechsel dazwischen
   → liefert endlich belastbare σ-Werte für beide Seiten
3. Erst danach `AXIS=DIAG` für die Kreuzkopplung, und erst danach
   entscheiden, ob die Messroutine aufwendiger werden muss
