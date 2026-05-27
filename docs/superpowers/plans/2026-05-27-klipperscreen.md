# KlipperScreen HappyToolchanger Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Custom KlipperScreen configs with toolchanger menus, Spoolman integration, preheat presets, and noVNC browser access for both printers (350er + 250er).

**Architecture:** Two printer-specific `KlipperScreen.conf` files in `configs/250/` and `configs/350/`. Four new Klipper macros in each printer's `tobi-macros.cfg`. A shared VNC launch script in `configs/shared/`. The `install.sh` gets two new sections for deploying KlipperScreen config and setting up VNC+noVNC. Both printers run a second KlipperScreen instance on a virtual X display, accessible via noVNC in the browser.

**Tech Stack:** KlipperScreen (INI config), Klipper macros (Jinja2 gcode), TigerVNC, noVNC/websockify, systemd, bash

---

### Task 1: Create KlipperScreen.conf for 350er

**Files:**
- Create: `configs/350/KlipperScreen.conf`

- [ ] **Step 1: Create the 350er KlipperScreen config**

```ini
# HappyToolchanger 350 — KlipperScreen Configuration

[main]
use_default_menu: False

# --- Printer ---
[printer HappyToolchanger350]
titlebar_items: heater_bed, MCU, Pi, spool
titlebar_name_type: full
spool_low_limit: 20
extrude_distances: 5, 10, 25, 50
extrude_speeds: 1, 2, 5, 25
move_distances: 0.1, 0.5, 1, 5, 10, 25, 50

# --- Preheat Presets ---
[preheat PLA]
extruder: 210
heater_bed: 60

[preheat PETG]
extruder: 240
heater_bed: 80

[preheat ABS/ASA]
extruder: 250
heater_bed: 110

[preheat TPU]
extruder: 230
heater_bed: 50

[preheat Nylon/PA]
extruder: 260
heater_bed: 100

[preheat cooldown]
gcode: M107

# =====================================================================
# HAUPTMENÜ — 2×4 Grid: Tools, Drucken, Preheat, Extrude,
#                        Wartung, Spoolman, System
# =====================================================================

[menu __main tools]
name: Tools
icon: extruder

[menu __main print]
name: Drucken
icon: print
panel: gcodes

[menu __main preheat]
name: Preheat
icon: heat-up
panel: preheat

[menu __main extrude]
name: Extrude
icon: filament
panel: extrude

[menu __main wartung]
name: Wartung
icon: settings

[menu __main spoolman]
name: Spoolman
icon: filament
panel: spoolman

[menu __main system]
name: System
icon: info

# =====================================================================
# TOOLS MENÜ — T0-T5 mit Untermenüs
# =====================================================================

# --- T0 ---
[menu __main tools t0]
name: T0
icon: extruder

[menu __main tools t0 activate]
name: Aktivieren (Dock)
icon: home
method: printer.gcode.script
params: {"script":"T0"}
confirm: Tool T0 aktivieren (Dock/Undock)?

[menu __main tools t0 load]
name: Filament laden
icon: arrow-down
method: printer.gcode.script
params: {"script":"HTC_LOAD_FILAMENT T=0"}

[menu __main tools t0 unload]
name: Filament entladen
icon: arrow-up
method: printer.gcode.script
params: {"script":"HTC_UNLOAD_FILAMENT T=0"}

[menu __main tools t0 extrude5]
name: Extrude 5mm
icon: filament
method: printer.gcode.script
params: {"script":"HTC_EXTRUDE T=0 E=5"}

[menu __main tools t0 extrude10]
name: Extrude 10mm
icon: filament
method: printer.gcode.script
params: {"script":"HTC_EXTRUDE T=0 E=10"}

[menu __main tools t0 extrude25]
name: Extrude 25mm
icon: filament
method: printer.gcode.script
params: {"script":"HTC_EXTRUDE T=0 E=25"}

[menu __main tools t0 retract5]
name: Retract 5mm
icon: filament
method: printer.gcode.script
params: {"script":"HTC_RETRACT T=0 E=5"}

[menu __main tools t0 retract10]
name: Retract 10mm
icon: filament
method: printer.gcode.script
params: {"script":"HTC_RETRACT T=0 E=10"}

[menu __main tools t0 retract25]
name: Retract 25mm
icon: filament
method: printer.gcode.script
params: {"script":"HTC_RETRACT T=0 E=25"}

# --- T1 ---
[menu __main tools t1]
name: T1
icon: extruder

[menu __main tools t1 activate]
name: Aktivieren (Dock)
icon: home
method: printer.gcode.script
params: {"script":"T1"}
confirm: Tool T1 aktivieren (Dock/Undock)?

[menu __main tools t1 load]
name: Filament laden
icon: arrow-down
method: printer.gcode.script
params: {"script":"HTC_LOAD_FILAMENT T=1"}

[menu __main tools t1 unload]
name: Filament entladen
icon: arrow-up
method: printer.gcode.script
params: {"script":"HTC_UNLOAD_FILAMENT T=1"}

[menu __main tools t1 extrude5]
name: Extrude 5mm
icon: filament
method: printer.gcode.script
params: {"script":"HTC_EXTRUDE T=1 E=5"}

[menu __main tools t1 extrude10]
name: Extrude 10mm
icon: filament
method: printer.gcode.script
params: {"script":"HTC_EXTRUDE T=1 E=10"}

[menu __main tools t1 extrude25]
name: Extrude 25mm
icon: filament
method: printer.gcode.script
params: {"script":"HTC_EXTRUDE T=1 E=25"}

[menu __main tools t1 retract5]
name: Retract 5mm
icon: filament
method: printer.gcode.script
params: {"script":"HTC_RETRACT T=1 E=5"}

[menu __main tools t1 retract10]
name: Retract 10mm
icon: filament
method: printer.gcode.script
params: {"script":"HTC_RETRACT T=1 E=10"}

[menu __main tools t1 retract25]
name: Retract 25mm
icon: filament
method: printer.gcode.script
params: {"script":"HTC_RETRACT T=1 E=25"}

# --- T2 ---
[menu __main tools t2]
name: T2
icon: extruder

[menu __main tools t2 activate]
name: Aktivieren (Dock)
icon: home
method: printer.gcode.script
params: {"script":"T2"}
confirm: Tool T2 aktivieren (Dock/Undock)?

[menu __main tools t2 load]
name: Filament laden
icon: arrow-down
method: printer.gcode.script
params: {"script":"HTC_LOAD_FILAMENT T=2"}

[menu __main tools t2 unload]
name: Filament entladen
icon: arrow-up
method: printer.gcode.script
params: {"script":"HTC_UNLOAD_FILAMENT T=2"}

[menu __main tools t2 extrude5]
name: Extrude 5mm
icon: filament
method: printer.gcode.script
params: {"script":"HTC_EXTRUDE T=2 E=5"}

[menu __main tools t2 extrude10]
name: Extrude 10mm
icon: filament
method: printer.gcode.script
params: {"script":"HTC_EXTRUDE T=2 E=10"}

[menu __main tools t2 extrude25]
name: Extrude 25mm
icon: filament
method: printer.gcode.script
params: {"script":"HTC_EXTRUDE T=2 E=25"}

[menu __main tools t2 retract5]
name: Retract 5mm
icon: filament
method: printer.gcode.script
params: {"script":"HTC_RETRACT T=2 E=5"}

[menu __main tools t2 retract10]
name: Retract 10mm
icon: filament
method: printer.gcode.script
params: {"script":"HTC_RETRACT T=2 E=10"}

[menu __main tools t2 retract25]
name: Retract 25mm
icon: filament
method: printer.gcode.script
params: {"script":"HTC_RETRACT T=2 E=25"}

# --- T3 ---
[menu __main tools t3]
name: T3
icon: extruder

[menu __main tools t3 activate]
name: Aktivieren (Dock)
icon: home
method: printer.gcode.script
params: {"script":"T3"}
confirm: Tool T3 aktivieren (Dock/Undock)?

[menu __main tools t3 load]
name: Filament laden
icon: arrow-down
method: printer.gcode.script
params: {"script":"HTC_LOAD_FILAMENT T=3"}

[menu __main tools t3 unload]
name: Filament entladen
icon: arrow-up
method: printer.gcode.script
params: {"script":"HTC_UNLOAD_FILAMENT T=3"}

[menu __main tools t3 extrude5]
name: Extrude 5mm
icon: filament
method: printer.gcode.script
params: {"script":"HTC_EXTRUDE T=3 E=5"}

[menu __main tools t3 extrude10]
name: Extrude 10mm
icon: filament
method: printer.gcode.script
params: {"script":"HTC_EXTRUDE T=3 E=10"}

[menu __main tools t3 extrude25]
name: Extrude 25mm
icon: filament
method: printer.gcode.script
params: {"script":"HTC_EXTRUDE T=3 E=25"}

[menu __main tools t3 retract5]
name: Retract 5mm
icon: filament
method: printer.gcode.script
params: {"script":"HTC_RETRACT T=3 E=5"}

[menu __main tools t3 retract10]
name: Retract 10mm
icon: filament
method: printer.gcode.script
params: {"script":"HTC_RETRACT T=3 E=10"}

[menu __main tools t3 retract25]
name: Retract 25mm
icon: filament
method: printer.gcode.script
params: {"script":"HTC_RETRACT T=3 E=25"}

# --- T4 ---
[menu __main tools t4]
name: T4
icon: extruder

[menu __main tools t4 activate]
name: Aktivieren (Dock)
icon: home
method: printer.gcode.script
params: {"script":"T4"}
confirm: Tool T4 aktivieren (Dock/Undock)?

[menu __main tools t4 load]
name: Filament laden
icon: arrow-down
method: printer.gcode.script
params: {"script":"HTC_LOAD_FILAMENT T=4"}

[menu __main tools t4 unload]
name: Filament entladen
icon: arrow-up
method: printer.gcode.script
params: {"script":"HTC_UNLOAD_FILAMENT T=4"}

[menu __main tools t4 extrude5]
name: Extrude 5mm
icon: filament
method: printer.gcode.script
params: {"script":"HTC_EXTRUDE T=4 E=5"}

[menu __main tools t4 extrude10]
name: Extrude 10mm
icon: filament
method: printer.gcode.script
params: {"script":"HTC_EXTRUDE T=4 E=10"}

[menu __main tools t4 extrude25]
name: Extrude 25mm
icon: filament
method: printer.gcode.script
params: {"script":"HTC_EXTRUDE T=4 E=25"}

[menu __main tools t4 retract5]
name: Retract 5mm
icon: filament
method: printer.gcode.script
params: {"script":"HTC_RETRACT T=4 E=5"}

[menu __main tools t4 retract10]
name: Retract 10mm
icon: filament
method: printer.gcode.script
params: {"script":"HTC_RETRACT T=4 E=10"}

[menu __main tools t4 retract25]
name: Retract 25mm
icon: filament
method: printer.gcode.script
params: {"script":"HTC_RETRACT T=4 E=25"}

# --- T5 ---
[menu __main tools t5]
name: T5
icon: extruder

[menu __main tools t5 activate]
name: Aktivieren (Dock)
icon: home
method: printer.gcode.script
params: {"script":"T5"}
confirm: Tool T5 aktivieren (Dock/Undock)?

[menu __main tools t5 load]
name: Filament laden
icon: arrow-down
method: printer.gcode.script
params: {"script":"HTC_LOAD_FILAMENT T=5"}

[menu __main tools t5 unload]
name: Filament entladen
icon: arrow-up
method: printer.gcode.script
params: {"script":"HTC_UNLOAD_FILAMENT T=5"}

[menu __main tools t5 extrude5]
name: Extrude 5mm
icon: filament
method: printer.gcode.script
params: {"script":"HTC_EXTRUDE T=5 E=5"}

[menu __main tools t5 extrude10]
name: Extrude 10mm
icon: filament
method: printer.gcode.script
params: {"script":"HTC_EXTRUDE T=5 E=10"}

[menu __main tools t5 extrude25]
name: Extrude 25mm
icon: filament
method: printer.gcode.script
params: {"script":"HTC_EXTRUDE T=5 E=25"}

[menu __main tools t5 retract5]
name: Retract 5mm
icon: filament
method: printer.gcode.script
params: {"script":"HTC_RETRACT T=5 E=5"}

[menu __main tools t5 retract10]
name: Retract 10mm
icon: filament
method: printer.gcode.script
params: {"script":"HTC_RETRACT T=5 E=10"}

[menu __main tools t5 retract25]
name: Retract 25mm
icon: filament
method: printer.gcode.script
params: {"script":"HTC_RETRACT T=5 E=25"}

# =====================================================================
# WARTUNG MENÜ
# =====================================================================

[menu __main wartung clean]
name: Clean Nozzle
icon: filament
method: printer.gcode.script
params: {"script":"CLEAN_NOZZLE"}

[menu __main wartung cleanall]
name: Clean All Nozzles
icon: filament
method: printer.gcode.script
params: {"script":"CLEAN_ALL_NOZZLES"}
confirm: Alle Nozzles reinigen?

[menu __main wartung demo]
name: Toolchange Demo
icon: refresh
method: printer.gcode.script
params: {"script":"TOOLCHANGE_DEMO"}
confirm: Toolchange Demo starten?

[menu __main wartung nozzlecal]
name: Nozzle-Kalibrierung
icon: bed-level
method: printer.gcode.script
params: {"script":"INITIALISIERUNG_NOZZLEKALIBRIERUNG"}
confirm: Nozzle-Kalibrierung starten? Entlädt alle Tools und reinigt.

[menu __main wartung qgl]
name: QGL Ergebnis
icon: info
method: printer.gcode.script
params: {"script":"SHOW_QGL_RESULT"}

[menu __main wartung offsets]
name: Tool Offsets speichern
icon: complete
method: printer.gcode.script
params: {"script":"WRITE_TOOL_OFFSETS"}
confirm: Tool Offsets speichern?

[menu __main wartung unloadone]
name: Unload One Filament
icon: arrow-up
method: printer.gcode.script
params: {"script":"UNLOAD_ONE_FILAMENT"}

[menu __main wartung unloadall]
name: Unload All Filament
icon: arrow-up
method: printer.gcode.script
params: {"script":"UNLOAD_ALL_FILAMENT"}
confirm: Alle Filamente entladen?

# 250er-only macros (hidden on 350er via enable condition)
[menu __main wartung zswitch]
name: Z-Switch Kalibrierung
icon: bed-level
method: printer.gcode.script
params: {"script":"CALIBRATE_Z_SWITCH"}
confirm: Z-Switch Kalibrierung starten?
enable: {{ 'CALIBRATE_Z_SWITCH' in printer.available_commands }}

[menu __main wartung emergencyhome]
name: Emergency Home Z
icon: home
method: printer.gcode.script
params: {"script":"EMERGENCY_HOME_Z"}
confirm: Emergency Home Z ausführen?
enable: {{ 'EMERGENCY_HOME_Z' in printer.available_commands }}

# =====================================================================
# SYSTEM MENÜ
# =====================================================================

[menu __main system homing]
name: Homing (G28)
icon: home
method: printer.gcode.script
params: {"script":"G28"}
confirm: Alle Achsen homen?

[menu __main system move]
name: Bewegung
icon: move
panel: move

[menu __main system temperature]
name: Temperatur
icon: heat-up
panel: temperature

[menu __main system bedmesh]
name: Bed Mesh
icon: bed-mesh
panel: bed_mesh

[menu __main system zcalibrate]
name: Z-Kalibrierung
icon: z-tilt
panel: zcalibrate

[menu __main system camera]
name: Kamera
icon: camera
panel: camera
enable: {{ moonraker.cameras.count > 0 }}

[menu __main system console]
name: Konsole
icon: console
panel: console

[menu __main system network]
name: Netzwerk
icon: network
panel: network

[menu __main system settings]
name: Einstellungen
icon: settings
panel: settings
```

