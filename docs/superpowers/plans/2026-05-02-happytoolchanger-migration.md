# HappyToolchanger Migration Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Consolidate all Stealthchanger components (KTC Easy, Eddy-NG, Offset, Mainsail HTC) into a single `daTobi1/HappyToolchanger` monorepo with Moonraker Update Manager support, and bring the 350er printer up to the 250er's current state.

**Architecture:** Files are collected from the 250er printer (source of truth) and local repos via SSH/copy, organized into the spec'd directory structure, wrapped with install/uninstall scripts for Moonraker compatibility. The 350er gets updated by deploying the new repo.

**Tech Stack:** Klipper (Python), Eddy-NG (Python + C firmware), Mainsail (Vue.js/TypeScript, pre-built dist), Flask (Offset webapp), Bash (install scripts), Git/GitHub

---

## File Map

| Directory | Source | What |
|---|---|---|
| `klippy/extras/*.py` | 250er `~/klipper-toolchanger-easy/klipper/extras/` + `~/klipper/klippy/extras/tool_z_calibrate.py` + `~/offset/klippy/extras/offset.py` | Klipper Python modules |
| `eddy-ng/` | 250er `~/eddy-ng/` (excl. .git, __pycache__, *.bak) | Eddy-NG probe |
| `webapp/` | 250er `~/offset/` (app.py, index.html, css/, js/) | Offset calibration webapp |
| `mainsail/` | Local `D:/Claude Code/mainsail-htc/` (excl. node_modules, .git) | Mainsail fork with HTC panel |
| `configs/250/` | 250er `~/printer_data/config/toolchanger/` + `~/printer_data/config/macros/tobi-macros.cfg` | 250er printer configs |
| `configs/350/` | 350er `~/printer_data/config/toolchanger/` + `~/printer_data/config/macros/tobi-macros.cfg` | 350er printer configs |

## SSH Access

- **250er:** `biqu@192.168.178.60` (ed25519 key auth)
- **350er:** `biqu@192.168.178.113` (ed25519 key auth)

---

### Task 1: Create GitHub repo and init local git

**Files:**
- Create: `D:/Claude Code/HappyToolchanger/.git` (git init)

- [ ] **Step 1: Create GitHub repo**

```bash
gh repo create daTobi1/HappyToolchanger --public --description "Klipper Stealthchanger Monorepo: Toolchanger, Eddy-NG, Offset, Mainsail HTC Panel" --clone=false
```

Expected: repo created at `https://github.com/daTobi1/HappyToolchanger`

- [ ] **Step 2: Init local git repo**

```bash
cd "D:/Claude Code/HappyToolchanger"
git init
git remote add origin https://github.com/daTobi1/HappyToolchanger.git
```

- [ ] **Step 3: Create .gitignore**

Write `D:/Claude Code/HappyToolchanger/.gitignore`:

```
# Python
__pycache__/
*.pyc
*.pyo

# Backups
*.bak
*.orig

# Mainsail
mainsail/node_modules/
mainsail/.git/

# Venv (created by install.sh on printer)
*-env/
.venv/

# OS
.DS_Store
Thumbs.db

# Editor
*.swp
*.swo
*~
.idea/
.vscode/
```

- [ ] **Step 4: Commit .gitignore**

```bash
cd "D:/Claude Code/HappyToolchanger"
git add .gitignore
git commit -m "chore: initial .gitignore"
```

---

### Task 2: Collect KTC Easy Python modules from 250er

**Files:**
- Create: `klippy/extras/toolchanger.py`
- Create: `klippy/extras/tool_probe.py`
- Create: `klippy/extras/tool_probe_endstop.py`
- Create: `klippy/extras/tool.py`
- Create: `klippy/extras/tools_calibrate.py`
- Create: `klippy/extras/bed_thermal_adjust.py`
- Create: `klippy/extras/manual_rail.py`
- Create: `klippy/extras/multi_fan.py`
- Create: `klippy/extras/rounded_path.py`

- [ ] **Step 1: Create target directory**

```bash
mkdir -p "D:/Claude Code/HappyToolchanger/klippy/extras"
```

- [ ] **Step 2: SCP all KTC Easy modules from 250er**

```bash
scp biqu@192.168.178.60:~/klipper-toolchanger-easy/klipper/extras/*.py "D:/Claude Code/HappyToolchanger/klippy/extras/"
```

Expected: 9 files copied (toolchanger.py, tool_probe.py, tool_probe_endstop.py, tool.py, tools_calibrate.py, bed_thermal_adjust.py, manual_rail.py, multi_fan.py, rounded_path.py)

- [ ] **Step 3: Verify files**

```bash
ls -la "D:/Claude Code/HappyToolchanger/klippy/extras/"
```

Expected: 9 .py files, with toolchanger.py ~35KB, tool_probe_endstop.py ~17KB, tool_probe.py ~9.6KB

- [ ] **Step 4: Commit**

```bash
cd "D:/Claude Code/HappyToolchanger"
git add klippy/extras/
git commit -m "feat: add KTC Easy Python modules from 250er

Source: biqu@192.168.178.60:~/klipper-toolchanger-easy/klipper/extras/
Based on jwellman80/klipper-toolchanger-easy with z_probe modifications"
```

---

### Task 3: Collect tool_z_calibrate.py and offset.py

