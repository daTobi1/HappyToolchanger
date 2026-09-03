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
| 3 — Z-Anfahrt, Sweep, Ortung | offen — **naechster Schritt**, braucht die Halterung |
| 4 — Extraktion `_resolve_tool_run()` | **gestrichen**, siehe §4 |
| 5 — `CALIBRATE_XY_OFFSETS` | offen, hängt an Task 3 |
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

Damit trägt der Ansatz. Der einzige offene Vorversuch aus dem Plan ist
der Metalltest (Hand oder Düse über die Spule, Frequenz muss steigen).

**Nächste Schritte in dieser Reihenfolge:**

1. **Halterung** mit bekannter Bauhöhe, `holder_top_z` in der `.disabled`
   eintragen (steht derzeit auf dem Plan-Default 8 mm).
2. **Task 3 bauen** (Plan hat den vollständigen Code): Z-Anfahrt, Sweep,
   `NOZZLE_LOCATE`. Zwei Planannahmen sind beim Bau von Task 2 gefallen
   und gelten auch für Task 3 — siehe §3 d).
3. Dann Task 5.
4. **Erst danach die Webapp im Browser ansehen.** Sie ist nie geöffnet
   worden; siehe §3.

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

Sie ist **weiterhin unbeantwortet**, und alles oben dient nur dazu, sie
beantworten zu können:

> Ist das Eddy-Verfahren genauer als die Kamera von Hand?

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
