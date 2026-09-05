# `[htc_heater_fan]` — Hotend-Lüfter mit einstellbarer Drehzahl

Klippers `[heater_fan]` kennt genau eine Drehzahl (`fan_speed`) und kein
Kommando, sie zur Laufzeit zu ändern. Wer die Hotend-Lüfter leiser haben,
geparkte Tools anders behandeln oder bei warmem Gehäuse mehr Luft geben will,
kommt damit nicht weiter.

`htc_heater_fan` ist ein eigenes Klipper-Extra in `klippy/extras/`, wird von
`install.sh` wie alle anderen Module nach `~/klipper/klippy/extras/` verlinkt
und fasst keine Klipper-Datei an — es übersteht Klipper-Updates. Es verhält
sich wie `[heater_fan]` (an, sobald geheizt wird oder das Hotend noch über
`heater_temp` liegt; bei MCU-Shutdown volle Drehzahl; Kick-Start aus Klippers
Fan-Klasse), kann aber mehr.

## Was es bewusst nicht tut

Keine Kennlinie nach Hotend-Temperatur. Die Heizpatrone wird schon per PID
geregelt; ein zweiter Regler, der auf dieselbe Temperatur reagiert und ihre
Kühlung verändert, könnte mit dem ersten schwingen. Die Drehzahl hängt deshalb
nur an **diskreten Zuständen** und an der **Gehäusetemperatur**, die sich
langsam und unabhängig vom Hotend-PID ändert.

## Zustände

| Zustand | wann | Drehzahl aus |
|---|---|---|
| `off` | kein Sollwert und Hotend unter `heater_temp` | 0 |
| `active` | Sollwert gesetzt, Tool ist montiert | `fan_speed` |
| `parked` | Sollwert gesetzt, Tool ist nicht das aktive | `parked_speed` |
| `cooldown` | Sollwert 0, Hotend noch über `heater_temp` | `cooldown_speed` |

Welches Tool zum Lüfter gehört, findet das Modul über den Extruder: das
`[tool]`, dessen `extruder:` mit dem `heater:` des Lüfters übereinstimmt. Ohne
`[toolchanger]` (fremde Config, Einzelhotend) gilt immer `active`. Mit `tool:`
lässt sich die Zuordnung erzwingen.

Auf die Zustandsdrehzahl setzt die Gehäuse-Anhebung auf, danach greift
`min_speed` als Untergrenze. `min_speed` gilt nur, solange der Lüfter
überhaupt läuft — ein kaltes Hotend schaltet er nicht ein.

## Config

```ini
[htc_heater_fan T1_hotend_fan]
pin: EBBT1:PA0
heater: extruder1
heater_temp: 50.0
fan_speed: 0.8            # aktives Tool
parked_speed: 0.6         # geparktes Tool (Default: fan_speed)
cooldown_speed: 0.8       # Heizung aus, noch warm (Default: fan_speed)
min_speed: 0.5            # Untergrenze, solange der Luefter laeuft (Default 0)
kick_start_time: 0.5      # beim Einschalten so lange volle Drehzahl
#chamber_sensor: temperature_sensor chamber
#chamber_temp_start: 40   # ab hier anheben (Default 40)
#chamber_temp_full: 60    # hier ist chamber_max_speed erreicht (Default 60)
#chamber_max_speed: 1.0   # Drehzahl bei chamber_temp_full (Default 1.0)
```

Alle Optionen von Klippers Fan-Klasse gelten weiter: `max_power`, `off_below`,
`cycle_time`, `hardware_pwm`, `shutdown_speed` (Default 1.0 wie bei
`heater_fan`), `enable_pin`, `tachometer_pin` usw.

**Kick-Start.** Klippers Fan-Klasse fährt beim Einschalten (und bei einem
Sprung um mehr als 50 %) für `kick_start_time` Sekunden volle Drehzahl und
regelt dann auf den Sollwert herunter. Zweipolige DC-Lüfter laufen unter etwa
30 % nicht sicher an — deshalb 0,5 s statt Klippers Default 0,1 s, und
`min_speed` nicht zu tief wählen.

**Gehäuse-Anhebung.** Zwischen `chamber_temp_start` und `chamber_temp_full`
steigt die Drehzahl linear von der Zustandsdrehzahl auf `chamber_max_speed`.
Sie senkt nie unter die Zustandsdrehzahl, und ein Sensorausfall bedeutet
„keine Anhebung", nicht „Lüfter aus". Damit sie nicht jede Sekunde nachstellt,
werden Änderungen unter 2 % verschluckt; Zustandswechsel und Ein/Aus gehen
immer sofort raus.

**PWM.** Vierpolige PWM-Lüfter brauchen `cycle_time: 0.00004` (25 kHz) und
möglichst `hardware_pwm: True`, sonst pfeifen sie. Zweipolige Lüfter laufen mit
Klippers Default (10 ms).

## Zur Laufzeit

```
SET_HEATER_FAN FAN=T1_hotend_fan SPEED=0.7        # fest 70 %, in jedem Zustand
SET_HEATER_FAN FAN=T1_hotend_fan RESET=1          # zurueck auf die Zustaende
SET_HEATER_FAN FAN=T1_hotend_fan PARKED_SPEED=0.5 # Zustandsdrehzahl aendern
SET_HEATER_FAN FAN=T1_hotend_fan FAN_SPEED=0.9 COOLDOWN_SPEED=1 MIN_SPEED=0.4
```

`SPEED=` ersetzt nur die Zustandsdrehzahl; Gehäuse-Anhebung und `min_speed`
bleiben wirksam, und ein kaltes Hotend bleibt aus. Änderungen gelten bis zum
nächsten Klipper-Neustart, dauerhaft gehört der Wert in die Config.

Status (`printer["htc_heater_fan T1_hotend_fan"]`): `speed`, `rpm`, `state`,
`target_speed`, `override`, `fan_speed`, `parked_speed`, `cooldown_speed`,
`chamber_temp`, `tool`.

## Anzeige

Der Mainsail-Fork im Repo listet `htc_heater_fan` neben `heater_fan` in der
Lüfteransicht (nicht steuerbar, wie `heater_fan`). Ein unveränderter Mainsail
oder KlipperScreen kennt den Sektionstyp nicht und zeigt den Lüfter nicht an —
er läuft trotzdem.

## Umstellung

`[heater_fan Tn_hotend_fan]` durch `[htc_heater_fan Tn_hotend_fan]` ersetzen,
die Drehzahlen eintragen, `RESTART` reicht nicht: das Modul ist eine neue
Datei, Klipper lädt sie erst nach `sudo systemctl restart klipper`.

Die Startwerte in `configs/250` und `configs/350` (aktiv 80 %, geparkt 60 %,
Untergrenze 50 %) sind ein Ausgangspunkt, keine Messung. Ob 60 % am geparkten
Tool gegen Heat-Creep reicht, hängt an Hotend und Lüfter — nach dem ersten
längeren Druck die Extruder-Temperaturen im Leerlauf und das Anlaufen der
Lüfter prüfen.

## Tests

`tests/check_htc_heater_fan.py` prüft die Entscheidungslogik ohne Klipper,
`tests/check_klipper_api.py` sichert die genutzten Klipper-Interna
(`fan.Fan`, `heater.get_temp`, Tool-Zuordnung über `extruder_name`).
