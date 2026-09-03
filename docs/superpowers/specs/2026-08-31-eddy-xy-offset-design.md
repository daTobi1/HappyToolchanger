# Automatisierte X/Y-Offset-Kalibrierung per Eddy-Sonde — Design

## Übersicht

Tool-XY-Offsets werden heute von Hand über eine Kamera mit Fadenkreuz
ermittelt (`webapp/js/camera.js`, manuelles Jogging). Dieses Design ergänzt
ein **automatisches** Verfahren: eine zweite, aufwärts gerichtete
LDC1612-Spule steht in einer Halterung auf dem Druckbett, jedes Tool fährt
darüber, und die laterale Position der Düse wird aus dem Frequenzverlauf
gefittet.

Beide Verfahren stehen nebeneinander in einem gemeinsamen XY-Block; der
Nutzer wählt pro Lauf. Der Z-Offset wird mitgemessen, aber **nur als
Vergleichswert** ausgegeben — nicht als übernehmbarer Offset.

## 1. Nachweislage

### 1.1 Was der Vorversuch belegt

Handtest und motorischer Sweep am 250er (2026-08-31, siehe Memory
`eddy-xy-offset-spike`), reziproke Geometrie: T0-Eddy oben, Düse im Halter
darunter.

| Größe | Wert |
|---|---|
| Rauschen (sd) | 41–50 Hz, keine Fehlerflags |
| Signal Düse ~1 mm | +7.297 Hz = 152 × sd |
| Lateraler Gradient | 473–757 Hz/mm |
| Krümmung der Glocke | a ≈ 100–130 Hz/mm² |
| Wiederholbarkeit X, n=5 | σ = 9,87 µm |
| Temperaturgang Basislinie | 39,8 Hz/K |
| Kreuzcheck X- gegen Y-Sweep | 0,06 % |

Der Wandler taugt. Das ist die einzige Frage, die als beantwortet gilt.

### 1.2 Was er nicht belegt

- **σ = 9,87 µm ist statistisch bedeutungslos.** 95-%-Konfidenzintervall
  bei n=5: 5,9–28,4 µm (χ², 4 Freiheitsgrade).
- **σ misst keinen Bias.** Alle Läufe wurden aus derselben Richtung
  angefahren; ein zeitlinearer Drift wirkt auf alle gleich und taucht in
  der Streuung nicht auf.
- **Der Vorversuch maß eine nackte Düse im Halter.** Real nähert sich der
  komplette Hotend inklusive Heizblock. Siehe Risiko R1.
- Toolwechsel-Wiederholbarkeit, gehärteter Stahl, heiße Düse: ungemessen.

### 1.3 Drift verschiebt den Scheitel — der Fix

Der Sweep läuft monoton in x. Ein zeitlinearer Drift wird dadurch zu einem
linearen Term in x. Für `y = -a(x-c)² + m·x` liegt das Maximum bei
`c + m/(2a)`. Mit a ≈ 100–130 Hz/mm² und 8 mm Sweepbreite:

| Drift während eines Laufs | Scheitelverschiebung |
|---|---|
| 0,5 K (~20 Hz) | ~10 µm |
| 1 K (~40 Hz) | ~19 µm |
| 2,5 K (~100 Hz) | ~48 µm |

Der Bias kürzt sich **nicht** zwischen den Tools weg: das Referenztool wird
mit kalter Spule gemessen, spätere Tools mit einer von der vorigen Messung
aufgewärmten.

**Fix: bidirektional sweepen.** Hin und zurück, beide Scheitel mitteln.
Beim Rücklauf dreht das Vorzeichen von m, der lineare Term fällt exakt
heraus. Nicht optional, sondern Teil jeder Messung.

## 2. Randbedingungen

**R-A: Eine ortsfeste Spule ist zwingend.** Ein toolhead-montierter Sensor
fährt mit der Düse mit und kann sie prinzipiell nicht orten.

