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
LOADER_PROBE="${KLIPPER_EXTRAS}/probe_eddy_ng.py"
cat > "$LOADER_PROBE" <<PYEOF
import importlib, sys, os
sys.path.insert(0, "${EDDY_DIR}")
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
sys.path.insert(0, "${EDDY_DIR}")
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
