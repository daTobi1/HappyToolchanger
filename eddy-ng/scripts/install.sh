#!/bin/bash
#
# eddy-ng interactive installer
#
# Supports three sensor types:
#   1) Eddy Duo (RP2040) - patchless: pip package + pre-built firmware
#   2) Cartographer (RP2040) - patchless: pip package (no firmware flash)
#   3) Other (Eddy Coil, etc.) - traditional: pip + Klipper source patching
#
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

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

# ─── Argument parsing ───────────────────────────────────────────────────────

KLIPPER_DIR=""
KLIPPY_ENV=""
SENSOR_TYPE=""
UNINSTALL=0

usage() {
    echo "Usage: $0 [OPTIONS] [KLIPPER_DIR]"
    echo ""
    echo "Options:"
    echo "  --duo              Select Eddy Duo sensor (patchless install)"
    echo "  --cartographer     Select Cartographer (native firmware, macros only)"
    echo "  --cartographer-eddy Select Cartographer (eddy-ng firmware, full install)"
    echo "  --other            Select other Eddy sensor (traditional install)"
    echo "  -e, --klippy-env   Klippy virtualenv directory"
    echo "  -u, --uninstall    Uninstall eddy-ng"
    echo "  -h, --help         Show this help"
    exit 0
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --duo)              SENSOR_TYPE="duo"; shift ;;
        --cartographer)     SENSOR_TYPE="cartographer"; shift ;;
        --cartographer-eddy) SENSOR_TYPE="cartographer_eddy"; shift ;;
        --other)            SENSOR_TYPE="other"; shift ;;
        -e|--klippy-env) KLIPPY_ENV="$2"; shift 2 ;;
        -u|--uninstall)  UNINSTALL=1; shift ;;
        -h|--help)     usage ;;
        -*)            error "Unknown option: $1"; usage ;;
        *)             KLIPPER_DIR="$1"; shift ;;
    esac
done

# ─── Detect Klipper / Kalico ────────────────────────────────────────────────

find_klipper_dir() {
    if [ -n "$KLIPPER_DIR" ]; then
        return
    fi
    if [ -d "$HOME/klipper" ]; then
        KLIPPER_DIR="$HOME/klipper"
    elif [ -d "$HOME/kalico" ]; then
        KLIPPER_DIR="$HOME/kalico"
    else
        error "Klipper directory not found. Specify: $0 /path/to/klipper"
        exit 1
    fi
}

find_klippy_env() {
    if [ -n "$KLIPPY_ENV" ]; then
        return
    fi
    if [ -d "$HOME/klippy-env" ]; then
        KLIPPY_ENV="$HOME/klippy-env"
    elif [ -d "$HOME/kalico-env" ]; then
        KLIPPY_ENV="$HOME/kalico-env"
    else
        error "Klippy virtualenv not found. Specify: $0 --klippy-env /path/to/env"
        exit 1
    fi
}

detect_kalico() {
    IS_KALICO=0
    if [ -f "$KLIPPER_DIR/klippy/extras/danger_options.py" ]; then
        IS_KALICO=1
    fi
}

# ─── Scaffolding directory ──────────────────────────────────────────────────

get_scaffolding_dir() {
    if [ $IS_KALICO -eq 1 ] || [ -d "$KLIPPER_DIR/klippy/plugins" ]; then
        echo "$KLIPPER_DIR/klippy/plugins"
    else
        echo "$KLIPPER_DIR/klippy/extras"
    fi
}

# ─── Cleanup legacy installations ───────────────────────────────────────────