**R-B: Die absolute Spulenposition ist irrelevant.** Gemessen wird pro Tool
der Scheitel in Maschinenkoordinaten; der Offset ist die Differenz zum
Referenztool. Die Spulenposition kürzt sich weg. Deshalb darf die Halterung
abnehmbar sein — sie muss nur grob auffindbar sein und während eines Laufs
stillstehen.

**R-B': Die Sonde ist portabel, der Kopf gibt die Position vor** (Änderung
vom 2026-09-03). Nicht die Halterung hat eine Nominalposition, sondern der
Druckkopf: er fährt auf eine vom Nutzer einstellbare **Anfahrposition**
(`park_x`, `park_y`, `park_z`; Default Bettmitte und Z = 60), und der Nutzer
stellt Sonde samt Halterung *danach* grob mittig darunter. Damit entfällt
der feste Sitz der Halterung; die bekannte Bauhöhe bleibt, weil sie der
einzige harte Z-Boden ist. `park_z` ist eine reine Freihöhe zum
Unterschieben, keine Messhöhe — der Kopf tastet sich von dort herunter.

**R-C: Eine zweite `[probe_eddy_ng]`-Instanz ist unmöglich.**
`eddy-ng/probe_eddy_ng/probe.py:186` ruft `define_commands()` unbedingt im
Konstruktor; die registriert globale, nicht instanz-skopierte Kommandos
(`probe.py:214-312`). Zweite Instanz → "Command already registered" →
Klipper startet nicht. Deshalb: **Klippers eingebauter `ldc1612`-Treiber**.

**R-D: Keine Frequenz→Höhe-Kalibrierung nötig und möglich.** Ein aufwärts
gerichteter Sensor, der eine Düse sucht, lässt sich nicht gegen ein Bett
kalibrieren. Es zählt ausschließlich die Rohfrequenz.

**R-E: Klipper kennt keinen optionalen MCU.** Die Sonde wird nur zum Messen
angesteckt; steht ihre Definition in der Config und fehlt der Knoten,
startet Klipper nicht. Deshalb der Aktivieren-/Deaktivieren-Ablauf in §6.

Verifiziert am 250er (Klipper v0.13.0-687): `ldc1612.py`, `bulk_sensor.py`
und `probe_eddy_current.py` sind vorhanden.

## 3. Architektur

### 3.1 Komponenten

**`klippy/extras/nozzle_locator.py` (neu, ~350 Zeilen)**

Besitzt den Sensor über Klippers `ldc1612`. Kennt **keine Tools und keine
Offsets**. Beantwortet ausschließlich: "wo über mir liegt Metall?"

| Primitive | Aufgabe |
|---|---|
| `read_frequency(duration)` | Mittelwert, sd, Sample-Zahl, Fehlerflags aus einer `bulk_sensor`-Session |
| `park_position()` | liefert (x, y, z) der Anfahrposition: Config-Werte, sonst Bettmitte aus den Achsgrenzen und Z = 60 |
| `park(x, y, z)` | fährt das montierte Tool auf die Anfahrposition; unhomed -> Abbruch, nie homen |
| `approach_z(target_amplitude)` | vorsichtige Z-Annäherung von `park_z` herab bis Zielamplitude, harter Boden |
| `sweep(axis, center, span, step)` | ein gerichteter Sweep, Punkt für Punkt |
| `fit_center(points)` | Symmetrie-/Parabelfit über festen Punktesatz |
| `locate(axis, runs)` | Hin- und Rücksweep, Scheitel gemittelt |
| `measure_coupling(cx, cy)` | zwei Diagonal-Sweeps, liefert den Kreuzterm c und rho |

G-Code: `NOZZLE_LOCATOR_READ` (Rohfrequenz, Präsenz- und
Platzierungsprüfung), `NOZZLE_LOCATOR_PARK [X= Y= Z=]` (Anfahrposition
anfahren, ohne zu messen; fehlende Parameter aus der Config),
`NOZZLE_LOCATE AXIS=X|Y|DIAG [REPEATS=n]` (Einzelmessung, Diagnose,
Validierungsreihen ohne Webapp).

