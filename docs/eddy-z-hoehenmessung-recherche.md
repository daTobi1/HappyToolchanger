# Düsenhöhe mit der Bett-Spule messen — Recherche (2026-09-12)

Frage von Tobi: Gibt es Verfahren, mit einem Aufbau wie unserem (LDC1612-Spule fest
auf dem Bett, Düse kommt von oben) die **Höhe** der Düse zu bestimmen — und lassen sie
sich auf unsere Anwendung übertragen?

Anlass: Der Amplitudenmodus des XY-Laufs liefert für T2 −0,35 mm, der Z-Switch +0,211 mm.
Kein Vorzeichenfehler, sondern Physik: gleiche Amplitude ist nicht gleicher Spalt. Am
Z-Boden liefert T0 17.615 Hz, T2 nur 14.271 Hz (Kupferplatine der Eddy-NG-Sonde am
T0-Kopf). Ein signalschwächeres Tool muss tiefer fahren, um dieselbe Amplitude zu
erreichen, und genau das steckt in der Differenz. Der Amplitudenmodus misst also
Signalstärke, nicht Düsenlänge.

## 1. Kurzfassung

| Verfahren | Unabhängig von der Signalstärke des Tools? | Mit unserem LDC1612 machbar? | Aufwand | Bewertung |
|---|---|---|---|---|
| **A. Inverser Tap** — Düse tippt auf die Abdeckung über der Spule, Kontakt am Knick der Kurve | ja (Knick, nicht Pegel) | ja, mit Einschränkungen bei der Echtzeit-Erkennung | Hardware klein, Software mittel | **aussichtsreichster Kandidat** |
| **B. Metallmembran über der Spule** (Touch-on-Metal, TI) | ja (die Spule sieht die Membran, nicht die Düse) | ja | Hardware mittel | gut, aber Umbau der Halterung, XY-Messung braucht dann eine zweite Position |
| **C. Kurvenform-Fit** — Kurve je Tool aufnehmen, Skalierung frei, Nullpunkt aus der Form | nur teilweise (Form hängt auch von Zielgeometrie ab) | ja, nur Software | klein | Experiment wert, Genauigkeit offen |
| **D. Amplituden-Abgleich mit tool-eigener Referenz** (Stratasys-Prinzip) | ja, aber braucht je Tool eine einmalige Referenz | ja, nur Software | klein | ersetzt den Z-Switch nicht, taugt als Drift-Prüfung |
| E. Multi-Frequenz, Phase, Rp, Impedanzebene (NDT-Literatur) | ja | **nein** — LDC1612 liefert nur eine Frequenz bei fester Resonanz | — | nicht übertragbar |
| F. Z-Switch behalten | ja (mechanisch) | vorhanden | 0 | Basislinie, funktioniert nachweislich |

## 2. Was andere machen