- [ ] **Step 2: Verify file was created**

Run: `ls -la configs/350/KlipperScreen.conf`
Expected: File exists, ~400+ lines

- [ ] **Step 3: Commit**

```bash
git add configs/350/KlipperScreen.conf
git commit -m "feat: add KlipperScreen config for 350er with toolchanger menus"
```

---

### Task 2: Create KlipperScreen.conf for 250er

**Files:**
- Create: `configs/250/KlipperScreen.conf`

- [ ] **Step 1: Create the 250er KlipperScreen config**

Copy the 350er config as base, then apply these differences:
- Printer name: `HappyToolchanger250` instead of `HappyToolchanger350`
- Remove T4 and T5 tool menu sections entirely (250er has only T0-T3)
- The Wartung menu stays identical (250er-only macros are handled via `enable` conditions that auto-show them)

The file is identical to Task 1's config except:
1. `[printer HappyToolchanger250]` instead of `[printer HappyToolchanger350]`
2. No `[menu __main tools t4]` or `[menu __main tools t5]` sections (and all their sub-entries)

- [ ] **Step 2: Verify file was created**

Run: `ls -la configs/250/KlipperScreen.conf`
Expected: File exists, shorter than 350er (no T4/T5)

Run: `grep -c "menu __main tools t" configs/250/KlipperScreen.conf`
Expected: Lines matching T0-T3 only (no t4, t5)