remove_legacy() {
    local scaff_dir
    scaff_dir=$(get_scaffolding_dir)
    local extras_dir="$KLIPPER_DIR/klippy/extras"

    # Remove old symlinks/files from both possible locations
    for dir in "$extras_dir" "$KLIPPER_DIR/klippy/plugins"; do
        [ ! -d "$dir" ] && continue
        for f in probe_eddy_ng.py ldc1612_ng.py; do
            if [ -L "$dir/$f" ] || [ -f "$dir/$f" ]; then
                info "Removing legacy file: $dir/$f"
                rm -f "$dir/$f"
            fi
        done
        if [ -L "$dir/probe_eddy_ng" ] || [ -d "$dir/probe_eddy_ng" ]; then
            info "Removing legacy directory: $dir/probe_eddy_ng"
            rm -rf "$dir/probe_eddy_ng"
        fi
    done

    # Remove legacy firmware symlinks
    if [ -L "$KLIPPER_DIR/src/sensor_ldc1612_ng.c" ] || [ -f "$KLIPPER_DIR/src/sensor_ldc1612_ng.c" ]; then
        info "Removing legacy firmware file: $KLIPPER_DIR/src/sensor_ldc1612_ng.c"
        rm -f "$KLIPPER_DIR/src/sensor_ldc1612_ng.c"
    fi
    if [ -L "$KLIPPER_DIR/src/extras/eddy-ng" ]; then
        info "Removing legacy firmware link: $KLIPPER_DIR/src/extras/eddy-ng"
        rm -f "$KLIPPER_DIR/src/extras/eddy-ng"
    fi
}

# ─── pip install ─────────────────────────────────────────────────────────────

install_pip_package() {
    header "Installing Python dependencies"

    # Uninstall any previous eddy-ng pip package to avoid import conflicts
    "$KLIPPY_ENV/bin/pip" uninstall -y eddy-ng 2>/dev/null || true

    # Ensure numpy is available
    "$KLIPPY_ENV/bin/python" -c "
import sys
try:
    import numpy
    version = tuple(map(int, numpy.__version__.split('.')[:2]))
    if version >= (1, 16):
        sys.exit(0)
    sys.exit(1)
except ImportError:
    sys.exit(1)
" || {
        info "Installing numpy..."
        "$KLIPPY_ENV/bin/pip" install "numpy>=1.16"
    }

    success "Python dependencies installed"
}

uninstall_pip_package() {
    info "Uninstalling eddy-ng from $KLIPPY_ENV..."
    "$KLIPPY_ENV/bin/pip" uninstall -y eddy-ng 2>/dev/null || true
    success "Python package uninstalled"
}

# ─── Scaffolding ─────────────────────────────────────────────────────────────

create_scaffolding() {
    local scaff_dir
    scaff_dir=$(get_scaffolding_dir)

    header "Creating scaffolding files"

    # Generate probe_eddy_ng.py scaffolding with repo path baked in
    cat > "$scaff_dir/probe_eddy_ng.py" << PYEOF
# eddy-ng scaffolding -- generated by install.sh
import sys
_repo_dir = "$REPO_DIR"
if _repo_dir not in sys.path:
    sys.path.insert(0, _repo_dir)
from probe_eddy_ng import load_config_prefix  # noqa: F401
PYEOF
    success "Created $scaff_dir/probe_eddy_ng.py"

    # Generate ldc1612_ng.py scaffolding with repo path baked in
    cat > "$scaff_dir/ldc1612_ng.py" << PYEOF
# eddy-ng scaffolding -- generated by install.sh
import sys
_repo_dir = "$REPO_DIR"
if _repo_dir not in sys.path:
    sys.path.insert(0, _repo_dir)
from ldc1612_ng import *  # noqa: F401,F403
PYEOF
    success "Created $scaff_dir/ldc1612_ng.py"

    # Add to .git/info/exclude (for stock Klipper only, not Kalico plugins dir)
    if [ $IS_KALICO -eq 0 ] && [ -d "$KLIPPER_DIR/.git" ]; then
        local exclude_file="$KLIPPER_DIR/.git/info/exclude"
        for f in "klippy/extras/probe_eddy_ng.py" "klippy/extras/ldc1612_ng.py"; do
            if ! grep -qF "$f" "$exclude_file" 2>/dev/null; then
                echo "$f" >> "$exclude_file"
            fi
        done
        info "Added scaffolding to git exclude"
    fi
}

remove_scaffolding() {
    local scaff_dir
    scaff_dir=$(get_scaffolding_dir)

    for f in probe_eddy_ng.py ldc1612_ng.py; do
        if [ -f "$scaff_dir/$f" ]; then
            rm -f "$scaff_dir/$f"
            info "Removed $scaff_dir/$f"
        fi
    done
}

# ─── Config files ────────────────────────────────────────────────────────────

get_config_dir() {
    # Detect the Klipper config directory (printer_data/config or similar)
    if [ -d "$HOME/printer_data/config" ]; then
        echo "$HOME/printer_data/config"
    elif [ -d "$HOME/klipper_config" ]; then
        echo "$HOME/klipper_config"
    else
        echo ""
    fi
}