**Files:**
- Create: `klippy/extras/tool_z_calibrate.py`
- Create: `klippy/extras/offset.py`

- [ ] **Step 1: SCP tool_z_calibrate.py from 250er**

```bash
scp biqu@192.168.178.60:~/klipper/klippy/extras/tool_z_calibrate.py "D:/Claude Code/HappyToolchanger/klippy/extras/"
```

- [ ] **Step 2: SCP offset.py from 250er**

```bash
scp biqu@192.168.178.60:~/offset/klippy/extras/offset.py "D:/Claude Code/HappyToolchanger/klippy/extras/"
```

- [ ] **Step 3: Verify**

```bash
ls -la "D:/Claude Code/HappyToolchanger/klippy/extras/tool_z_calibrate.py" "D:/Claude Code/HappyToolchanger/klippy/extras/offset.py"
```

Expected: tool_z_calibrate.py ~7.9KB, offset.py present

- [ ] **Step 4: Commit**

```bash
cd "D:/Claude Code/HappyToolchanger"
git add klippy/extras/tool_z_calibrate.py klippy/extras/offset.py
git commit -m "feat: add tool_z_calibrate and offset modules

tool_z_calibrate.py: Z-switch calibration
offset.py: probe offset calibration (ex daTobi1/Offset)"
```

---

### Task 4: Collect Offset webapp

**Files:**
- Create: `webapp/app.py`
- Create: `webapp/index.html`
- Create: `webapp/css/camera.css`
- Create: `webapp/js/camera.js`
- Create: `webapp/js/gcode.js`
- Create: `webapp/js/index.js`
- Create: `webapp/js/tools.js`

- [ ] **Step 1: Create target directories**

```bash
mkdir -p "D:/Claude Code/HappyToolchanger/webapp/css" "D:/Claude Code/HappyToolchanger/webapp/js"
```

- [ ] **Step 2: SCP webapp files from 250er**

```bash
scp biqu@192.168.178.60:~/offset/app.py "D:/Claude Code/HappyToolchanger/webapp/"
scp biqu@192.168.178.60:~/offset/index.html "D:/Claude Code/HappyToolchanger/webapp/"
scp biqu@192.168.178.60:~/offset/css/* "D:/Claude Code/HappyToolchanger/webapp/css/"
scp biqu@192.168.178.60:~/offset/js/* "D:/Claude Code/HappyToolchanger/webapp/js/"
```

- [ ] **Step 3: Verify**

```bash
find "D:/Claude Code/HappyToolchanger/webapp" -type f | sort
```

Expected: 7 files (app.py, index.html, css/camera.css, js/camera.js, js/gcode.js, js/index.js, js/tools.js)

- [ ] **Step 4: Commit**

```bash
cd "D:/Claude Code/HappyToolchanger"
git add webapp/
git commit -m "feat: add Offset calibration webapp (ex daTobi1/Offset)"
```

---

### Task 5: Collect Eddy-NG from 250er

**Files:**
- Create: `eddy-ng/` (full directory tree, excluding .git, __pycache__, *.bak)

- [ ] **Step 1: Create target directory**

```bash
mkdir -p "D:/Claude Code/HappyToolchanger/eddy-ng"
```

- [ ] **Step 2: SCP eddy-ng from 250er (excluding .git, __pycache__, .bak files)**

```bash
scp -r biqu@192.168.178.60:~/eddy-ng/probe_eddy_ng.py "D:/Claude Code/HappyToolchanger/eddy-ng/"
scp -r biqu@192.168.178.60:~/eddy-ng/probe_eddy_ng "D:/Claude Code/HappyToolchanger/eddy-ng/"
scp biqu@192.168.178.60:~/eddy-ng/ldc1612_ng.py "D:/Claude Code/HappyToolchanger/eddy-ng/"
scp biqu@192.168.178.60:~/eddy-ng/__init__.py "D:/Claude Code/HappyToolchanger/eddy-ng/"
scp biqu@192.168.178.60:~/eddy-ng/calibrate_macros.cfg "D:/Claude Code/HappyToolchanger/eddy-ng/"
scp biqu@192.168.178.60:~/eddy-ng/calibrate_macros_cartographer.cfg "D:/Claude Code/HappyToolchanger/eddy-ng/"
scp biqu@192.168.178.60:~/eddy-ng/klipper.patch "D:/Claude Code/HappyToolchanger/eddy-ng/"
scp biqu@192.168.178.60:~/eddy-ng/flash.sh "D:/Claude Code/HappyToolchanger/eddy-ng/"
scp biqu@192.168.178.60:~/eddy-ng/install_eddy_ng.py "D:/Claude Code/HappyToolchanger/eddy-ng/"
scp biqu@192.168.178.60:~/eddy-ng/install.sh "D:/Claude Code/HappyToolchanger/eddy-ng/"
scp biqu@192.168.178.60:~/eddy-ng/install.py "D:/Claude Code/HappyToolchanger/eddy-ng/"
scp biqu@192.168.178.60:~/eddy-ng/conftest.py "D:/Claude Code/HappyToolchanger/eddy-ng/"
scp biqu@192.168.178.60:~/eddy-ng/example-printer.cfg "D:/Claude Code/HappyToolchanger/eddy-ng/"
scp biqu@192.168.178.60:~/eddy-ng/pyproject.toml "D:/Claude Code/HappyToolchanger/eddy-ng/"
scp biqu@192.168.178.60:~/eddy-ng/LICENSE "D:/Claude Code/HappyToolchanger/eddy-ng/"
scp biqu@192.168.178.60:~/eddy-ng/README.md "D:/Claude Code/HappyToolchanger/eddy-ng/"
scp biqu@192.168.178.60:~/eddy-ng/update-klipper.sh "D:/Claude Code/HappyToolchanger/eddy-ng/"
scp biqu@192.168.178.60:~/eddy-ng/uninstall.sh "D:/Claude Code/HappyToolchanger/eddy-ng/"
scp -r biqu@192.168.178.60:~/eddy-ng/firmware "D:/Claude Code/HappyToolchanger/eddy-ng/"
scp -r biqu@192.168.178.60:~/eddy-ng/eddy-ng "D:/Claude Code/HappyToolchanger/eddy-ng/"
scp -r biqu@192.168.178.60:~/eddy-ng/scripts "D:/Claude Code/HappyToolchanger/eddy-ng/"
scp -r biqu@192.168.178.60:~/eddy-ng/src "D:/Claude Code/HappyToolchanger/eddy-ng/"
scp -r biqu@192.168.178.60:~/eddy-ng/tests "D:/Claude Code/HappyToolchanger/eddy-ng/"
scp -r biqu@192.168.178.60:~/eddy-ng/.beads "D:/Claude Code/HappyToolchanger/eddy-ng/"
```

