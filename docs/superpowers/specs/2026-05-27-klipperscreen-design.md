# KlipperScreen Design — HappyToolchanger

## Übersicht

Custom KlipperScreen-Konfiguration für beide HappyToolchanger-Drucker (350er: 6 Tools, 250er: 4 Tools) mit vollständiger Toolchanger-Integration, Spoolman-Anbindung, und Browser-Spiegelung via VNC/noVNC. Deployment automatisiert über `install.sh`.

## Drucker

| | 350er | 250er |
|---|---|---|
| IP | 192.168.178.114 | 192.168.178.60 |
| Tools | T0-T5 (6) | T0-T3 (4) |
| Screen | 800x480 Touchscreen | 800x480 Touchscreen |
| Spoolman | 192.168.178.54:7912 | 192.168.178.54:7912 |

## 1. Hauptmenü (Hybrid-Layout, 2×4 Grid)

```
┌──────────┬──────────┬──────────┬──────────┐
│  Tools   │ Drucken  │ Preheat  │ Extrude  │
│ T0-T5+   │ Dateien  │ Material │  Panel   │
│ Filament │ + Start  │ Presets  │(aktiver) │
├──────────┼──────────┼──────────┼──────────┤
│ Wartung  │ Spoolman │  System  │          │
│ Clean +  │  Spulen  │ Move +   │          │
│ Kalibr.  │verwalten │ Settings │          │
└──────────┴──────────┴──────────┴──────────┘
```

- **Tools** — Custom Untermenü (s. Abschnitt 2)
- **Drucken** — Built-in `gcodes` Panel
- **Preheat** — Built-in `preheat` Panel mit Custom-Presets (s. Abschnitt 3)
- **Extrude** — Built-in `extrude` Panel (arbeitet auf aktivem Extruder)
- **Wartung** — Custom Untermenü (s. Abschnitt 4)
- **Spoolman** — Built-in `spoolman` Panel
- **System** — Custom Untermenü (s. Abschnitt 5)

## 2. Tools-Menü (pro Tool)

### Ebene 1: Tool-Auswahl

Grid mit T0-T5 (350er) bzw. T0-T3 (250er). Jeder Button zeigt den Tool-Namen.

### Ebene 2: Aktionen pro Tool

Jedes Tool hat folgendes Untermenü:

| Button | Makro/Aktion | Beschreibung |
|---|---|---|
| Aktivieren | `T{n}` | Volles Dock/Undock über Toolchanger |
| Filament laden | `HTC_LOAD_FILAMENT T={n}` | ACTIVATE_EXTRUDER + LOAD_FILAMENT |
| Filament entladen | `HTC_UNLOAD_FILAMENT T={n}` | ACTIVATE_EXTRUDER + UNLOAD_FILAMENT |
| Extrude 5mm | `HTC_EXTRUDE T={n} E=5` | ACTIVATE_EXTRUDER + Extrude |
| Extrude 10mm | `HTC_EXTRUDE T={n} E=10` | ACTIVATE_EXTRUDER + Extrude |
| Extrude 25mm | `HTC_EXTRUDE T={n} E=25` | ACTIVATE_EXTRUDER + Extrude |
| Retract 5mm | `HTC_RETRACT T={n} E=5` | ACTIVATE_EXTRUDER + Retract |
| Retract 10mm | `HTC_RETRACT T={n} E=10` | ACTIVATE_EXTRUDER + Retract |
| Retract 25mm | `HTC_RETRACT T={n} E=25` | ACTIVATE_EXTRUDER + Retract |

**Wichtig:** Die Extrude/Retract/Load/Unload-Aktionen lösen KEIN automatisches Dock/Undock aus. Sie nutzen `ACTIVATE_EXTRUDER` um den Extruder umzuschalten (für den Fall, dass der User das Tool manuell gewechselt hat), und führen dann die Aktion aus. Der User ist verantwortlich, dass das richtige Tool physisch montiert ist.

**"Aktivieren"** ist der einzige Button der den vollen Toolchange-Zyklus (Dock/Undock) auslöst.

## 3. Preheat-Presets

Alle Custom-Presets ersetzen die KlipperScreen-Defaults:

| Preset | Extruder | Bed |
|---|---|---|
| PLA | 210°C | 60°C |
| PETG | 240°C | 80°C |
| ABS/ASA | 250°C | 110°C |
| TPU | 230°C | 50°C |
| Nylon/PA | 260°C | 100°C |
| Cooldown | — | — |

Cooldown ruft `M107` auf (Part-Fan aus). `TURN_OFF_ALL_HEATERS` wird von KlipperScreen automatisch hinzugefügt.