**Bambu Lab H2D.** Zwei Wirbelstromsensoren im Drucker, aber nur für **X/Y**: jede Düse
sucht das Signalmaximum über der Spule, die Differenz der Koordinaten ist der XY-Offset —
exakt unser Verfahren. Für **Z** tippt jede Düse **auf das Heizbett** („ähnlich wie beim
Homen"), die Differenz der Kontakt-Koordinaten ist der Z-Offset. Bambu benutzt die
Spule für Z ausdrücklich nicht. ([Bambu Wiki](https://wiki.bambulab.com/en/h2/maintenance/replace-nozzle-eddy-sensor),
[Wiki Kalibrierfehler](https://wiki.bambulab.com/en/h2/troubleshooting/nozzle-offset-calibration-failure))

**Stratasys, Patent US 12186990.** Düse wird über einer festen Wirbelstromspule in
einem Muster bewegt, Maximum = Mitte (XY). Für Z wird die Düse angehoben, bis der
Induktionswert einem **im Werk je Düsenspitze hinterlegten Referenzwert** entspricht.
Das ist Amplituden-Abgleich — und er funktioniert nur, weil die Referenz je Spitze
vorher gemessen wurde. Genau die Referenz fehlt uns; ohne sie ist der Amplitudenmodus
das, was wir beobachtet haben. ([Google Patents](https://patents.google.com/patent/US12186990B2/en))

**Beacon Contact, Cartographer Touch, eddy-ng Tap.** Sensor sitzt am Kopf, Düse fährt
aufs Bett. Erkannt wird nicht ein Frequenzwert, sondern dass die Bewegung aufhört:
„die Steigung der Kurve flacht ab, sobald die Düse steht". Beacon nennt 3 mm/s als
optimale Tippgeschwindigkeit, unter 30 µm Überschwingen, 10–20 g Kraft und Standard-
abweichungen unter 1 µm; Voraussetzung ist eine **starre Kopplung** zwischen Sensor und
Düse, damit der Kontakt die Sensorbewegung unterbricht. Cartographer schreibt dazu,
dass der Algorithmus genau deshalb egal findet, bei welcher Frequenz der Kontakt
passiert — robust gegen Temperatur und Sensordrift. eddy-ng macht dasselbe mit
Detrending plus Butterworth-Bandpass auf dem Frequenzstrom bei 250/500 Hz Datenrate,
Schwelle 250 (butter), Tippgeschwindigkeit 3 mm/s. ([Beacon](https://docs.beacon3d.com/contact/),
[Cartographer](https://docs.cartographer3d.com/cartographer-probe/classic-vs-survey-touch),
[eddy-ng Wiki](https://github.com/vvuk/eddy-ng/wiki/Tap), `eddy-ng/probe_eddy_ng/params.py`)

**Prusa XL.** Wägezelle im Kopf, Düse tippt aufs Bett; Z-Offsets der Tools über den
Kontakt. ([Prusa](https://help.prusa3d.com/article/loadcell-mk4-s-mk3-9-s-xl_401253))

**Klipper-Toolchanger-Szene.** Nudge (Kontaktschalter, XYZ), Sexbolt/Z-Switch
(unser Weg), E3D PZ Probe (Piezoscheibe, ausdrücklich auch für Tool-Offsets am
Toolchanger, σ 0,1 µm), StealthChanger dokumentiert Z ausschließlich über Kontakt-
Probes. ([Nudge](https://github.com/zruncho3d/nudge), [PZ Probe](https://e3d-online.com/blogs/news/introducing-pz-probe),
[StealthChanger](https://stealthchanger.com/calibration/), [klipper-toolchanger](https://github.com/viesturz/klipper-toolchanger/blob/main/tools_calibrate.md))

Befund: **Niemand bestimmt Düsenhöhen berührungslos aus dem Wirbelstrom-Pegel.** Wer
eine Bett-Spule hat (Bambu, Stratasys), nimmt sie für XY und löst Z über Kontakt oder
über eine tool-eigene Referenz.

## 3. Was die Messtechnik-Literatur anbietet — und warum es bei uns nicht greift

Der „Lift-off-Effekt" (Abstand und Zielmaterial vermischen sich im Signal) ist ein
Standardproblem der Wirbelstromprüfung. Bekannte Auswege:

- **Multi-Frequenz:** gemeinsame Lösung für Abstand und Leitfähigkeit aus Messungen
  bei mehreren Anregungsfrequenzen; Restfehler unter 6 % statt 70 %.
  ([Sensors 2026](https://doi.org/10.3390/s26020555))
- **Lift-off-invariante Induktivität:** bei einer bestimmten Arbeitsfrequenz ist die
  gemessene Induktivität nahezu abstandsunabhängig. ([NDT&E 2021](https://www.sciencedirect.com/science/article/abs/pii/S0963869521000578))
- **Phase / Impedanzebene / Rp:** Real- und Imaginärteil der Spulenimpedanz trennen
  Abstand von Material. ([Sensors 2021](https://doi.org/10.3390/s21020419), [PMC](https://www.ncbi.nlm.nih.gov/pmc/articles/PMC10934737/))

Alle drei brauchen entweder mehrere Anregungsfrequenzen oder die Messung des
Parallelwiderstands Rp. Der **LDC1612 misst nur die Resonanzfrequenz eines festen
LC-Kreises** ([TI](https://www.ti.com/product/LDC1612)); Rp gibt es erst bei LDC1101/LDC1041.
Nicht übertragbar ohne andere Elektronik.

Dazu kommt: TI weist darauf hin, dass Zielgröße, -form und -material nicht nur die
Amplitude skalieren, sondern die Kennlinie verändern (Reichweite ~ halber Spulen-
durchmesser, kleine Ziele verkürzen sie). ([TI SNOA957](https://www.ti.com/lit/pdf/snoa957))
Unsere Ziele sind verschieden lange Düsen, teils Messing, teils gehärteter Stahl,
mit Heizblock dahinter — die Kurvenform ist also nicht garantiert gleich.

## 4. Übertragbar auf unseren Aufbau

### A. Inverser Tap — Düse tippt auf die Spulenabdeckung

Das Beacon/Cartographer-Prinzip, um 180° gedreht: Spule fest, Düse fährt langsam
herunter und berührt eine harte, nichtmetallische Fläche unmittelbar über der Spule.
Solange die Düse sich nähert, steigt die Frequenz steil; sobald sie auf der Abdeckung
steht und der Rest des Drucker nachgibt, **flacht die Kurve ab**, obwohl Z weiterfährt.
Die Z-Position des Knicks ist die Kontakt-Höhe, unabhängig davon, wie stark das Tool
die Spule anspricht. Differenz zwischen zwei Tools = Differenz der Düsenlängen.

Das ist ein Z-Switch, dessen „Schalter" die Spule selbst ist. Vorteile: kein zweiter
Aufbau, dieselbe Halterung wie XY, Messung direkt an der Düsenspitze.

Voraussetzungen:
- **Harte Abdeckung** über der Spule (≤ 1 mm, Glas/Keramik/FR4), plan und auf der
  Halterung fest. Die Düse darf sie berühren; bei 3 mm/s und einigen 10 ms Latenz
  sind das einige 10 µm Überschwingen — eine Halterung mit etwas Nachgiebigkeit
  hilft, wie Beacon es für weiche Betten beschreibt.
- **Knick-Erkennung auf dem Host** aus dem gestreamten Frequenzsignal der Bett-
  Spule. Die Bett-Spule läuft mit Standard-Klipper-`ldc1612`-Firmware (siehe
  [eddy-ng gegen Klipper](eddy-ng-vs-klipper-ldc1612.md)), eddy-ngs MCU-seitiger
  Tap-Trigger steht dort nicht zur Verfügung. Klipper-mainline hat keinen Tap
  (vvuks PR #6785 ist ein Entwurf). Also: langsame Fahrt, Host liest den Strom,
  detrendet, erkennt den Knick, stoppt. Die Latenz begrenzt die Geschwindigkeit,
  nicht die Genauigkeit — die Kontakt-Höhe wird nachträglich aus dem Knick
  bestimmt, nicht aus dem Stopp-Punkt.
- **Sicherheitsnetz** über Klippers vorhandenen MCU-Frequenz-Trigger
  (`probe_eddy_current`-Homing): Schwelle deutlich hinter dem Kontaktwert des
  stärksten Tools, damit ein verpasster Knick nicht in die Halterung fährt.

Erwartung: Cartographer und eddy-ng erreichen damit ±5 µm auf dem Bett. Bei uns ist
der Kontaktpartner leichter (Halterung statt Bett), die Signale sind steiler
(Spalt 0,2–1 mm statt 2–3 mm), das spricht eher für als gegen die Erkennung.
Offen ist, ob eine 200-°C-Düse die Abdeckung verträgt — kalt tippen ist der Default
und für Längendifferenzen zulässig, solange alle Tools bei gleicher Temperatur
gemessen werden (Wärmedehnung des Hotends ~60 µm zwischen kalt und heiß, laut
Beacon).

### B. Metallmembran über der Spule (Touch-on-Metal)

TI baut aus dem LDC1612 Metall-Taster: eine dünne Metallfläche liegt 150 µm über der
Spule; drückt man darauf, verbiegt sie sich um Nanometer und die Frequenz springt.
Auflösbar sind < 200 nm Durchbiegung; 0,6 mm Aluminium über 10 mm Durchmesser
biegt sich bei 1 N um ~90 nm. ([TI SNOA951](https://www.ti.com/lit/an/snoa951/snoa951.pdf),
[TI SNOA961](https://www.ti.com/lit/an/snoa961a/snoa961a.pdf))

Übertragen: Membran über die Spule, Düse tippt auf die Membran. Die Spule sieht
**nur die Membran**, nie die Düse — deshalb völlig unabhängig vom Tool. Kontakt bei
Kräften weit unter dem, was ein Z-Switch braucht. Nachteil: mit Membran ist die
Spule für XY blind, also entweder abnehmbar oder eine zweite Position auf der
Halterung. Mehr Bastelaufwand als A, dafür die sauberste Physik.

### C. Kurvenform-Fit (nur Software)

Idee: je Tool eine Anfahrkurve Frequenz über Z aufnehmen (das kann
`NOZZLE_LOCATE GAPS=…` heute schon), dann eine gemeinsame Kurvenform mit freier
Amplitude und freiem Nullpunkt fitten. Bei einer reinen Potenzfunktion
f = A/(z−z₀)ⁿ hebt die logarithmische Ableitung f′/f die Amplitude auf und liefert
z₀ direkt. Real ist die Kennlinie zwischen Potenz- und Exponentialverlauf, und TI
sagt, dass die Form vom Ziel abhängt. Der Test ist billig: die vorhandenen Kurven
von T0–T3 normieren und übereinanderlegen. Decken sie sich nicht, ist die Methode
erledigt; decken sie sich, ist sie einen Versuch gegen den Z-Switch wert. Vorher
keine Erwartung unter 0,1 mm.

### D. Amplituden-Abgleich mit tool-eigener Referenz

Stratasys-Prinzip mit unserem Z-Switch als „Werkskalibrierung": einmal Z-Switch
messen, dann je Tool bei bekanntem Spalt die Amplitude als Referenz speichern.
Später misst der XY-Lauf je Tool die Höhe, bei der die **eigene** Referenzamplitude
erreicht ist; Abweichung vom gespeicherten Wert = Düse hat sich geändert
(getauscht, verschlissen, verschmutzt). Das ersetzt den Z-Switch nicht, macht aber
aus dem Amplitudenmodus eine ehrliche Drift-Prüfung statt einer falschen Messung.
Reine Software, kleine Erweiterung der bestehenden Ergebnisdatei.

## 5. Empfehlung

1. **Jetzt:** Z-Switch bleibt die Z-Quelle. Alle vier Tools mit
   `CALIBRATE_ALL_Z_OFFSETS` fahren; der Lauf mit nur T0/T2 hat die Daten von T1/T3
   gelöscht. Den Knopf „Z-Offsets aus der Sonde" im Amplitudenmodus sperren.
2. **Kleiner Versuch ohne Umbau:** Verfahren C mit den vorhandenen Höhenserien
   prüfen (Kurven normieren, vergleichen). Kostet einen Messlauf, kein Hardware.
3. **Wenn es ohne Z-Switch gehen soll:** Verfahren A bauen. Abdeckung auf die
   Halterung, Host-seitige Knick-Erkennung auf dem Standard-`ldc1612`-Strom,
   Sicherheits-Trigger im MCU. Das ist die Methode, die die gesamte Branche für
   Düsenhöhen benutzt, nur mit vertauschten Rollen von Sensor und Bett.
4. Verfahren D als Nebenprodukt von A oder C mitnehmen: Referenzamplitude je Tool
   speichern und beim nächsten XY-Lauf vergleichen.

## Quellen

- Bambu Lab Wiki: [Nozzle Eddy Sensor](https://wiki.bambulab.com/en/h2/maintenance/replace-nozzle-eddy-sensor), [Nozzle offset calibration failure](https://wiki.bambulab.com/en/h2/troubleshooting/nozzle-offset-calibration-failure)
- Stratasys: [US 12186990 B2 — Tip calibration in an additive manufacturing system](https://patents.google.com/patent/US12186990B2/en)
- Beacon: [Contact](https://docs.beacon3d.com/contact/), [Pressemitteilung Beacon Contact](https://beacon3d.com/press-release/)
- Cartographer: [Scan vs Touch](https://docs.cartographer3d.com/cartographer-probe/classic-vs-survey-touch), [Touch](https://docs.cartographer3d.com/cartographer-probe/features/touch)
- eddy-ng: [Wiki Tap](https://github.com/vvuk/eddy-ng/wiki/Tap), [Klipper PR #6785](https://github.com/Klipper3d/klipper/pull/6785), [Klipper Discourse: Tap detection with ldc1612](https://klipper.discourse.group/t/tap-detection-with-ldc1612-inductive-sensors/16234)
- Klipper: [Eddy Current Inductive probe](https://www.klipper3d.org/Eddy_Probe.html)
- Prusa: [Loadcell](https://help.prusa3d.com/article/loadcell-mk4-s-mk3-9-s-xl_401253)
- Toolchanger: [Nudge](https://github.com/zruncho3d/nudge), [E3D PZ Probe](https://e3d-online.com/blogs/news/introducing-pz-probe), [StealthChanger Calibration](https://stealthchanger.com/calibration/), [klipper-toolchanger tools_calibrate](https://github.com/viesturz/klipper-toolchanger/blob/main/tools_calibrate.md)
- TI: [LDC1612](https://www.ti.com/product/LDC1612), [SNOA957 LDC Target Design](https://www.ti.com/lit/pdf/snoa957), [SNOA951 Touch-on-Metal Buttons](https://www.ti.com/lit/an/snoa951/snoa951.pdf), [SNOA961 Inductive Touch System Design](https://www.ti.com/lit/an/snoa961a/snoa961a.pdf)
- Literatur Lift-off: [Multi-frequency liftoff reduction, Sensors 2026](https://doi.org/10.3390/s26020555), [Lift-off invariant inductance, NDT&E 2021](https://www.sciencedirect.com/science/article/abs/pii/S0963869521000578), [Coating thickness via lift-off insensitivity, Sensors 2021](https://doi.org/10.3390/s21020419), [Temperature compensation eddy position, PMC 2024](https://www.ncbi.nlm.nih.gov/pmc/articles/PMC10934737/), [Lift-off inversion, NDT.net](https://www.ndt.net/article/ndtnet/papers/Inversion_of_Lift-Off_Distance_and_Thickness_for_Non-Magnetic_Metal_Using_Eddy_Current_Testing.pdf)