- [ ] **Step 3: Remove __pycache__ and .bak files that may have come along**

```bash
find "D:/Claude Code/HappyToolchanger/eddy-ng" -name "__pycache__" -type d -exec rm -rf {} + 2>/dev/null
find "D:/Claude Code/HappyToolchanger/eddy-ng" -name "*.bak" -delete 2>/dev/null
find "D:/Claude Code/HappyToolchanger/eddy-ng" -name "*.pyc" -delete 2>/dev/null
```

- [ ] **Step 4: Verify key files**

```bash
ls -la "D:/Claude Code/HappyToolchanger/eddy-ng/probe_eddy_ng.py"
ls -la "D:/Claude Code/HappyToolchanger/eddy-ng/probe_eddy_ng/"
ls -la "D:/Claude Code/HappyToolchanger/eddy-ng/ldc1612_ng.py"
```

Expected: probe_eddy_ng.py ~151KB, probe_eddy_ng/ directory with ~12 .py files, ldc1612_ng.py ~21KB

- [ ] **Step 5: Commit**

```bash
cd "D:/Claude Code/HappyToolchanger"
git add eddy-ng/
git commit -m "feat: add Eddy-NG probe (ex daTobi1/neweddy)

Complete Eddy-NG with patched probe_eddy_ng.py, endstop.py, probe.py
for z_probe compatibility with toolchanger"
```

---

### Task 6: Copy Mainsail fork

**Files:**
- Create: `mainsail/` (full source tree + dist, excluding node_modules and .git)

- [ ] **Step 1: Copy mainsail source (excluding node_modules and .git)**

```bash
rsync -av --exclude='node_modules' --exclude='.git' "D:/Claude Code/mainsail-htc/" "D:/Claude Code/HappyToolchanger/mainsail/"
```

If rsync is not available on Windows:

```bash
cd "D:/Claude Code"
cp -r mainsail-htc/ HappyToolchanger/mainsail/
rm -rf "D:/Claude Code/HappyToolchanger/mainsail/node_modules"
rm -rf "D:/Claude Code/HappyToolchanger/mainsail/.git"
```

- [ ] **Step 2: Verify dist and key HTC files exist**

```bash
ls "D:/Claude Code/HappyToolchanger/mainsail/dist/index.html"
ls "D:/Claude Code/HappyToolchanger/mainsail/src/components/panels/HtcPanel.vue"
ls "D:/Claude Code/HappyToolchanger/mainsail/src/components/panels/Htc/HtcMixin.ts"
ls "D:/Claude Code/HappyToolchanger/mainsail/package.json"
```

Expected: all 4 files exist

- [ ] **Step 3: Commit**

```bash
cd "D:/Claude Code/HappyToolchanger"
git add mainsail/
git commit -m "feat: add Mainsail fork with HTC Panel (ex daTobi1/mainsail)

Branch htc-panel: HappyHare/Toolchanger dashboard panel with
gate overview, TTG map, status bar, Spoolman integration"
```

---

### Task 7: Collect configs from both printers

**Files:**
- Create: `configs/250/toolchanger/` (toolchanger-config.cfg + tools/T0-T3.cfg)
- Create: `configs/250/macros/tobi-macros.cfg`
- Create: `configs/350/toolchanger/` (full tree incl. probe/)
- Create: `configs/350/macros/tobi-macros.cfg`

- [ ] **Step 1: Create target directories**

```bash
mkdir -p "D:/Claude Code/HappyToolchanger/configs/250/toolchanger/tools"
mkdir -p "D:/Claude Code/HappyToolchanger/configs/250/macros"
mkdir -p "D:/Claude Code/HappyToolchanger/configs/350/toolchanger/tools"
mkdir -p "D:/Claude Code/HappyToolchanger/configs/350/toolchanger/probe"
mkdir -p "D:/Claude Code/HappyToolchanger/configs/350/macros"
```