- [ ] **Step 3: Commit**

```bash
git add configs/250/KlipperScreen.conf
git commit -m "feat: add KlipperScreen config for 250er (4 tools)"
```

---

### Task 3: Add new HTC macros to 350er tobi-macros.cfg

**Files:**
- Modify: `configs/350/macros/tobi-macros.cfg` (append at end, before `PRINT_START`)

- [ ] **Step 1: Append four new macros**

Add these macros before the `PRINT_START` macro (around line 611). Insert them after the `_BEFORE_TOOL_DROPOFF` macro block (line 501) and before `PRIME_LINE` (line 503):

```ini
# ── HTC KlipperScreen Extruder-Makros ─────────────────────────────────
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

- [ ] **Step 2: Verify macros are syntactically valid**

Run: `grep -c "gcode_macro HTC_" configs/350/macros/tobi-macros.cfg`
Expected: `4`

- [ ] **Step 3: Commit**

```bash
git add configs/350/macros/tobi-macros.cfg
git commit -m "feat: add HTC_EXTRUDE/RETRACT/LOAD/UNLOAD macros for 350er"
```

---

### Task 4: Add new HTC macros to 250er tobi-macros.cfg

**Files:**
- Modify: `configs/250/macros/tobi-macros.cfg` (append same 4 macros)

- [ ] **Step 1: Append the same four macros from Task 3**

The macros are identical to Task 3. Append them at the same logical position (after `_BEFORE_TOOL_DROPOFF`, before `PRIME_LINE`).

- [ ] **Step 2: Verify**

Run: `grep -c "gcode_macro HTC_" configs/250/macros/tobi-macros.cfg`
Expected: `4`

- [ ] **Step 3: Commit**

```bash
git add configs/250/macros/tobi-macros.cfg
git commit -m "feat: add HTC_EXTRUDE/RETRACT/LOAD/UNLOAD macros for 250er"
```

---

### Task 5: Create shared VNC launch script

**Files:**
- Create: `configs/shared/klipperscreen-vnc.sh`

- [ ] **Step 1: Create the configs/shared/ directory and VNC script**

```bash
#!/bin/bash
# KlipperScreen VNC — runs a second KlipperScreen instance on a virtual display
# Accessed via noVNC at http://<printer-ip>:8080
#
# This script is managed by install.sh — do not edit on the printer directly.

