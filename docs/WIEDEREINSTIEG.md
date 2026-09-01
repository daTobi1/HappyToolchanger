# Wiedereinstieg: XY-Offset-Kalibrierung per Eddy-Spule

**Das hier zuerst lesen.** Danach weißt du, wo du stehst und was als Nächstes
zu tun ist. Die Details stehen woanders — dieses Dokument sagt nur, wo.

Stand: 2026-09-01, Commit `c6929699`, alles auf `main` und gepusht.

---

## 1. Wo du stehst

| Task | Zustand |
|---|---|
| 1 — Fit-Mathematik `nozzle_locator_fit.py` | **fertig**, 18 Zusicherungen |
| 2 — Sensoranbindung `nozzle_locator.py` | offen — **braucht die Spule** |
| 3 — Z-Anfahrt, Sweep, Ortung | offen — **braucht die Spule** |
| 4 — Extraktion `_resolve_tool_run()` | **gestrichen**, siehe §4 |
| 5 — `CALIBRATE_XY_OFFSETS` | offen, hängt an Task 3 |
| 6 — Extraktion `updateConfigFile()` | **fertig** |
| 7–9 — Webapp (Block, Assistent, Kamera) | **fertig gebaut, nie im Browser gesehen** |

Tests heute: 18 (Fit, Python) + 63 (Webapp, node) + 19 (Recovery, node).
Ein Abschluss-Review über den gesamten Umfang ist gelaufen und sauber.

**Blockiert ist alles Weitere an genau einer Sache: die zweite Eddy-Spule
ist bestellt, aber noch nicht da.**

---

## 2. Das Erste, was du tust, wenn die Spule ankommt

In dieser Reihenfolge. Schritt 1 und 2 entscheiden, ob der Rest überhaupt
Sinn hat.

1. **Firmware prüfen, bevor irgendetwas anderes passiert.**
   Die Spule braucht **Standard-Klipper-Firmware, nicht die von eddy-ng** —
   die beiden sprechen verschiedene MCU-Befehlssätze. Falsche Firmware heißt:
   Klippers `ldc1612` sieht das Gerät nicht, und niemand versteht warum.
   Begründung in `xy-offset-offene-arbeiten.md` §2.1.

2. **`serial`-Pfad ermitteln und eintragen.** Mit gesteckter Sonde:
   ```bash
   ls /dev/serial/by-id/
   ```
   Den `by-id`-Pfad in `configs/<250|350>/xy_probe.cfg.disabled` eintragen —
   nie `/dev/ttyACM0`, der wandert.

3. **Task 2 bauen** (Plan hat den vollständigen Code) und mit einem einzigen
   `NOZZLE_LOCATOR_READ` beweisen, dass Klippers `ldc1612` die Spule
   anspricht. Plausible Frequenz, keine Fehlerflags. **Das ist der Moment,
   an dem sich entscheidet, ob der ganze Ansatz trägt** — vorher lohnt kein
   weiterer Aufwand.

4. Dann Task 3, dann Task 5. Beide im Plan ausgeschrieben.

5. **Erst danach die Webapp im Browser ansehen.** Sie ist nie geöffnet
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