- [ ] **Step 2: SCP 250er configs**

```bash
scp biqu@192.168.178.60:~/printer_data/config/toolchanger/toolchanger-config.cfg "D:/Claude Code/HappyToolchanger/configs/250/toolchanger/"
scp biqu@192.168.178.60:~/printer_data/config/toolchanger/tools/T0.cfg "D:/Claude Code/HappyToolchanger/configs/250/toolchanger/tools/"
scp biqu@192.168.178.60:~/printer_data/config/toolchanger/tools/T1.cfg "D:/Claude Code/HappyToolchanger/configs/250/toolchanger/tools/"
scp biqu@192.168.178.60:~/printer_data/config/toolchanger/tools/T2.cfg "D:/Claude Code/HappyToolchanger/configs/250/toolchanger/tools/"
scp biqu@192.168.178.60:~/printer_data/config/toolchanger/tools/T3.cfg "D:/Claude Code/HappyToolchanger/configs/250/toolchanger/tools/"
scp biqu@192.168.178.60:~/printer_data/config/macros/tobi-macros.cfg "D:/Claude Code/HappyToolchanger/configs/250/macros/"
```

- [ ] **Step 3: SCP 350er configs**

```bash
scp biqu@192.168.178.113:~/printer_data/config/toolchanger/toolchanger-config.cfg "D:/Claude Code/HappyToolchanger/configs/350/toolchanger/"
scp biqu@192.168.178.113:~/printer_data/config/toolchanger/tools/T0.cfg "D:/Claude Code/HappyToolchanger/configs/350/toolchanger/tools/"
scp biqu@192.168.178.113:~/printer_data/config/toolchanger/tools/T1.cfg "D:/Claude Code/HappyToolchanger/configs/350/toolchanger/tools/"
scp biqu@192.168.178.113:~/printer_data/config/toolchanger/tools/T2.cfg "D:/Claude Code/HappyToolchanger/configs/350/toolchanger/tools/"
scp biqu@192.168.178.113:~/printer_data/config/toolchanger/tools/T3.cfg "D:/Claude Code/HappyToolchanger/configs/350/toolchanger/tools/"
scp biqu@192.168.178.113:~/printer_data/config/toolchanger/tools/T4.cfg "D:/Claude Code/HappyToolchanger/configs/350/toolchanger/tools/"
scp biqu@192.168.178.113:~/printer_data/config/toolchanger/tools/T5.cfg "D:/Claude Code/HappyToolchanger/configs/350/toolchanger/tools/"
scp biqu@192.168.178.113:~/printer_data/config/toolchanger/probe/eddy_probe.cfg "D:/Claude Code/HappyToolchanger/configs/350/toolchanger/probe/"
scp biqu@192.168.178.113:~/printer_data/config/toolchanger/macros/tobi-macros.cfg "D:/Claude Code/HappyToolchanger/configs/350/macros/"
```

Note: 350er has macros at `toolchanger/macros/tobi-macros.cfg` AND possibly at `config/macros/tobi-macros.cfg` — check both:

```bash
scp biqu@192.168.178.113:~/printer_data/config/macros/tobi-macros.cfg "D:/Claude Code/HappyToolchanger/configs/350/macros/" 2>/dev/null || echo "Not at config/macros/, using toolchanger/macros/ copy"
```

- [ ] **Step 4: Verify**

```bash
find "D:/Claude Code/HappyToolchanger/configs" -type f | sort
```

Expected: 250er has 6 files (toolchanger-config + T0-T3 + tobi-macros), 350er has 9-10 files (toolchanger-config + T0-T5 + eddy_probe + tobi-macros)

- [ ] **Step 5: Commit**

```bash
cd "D:/Claude Code/HappyToolchanger"
git add configs/
git commit -m "feat: add printer configs for 250er and 350er

250er: 4 tools (T0-T3), Voron 2.4 250mm
350er: 6 tools (T0-T5), Voron 2.4 350mm"
```

---

### Task 8: Write install.sh

**Files:**
- Create: `install.sh`

- [ ] **Step 1: Write install.sh**

Write `D:/Claude Code/HappyToolchanger/install.sh`:

