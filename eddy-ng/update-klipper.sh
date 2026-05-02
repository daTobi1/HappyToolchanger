#!/bin/bash
#
# Update Klipper/Kalico while preserving eddy-ng installation.
#
# A plain "git pull" in Klipper overwrites src/Makefile, which removes
# the eddy-ng firmware patch. This script handles everything:
#
#   1. Updates eddy-ng via git pull
#   2. Updates Klipper via git pull
#   3. Re-installs eddy-ng (re-patches Makefile, re-links files)
#   4. Asks whether to rebuild/flash firmware
#   5. Restarts Klipper service
#
# Usage:
#   ./update-klipper.sh                  # Interactive update
#   ./update-klipper.sh /path/to/klipper # Custom Klipper directory
#   ./update-klipper.sh --yes            # Skip confirmations (auto-yes)
#
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
BOLD='\033[1m'
NC='\033[0m'

info()    { echo -e "${BLUE}[INFO]${NC} $*"; }
success() { echo -e "${GREEN}[OK]${NC} $*"; }
warn()    { echo -e "${YELLOW}[WARN]${NC} $*"; }
error()   { echo -e "${RED}[ERROR]${NC} $*"; }
header()  { echo -e "\n${BOLD}${CYAN}=== $* ===${NC}\n"; }

ask() {
    # ask "question" default
    # Returns 0 for yes, 1 for no
    local prompt="$1"
    local default="${2:-n}"
    if [ $AUTO_YES -eq 1 ]; then
        return 0
    fi
    local yn_hint="[y/N]"
    if [ "$default" = "y" ]; then
        yn_hint="[Y/n]"
    fi
    echo ""
    read -p "$(echo -e "${BOLD}$prompt${NC} $yn_hint ") " answer
    answer="${answer:-$default}"
    case "$answer" in
        [yY]|[yY][eE][sS]|[jJ]|[jJ][aA]) return 0 ;;
        *) return 1 ;;
    esac
}

# Parse arguments
KLIPPER_DIR=""
AUTO_YES=0

while [[ $# -gt 0 ]]; do
    case "$1" in
        --yes|-y)
            AUTO_YES=1
            shift
            ;;
        --help|-h)
            echo "Update Klipper while preserving eddy-ng installation"
            echo ""
            echo "Usage: $0 [OPTIONS] [KLIPPER_DIR]"
            echo ""
            echo "Options:"
            echo "  -y, --yes    Skip confirmations (auto-yes to all prompts)"
            echo "  -h, --help   Show this help"
            echo ""
            echo "KLIPPER_DIR defaults to ~/klipper or ~/kalico"
            exit 0
            ;;
        -*)
            error "Unknown option: $1"
            exit 1
            ;;
        *)
            KLIPPER_DIR="$1"
            shift
            ;;
    esac
done

# --- Find Klipper directory ---
if [ -z "$KLIPPER_DIR" ]; then
    if [ -d "$HOME/klipper" ]; then
        KLIPPER_DIR="$HOME/klipper"
    elif [ -d "$HOME/kalico" ]; then
        KLIPPER_DIR="$HOME/kalico"
    else
        error "No Klipper directory found. Specify path: $0 /path/to/klipper"
        exit 1
    fi
fi

if [ ! -d "$KLIPPER_DIR/.git" ]; then
    error "$KLIPPER_DIR is not a git repository"
    exit 1
fi

IS_KALICO=0
if [ -f "$KLIPPER_DIR/klippy/extras/danger_options.py" ]; then
    IS_KALICO=1
fi

FIRMWARE_NAME="Klipper"
if [ $IS_KALICO -eq 1 ]; then
    FIRMWARE_NAME="Kalico"
fi

info "Detected: $FIRMWARE_NAME at $KLIPPER_DIR"

# Track whether Klipper actually changed (for firmware rebuild prompt)
KLIPPER_CHANGED=0

# --- Step 1: Update eddy-ng ---
header "Step 1: Updating eddy-ng"

cd "$SCRIPT_DIR"
if git rev-parse --git-dir > /dev/null 2>&1; then
    OLD_EDDY=$(git rev-parse --short HEAD 2>/dev/null || echo "unknown")
    git pull --ff-only 2>/dev/null && {
        NEW_EDDY=$(git rev-parse --short HEAD 2>/dev/null || echo "unknown")
        if [ "$OLD_EDDY" = "$NEW_EDDY" ]; then
            info "eddy-ng already up to date ($OLD_EDDY)"
        else
            success "eddy-ng updated: $OLD_EDDY -> $NEW_EDDY"
        fi
    } || {
        warn "eddy-ng git pull failed (local changes?). Continuing with current version."
    }
else
    info "eddy-ng is not a git repo, skipping update"
fi

# --- Step 2: Update Klipper ---
header "Step 2: Updating $FIRMWARE_NAME"

cd "$KLIPPER_DIR"
OLD_REV=$(git rev-parse --short HEAD 2>/dev/null || echo "unknown")