**`klippy/extras/offset.py` (erweitert, ~250 Zeilen)**

Nur Orchestrierung: `CALIBRATE_XY_OFFSETS`. Tools durchlaufen, Referenztool
zuerst und zuletzt, Differenzen bilden, persistieren. Nutzt vorhandene
`_require_leveled`, Recovery-Helfer, `OFFSET_*_GCODE`-Hooks und
`_return_to_ref_tool`.

**`webapp/js/tools.js` + `index.html`**

Gemeinsamer XY-Block je Tool mit Methodenumschalter, nach dem Muster von
`_dockResults`.

### 3.2 Warum diese Grenze

Der Locator ist ohne Toolchanger verstehbar, einzeln testbar und auf
fremden Configs mit beliebigem LDC1612 nutzbar. Alles Tool-Wissen bleibt
draußen. Keine eddy-ng-Abhängigkeit.

## 4. Config-Schema

Die gesamte Sensordefinition liegt in **einer** Datei, `xy_probe.cfg`, die
dauerhaft aus `printer.cfg` inkludiert wird. Umgeschaltet wird nicht die
Include-Zeile, sondern der **Inhalt** — aktiv oder leer. Damit gibt es keine
Include-Chirurgie, die Datei existiert immer, und der Zustand ist eindeutig
lesbar.

```ini
# xy_probe.cfg — aktiver Zustand
[mcu xyprobe]
serial: /dev/serial/by-id/<vom Nutzer einmalig eingetragen>

[nozzle_locator]
# Sensoranbindung: exakte Schlüssel folgen Klippers ldc1612 (i2c_mcu,
# i2c_bus, i2c_speed, ...) und werden unverändert durchgereicht.
i2c_mcu: xyprobe
i2c_bus: i2c0f

#park_x: 125              # Anfahrposition; fehlt sie, Bettmitte aus den
#park_y: 125              # Achsgrenzen. Der Assistent schreibt geänderte
park_z: 60                # Werte hierher zurück. park_z = Freihöhe zum
                          # Unterschieben UND Fahrhöhe aller Verfahrwege
search_span: 30           # Bereich der Grobsuche um die Anfahrposition
holder_top_z: 8           # Oberkante der Halterung (Bauhöhe)
min_gap: 0.5              # harter Z-Boden = holder_top_z + min_gap

sweep_span: 8             # Feinsweep, symmetrisch um den Grobscheitel
sweep_step: 1
dwell_time: 0.5
runs: 3                   # Läufe je Achse und Richtung
runs_tolerance: 0.05      # mm, Spannweite der Läufe; darüber Abbruch

min_amplitude: 2000       # darunter: kein Ziel gefunden
target_amplitude: 6000    # Zielsignal der Z-Anfahrt
max_offset: 5.0           # Plausibilitätsgrenze des Ergebnisses
```

**Woher der aktive Inhalt kommt.** Die vollständige Konfiguration steht
dauerhaft in `xy_probe.cfg.disabled` — dort trägt der Nutzer den serial-Pfad
und die Halterungsmaße **einmalig** ein. Aktivieren heißt: Inhalt von
`.disabled` nach `xy_probe.cfg` kopieren. Deaktivieren heißt: `xy_probe.cfg`
leeren. Damit überlebt die Konfiguration jeden Zyklus, die Webapp muss
nichts generieren, und der aktuelle Zustand ist an einer einzigen leeren
oder befüllten Datei ablesbar.

Orchestrierungs-Parameter kommen aus dem bestehenden `[offset]`,
insbesondere `default_ref_tool`. Kein zweiter Ort für dieselbe Information.

**Kommandoparameter.** `CALIBRATE_XY_OFFSETS` nimmt `REF_TOOL=`,
`TOOLS=` (Teilmenge, Default alle), `DRY_RUN=1` (Trockenlauf, §6 Schritt 7)
und `TEMP=` (Düsentemperatur, Default 0 = kalt messen, §14). Die Namen
folgen den bestehenden Kalibrierkommandos in `offset.py`.