install_config_files_eddy() {
    local config_dir
    config_dir=$(get_config_dir)

    if [ -z "$config_dir" ]; then
        warn "Could not detect Klipper config directory."
        warn "Manually copy calibrate_macros.cfg to your config directory."
        return
    fi

    header "Installing config files (eddy-ng)"

    # Copy calibration macros
    cp "$REPO_DIR/calibrate_macros.cfg" "$config_dir/calibrate_macros.cfg"
    success "Copied calibrate_macros.cfg to $config_dir/"

    # Copy example config if no eddy-ng config exists yet
    if [ ! -f "$config_dir/eddy-ng.cfg" ]; then
        cp "$REPO_DIR/example-printer.cfg" "$config_dir/eddy-ng.cfg"
        success "Copied example config to $config_dir/eddy-ng.cfg"
        warn "Edit eddy-ng.cfg with your sensor settings (MCU, offsets, etc.)"
    else
        info "eddy-ng.cfg already exists, skipping"
    fi

    # Check if includes are in printer.cfg
    local printer_cfg="$config_dir/printer.cfg"
    if [ -f "$printer_cfg" ]; then
        local needs_include=0
        if ! grep -qF "[include eddy-ng.cfg]" "$printer_cfg" && \
           ! grep -qF "include.*eddy" "$printer_cfg" 2>/dev/null; then
            needs_include=1
        fi
        if ! grep -qF "[include calibrate_macros.cfg]" "$printer_cfg"; then
            needs_include=1
        fi

        if [ $needs_include -eq 1 ]; then
            echo ""
            warn "Add these lines to your printer.cfg if not already present:"
            echo ""
            echo "  [include eddy-ng.cfg]"
            echo "  [include calibrate_macros.cfg]"
            echo ""
        fi
    fi
}

install_config_files_cartographer() {
    local config_dir
    config_dir=$(get_config_dir)

    if [ -z "$config_dir" ]; then
        warn "Could not detect Klipper config directory."
        warn "Manually copy calibrate_macros_cartographer.cfg to your config directory."
        return
    fi

    header "Installing config files (Cartographer)"

    # Copy Cartographer calibration macros
    cp "$REPO_DIR/calibrate_macros_cartographer.cfg" "$config_dir/calibrate_macros_cartographer.cfg"
    success "Copied calibrate_macros_cartographer.cfg to $config_dir/"

    # Check if includes are in printer.cfg
    local printer_cfg="$config_dir/printer.cfg"
    if [ -f "$printer_cfg" ]; then
        if ! grep -qF "[include calibrate_macros_cartographer.cfg]" "$printer_cfg"; then
            echo ""
            warn "Add this line to your printer.cfg:"
            echo ""
            echo "  [include calibrate_macros_cartographer.cfg]"
            echo ""
        fi

        # Check for axis_twist_compensation
        if ! grep -qE "^\[axis_twist_compensation\]" "$printer_cfg"; then
            echo ""
            warn "For axis twist calibration (step 5), add to your printer.cfg:"
            echo ""
            echo "  [axis_twist_compensation]"
            echo ""
        fi
    fi
}

remove_config_files() {
    local config_dir
    config_dir=$(get_config_dir)

    if [ -n "$config_dir" ]; then
        if [ -f "$config_dir/calibrate_macros.cfg" ]; then
            rm -f "$config_dir/calibrate_macros.cfg"
            info "Removed $config_dir/calibrate_macros.cfg"
        fi
        if [ -f "$config_dir/calibrate_macros_cartographer.cfg" ]; then
            rm -f "$config_dir/calibrate_macros_cartographer.cfg"
            info "Removed $config_dir/calibrate_macros_cartographer.cfg"
        fi
        # Don't remove eddy-ng.cfg on uninstall -- it has user customizations
    fi
}

# ─── Firmware: Makefile patching (for non-Duo sensors) ───────────────────────