set -euo pipefail

DISPLAY_NUM=":10"
VNC_PORT=5900
GEOMETRY="800x480"
KS_ENV="${HOME}/.KlipperScreen-env/bin/python"
KS_SCRIPT="${HOME}/KlipperScreen/screen.py"

# Start virtual X server via TigerVNC
Xtigervnc -rfbport ${VNC_PORT} \
    -geometry ${GEOMETRY} \
    -noreset \
    -AlwaysShared \
    -SecurityTypes none \
    ${DISPLAY_NUM} &

VNC_PID=$!
sleep 2

# Start second KlipperScreen instance on the virtual display
DISPLAY=${DISPLAY_NUM} ${KS_ENV} ${KS_SCRIPT} &
KS_PID=$!

# Wait for either process to exit
wait -n ${VNC_PID} ${KS_PID} 2>/dev/null || true

# Clean up
kill ${VNC_PID} ${KS_PID} 2>/dev/null || true
wait 2>/dev/null || true
```

- [ ] **Step 2: Make executable and verify**

Run: `chmod +x configs/shared/klipperscreen-vnc.sh && head -5 configs/shared/klipperscreen-vnc.sh`
Expected: Shows the shebang and comments

- [ ] **Step 3: Commit**

```bash
git add configs/shared/klipperscreen-vnc.sh
git commit -m "feat: add shared VNC launch script for KlipperScreen browser access"
```

---

### Task 6: Add KlipperScreen + VNC sections to install.sh

**Files:**
- Modify: `install.sh` (add sections 10 and 11 before the restart section)

- [ ] **Step 1: Add KlipperScreen config deployment section**

Insert before `# --- 9. Restart services ---` (line 331):