## 5. Messablauf

Kernproblem: **das Signal existiert erst bei ~1–3 mm Spalt.** Eine
Grobsuche aus sicherer Höhe sieht nichts. Lösung ist eine getastete
Annäherung an der Nominalposition.

```
 0  (Assistent) Referenztool auf park_x/park_y/park_z gefahren,
    Nutzer hat die Sonde darunter gestellt — Voraussetzung, nicht Teil
    des Kommandos
 1  Referenztool aufnehmen (steht bereits)
 2  Basislinie: auf park_z messen — 60 mm über der Spule ist sie
    praktisch unsichtbar, ein Wegfahren entfällt
 3  Über park_x/park_y auf park_z stehen (Kontrollfahrt, falls ein
    Werkzeugwechsel dazwischen lag)
 4  approach_z: von park_z stufenweise absenken bis Amplitude > min_amplitude
    -> harter Boden bei holder_top_z + min_gap, sonst Abbruch
 5  Grobsweep X über search_span              -> Grobscheitel X
 6  Auf Grobscheitel X, Grobsweep Y           -> Grobscheitel Y
 7  Z-Feinanfahrt auf target_amplitude über dem Grobscheitel
    -> diese Messhöhe gilt für ALLE Tools dieses Laufs
 8  Referenztool feinmessen: locate(X), locate(Y)
 9  Je weiteres Tool:
      a  Werkzeugwechsel (auf park_z)
      b  auf gemerkten Grobscheitel, auf park_z
      c  approach_z auf dieselbe target_amplitude
         -> Delta-z gegenüber Ref-Tool = Z-Vergleichswert
      d  locate(X), locate(Y)
10  Zurück auf das Referenztool, auf park_z
11  Differenzen bilden, persistieren
```

**Zu Schritt 4:** großzügig genug — der Vorversuch zeigte 5–8 mm seitlich
daneben noch +3.513 Hz. „Grob mittig unter die Düse gestellt" ist damit
Millimeter-tolerant; `search_span` fängt den Rest.

**Zu Schritt 4, Abstieg:** der Weg von `park_z` = 60 bis zum Signal ist
lang. `approach_z` darf deshalb oberhalb von `holder_top_z + 5 mm` in
großen Schritten fahren und erst darunter tasten — der Boden bleibt hart.

**Zu `park_z` als Fahrhöhe:** die Halterung wurde bei stehender Düse auf
dieser Höhe untergeschoben, also ist sie per Konstruktion frei. Ein
eigenes `safe_z` gibt es nicht mehr.

**Zu Schritt 9c:** alle Tools werden auf dieselbe *Amplitude* angefahren,
nicht auf dasselbe kommandierte Z — nur so misst jedes Tool bei gleichem
Spalt. Die Differenz im kommandierten Z ist dann der Z-Offset-Unterschied.

## 6. Assistent und Steck-Flow