install_firmware_patch() {
    header "Installing firmware module (Makefile patching)"

    local makefile="$KLIPPER_DIR/src/Makefile"

    if [ $IS_KALICO -eq 1 ]; then
        # Kalico: symlink the firmware extras directory
        local fw_dest="$KLIPPER_DIR/src/extras/eddy-ng"
        if [ -L "$fw_dest" ] || [ -d "$fw_dest" ]; then
            rm -rf "$fw_dest"
        fi
        ln -s "$REPO_DIR/eddy-ng" "$fw_dest"
        success "Firmware module linked to $fw_dest"
        info "In menuconfig, enable eddy-ng under firmware extras."
    else
        # Stock Klipper: symlink C file + patch Makefile
        local src_file="$KLIPPER_DIR/src/sensor_ldc1612_ng.c"
        local real_src="$REPO_DIR/eddy-ng/sensor_ldc1612_ng.c"

        if [ -L "$src_file" ] || [ -f "$src_file" ]; then
            rm -f "$src_file"
        fi
        ln -s "$(realpath "$real_src")" "$src_file"
        success "Firmware C file linked"

        if ! grep -q "sensor_ldc1612_ng.c" "$makefile" 2>/dev/null; then
            sed -i 's,sensor_ldc1612.c$,sensor_ldc1612.c sensor_ldc1612_ng.c,' "$makefile"
            success "Makefile patched"
        else
            success "Makefile already patched"
        fi
    fi

    echo ""
    warn "You need to rebuild and flash your printer's MCU firmware."
    echo "  Run: $REPO_DIR/flash.sh"
}

remove_firmware_patch() {
    local makefile="$KLIPPER_DIR/src/Makefile"
    if [ -f "$makefile" ]; then
        sed -i 's, sensor_ldc1612_ng.c,,' "$makefile"
        info "Makefile unpatched"
    fi
}

# ─── Duo: pre-built firmware flash ──────────────────────────────────────────

offer_duo_flash() {
    echo ""
    echo -e "${BOLD}Firmware for Eddy Duo${NC}"
    echo ""
    echo "Your Eddy Duo has its own RP2040 MCU. You can flash it with"
    echo "a pre-built firmware image (no Klipper source patching needed)."
    echo ""
    read -p "Flash Eddy Duo firmware now? [y/N]: " do_flash

    if [[ "$do_flash" =~ ^[yYjJ]$ ]]; then
        "$SCRIPT_DIR/flash-duo.sh" ${KLIPPER_DIR:+"$KLIPPER_DIR"}
    else
        echo ""
        info "You can flash later with: $SCRIPT_DIR/flash-duo.sh"
    fi
}

# ─── Uninstall ───────────────────────────────────────────────────────────────

do_uninstall() {
    header "Uninstalling eddy-ng"
    remove_legacy
    remove_scaffolding
    remove_config_files
    remove_firmware_patch
    uninstall_pip_package
    success "eddy-ng uninstalled"
}

# ─── Install flows ──────────────────────────────────────────────────────────

install_duo() {
    header "Installing eddy-ng for Eddy Duo (patchless)"

    remove_legacy
    install_pip_package
    create_scaffolding
    install_config_files_eddy

    success "Python plugin installed (no Klipper source patching needed)"
    offer_duo_flash

    header "Done"
    echo -e "${GREEN}eddy-ng installed for Eddy Duo!${NC}"
    echo ""
    echo "Next steps:"
    echo "  1. Make sure your Eddy Duo firmware is flashed"
    echo "  2. Edit eddy-ng.cfg with your sensor settings (MCU UUID, offsets, etc.)"
    echo "  3. Restart Klipper: sudo systemctl restart klipper"
    echo "  4. Run calibration: EDDY_NG_SETUP_1VON7"
    echo "     Or automatic:    $REPO_DIR/scripts/calibrate.sh"
}

install_cartographer_native() {
    header "Installing calibration macros for Cartographer (native firmware)"

    install_config_files_cartographer

    header "Done"
    echo -e "${GREEN}Cartographer calibration macros installed!${NC}"
    echo ""
    echo "This installs only the calibration macro workflow."
    echo "Your Cartographer probe continues to use its native firmware."
    echo ""
    echo "Next steps:"
    echo "  1. Add [include calibrate_macros_cartographer.cfg] to printer.cfg"
    echo "  2. Add [axis_twist_compensation] to printer.cfg (for axis twist)"
    echo "  3. Edit _CARTO_SETTINGS in calibrate_macros_cartographer.cfg"
    echo "  4. Restart Klipper: sudo systemctl restart klipper"
    echo "  5. Run calibration: CARTO_SCAN_CALIBRATE_1VON7"
}