```bash
# --- 10. KlipperScreen config ---
if [ -n "$PRINTER" ]; then
  echo "--- 10. Configuring KlipperScreen ---"
  KS_SRC="${CONFIG_SRC}/KlipperScreen.conf"
  KS_DST="${CONFIG_DST}/KlipperScreen.conf"
  if [ -f "$KS_SRC" ]; then
    cp "$KS_SRC" "$KS_DST"
    echo "  Deployed KlipperScreen.conf"
  else
    echo "  WARNING: KlipperScreen.conf not found in configs/${PRINTER}/"
  fi
else
  echo "--- 10. Skipping KlipperScreen config (no --printer specified) ---"
fi
```

- [ ] **Step 2: Add VNC + noVNC setup section**

Insert after section 10, before the restart section:

```bash
# --- 11. VNC + noVNC for KlipperScreen browser access ---
if [ -n "$PRINTER" ]; then
  echo "--- 11. Setting up VNC + noVNC ---"

  # Install packages
  PKGS_NEEDED=""
  dpkg -l tigervnc-standalone-server &>/dev/null || PKGS_NEEDED="tigervnc-standalone-server"
  dpkg -l novnc &>/dev/null || PKGS_NEEDED="${PKGS_NEEDED} novnc"
  dpkg -l websockify &>/dev/null || PKGS_NEEDED="${PKGS_NEEDED} websockify"
  if [ -n "$PKGS_NEEDED" ]; then
    echo "  Installing:${PKGS_NEEDED}"
    sudo apt-get update -qq
    sudo apt-get install -y -qq ${PKGS_NEEDED}
  fi

  # Deploy VNC launch script
  VNC_SCRIPT="${HOME}/klipperscreen-vnc.sh"
  cp "${INSTALL_DIR}/configs/shared/klipperscreen-vnc.sh" "$VNC_SCRIPT"
  chmod +x "$VNC_SCRIPT"
  echo "  Deployed klipperscreen-vnc.sh"

  # Create systemd service for KlipperScreen VNC
  sudo tee /etc/systemd/system/klipperscreen-vnc.service > /dev/null <<VNCEOF
[Unit]
Description=KlipperScreen VNC (second instance on :10)
After=KlipperScreen.service moonraker.service
Wants=KlipperScreen.service

[Service]
Type=simple
User=${USER}
ExecStart=${VNC_SCRIPT}
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
VNCEOF

  # Create systemd service for noVNC
  NOVNC_PATH="/usr/share/novnc"
  sudo tee /etc/systemd/system/novnc.service > /dev/null <<NOVNCEOF
[Unit]
Description=noVNC WebSocket proxy for KlipperScreen
After=klipperscreen-vnc.service
Wants=klipperscreen-vnc.service

[Service]
Type=simple
User=${USER}
ExecStart=/usr/bin/websockify --web=${NOVNC_PATH} 8080 localhost:5900
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
NOVNCEOF

  sudo systemctl daemon-reload
  sudo systemctl enable klipperscreen-vnc.service
  sudo systemctl enable novnc.service
  sudo systemctl restart klipperscreen-vnc.service
  sudo systemctl restart novnc.service
  echo "  Started VNC on port 5900, noVNC on port 8080"

  # Register services with Moonraker
  for svc in klipperscreen-vnc novnc; do
    if ! grep -q "^${svc}$" "${ASVC_FILE}" 2>/dev/null; then
      echo "${svc}" >> "${ASVC_FILE}"
      echo "  Added ${svc} to moonraker.asvc"
    fi
  done
else
  echo "--- 11. Skipping VNC + noVNC (no --printer specified) ---"
fi
```