## 4. Wartung-Menü

| Button | Makro | Confirm-Dialog |
|---|---|---|
| Clean Nozzle | `CLEAN_NOZZLE` | Nein |
| Clean All Nozzles | `CLEAN_ALL_NOZZLES` | Ja |
| Toolchange Demo | `TOOLCHANGE_DEMO` | Ja |
| Nozzle-Kalibrierung | `INITIALISIERUNG_NOZZLEKALIBRIERUNG` | Ja |
| QGL Ergebnis | `SHOW_QGL_RESULT` | Nein |
| Tool Offsets speichern | `WRITE_TOOL_OFFSETS` | Ja |
| Unload One Filament | `UNLOAD_ONE_FILAMENT` | Nein |
| Unload All Filament | `UNLOAD_ALL_FILAMENT` | Ja |
| Z-Switch Kalibrierung | `CALIBRATE_Z_SWITCH` | Ja, nur 250er |
| Emergency Home Z | `EMERGENCY_HOME_Z` | Ja, nur 250er |

Die 250er-only Makros werden über `enable`-Conditions gesteuert:
```
enable: {{ 'CALIBRATE_Z_SWITCH' in printer.available_commands }}
```

## 5. System-Menü

Standard KlipperScreen-Panels als Untermenü:

| Button | Panel |
|---|---|
| Homing (G28) | method: `printer.gcode.script` params: `{"script":"G28"}` |
| Bewegung | `move` |
| Temperatur | `temperature` |
| Bed Mesh | `bed_mesh` |
| Z-Kalibrierung | `zcalibrate` |
| Kamera | `camera` |
| Konsole | `console` |
| Netzwerk | `network` |
| Einstellungen | `settings` |

## 6. Titlebar

```ini
[printer HappyToolchanger350]
titlebar_items: heater_bed, MCU, Pi, spool
titlebar_name_type: full
spool_low_limit: 20
```

Zeigt: Aktives Tool + Temp, Bed-Temp, MCU-Temp, Pi-Temp, aktive Spule (Material + Restmenge).
Spule wird rot unter 20g.

## 7. Versteckte Makros

Folgende Makros werden NICHT im Macros-Panel angezeigt (bereits durch `_` Prefix oder `rename_existing` versteckt):

- `_TAP_PROBE_ACTIVATE`, `_TAP_PROBE_DEACTIVATE`
- `_HEAT_ACTIVE`, `_WAIT_ACTIVE`, `_COOL_ACTIVE`
- `_AFTER_TOOL_PICKUP`, `_BEFORE_TOOL_DROPOFF`
- `_TOOLCHANGER_TOOL_BEFORE_CHANGE`, `_TOOLCHANGER_TOOL_AFTER_CHANGE`
- `_CALIBRATE_Z_SWITCH_STEP2`

Nicht im Menü (nur aus Slicer/Macros aufgerufen):
- `PRINT_START`, `PRINT_END`, `PRIME_LINE`, `TOOL_ACTIVATE`, `CHANGE_NOZZLE`

## 8. Neue Klipper-Makros

Vier neue Makros in `tobi-macros.cfg`:

```ini
[gcode_macro HTC_EXTRUDE]
description: Extrudiert für ein bestimmtes Tool (ohne Dock)
gcode:
    {% set tool = params.T|default(0)|int %}
    {% set length = params.E|default(10)|float %}
    {% set speed = params.F|default(300)|int %}
    {% if tool == 0 %}
      ACTIVATE_EXTRUDER EXTRUDER=extruder
    {% else %}
      ACTIVATE_EXTRUDER EXTRUDER=extruder{tool}
    {% endif %}
    M83
    G1 E{length} F{speed}

[gcode_macro HTC_RETRACT]
description: Retract für ein bestimmtes Tool (ohne Dock)
gcode:
    {% set tool = params.T|default(0)|int %}
    {% set length = params.E|default(10)|float %}
    {% set speed = params.F|default(300)|int %}
    {% if tool == 0 %}
      ACTIVATE_EXTRUDER EXTRUDER=extruder
    {% else %}
      ACTIVATE_EXTRUDER EXTRUDER=extruder{tool}
    {% endif %}
    M83
    G1 E-{length} F{speed}

[gcode_macro HTC_LOAD_FILAMENT]
description: Filament laden für ein bestimmtes Tool (ohne Dock)
gcode:
    {% set tool = params.T|default(0)|int %}
    {% if tool == 0 %}
      ACTIVATE_EXTRUDER EXTRUDER=extruder
    {% else %}
      ACTIVATE_EXTRUDER EXTRUDER=extruder{tool}
    {% endif %}
    LOAD_FILAMENT

[gcode_macro HTC_UNLOAD_FILAMENT]
description: Filament entladen für ein bestimmtes Tool (ohne Dock)
gcode:
    {% set tool = params.T|default(0)|int %}
    {% if tool == 0 %}
      ACTIVATE_EXTRUDER EXTRUDER=extruder
    {% else %}
      ACTIVATE_EXTRUDER EXTRUDER=extruder{tool}
    {% endif %}
    UNLOAD_FILAMENT
```