| # | Schritt | Inhalt |
|---|---|---|
| 1 | Anstecken | Zum Anstecken der USB-Sonde auffordern — **die Halterung bleibt ausdrücklich noch unten.** |
| 2 | Prüfen | `/machine/peripherals/serial`: ist der `serial`-Pfad da? Fehlt er, Ende — ohne Config-Änderung, ohne Neustart. |
| 3 | Aktivieren | Inhalt aus `xy_probe.cfg.disabled` nach `xy_probe.cfg` kopieren -> `FIRMWARE_RESTART` -> warten bis ready. |
| 4 | Homen | **Jetzt**, direkt nach dem Neustart, bei leerem Bett. Siehe die Begründung unten. |
| 5 | Sensor prüfen | `NOZZLE_LOCATOR_READ` muss plausibel und fehlerfrei antworten. Bewegt nichts. |
| 5a | Anfahren | Drei Felder X, Y, Z, vorbelegt aus der Config bzw. Bettmitte und 60. Weichen sie von der Config ab, werden sie **vor** dem Anfahren in `xy_probe.cfg.disabled` und `xy_probe.cfg` zurückgeschrieben. Dann `NOZZLE_LOCATOR_PARK` mit dem Referenztool, und **`waitForPrinterIdle`**, bis der Drucker nachweislich steht. Das Bett ist noch leer. |
| 6 | Aufsetzen | **Erst jetzt**: „Sonde samt Halterung grob mittig unter die Düse stellen." Ab hier ist jedes Homing gefährlich. |
| 7 | Trockenlauf | Ganze Sequenz inkl. Werkzeugwechsel und Verfahrwege auf `park_z`, **ohne jedes Absenken**. Muster aus der Dock-Kalibrierung (`c5e51157`). |
| 8 | Messen | Ablauf nach §5. |
| 9 | Ergebnisse | Anzeige, Vergleich, Übernehmen bzw. Übernehmen + schreiben. |
| 10 | Abschließen | `xy_probe.cfg` leeren -> `FIRMWARE_RESTART` -> **und erst danach** zum Abstecken und Abnehmen der Halterung auffordern. |

**Warum das Aufsetzen hinter den Neustart gehört.** Die erste Fassung homte
in Schritt 1 und ließ die Halterung danach aufsetzen. Das war falsch, und der
Fehler ist erst im Review aufgefallen.

Ein `FIRMWARE_RESTART` **löscht `homed_axes`** — Standardverhalten von
Klipper, und die Webapp warnt an anderer Stelle selbst wörtlich „Der Drucker
verliert das Homing" (`tools.js:3536`). In der alten Reihenfolge stand der
Drucker nach dem Aktivieren also ungehomt da, **mit der Halterung auf dem
Bett**.

Damit wurde ein vorhandener Komfortmechanismus zur Falle: die folgenden
Aufrufe liefen über `sendGcodeWithRecovery`, und scheitert einer mit „must
home", bietet `recoveryFor()` (`tools.js:262-267`) einen Knopf „Home + QGL,
dann weiter" an, der `G28 -> QUAD_GANTRY_LEVEL -> G28 Z` fährt — genau die
Sequenz aus §7.1. Läuft sie durch, meldet der Aufruf Erfolg, der Assistent
macht weiter, und der Crash taucht in keiner Fehlerbehandlung auf.

Mit der neuen Reihenfolge findet **jedes** Homing bei leerem Bett statt.

**Ab Schritt 6 zusätzlich kein G28-Recovery mehr.** Ein Restpfad bliebe sonst:
der Idle-Timeout kann zuschlagen, während der Nutzer einen Dialog liest, und
die Motoren abwerfen. Ab dem Aufsetzen laufen die G-Code-Aufrufe deshalb über
einen wizard-eigenen Sender ohne Reparaturdialog; ein Fehlschlag geht direkt
in den `catch`, und der sagt ausdrücklich: erst die Halterung abnehmen, dann
homen.
**Rettungsnetz.** Ist die Sonde aktiviert, aber abgesteckt, startet Klipper
nicht. Die Webapp erkennt diese Lage (Config aktiv + Klipper im
Fehlerzustand) und bietet einen Ein-Klick-Fix. Das funktioniert auch bei
totem Klipper, weil `webapp/app.py` ein reiner Dateiserver ist und alle
Config-Zugriffe über Moonrakers `/server/files/`-API laufen — Moonraker
läuft weiter.

## 7. Sicherheit

1. **`CALIBRATE_XY_OFFSETS` homt nie selbst.** Unhomed -> Abbruch.
   `homing.cfg:35` setzt bei unhomed Z ein `SET_KINEMATIC_POSITION Z=0`,
   hebt nur 10 mm und fährt Y quer über die Bettmitte — mit Aufbau auf dem
   Bett ein Crash.
2. **Idle-Timeout** wird auf 3600 s gesetzt und am Ende **und bei jedem
   Abbruch** zurückgestellt.
