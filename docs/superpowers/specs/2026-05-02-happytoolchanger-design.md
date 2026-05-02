# HappyToolchanger - Design Spec

## Zusammenfassung

Eigener Klipper-Toolchanger-Fork basierend auf dem aktuellen Stand des Voron 250er Druckers. Monorepo mit Python-Modulen, Eddy-NG Probe, Offset-Webapp, Mainsail-Fork (HTC Panel), Drucker-Configs und Moonraker-Update-Manager-Support.

## Ausgangslage

- Zwei Voron 2.4 Stealthchanger (250mm/4 Tools, 350mm/6 Tools)
- Basis: jwellman80/klipper-toolchanger-easy
- Eigene Erweiterungen: z_probe Routing (Eddy/Tap), offset.py Kalibrierung, tool_z_calibrate.py
- 250er ist aktuell (Commit 9ea0564), 350er veraltet (Commit dd502ae)
- offset.py existiert als separates Repo (daTobi1/Offset) mit Flask-Webapp
- eddy-ng existiert als separates Repo (daTobi1/neweddy) mit lokalen Patches auf dem 250er
- Mainsail-Fork (daTobi1/mainsail) mit HTC Panel auf Branch htc-panel

## Repo-Struktur

```
HappyToolchanger/
├── README.md
├── install.sh
├── uninstall.sh
├── moonraker.conf.example
├── .gitignore
│
├── klippy/extras/                    # Klipper Python-Module
│   ├── toolchanger.py                # Modifiziert: Probe-Umschaltung bei Toolwechsel
│   ├── tool_probe.py                 # Modifiziert: z_probe Config-Parameter
│   ├── tool_probe_endstop.py         # Modifiziert: z_probe Routing, SET_ACTIVE_Z_PROBE
│   ├── tool.py                       # KTC Easy Original
│   ├── tools_calibrate.py            # KTC Easy Original
│   ├── tool_z_calibrate.py           # Z-Switch Kalibrierung
│   ├── offset.py                     # Offset-Kalibrierung (ex daTobi1/Offset)
│   ├── bed_thermal_adjust.py         # KTC Easy Original
│   ├── manual_rail.py                # KTC Easy Original
│   ├── multi_fan.py                  # KTC Easy Original
│   └── rounded_path.py               # KTC Easy Original
│
├── eddy-ng/                          # Eddy-NG Probe (ex daTobi1/neweddy)
│   ├── probe_eddy_ng.py              # Monolith-Loader (gepatcht)
│   ├── probe_eddy_ng/                # Paket mit Modulen
│   │   ├── endstop.py                # Gepatcht: z_probe Kompatibilitaet
│   │   ├── probe.py                  # Gepatcht: ProbeResult Fix
│   │   ├── alpha_beta_filter.py
│   │   ├── backlash.py
│   │   ├── bed_mesh_helper.py
│   │   ├── frequency_map.py
│   │   ├── mesh_paths.py
│   │   ├── params.py
│   │   ├── sampler.py
│   │   ├── scanning.py
│   │   ├── streaming.py
│   │   └── temperature_compensation.py
│   ├── ldc1612_ng.py
│   ├── calibrate_macros.cfg
│   ├── calibrate_macros_cartographer.cfg
│   ├── firmware/                     # MCU Firmware-Dateien
│   ├── flash.sh
│   ├── install_eddy_ng.py
│   └── klipper.patch
│
├── webapp/                           # Offset Calibration Webapp (ex daTobi1/Offset)
│   ├── app.py
│   ├── index.html
│   ├── css/
│   └── js/
│
├── mainsail/                         # Mainsail Fork mit HTC Panel (ex daTobi1/mainsail)
│   ├── src/
│   │   └── components/panels/
│   │       ├── HtcPanel.vue
│   │       └── Htc/                  # HTC Subcomponents
│   │           ├── HtcMixin.ts
│   │           ├── HtcGateOverview.vue
│   │           ├── HtcGateRow.vue
│   │           ├── HtcTtgMap.vue
│   │           ├── HtcStatusBar.vue
│   │           ├── HtcEndlessSpool.vue
│   │           ├── HtcSensorStatus.vue
│   │           ├── HtcStatistics.vue
│   │           ├── HtcSpoolDialog.vue
│   │           ├── HtcEditGateDialog.vue
│   │           ├── HtcEditGroupsDialog.vue
│   │           └── HtcEditTtgDialog.vue
│   ├── dist/                         # Gebaute Dateien fuer Deployment
│   ├── package.json
│   ├── vite.config.ts
│   └── ...                           # Restlicher Mainsail-Quellcode
│
├── configs/
│   ├── 250/                          # Voron 250 (4 Tools, CB1)
│   │   ├── toolchanger/
│   │   │   ├── toolchanger-config.cfg
│   │   │   └── tools/
│   │   │       ├── T0.cfg
│   │   │       ├── T1.cfg
│   │   │       ├── T2.cfg
│   │   │       └── T3.cfg
│   │   └── macros/
│   │       └── tobi-macros.cfg
│   └── 350/                          # Voron 350 (6 Tools)
│       ├── toolchanger/
│       │   ├── toolchanger-config.cfg
│       │   ├── probe/
│       │   │   └── eddy_probe.cfg
│       │   └── tools/
│       │       ├── T0.cfg - T5.cfg
│       └── macros/
│           └── tobi-macros.cfg
│
└── examples/
    └── z_probe_example.cfg           # Referenz-Config fuer z_probe Setup
```