```bash
#!/bin/bash
set -euo pipefail

# =============================
# HappyToolchanger Installer
# Klipper extras, Eddy-NG, Mainsail, Offset webapp
# =============================

APP_NAME="HappyToolchanger"
INSTALL_DIR="$(cd "$(dirname "$0")" && pwd)"
VENV_DIR="${HOME}/happytoolchanger-env"
KLIPPER_EXTRAS="${HOME}/klipper/klippy/extras"
MAINSAIL_DIR="${HOME}/mainsail"
ASVC_FILE="${HOME}/printer_data/moonraker.asvc"
MOONRAKER_CONF="${HOME}/printer_data/config/moonraker.conf"
PRINTER=""

# Parse args
while [[ $# -gt 0 ]]; do
  case $1 in
    --printer)
      PRINTER="$2"
      shift 2
      ;;
    *)
      echo "Unknown parameter: $1"
      echo "Usage: ./install.sh [--printer 250|350]"
      exit 1
      ;;
  esac
done

# Refuse root
if [ "${EUID}" -eq 0 ]; then
  echo "Please do not run as root/sudo."
  exit 1
fi

echo "=== Installing ${APP_NAME} ==="
echo "Install dir: ${INSTALL_DIR}"
echo

# --- 1. Klipper extras symlinks ---
echo "--- Linking Klipper extras ---"
for py_file in "${INSTALL_DIR}/klippy/extras/"*.py; do
  fname="$(basename "$py_file")"
  target="${KLIPPER_EXTRAS}/${fname}"
  if [ -L "$target" ] || [ -f "$target" ]; then
    rm -f "$target"
  fi
  ln -sf "$py_file" "$target"
  echo "  Linked: ${fname}"
done

# --- 2. Eddy-NG ---
echo "--- Installing Eddy-NG ---"
EDDY_DIR="${INSTALL_DIR}/eddy-ng"

# Loader files for probe_eddy_ng and ldc1612_ng
# These small files import from the eddy-ng directory
LOADER_PROBE="${KLIPPER_EXTRAS}/probe_eddy_ng.py"
cat > "$LOADER_PROBE" <<PYEOF
import importlib, sys, os
sys.path.insert(0, os.path.expanduser("${EDDY_DIR}"))
from probe_eddy_ng import *
def load_config(config):
    mod = importlib.import_module("probe_eddy_ng")
    return mod.load_config(config)
def load_config_prefix(config):
    mod = importlib.import_module("probe_eddy_ng")
    return mod.load_config_prefix(config)
PYEOF
echo "  Created loader: probe_eddy_ng.py"

LOADER_LDC="${KLIPPER_EXTRAS}/ldc1612_ng.py"
cat > "$LOADER_LDC" <<PYEOF
import importlib, sys, os
sys.path.insert(0, os.path.expanduser("${EDDY_DIR}"))
from ldc1612_ng import *
def load_config(config):
    mod = importlib.import_module("ldc1612_ng")
    return mod.load_config(config)
PYEOF
echo "  Created loader: ldc1612_ng.py"

# Symlink probe_eddy_ng package directory
if [ -L "${KLIPPER_EXTRAS}/probe_eddy_ng" ] || [ -d "${KLIPPER_EXTRAS}/probe_eddy_ng" ]; then
  rm -rf "${KLIPPER_EXTRAS}/probe_eddy_ng"
fi
ln -sf "${EDDY_DIR}/probe_eddy_ng" "${KLIPPER_EXTRAS}/probe_eddy_ng"
echo "  Linked: probe_eddy_ng/"

# Apply klipper patch if needed
if [ -f "${EDDY_DIR}/klipper.patch" ]; then
  echo "  Checking klipper.patch..."
  cd "${HOME}/klipper"
  if git apply --check "${EDDY_DIR}/klipper.patch" 2>/dev/null; then
    git apply "${EDDY_DIR}/klipper.patch"
    echo "  Applied klipper.patch"
  else
    echo "  klipper.patch already applied or conflicts (skipped)"
  fi
  cd "${INSTALL_DIR}"
fi

# --- 3. Mainsail ---
echo "--- Deploying Mainsail ---"
if [ -d "${INSTALL_DIR}/mainsail/dist" ]; then
  if [ -d "${MAINSAIL_DIR}" ] && [ ! -f "${MAINSAIL_DIR}/.happytoolchanger" ]; then
    echo "  Backing up existing mainsail to ${MAINSAIL_DIR}.bak"
    rm -rf "${MAINSAIL_DIR}.bak"
    mv "${MAINSAIL_DIR}" "${MAINSAIL_DIR}.bak"
  fi
  mkdir -p "${MAINSAIL_DIR}"
  cp -r "${INSTALL_DIR}/mainsail/dist/"* "${MAINSAIL_DIR}/"
  touch "${MAINSAIL_DIR}/.happytoolchanger"
  echo "  Deployed mainsail dist to ${MAINSAIL_DIR}"
else
  echo "  WARNING: mainsail/dist/ not found, skipping"
fi

# --- 4. Printer configs (optional) ---
if [ -n "$PRINTER" ]; then
  echo "--- Deploying configs for printer ${PRINTER} ---"
  CONFIG_SRC="${INSTALL_DIR}/configs/${PRINTER}"
  if [ -d "$CONFIG_SRC" ]; then
    CONFIG_DST="${HOME}/printer_data/config"

    # Toolchanger configs
    if [ -d "${CONFIG_SRC}/toolchanger" ]; then
      mkdir -p "${CONFIG_DST}/toolchanger"
      cp -r "${CONFIG_SRC}/toolchanger/"* "${CONFIG_DST}/toolchanger/"
      echo "  Copied toolchanger configs"
    fi

    # Macros
    if [ -d "${CONFIG_SRC}/macros" ]; then
      mkdir -p "${CONFIG_DST}/macros"
      cp -r "${CONFIG_SRC}/macros/"* "${CONFIG_DST}/macros/"
      echo "  Copied macros"
    fi
  else
    echo "  WARNING: configs/${PRINTER}/ not found"
  fi
fi

# --- 5. Offset webapp ---
echo "--- Installing Offset webapp ---"

# Check for python3-venv
if ! dpkg -l | grep -q python3-venv 2>/dev/null; then
  echo "  Installing python3-venv..."
  sudo apt-get update -qq
  sudo apt-get install -y -qq python3-venv
fi

# Create/update venv
if [ ! -d "${VENV_DIR}" ]; then
  echo "  Creating venv at ${VENV_DIR}..."
  python3 -m venv --copies "${VENV_DIR}"
fi
source "${VENV_DIR}/bin/activate"
pip install --quiet --upgrade pip
pip install --quiet flask waitress
deactivate

# Create systemd service
SERVICE_FILE="/etc/systemd/system/happytoolchanger-webapp.service"
sudo tee "$SERVICE_FILE" > /dev/null <<EOL
[Unit]
Description=HappyToolchanger Offset Webapp
After=network.target moonraker.service
StartLimitIntervalSec=0

[Service]
Type=simple
User=${USER}
WorkingDirectory=${INSTALL_DIR}/webapp
ExecStart=${VENV_DIR}/bin/python3 -m flask run --host=0.0.0.0 --port=3000
Environment="PATH=${VENV_DIR}/bin"
Environment="FLASK_APP=app.py"
Restart=always
RestartSec=1

[Install]
WantedBy=multi-user.target
EOL

sudo systemctl daemon-reload
sudo systemctl enable happytoolchanger-webapp.service
sudo systemctl restart happytoolchanger-webapp.service
echo "  Webapp service started on port 3000"

# --- 6. Moonraker integration ---
echo "--- Configuring Moonraker ---"

# Add to moonraker.asvc
mkdir -p "$(dirname "${ASVC_FILE}")"
touch "${ASVC_FILE}"
if ! grep -q "^happytoolchanger-webapp$" "${ASVC_FILE}" 2>/dev/null; then
  echo "happytoolchanger-webapp" >> "${ASVC_FILE}"
  echo "  Added happytoolchanger-webapp to moonraker.asvc"
fi

# Add update_manager if not present
if [ -f "${MOONRAKER_CONF}" ]; then
  if ! grep -q "^\[update_manager ${APP_NAME}\]" "${MOONRAKER_CONF}"; then
    cat >> "${MOONRAKER_CONF}" <<EOL

[update_manager ${APP_NAME}]
type: git_repo
path: ${INSTALL_DIR}
origin: https://github.com/daTobi1/HappyToolchanger.git
primary_branch: main
is_system_service: True
managed_services: happytoolchanger-webapp klipper
install_script: install.sh
EOL
    echo "  Added update_manager config to moonraker.conf"
  else
    echo "  update_manager config already exists"
  fi
fi

# --- 7. Restart services ---
echo "--- Restarting services ---"
sudo systemctl restart klipper
sudo systemctl restart moonraker

echo
echo "=== ${APP_NAME} installation complete! ==="
PRINTER_IP=$(hostname -I | awk '{print $1}')
echo "Offset Webapp: http://${PRINTER_IP}:3000"
echo "Mainsail:      http://${PRINTER_IP}/"
```