3. **Harter Z-Boden** aus `holder_top_z + min_gap`. Darunter fährt nichts.
   `park_z` muss darüber liegen, sonst lehnt `NOZZLE_LOCATOR_PARK` ab.
5. **`NOZZLE_LOCATOR_PARK` homt nie selbst** und bewegt nur bei gehomten
   Achsen — es ist der letzte Move vor dem Aufsetzen, danach verbietet
   sich jede Recovery, die homt.
4. **Der Trockenlauf ist die Kollisionsprüfung.** Ob der Wechselweg über die
   Halterung führt, ist geometrieabhängig und im Code nicht allgemein
   prüfbar — also einmal ungefährlich abfahren statt behaupten.

## 8. Abbruchkriterien

Jeder Abbruch stellt den Idle-Timeout zurück und kehrt auf das
Referenztool zurück.

| Kriterium | Meldung |
|---|---|
| Sensor stumm oder Statusflags mit Fehlerbits | Sonde nicht bereit |
| Amplitude < `min_amplitude` bei der Grobsuche | Halterung nicht gefunden oder falsch platziert |
| Scheitel am Rand des Sweepfensters | Bereich verfehlt |
| Spannweite der `runs` Läufe über `runs_tolerance` | Messung instabil, Einzelwerte werden ausgegeben |
| Ergebnis über `max_offset` | Vermutlich falscher Scheitel |
| Achsen unhomed oder nicht geleveled | vorhandene `_require_leveled`-Prüfung |
| Werkzeugwechsel scheitert | vorhandene Recovery |

Der Preflight auf ausreichendes Signal ist **Teil jeder Sweep-Primitive**,
kein optionaler Guard. Sein Fehlen in einem Ad-hoc-Skript hat im Vorversuch
fünf wertlose Messläufe produziert.

## 9. Datenfluss und Persistenz

```
nozzle_locator.locate()  ->  (x_peak, y_peak) je Tool, Maschinenkoordinaten
        |
offset.py:  Delta = (x_n - x_ref, y_n - y_ref)
        |
.offset_xy_results.json          (vorhandenes _get_state_file_path)
        |
get_status -> 'xy_results'       (Webapp pollt wie _dockResults)
        |
"Übernehmen"             -> SET_TOOL_GCODE_OFFSET T=n X=.. Y=..
"Übernehmen + schreiben" -> zusätzlich replaceInConfigSection in T<n>.cfg
```

**Der Apply-Pfad existiert bereits.** `cmd_SET_TOOL_GCODE_OFFSET`
(`offset.py:1976`) behandelt X, Y und Z gleichberechtigt über
`set_parameter` + `save_parameter`. Es wird nichts Neues gebaut, nur
befüllt.

Je Tool und Achse werden **Hin- und Rückwert einzeln** gespeichert, nicht
nur der Mittelwert. Ihre Differenz ist der gemessene Drift-Bias.

## 10. UI

Neuer Bereich "XY-Offsets" im Offset-Tab, gleichrangig neben Z, Probe,
Dock und PID.

```
+- XY-Offsets ---------------------------------------------------+
|  Verfahren:  ( ) Kamera (manuell)   (o) Eddy-Sweep              |
|  Sonde: aktiv (T0 Referenz)            [Assistent...]           |
+-----------------------------------------------------------------+
| Tool | aktuell X/Y  | gemessen X/Y | Delta  | Z-Vgl. | Aktionen  |
| T0   | 0.000 0.000  |  - Referenz -|   -    |   -    |           |
| T1   | 0.120 -0.045 | 0.134 -0.038 | +14 um | +8 um  | [Üb.][+S] |
+-----------------------------------------------------------------+
|  [Alle übernehmen + schreiben]                                  |
|  Sweep T1/X   .:-=#=-:.   fwd 124.003  rev 124.011  M 124.007   |
+-----------------------------------------------------------------+
```