## Quelle der Wahrheit

Der **250er Drucker** (192.168.178.60) liefert den aktuellen Stand fuer alle Python-Module. Dateien werden per SSH direkt vom Drucker geholt:

- `~/klipper-toolchanger-easy/klipper/extras/*.py` (KTC Easy Module)
- `~/klipper/klippy/extras/tool_z_calibrate.py` (separate Datei)
- `~/offset/klippy/extras/offset.py` (Offset Klipper-Modul)
- `~/offset/app.py`, `~/offset/css/`, `~/offset/js/`, `~/offset/index.html` (Webapp)
- `~/eddy-ng/` (komplettes Eddy-NG mit gepatchten Dateien)
- `~/printer_data/config/toolchanger/` (250er Configs)

Fuer den 350er werden die Configs von 192.168.178.113 geholt:
- `~/printer_data/config/toolchanger/` (350er Configs)
- `~/printer_data/config/macros/tobi-macros.cfg`

## install.sh

Aufgaben:
1. Symlinks aller `klippy/extras/*.py` nach `~/klipper/klippy/extras/`
2. Eddy-NG installieren: Symlinks/Kopien fuer probe_eddy_ng, ldc1612_ng + klipper.patch anwenden
3. Mainsail deployen: `mainsail/dist/` nach `~/mainsail/` kopieren
4. Optional: Drucker-Config auswaehlen (`--printer 250` oder `--printer 350`)
   - Kopiert Config-Dateien nach `~/printer_data/config/toolchanger/`
5. Webapp installieren (venv, Flask, systemd service) - wiederverwendet Logik aus Offset install.sh
6. Moonraker Update Manager Config anbieten
7. Klipper neustarten
8. Idempotent (mehrfach ausfuehrbar)

## uninstall.sh

- Klipper-Extras Symlinks entfernen (toolchanger + eddy-ng)
- Mainsail dist zurueck auf Original ersetzen (oder Warnung ausgeben)
- Webapp-Service stoppen und entfernen
- Moonraker-Eintraege entfernen
- Klipper/Moonraker neustarten

## Moonraker Update Manager

```ini
[update_manager HappyToolchanger]
type: git_repo
path: ~/HappyToolchanger
origin: https://github.com/daTobi1/HappyToolchanger.git
primary_branch: main
is_system_service: True
managed_services: HappyToolchanger klipper
install_script: install.sh
```

## 350er Aktualisierung

Nach dem initialen Repo-Setup wird das Repo auf dem 350er deployed:
1. Altes KTC Easy entfernen (Symlinks loesen)
2. `git clone` + `./install.sh --printer 350`
3. Alte direkte Dateien (tool_probe_endstop.py, tool_probe.py) werden durch Symlinks ersetzt
4. offset.py Symlink wird auf neuen Pfad umgestellt
5. Klipper + Moonraker Neustart
6. Funktionstest (Homing, Tool-Wechsel, Probe)

## Mainsail-Fork

- Quelle: `D:/Claude Code/mainsail-htc/` (Branch `htc-panel`)
- GitHub: `daTobi1/mainsail`
- Komplett ins Repo unter `mainsail/` — Source + gebaute dist-Dateien
- HTC Panel: Dashboard-Panel fuer HappyHare/Toolchanger-Status
  - Gate-Uebersicht, TTG-Map, Endlos-Spool, Sensor-Status, Statistiken
  - Spoolman-Integration mit Pending-Buffer
  - Status-Bar mit farbigen Tool-Dots
- Mainsail wird auf dem Drucker NICHT aus Source gebaut — nur `dist/` wird deployed
- Fuer Entwicklung: lokal `npm run build` in `mainsail/`, dann commit + push, auf Drucker `git pull` + install.sh

## Migration Schritte

1. GitHub Repo `daTobi1/HappyToolchanger` erstellen
2. Alle Dateien vom 250er per SSH einsammeln (KTC Easy, Eddy-NG, Offset, tool_z_calibrate)
3. Webapp-Dateien aus Offset-Repo uebernehmen
4. Mainsail-Fork (htc-panel Branch) einsammeln inkl. dist/
5. Configs von beiden Druckern einsammeln
6. install.sh / uninstall.sh schreiben
7. README.md mit Setup-Anleitung
8. Initialer Commit + Push
9. Auf 350er deployen und testen
10. Alte Repos archivieren: `daTobi1/Offset`, `daTobi1/neweddy`, `daTobi1/mainsail`