- [ ] **Step 2: Make executable**

```bash
chmod +x "D:/Claude Code/HappyToolchanger/install.sh"
```

- [ ] **Step 3: Commit**

```bash
cd "D:/Claude Code/HappyToolchanger"
git add install.sh
git commit -m "feat: add install.sh with Moonraker Update Manager support

Installs: Klipper extras, Eddy-NG, Mainsail dist, Offset webapp
Options: --printer 250|350 for config deployment"
```

---

### Task 9: Write uninstall.sh

**Files:**
- Create: `uninstall.sh`

- [ ] **Step 1: Write uninstall.sh**

Write `D:/Claude Code/HappyToolchanger/uninstall.sh`:

```bash
#!/bin/bash
set -euo pipefail

APP_NAME="HappyToolchanger"
INSTALL_DIR="$(cd "$(dirname "$0")" && pwd)"
VENV_DIR="${HOME}/happytoolchanger-env"
KLIPPER_EXTRAS="${HOME}/klipper/klippy/extras"
MAINSAIL_DIR="${HOME}/mainsail"
MOONRAKER_CONF="${HOME}/printer_data/config/moonraker.conf"
ASVC_FILE="${HOME}/printer_data/moonraker.asvc"

echo "=== Uninstalling ${APP_NAME} ==="

# --- Klipper extras ---
echo "--- Removing Klipper extras symlinks ---"
for py_file in "${INSTALL_DIR}/klippy/extras/"*.py; do
  fname="$(basename "$py_file")"
  target="${KLIPPER_EXTRAS}/${fname}"
  if [ -L "$target" ]; then
    rm -f "$target"
    echo "  Removed: ${fname}"
  fi
done

# Eddy-NG loaders and symlink
rm -f "${KLIPPER_EXTRAS}/probe_eddy_ng.py"
rm -f "${KLIPPER_EXTRAS}/ldc1612_ng.py"
rm -rf "${KLIPPER_EXTRAS}/probe_eddy_ng"
echo "  Removed Eddy-NG loaders"

# --- Mainsail ---
if [ -f "${MAINSAIL_DIR}/.happytoolchanger" ]; then
  echo "--- Removing HappyToolchanger Mainsail ---"
  rm -rf "${MAINSAIL_DIR}"
  if [ -d "${MAINSAIL_DIR}.bak" ]; then
    mv "${MAINSAIL_DIR}.bak" "${MAINSAIL_DIR}"
    echo "  Restored backup mainsail"
  else
    echo "  WARNING: No backup found. Reinstall mainsail manually."
  fi
fi

# --- Webapp service ---
echo "--- Stopping webapp service ---"
sudo systemctl stop happytoolchanger-webapp.service 2>/dev/null || true
sudo systemctl disable happytoolchanger-webapp.service 2>/dev/null || true
sudo rm -f /etc/systemd/system/happytoolchanger-webapp.service
sudo systemctl daemon-reload

# --- Venv ---
if [ -d "${VENV_DIR}" ]; then
  rm -rf "${VENV_DIR}"
  echo "  Removed venv"
fi

# --- Moonraker ---
echo "--- Cleaning Moonraker config ---"
if [ -f "${ASVC_FILE}" ]; then
  sed -i '/^happytoolchanger-webapp$/d' "${ASVC_FILE}"
fi

if [ -f "${MOONRAKER_CONF}" ]; then
  awk '
    BEGIN{skip=0}
    /^\[update_manager HappyToolchanger\]/{skip=1; next}
    /^\[.*\]/{if(skip==1){skip=0}}
    skip==0{print}
  ' "${MOONRAKER_CONF}" > "${MOONRAKER_CONF}.tmp"
  mv "${MOONRAKER_CONF}.tmp" "${MOONRAKER_CONF}"
  echo "  Removed update_manager section"
fi

# --- Restart ---
echo "--- Restarting services ---"
sudo systemctl restart klipper 2>/dev/null || true
sudo systemctl restart moonraker 2>/dev/null || true

echo
echo "=== ${APP_NAME} uninstalled ==="
```