## 9. Browser-Spiegelung (VNC + noVNC)

### Architektur

```
KlipperScreen Instanz 1 (Display :0, physischer Touchscreen)
        └── Touchscreen 800x480

KlipperScreen Instanz 2 (Display :10, VNC)
        └── TigerVNC Server (Port 5900)
                └── noVNC/websockify (Port 8080)
                        └── Browser: http://<IP>:8080
```

Zwei unabhängige KlipperScreen-Instanzen mit derselben Config, beide verbunden zum selben Moonraker/Klipper. Zeigen denselben Drucker-Status, navigieren aber unabhängig (kein Pixel-Mirror). Touch am physischen Screen beeinflusst den Browser nicht und umgekehrt.

### Komponenten

1. **TigerVNC** (`tigervnc-standalone-server`)
   - Startet virtuellen X-Server auf Display :10
   - Zweite KlipperScreen-Instanz läuft auf :10
   - Port 5900, keine Auth (Trusted Network)
   - Auflösung: 800x480 (identisch zum physischen Screen)

2. **noVNC** (Web-basierter VNC-Client)
   - Statische Web-App, serviert über Python oder websockify
   - Port 8080, erreichbar unter `http://<IP>:8080`
   - Unterstützt Touch-Events im Browser

3. **Systemd-Services**
   - `klipperscreen-vnc.service` — TigerVNC + KlipperScreen auf :10
   - `novnc.service` — noVNC Webserver auf Port 8080

### KlipperScreen Launch-Script

```bash
#!/bin/bash
# /home/biqu/klipperscreen-vnc.sh
Xtigervnc -rfbport 5900 -geometry 800x480 -noreset -AlwaysShared -SecurityTypes none :10 &
sleep 2
DISPLAY=:10 /home/biqu/.KlipperScreen-env/bin/python /home/biqu/KlipperScreen/screen.py &
wait
```

### Moonraker-Integration

Services `klipperscreen-vnc` und `novnc` werden in `moonraker.asvc` eingetragen, damit Moonraker sie verwalten kann (Restart via UI). Kein Update-Manager-Eintrag nötig — die Services nutzen das bereits installierte KlipperScreen und System-Pakete.

## 10. Dateistruktur im Repo

```
configs/
├── 250/
│   └── KlipperScreen.conf        # 4 Tools (T0-T3), 250er-spezifische Makros
├── 350/
│   └── KlipperScreen.conf        # 6 Tools (T0-T5)
├── shared/
│   └── klipperscreen-vnc.sh      # VNC Launch-Script (beide Drucker)
```

### Änderungen an install.sh

Neuer Abschnitt (nach Schritt 8 "Configuring Moonraker"):

```
--- 10. Configuring KlipperScreen ---
  Deployed KlipperScreen.conf
--- 11. Setting up VNC + noVNC ---
  Installed tigervnc-standalone-server noVNC
  Deployed klipperscreen-vnc.sh
  Created klipperscreen-vnc.service
  Created novnc.service
  Started VNC on port 5900, noVNC on port 8080
```

### Änderungen an tobi-macros.cfg

Vier neue Makros hinzufügen (s. Abschnitt 8):
- `HTC_EXTRUDE`
- `HTC_RETRACT`
- `HTC_LOAD_FILAMENT`
- `HTC_UNLOAD_FILAMENT`

## 11. Unterschiede 250er vs 350er

| Aspekt | 350er | 250er |
|---|---|---|
| KlipperScreen.conf | T0-T5 (6 Tool-Einträge) | T0-T3 (4 Tool-Einträge) |
| Wartung-Menü | Standard | + Z-Switch Kalibr. + Emergency Home Z |
| Printer-Name | HappyToolchanger350 | HappyToolchanger250 |
| Makros | tobi-macros.cfg (25 + 4 neue) | tobi-macros.cfg (27 + 4 neue) |
| VNC/noVNC | Identisch | Identisch |