- [ ] **Step 3: Update the restart section number from 9 to 12**

Change `# --- 9. Restart services ---` to `# --- 12. Restart services ---` and update the echo/step numbering. Also add KlipperScreen restart:

```bash
# --- 12. Restart services ---
echo "--- 12. Restarting services ---"
sudo systemctl restart klipper
sudo systemctl restart moonraker
if systemctl is-active --quiet KlipperScreen 2>/dev/null; then
  sudo systemctl restart KlipperScreen
  echo "  Restarted KlipperScreen"
fi
if systemctl is-active --quiet crowsnest 2>/dev/null; then
  sudo systemctl restart crowsnest
  echo "  Restarted crowsnest"
fi
```

- [ ] **Step 4: Update the completion message**

Add the noVNC URL to the final output after `echo "Mainsail:      http://${PRINTER_IP}/"`:

```bash
echo "KlipperScreen: http://${PRINTER_IP}:8080"
```

- [ ] **Step 5: Verify install.sh syntax**

Run: `bash -n install.sh`
Expected: No output (no syntax errors)

- [ ] **Step 6: Commit**

```bash
git add install.sh
git commit -m "feat: add KlipperScreen config deployment and VNC/noVNC setup to install.sh"
```

---

### Task 7: Update .gitignore and add .superpowers