install_cartographer_eddy() {
    header "Installing eddy-ng for Cartographer (replaces native firmware)"

    echo ""
    warn "This replaces the Cartographer's native firmware with Klipper firmware."
    warn "The Cartographer v3 (STM32F042) has only 32KB flash and may not"
    warn "have enough space for eddy-ng. The Cartographer v4 (STM32G431) works."
    echo ""
    read -p "Continue? [y/N]: " confirm
    if [[ ! "$confirm" =~ ^[yYjJ]$ ]]; then
        info "Aborted."
        return
    fi

    remove_legacy
    install_pip_package
    create_scaffolding
    install_config_files_eddy

    success "Python plugin installed (no Klipper source patching needed)"

    header "Done"
    echo -e "${GREEN}eddy-ng installed for Cartographer!${NC}"
    echo ""
    echo "Next steps:"
    echo "  1. Flash Cartographer with Klipper firmware (requires custom build)"
    echo "  2. Edit eddy-ng.cfg: set sensor_type: cartographer, MCU UUID, offsets"
    echo "  3. Restart Klipper: sudo systemctl restart klipper"
    echo "  4. Run calibration: EDDY_NG_SETUP_1VON7"
}

install_other() {
    header "Installing eddy-ng for Eddy sensor (traditional)"

    remove_legacy
    install_pip_package
    create_scaffolding
    install_config_files_eddy
    install_firmware_patch

    header "Done"
    echo -e "${GREEN}eddy-ng installed!${NC}"
    echo ""
    echo "Next steps:"
    echo "  1. Rebuild and flash your MCU firmware: $REPO_DIR/flash.sh"
    echo "  2. Edit eddy-ng.cfg with your sensor settings (offsets, etc.)"
    echo "  3. Restart Klipper: sudo systemctl restart klipper"
    echo "  4. Run calibration: EDDY_NG_SETUP_1VON7"
    echo "     Or automatic:    $REPO_DIR/scripts/calibrate.sh"
}

# ─── Main ────────────────────────────────────────────────────────────────────

main() {
    find_klipper_dir
    find_klippy_env
    detect_kalico

    if [ $IS_KALICO -eq 1 ]; then
        info "Detected: Kalico at $KLIPPER_DIR"
    else
        info "Detected: Klipper at $KLIPPER_DIR"
    fi
    info "Virtualenv: $KLIPPY_ENV"

    if [ $UNINSTALL -eq 1 ]; then
        do_uninstall
        return
    fi

    # Sensor selection (interactive or via flag)
    if [ -z "$SENSOR_TYPE" ]; then
        header "eddy-ng Installer"
        echo "Which probe do you have?"
        echo ""
        echo -e "  ${BOLD}1)${NC} BTT Eddy Duo (RP2040-based, USB or CAN bus)"
        echo "     Patchless install: pip package + pre-built firmware"
        echo ""
        echo -e "  ${BOLD}2)${NC} Cartographer — keep native firmware (recommended)"
        echo "     Installs calibration macros only (no firmware change)"
        echo ""
        echo -e "  ${BOLD}3)${NC} Cartographer — replace with eddy-ng firmware"
        echo "     Full eddy-ng install (requires firmware flash, v4+ recommended)"
        echo ""
        echo -e "  ${BOLD}4)${NC} Other (Eddy Coil, generic LDC1612, Mellow Fly, etc.)"
        echo "     Traditional install: pip package + Klipper source patching"
        echo ""
        read -p "Choose [1-4]: " choice

        case "$choice" in
            1) SENSOR_TYPE="duo" ;;
            2) SENSOR_TYPE="cartographer" ;;
            3) SENSOR_TYPE="cartographer_eddy" ;;
            4) SENSOR_TYPE="other" ;;
            *) error "Invalid choice"; exit 1 ;;
        esac
    fi

    case "$SENSOR_TYPE" in
        duo)                  install_duo ;;
        cartographer)         install_cartographer_native ;;
        cartographer_eddy)    install_cartographer_eddy ;;
        other)                install_other ;;
        *)                    error "Invalid sensor type: $SENSOR_TYPE"; exit 1 ;;
    esac
}

main