- **Live-Kurve:** `nozzle_locator.get_status()` führt `state` und die
  bisherigen `(Position, Frequenz)`-Paare mit. Die Webapp pollt ohnehin.
  Kein Streaming, kein neuer Kanal.
- **Hin- und Rückwert einzeln sichtbar** — die Drift-Korrektur wird bei
  jedem Lauf nebenbei überprüft statt geglaubt.
- **Assistent** als Modal über den vorhandenen
  `confirmDialog`/`alertDialog`-Helfern (`tools.js:95-355`).
- **Kameramethode bekommt "Position übernehmen"**: hält bei zentriertem
  Fadenkreuz die aktuelle Kopfposition fest. Damit liefern beide Verfahren
  dasselbe Format — eine Position je Tool — und der Vergleich wird exakt.
  Eigenständiger Zusatz, ohne Kopplung an den Rest.

## 11. Tests

Das Repo prüft mit `check_*`-Skripten, die auf dem Drucker laufen
(`tests/check_klipper_api.py` und Geschwister, `check_webapp_recovery.js`).

**Ohne Hardware testbar, mit dem meisten Wert — die Fit-Mathematik.**
Synthetischer Glockenverlauf mit bekanntem Zentrum plus überlagertem
linearem Drift:

- ein einzelner Sweep verfehlt das Zentrum um den vorhergesagten Betrag
  `m/(2a)`
- der bidirektionale Mittelwert trifft es
- die Abbruchkriterien greifen: Scheitel am Fensterrand, Amplitude zu
  klein, negatives Vorzeichen

Damit ist der Drift-Fix eine getestete Zusicherung statt einer Behauptung.

**`check_klipper_api.py` wird erweitert** um `ldc1612` und `bulk_sensor` —
genau der Zweck der Datei.

**Nicht automatisierbar:** der Rest. Der Trockenlauf ist der Hardwaretest.

## 12. Gezielte Extraktionen

~~1. **`_resolve_tool_run()`** in `offset.py`~~ — **gestrichen.** Die Annahme,
`cmd_CALIBRATE_ALL_Z_OFFSETS` und `cmd_CALIBRATE_PROBE_OFFSETS` lösten
dieselbe Aufgabe doppelt, hat sich bei der Umsetzung als falsch erwiesen. Die
beiden haben absichtlich verschiedene Auswahlpolitiken — unter anderem wählt
das zweite ohne `TOOLS` **nur Tools mit vorhandenen Z-Switch-Daten** und
erzwingt das Referenztool weder in die Liste noch an deren Anfang
(`offset.py:904-908` behandelt „Referenz ist mitgemessen" ausdrücklich als
Sonderfall). Eine gemeinsame Funktion hätte das zweite Kommando im Normalfall
verändert. Gemeinsam wäre nur ein Dreizeiler geblieben. `CALIBRATE_XY_OFFSETS`
bringt stattdessen seine eigene, so benannte Auflösung `_xy_tool_run()` mit.
Details und die Gegenüberstellung stehen in Task 4 des Plans.

2. **`updateConfigFile(path, mutator)`** in `tools.js`: der
   Lese-/Upload-Block steht viermal fast identisch (`474/499`, `627/637`,
   `1784/1803`, `2640/2656`). Diese Extraktion bleibt — hier ist der Code
   tatsächlich gleich und nicht nur ähnlich geformt. Zeigt sich bei der
   Umsetzung das Gegenteil, gilt dieselbe Regel wie oben: nicht einebnen.

Weiteres Refactoring an `offset.py` (2005 Zeilen) ist ausdrücklich **nicht**
Teil dieser Arbeit.

## 13. Offene Risiken

**R1 — Heizblock statt Düsenspitze.** Der Vorversuch maß eine nackte Düse;
real nähert sich der komplette Hotend. Die Spule ortet den Metallschwerpunkt
im Feld, nicht die Spitze. Liegt der Schwerpunkt pro Tool anders
(Einschraubtiefe, Blockverdrehung, Fertigung), kürzt sich der Fehler
**nicht** in der Differenz weg. Das ist das größte Restrisiko und vorab
nicht ausräumbar.
*Umgang:* Der gemeinsame Block macht v1 selbst zum Messinstrument — Eddy
gegen Kamera pro Tool. Systematische Abweichung je Tool = genau dieser
Effekt, direkt gemessen.