**Files:**
- Modify: `.gitignore`

- [ ] **Step 1: Add .superpowers to .gitignore**

Append to `.gitignore`:

```
# Superpowers brainstorming
.superpowers/
```

- [ ] **Step 2: Commit**

```bash
git add .gitignore
git commit -m "chore: add .superpowers to .gitignore"
```

---

### Task 8: Deploy to 350er and verify

**Files:** None (remote deployment)

- [ ] **Step 1: Push all changes to GitHub**

```bash
git push origin main
```

- [ ] **Step 2: Pull on 350er and run install**

```bash
ssh biqu@192.168.178.114 "cd ~/HappyToolchanger && git pull origin main && ./install.sh --printer 350"
```

- [ ] **Step 3: Verify KlipperScreen restarted with new config**

```bash
ssh biqu@192.168.178.114 "sudo systemctl restart KlipperScreen && sleep 3 && systemctl is-active KlipperScreen"
```
Expected: `active`

- [ ] **Step 4: Verify VNC and noVNC are running**

```bash
ssh biqu@192.168.178.114 "systemctl is-active klipperscreen-vnc && systemctl is-active novnc"
```
Expected: Both show `active`

- [ ] **Step 5: Verify noVNC is reachable**

```bash
ssh biqu@192.168.178.114 "curl -s -o /dev/null -w '%{http_code}' http://localhost:8080"
```
Expected: `200`

- [ ] **Step 6: Verify Klipper still running (no macro syntax errors)**

```bash
ssh biqu@192.168.178.114 "curl -s http://localhost:7125/printer/objects/query?print_stats | python3 -c \"import sys,json; print(json.load(sys.stdin)['result']['status']['print_stats']['state'])\""
```
Expected: `standby`

---

### Task 9: Deploy to 250er and verify

**Files:** None (remote deployment)

- [ ] **Step 1: Pull on 250er and run install**

```bash
ssh biqu@192.168.178.60 "cd ~/HappyToolchanger && git pull origin main && ./install.sh --printer 250"
```

- [ ] **Step 2: Verify KlipperScreen restarted with new config**

```bash
ssh biqu@192.168.178.60 "sudo systemctl restart KlipperScreen && sleep 3 && systemctl is-active KlipperScreen"
```
Expected: `active`

- [ ] **Step 3: Verify VNC and noVNC are running**

```bash
ssh biqu@192.168.178.60 "systemctl is-active klipperscreen-vnc && systemctl is-active novnc"
```
Expected: Both show `active`

- [ ] **Step 4: Verify noVNC is reachable**

```bash
ssh biqu@192.168.178.60 "curl -s -o /dev/null -w '%{http_code}' http://localhost:8080"
```
Expected: `200`

- [ ] **Step 5: Verify Klipper still running**

```bash
ssh biqu@192.168.178.60 "curl -s http://localhost:7125/printer/objects/query?print_stats | python3 -c \"import sys,json; print(json.load(sys.stdin)['result']['status']['print_stats']['state'])\""
```
Expected: `standby`