- [ ] **Step 2: Make executable**

```bash
chmod +x "D:/Claude Code/HappyToolchanger/uninstall.sh"
```

- [ ] **Step 3: Commit**

```bash
cd "D:/Claude Code/HappyToolchanger"
git add uninstall.sh
git commit -m "feat: add uninstall.sh"
```

---

### Task 10: Write moonraker.conf.example and README.md

**Files:**
- Create: `moonraker.conf.example`
- Create: `README.md`
- Create: `examples/z_probe_example.cfg`

- [ ] **Step 1: Write moonraker.conf.example**

Write `D:/Claude Code/HappyToolchanger/moonraker.conf.example`:

```ini
# Add this to your moonraker.conf

[update_manager HappyToolchanger]
type: git_repo
path: ~/HappyToolchanger
origin: https://github.com/daTobi1/HappyToolchanger.git
primary_branch: main
is_system_service: True
managed_services: happytoolchanger-webapp klipper
install_script: install.sh
```

- [ ] **Step 2: Write z_probe example config**

```bash
mkdir -p "D:/Claude Code/HappyToolchanger/examples"
```

Write `D:/Claude Code/HappyToolchanger/examples/z_probe_example.cfg`:

```ini
# z_probe Example Configuration
# Use this as reference for setting up Eddy/Cartographer as z_probe
# alongside Tap for crash detection and tool detection

[tool_probe T0]
pin: ^EBBT0:gpio22
tool: 0
z_probe: probe_eddy_ng my_eddy   # Eddy for BED_MESH/PROBE
z_offset: 0

[tool_probe T1]
pin: ^EBBT1:PC15
tool: 1
z_offset: 0
# No z_probe -> uses Tap for everything

[tool_z_calibrate]
probe_x: 175
probe_y: 150
samples: 5
```

- [ ] **Step 3: Write README.md**

Write `D:/Claude Code/HappyToolchanger/README.md`:

```markdown
# HappyToolchanger

Klipper Stealthchanger Monorepo: Toolchanger modules, Eddy-NG probe, Offset calibration webapp, and Mainsail with HTC Panel.

## Components

| Component | Description |
|---|---|
| `klippy/extras/` | Klipper Python modules (KTC Easy fork with z_probe routing) |
| `eddy-ng/` | BTT Eddy-NG probe with patches for toolchanger compatibility |
| `webapp/` | Offset calibration webapp (Flask, port 3000) |
| `mainsail/` | Mainsail fork with HappyHare/Toolchanger dashboard panel |
| `configs/` | Printer-specific configs (250mm/4T, 350mm/6T Stealthchanger) |

## Quick Install

```bash
cd ~
git clone https://github.com/daTobi1/HappyToolchanger.git
cd HappyToolchanger
./install.sh                    # Base install (extras, eddy-ng, mainsail, webapp)
./install.sh --printer 250      # Also deploy 250er configs
./install.sh --printer 350      # Also deploy 350er configs
```

## What install.sh does

1. Symlinks Klipper Python modules to `~/klipper/klippy/extras/`
2. Installs Eddy-NG probe loaders and package symlink
3. Deploys Mainsail dist to `~/mainsail/`
4. Sets up Offset webapp (venv, Flask, systemd service on port 3000)
5. Adds Moonraker Update Manager config for auto-updates
6. Restarts Klipper and Moonraker

## Uninstall

```bash
cd ~/HappyToolchanger
./uninstall.sh
```

## Updates

After install, updates appear in Mainsail's Update Manager. Or manually:

```bash
cd ~/HappyToolchanger
git pull
./install.sh
```

## z_probe Feature

Tools can reference an external Z probe (Eddy, Cartographer) via the `z_probe` config parameter. BED_MESH/QGL/PROBE automatically route to the best available probe. Tap remains for crash detection and tool detection. See `examples/z_probe_example.cfg`.

## License

Based on [klipper-toolchanger-easy](https://github.com/jwellman80/klipper-toolchanger-easy) and [Eddy-NG](https://github.com/Cartographer3D/eddy-ng).
```

