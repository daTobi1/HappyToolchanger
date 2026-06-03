#!/bin/bash
# KlipperScreen VNC — runs a second KlipperScreen instance on a virtual display
# Accessed via noVNC at http://<printer-ip>:6080/vnc.html?autoconnect=true&resize=scale
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

# Set a visible mouse cursor for non-touch VNC clients
DISPLAY=${DISPLAY_NUM} xsetroot -cursor_name left_ptr 2>/dev/null || true

# Start second KlipperScreen instance on the virtual display
DISPLAY=${DISPLAY_NUM} ${KS_ENV} ${KS_SCRIPT} &
KS_PID=$!

# Wait for either process to exit
wait -n ${VNC_PID} ${KS_PID} 2>/dev/null || true

# Clean up
kill ${VNC_PID} ${KS_PID} 2>/dev/null || true
wait 2>/dev/null || true