# Check for uncommitted changes
if ! git diff-index --quiet HEAD -- 2>/dev/null; then
    warn "$FIRMWARE_NAME has local changes. Stashing them..."
    git stash
    STASHED=1
else
    STASHED=0
fi

if git pull --ff-only; then
    NEW_REV=$(git rev-parse --short HEAD 2>/dev/null || echo "unknown")
    if [ "$OLD_REV" = "$NEW_REV" ]; then
        info "$FIRMWARE_NAME already up to date ($OLD_REV)"
    else
        success "$FIRMWARE_NAME updated: $OLD_REV -> $NEW_REV"
        KLIPPER_CHANGED=1
    fi
else
    error "$FIRMWARE_NAME update failed. Check for merge conflicts."
    if [ $STASHED -eq 1 ]; then
        git stash pop 2>/dev/null || true
    fi
    exit 1
fi

if [ $STASHED -eq 1 ]; then
    info "Restoring stashed changes..."
    git stash pop 2>/dev/null || warn "Could not restore stash. Check manually with: cd $KLIPPER_DIR && git stash list"
fi

# --- Step 3: Re-install eddy-ng ---
header "Step 3: Re-installing eddy-ng"

# The Makefile patch is lost after git pull -- re-install restores it
"$SCRIPT_DIR/install.sh" "$KLIPPER_DIR"
success "eddy-ng re-installed (Makefile re-patched)"

# --- Step 4: Ask about firmware rebuild ---
header "Step 4: Firmware"

if [ $KLIPPER_CHANGED -eq 1 ]; then
    warn "$FIRMWARE_NAME was updated ($OLD_REV -> $NEW_REV)."
    echo -e "If the update includes MCU protocol changes, the firmware must be rebuilt."
    echo -e "You can check the Klipper changelog for details."
    echo ""
    # Check if src/ files changed (likely needs firmware rebuild)
    cd "$KLIPPER_DIR"
    SRC_CHANGES=$(git diff --name-only "$OLD_REV" "$NEW_REV" -- src/ klippy/mcu.py 2>/dev/null | wc -l)
    if [ "$SRC_CHANGES" -gt 0 ]; then
        warn "Detected $SRC_CHANGES changed files in src/ or mcu.py -- firmware rebuild likely needed!"
    else
        info "No changes in src/ or mcu.py -- firmware rebuild probably not needed."
    fi
fi

if [ ! -f "$KLIPPER_DIR/.config" ]; then
    info "No firmware .config found. Skipping firmware build."
    info "Run ./flash.sh when you want to build firmware for the first time."
else
    if ask "Rebuild MCU firmware?" "n"; then
        info "Rebuilding firmware..."

        # Ensure LDC1612 is still enabled
        if ! grep -q "CONFIG_WANT_LDC1612=y" "$KLIPPER_DIR/.config"; then
            warn "CONFIG_WANT_LDC1612 not enabled. Enabling..."
            if grep -q "CONFIG_WANT_LDC1612" "$KLIPPER_DIR/.config"; then
                sed -i 's/.*CONFIG_WANT_LDC1612.*/CONFIG_WANT_LDC1612=y/' "$KLIPPER_DIR/.config"
            else
                echo "CONFIG_WANT_LDC1612=y" >> "$KLIPPER_DIR/.config"
            fi
        fi

        # Update config for any new Klipper options
        make -C "$KLIPPER_DIR" olddefconfig

        NPROC=$(nproc 2>/dev/null || sysctl -n hw.ncpu 2>/dev/null || echo 4)
        if make -C "$KLIPPER_DIR" -j"$NPROC"; then
            success "Firmware rebuilt successfully"

            if ask "Flash firmware to MCU?" "n"; then
                "$SCRIPT_DIR/flash.sh" "$KLIPPER_DIR"
            else
                info "Firmware not flashed. Binary at: $KLIPPER_DIR/out/klipper.bin"
                info "Run ./flash.sh later to flash."
            fi
        else
            error "Firmware build failed!"
            exit 1
        fi
    else
        info "Firmware rebuild skipped."
    fi
fi

# --- Step 5: Restart Klipper ---
header "Step 5: Restarting $FIRMWARE_NAME"

if ask "Restart $FIRMWARE_NAME service?" "y"; then
    if systemctl is-active --quiet klipper 2>/dev/null; then
        sudo systemctl restart klipper
        success "$FIRMWARE_NAME service restarted"
    elif systemctl is-enabled --quiet klipper 2>/dev/null; then
        sudo systemctl start klipper
        success "$FIRMWARE_NAME service started"
    else
        warn "Klipper service not found. Restart manually."
    fi
else
    info "Service restart skipped. Remember to restart Klipper manually."
fi

# --- Summary ---
header "Update complete"
echo -e "${GREEN}$FIRMWARE_NAME and eddy-ng are up to date.${NC}"