- [ ] **Step 4: Commit**

```bash
cd "D:/Claude Code/HappyToolchanger"
git add moonraker.conf.example examples/ README.md
git commit -m "docs: add README, moonraker example, z_probe example config"
```

---

### Task 11: Push to GitHub

- [ ] **Step 1: Push**

```bash
cd "D:/Claude Code/HappyToolchanger"
git branch -M main
git push -u origin main
```

Expected: all commits pushed to `https://github.com/daTobi1/HappyToolchanger`

- [ ] **Step 2: Verify on GitHub**

```bash
gh repo view daTobi1/HappyToolchanger --web
```

---

### Task 12: Deploy to 350er and update

- [ ] **Step 1: Remove old KTC Easy symlinks on 350er**

```bash
ssh biqu@192.168.178.113 "
  # Remove old KTC Easy symlinks
  for f in toolchanger.py tool_probe.py tool_probe_endstop.py tool.py tools_calibrate.py; do
    rm -f ~/klipper/klippy/extras/\$f
  done
  # Remove old direct files
  rm -f ~/klipper/klippy/extras/tool_z_calibrate.py
  # Remove old offset symlink
  rm -f ~/klipper/klippy/extras/offset.py
  echo 'Old files removed'
"
```

- [ ] **Step 2: Clone HappyToolchanger on 350er**

```bash
ssh biqu@192.168.178.113 "
  cd ~
  git clone https://github.com/daTobi1/HappyToolchanger.git
"
```

- [ ] **Step 3: Run install on 350er**

```bash
ssh biqu@192.168.178.113 "cd ~/HappyToolchanger && ./install.sh --printer 350"
```

- [ ] **Step 4: Verify Klipper starts**

```bash
ssh biqu@192.168.178.113 "
  sleep 5
  curl -s http://localhost:7125/printer/info | python3 -m json.tool | head -10
"
```

Expected: `"state": "ready"` or `"state": "startup"`

- [ ] **Step 5: Verify symlinks**

```bash
ssh biqu@192.168.178.113 "
  ls -la ~/klipper/klippy/extras/toolchanger.py
  ls -la ~/klipper/klippy/extras/tool_probe.py
  ls -la ~/klipper/klippy/extras/tool_probe_endstop.py
  ls -la ~/klipper/klippy/extras/offset.py
"
```

Expected: all point to `~/HappyToolchanger/klippy/extras/`

- [ ] **Step 6: Verify Mainsail**

```bash
ssh biqu@192.168.178.113 "ls ~/mainsail/.happytoolchanger && cat ~/mainsail/release_info.json"
```

Expected: `.happytoolchanger` marker exists, release_info shows mainsail version

---

### Task 13: Deploy to 250er

- [ ] **Step 1: Remove old KTC Easy and Offset on 250er**

```bash
ssh biqu@192.168.178.60 "
  # Remove old KTC Easy symlinks
  for f in toolchanger.py tool_probe.py tool_probe_endstop.py tool.py tools_calibrate.py bed_thermal_adjust.py manual_rail.py multi_fan.py rounded_path.py; do
    rm -f ~/klipper/klippy/extras/\$f
  done
  rm -f ~/klipper/klippy/extras/tool_z_calibrate.py
  rm -f ~/klipper/klippy/extras/offset.py
  echo 'Old files removed'
"
```

- [ ] **Step 2: Clone HappyToolchanger on 250er**

```bash
ssh biqu@192.168.178.60 "
  cd ~
  git clone https://github.com/daTobi1/HappyToolchanger.git
"
```

- [ ] **Step 3: Run install on 250er**

```bash
ssh biqu@192.168.178.60 "cd ~/HappyToolchanger && ./install.sh --printer 250"
```

- [ ] **Step 4: Verify Klipper starts**

```bash
ssh biqu@192.168.178.60 "
  sleep 5
  curl -s http://localhost:7125/printer/info | python3 -m json.tool | head -10
"
```

Expected: `"state": "ready"` or `"state": "startup"`

- [ ] **Step 5: Stop old offset service if still running**

```bash
ssh biqu@192.168.178.60 "
  sudo systemctl stop offset.service 2>/dev/null || true
  sudo systemctl disable offset.service 2>/dev/null || true
  echo 'Old offset service stopped'
"
```

---

### Task 14: Archive old repos on GitHub

- [ ] **Step 1: Archive daTobi1/Offset**

```bash
gh repo archive daTobi1/Offset --yes
```

- [ ] **Step 2: Archive daTobi1/neweddy**

```bash
gh repo archive daTobi1/neweddy --yes
```

- [ ] **Step 3: Archive daTobi1/mainsail**

```bash
gh repo archive daTobi1/mainsail --yes
```

- [ ] **Step 4: Verify all three are archived**

```bash
gh repo list daTobi1 --limit 30 | grep -E "Offset|neweddy|mainsail"
```

Expected: all three show as archived
