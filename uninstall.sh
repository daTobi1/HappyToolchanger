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