**R2 — Der bidirektionale Fix ist hergeleitet, nicht gemessen.**
*Umgang:* beide Richtungen werden einzeln gespeichert und angezeigt.

**R3 — Gehärteter Stahl kann Vorzeichen drehen oder Amplitude einbrechen
lassen.** *Umgang:* Grobsweep bestimmt das Vorzeichen, der Fit arbeitet auf
dem Betrag; bricht die Amplitude ein, greift das Abbruchkriterium statt
eines erfundenen Scheitels.

**R4 — Wenige Läufe ergeben unbelastbares σ.** *Umgang:* `REPEATS=` an
`NOZZLE_LOCATE`, damit Validierungsreihen (n ≈ 20) mit dem ausgelieferten
Werkzeug laufen.

**R5 — Präsenzerkennung. ERLEDIGT.** War als offene Frage notiert; am 250er
nachgemessen. `/machine/peripherals/serial` liefert `serial_devices[]` mit
`path_by_id` und ist damit für die USB-Sonde eindeutig auswertbar. Der
zunächst geplante `/machine/peripherals/canbus` wäre untauglich gewesen: er
liefert `can_uuids: []`, obwohl zwei CAN-MCUs laufen — er sieht nur Knoten,
die kein laufender Klipper beansprucht. Der Rückfall auf eine manuelle
Rückfrage bleibt für ältere Moonraker-Versionen im Code.

**R6 — Ein FIRMWARE_RESTART löscht das Homing.** Im Review von Task 8
gefunden: die erste Fassung des Assistenten ließ die Halterung vor dem
Aktivieren aufsetzen, und der zum Laden der Sonde nötige Neustart hinterließ
den Drucker ungehomt mit Aufbau auf dem Bett — woraufhin der vorhandene
Recovery-Dialog ein `G28 → QGL → G28 Z` angeboten hätte. *Umgang:*
Reihenfolge geändert (§6), Homing findet jetzt ausschließlich bei leerem Bett
statt; zusätzlich ab Schritt 6 kein G28-Recovery mehr.

## 14. Nicht im Umfang

- Kontinuierlicher Sweep statt Punkt-für-Punkt (spätere Optimierung)
- Reaktivierung von `tools_calibrate` (Pin-Antasten)
- Z als übernehmbarer Offset
- Automatische Erkennung der Halterungsposition — überflüssig geworden,
  der Kopf gibt die Position vor (R-B')
- Temperaturkompensation der Basislinie — erledigt der bidirektionale Sweep
- Heizen als Default (Feld existiert, Default 0 = kalt; der XY-Offset ist
  weitgehend temperaturunabhängig, weil sich der Heizblock im Wesentlichen
  nach unten dehnt)

## 15. Betroffene Dateien

| Datei | Art |
|---|---|
| `klippy/extras/nozzle_locator.py` | neu |
| `klippy/extras/offset.py` | erweitert (Kommando + Extraktion) |
| `webapp/js/tools.js` | erweitert (XY-Block, Assistent, Extraktion) |
| `webapp/js/camera.js` | erweitert ("Position übernehmen") |
| `webapp/index.html` | erweitert |
| `configs/250/xy_probe.cfg`, `configs/350/xy_probe.cfg` | neu (leer = deaktiviert) |
| `configs/250/xy_probe.cfg.disabled`, `configs/350/…` | neu (Vorlage mit UUID und Halterungsmaßen) |
| `configs/250/printer.cfg`, `configs/350/printer.cfg` | Include-Zeile |
| `tests/check_klipper_api.py` | erweitert |
| `tests/check_nozzle_locator_fit.py` | neu |
| `install.sh` | Symlink für das neue Modul |
