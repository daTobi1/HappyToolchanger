#!/bin/bash
#
# eddy-ng firmware build & flash script
#
# Usage:
#   ./flash.sh                    # Build and flash (auto-detect klipper dir)
#   ./flash.sh /path/to/klipper   # Specify klipper directory
#   ./flash.sh --build-only       # Build without flashing
#   ./flash.sh --menuconfig       # Force menuconfig before build
#   ./flash.sh --clean            # Clean build and start fresh
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
NC='\033[0m' # No Color

info()    { echo -e "${BLUE}[INFO]${NC} $*"; }
success() { echo -e "${GREEN}[OK]${NC} $*"; }
warn()    { echo -e "${YELLOW}[WARN]${NC} $*"; }
error()   { echo -e "${RED}[ERROR]${NC} $*"; }
header()  { echo -e "\n${BOLD}${CYAN}=== $* ===${NC}\n"; }

# Parse arguments
KLIPPER_DIR=""
BUILD_ONLY=0
FORCE_MENUCONFIG=0
CLEAN_BUILD=0
FLASH_DEVICE=""

while [[ $# -gt 0 ]]; do
    case "$1" in
        --build-only)
            BUILD_ONLY=1
            shift
            ;;
        --menuconfig)
            FORCE_MENUCONFIG=1
            shift
            ;;
        --clean)
            CLEAN_BUILD=1
            shift
            ;;
        --flash-device)
            FLASH_DEVICE="$2"
            shift 2
            ;;
        --help|-h)
            echo "eddy-ng firmware build & flash script"
            echo ""
            echo "Usage: $0 [OPTIONS] [KLIPPER_DIR]"
            echo ""
            echo "Options:"
            echo "  --build-only       Build firmware without flashing"
            echo "  --menuconfig       Force menuconfig before building"
            echo "  --clean            Clean build directory first"
            echo "  --flash-device DEV Specify flash device (e.g. /dev/ttyACM0)"
            echo "  -h, --help         Show this help"
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

if [ ! -d "$KLIPPER_DIR" ]; then
    error "Directory not found: $KLIPPER_DIR"
    exit 1
fi

if [ ! -f "$KLIPPER_DIR/src/Makefile" ]; then
    error "$KLIPPER_DIR does not look like a Klipper installation (no src/Makefile)"
    exit 1
fi

IS_KALICO=0
if [ -f "$KLIPPER_DIR/klippy/extras/danger_options.py" ]; then
    IS_KALICO=1
fi

if [ $IS_KALICO -eq 1 ]; then
    info "Detected: Kalico at $KLIPPER_DIR"
else
    info "Detected: Klipper at $KLIPPER_DIR"
fi

# --- Ensure eddy-ng is installed ---
header "Checking eddy-ng installation"

MAKEFILE="$KLIPPER_DIR/src/Makefile"
if ! grep -q "sensor_ldc1612_ng.c" "$MAKEFILE" 2>/dev/null; then
    warn "eddy-ng not yet installed. Running install.sh first..."
    "$SCRIPT_DIR/install.sh" "$KLIPPER_DIR"
    success "Installation complete"
else
    success "eddy-ng already installed"
fi

# --- Handle .config ---
header "Firmware configuration"

CONFIG_FILE="$KLIPPER_DIR/.config"

if [ $CLEAN_BUILD -eq 1 ]; then
    info "Cleaning build..."
    make -C "$KLIPPER_DIR" clean
    success "Build cleaned"
fi

if [ $FORCE_MENUCONFIG -eq 1 ] || [ ! -f "$CONFIG_FILE" ]; then
    if [ ! -f "$CONFIG_FILE" ]; then
        warn "No .config found -- you need to configure your MCU first."
        echo ""
        echo -e "  ${BOLD}Important:${NC} In menuconfig, make sure to:"
        echo -e "  1. Select your MCU type and communication interface"
        echo -e "  2. Enable ${BOLD}LDC1612 support${NC} under Sensors"
        echo ""
        read -p "Press Enter to start menuconfig..." _
    else
        info "Forcing menuconfig..."
    fi
    make -C "$KLIPPER_DIR" menuconfig
fi

# --- Ensure LDC1612 is enabled ---
if [ -f "$CONFIG_FILE" ]; then
    if ! grep -q "CONFIG_WANT_LDC1612=y" "$CONFIG_FILE"; then
        warn "CONFIG_WANT_LDC1612 not enabled. Enabling it..."
        # Add or update the config option
        if grep -q "CONFIG_WANT_LDC1612" "$CONFIG_FILE"; then
            sed -i 's/.*CONFIG_WANT_LDC1612.*/CONFIG_WANT_LDC1612=y/' "$CONFIG_FILE"
        else
            echo "CONFIG_WANT_LDC1612=y" >> "$CONFIG_FILE"
        fi
        # Run olddefconfig to resolve dependencies
        make -C "$KLIPPER_DIR" olddefconfig
        success "CONFIG_WANT_LDC1612 enabled"
    else
        success "CONFIG_WANT_LDC1612 is enabled"
    fi

    # Check if sensor_ldc1612_ng.c is in the Makefile
    if ! grep -q "sensor_ldc1612_ng.c" "$MAKEFILE"; then
        warn "sensor_ldc1612_ng.c not in Makefile -- re-running install..."
        "$SCRIPT_DIR/install.sh" "$KLIPPER_DIR"
    fi
else
    error ".config still not found after menuconfig. Aborting."
    exit 1
fi

# --- Show current config summary ---
info "Current firmware configuration:"
MCU_TYPE=$(grep "^CONFIG_MCU=" "$CONFIG_FILE" 2>/dev/null | cut -d'"' -f2 || echo "unknown")
BOARD=$(grep "^CONFIG_BOARD_DIRECTORY=" "$CONFIG_FILE" 2>/dev/null | cut -d'"' -f2 || echo "unknown")
echo -e "  Board: ${BOLD}$BOARD${NC}"

if grep -q "CONFIG_WANT_LDC1612=y" "$CONFIG_FILE"; then
    echo -e "  LDC1612: ${GREEN}enabled${NC}"
fi
if grep -q "CONFIG_WANT_EDDY_NG=y" "$CONFIG_FILE" 2>/dev/null; then
    echo -e "  eddy-ng: ${GREEN}enabled${NC}"
fi

# --- Build firmware ---
header "Building firmware"

NPROC=$(nproc 2>/dev/null || sysctl -n hw.ncpu 2>/dev/null || echo 4)
info "Building with $NPROC parallel jobs..."

if make -C "$KLIPPER_DIR" -j"$NPROC"; then
    success "Firmware built successfully"
    FIRMWARE="$KLIPPER_DIR/out/klipper.bin"
    if [ -f "$FIRMWARE" ]; then
        FIRMWARE_SIZE=$(stat -c%s "$FIRMWARE" 2>/dev/null || stat -f%z "$FIRMWARE" 2>/dev/null || echo "?")
        info "Firmware: $FIRMWARE ($FIRMWARE_SIZE bytes)"
    fi
else
    error "Firmware build failed!"
    exit 1
fi

if [ $BUILD_ONLY -eq 1 ]; then
    success "Build complete (--build-only mode, skipping flash)"
    echo ""
    echo "To flash manually:"
    echo "  cd $KLIPPER_DIR"
    echo "  make flash FLASH_DEVICE=/dev/serial/by-id/YOUR_DEVICE"
    exit 0
fi

# --- Flash firmware ---
header "Flashing firmware"

# Auto-detect flash device if not specified
if [ -z "$FLASH_DEVICE" ]; then
    echo "Searching for connected MCU devices..."

    # List USB serial devices
    USB_DEVICES=()
    if [ -d /dev/serial/by-id ]; then
        while IFS= read -r dev; do
            USB_DEVICES+=("$dev")
        done < <(ls /dev/serial/by-id/ 2>/dev/null | grep -i -E "klipper|stm32|rp2040|marlin|usb" || true)
    fi

    # List DFU devices
    DFU_DEVICES=()
    if command -v dfu-util &>/dev/null; then
        while IFS= read -r dev; do
            [ -n "$dev" ] && DFU_DEVICES+=("$dev")
        done < <(dfu-util -l 2>/dev/null | grep "Found DFU" || true)
    fi

    # List CAN devices
    CAN_DEVICES=()
    if command -v ip &>/dev/null; then
        while IFS= read -r dev; do
            [ -n "$dev" ] && CAN_DEVICES+=("$dev")
        done < <(ip -brief link show type can 2>/dev/null | awk '{print $1}' || true)
    fi

    echo ""
    echo -e "${BOLD}Select flash method:${NC}"
    echo ""

    OPTIONS=()
    IDX=1

    for dev in "${USB_DEVICES[@]}"; do
        echo "  $IDX) USB Serial: /dev/serial/by-id/$dev"
        OPTIONS+=("usb:/dev/serial/by-id/$dev")
        IDX=$((IDX + 1))
    done

    for dev in "${DFU_DEVICES[@]}"; do
        echo "  $IDX) DFU: $dev"
        OPTIONS+=("dfu")
        IDX=$((IDX + 1))
    done

    for dev in "${CAN_DEVICES[@]}"; do
        echo "  $IDX) CAN Bus: $dev (requires Katapult UUID)"
        OPTIONS+=("can:$dev")
        IDX=$((IDX + 1))
    done

    echo "  $IDX) Enter device path manually"
    OPTIONS+=("manual")
    IDX=$((IDX + 1))

    echo "  $IDX) SD Card -- copy firmware to SD card"
    OPTIONS+=("sdcard")
    IDX=$((IDX + 1))

    echo "  $IDX) Skip flashing"
    OPTIONS+=("skip")

    echo ""
    read -p "Choose [1-$IDX]: " CHOICE

    if [ -z "$CHOICE" ] || [ "$CHOICE" -lt 1 ] || [ "$CHOICE" -gt "$IDX" ] 2>/dev/null; then
        warn "Invalid choice, skipping flash"
        exit 0
    fi

    SELECTED="${OPTIONS[$((CHOICE - 1))]}"

    case "$SELECTED" in
        usb:*)
            FLASH_DEVICE="${SELECTED#usb:}"
            ;;
        dfu)
            info "Flashing via DFU..."
            if make -C "$KLIPPER_DIR" flash; then
                success "DFU flash complete!"
            else
                error "DFU flash failed. Make sure your board is in DFU mode."
                exit 1
            fi
            exit 0
            ;;
        can:*)
            CAN_IFACE="${SELECTED#can:}"
            echo ""
            read -p "Enter Katapult/CANboot UUID: " CAN_UUID
            if [ -z "$CAN_UUID" ]; then
                error "No UUID provided"
                exit 1
            fi
            info "Flashing via CAN ($CAN_IFACE)..."
            KATAPULT_DIR="$HOME/katapult"
            if [ ! -d "$KATAPULT_DIR" ]; then
                KATAPULT_DIR="$HOME/CanBoot"
            fi
            if [ ! -d "$KATAPULT_DIR" ]; then
                KATAPULT_DIR="$HOME/canboot"
            fi
            if [ -f "$KATAPULT_DIR/scripts/flashtool.py" ]; then
                python3 "$KATAPULT_DIR/scripts/flashtool.py" \
                    -i "$CAN_IFACE" \
                    -u "$CAN_UUID" \
                    -f "$KLIPPER_DIR/out/klipper.bin"
                success "CAN flash complete!"
            elif [ -f "$KATAPULT_DIR/scripts/flash_can.py" ]; then
                python3 "$KATAPULT_DIR/scripts/flash_can.py" \
                    -i "$CAN_IFACE" \
                    -u "$CAN_UUID" \
                    -f "$KLIPPER_DIR/out/klipper.bin"
                success "CAN flash complete!"
            else
                error "Katapult/CANboot not found at ~/katapult or ~/canboot"
                echo "Install it first: https://github.com/Arksine/katapult"
                exit 1
            fi
            exit 0
            ;;
        manual)
            echo ""
            read -p "Enter device path (e.g. /dev/ttyACM0): " FLASH_DEVICE
            if [ -z "$FLASH_DEVICE" ]; then
                error "No device path provided"
                exit 1
            fi
            ;;
        sdcard)
            echo ""
            echo "SD Card flash:"
            echo "  1. Copy $KLIPPER_DIR/out/klipper.bin to your SD card"
            echo "  2. Rename it as required by your board (e.g. firmware.bin)"
            echo "  3. Insert the SD card and power-cycle the board"
            echo ""

            # Try to find mounted SD cards
            SD_MOUNTS=$(mount 2>/dev/null | grep -i -E "sd[a-z]|mmcblk|media" | awk '{print $3}' || true)
            if [ -n "$SD_MOUNTS" ]; then
                echo "Possible SD card mount points:"
                echo "$SD_MOUNTS"
                echo ""
                read -p "Copy firmware to path (or Enter to skip): " SD_PATH
                if [ -n "$SD_PATH" ]; then
                    read -p "Filename on SD card [firmware.bin]: " SD_FILENAME
                    SD_FILENAME="${SD_FILENAME:-firmware.bin}"
                    cp "$KLIPPER_DIR/out/klipper.bin" "$SD_PATH/$SD_FILENAME"
                    success "Firmware copied to $SD_PATH/$SD_FILENAME"
                    echo "Power-cycle your board to apply the firmware."
                fi
            else
                info "No SD card detected. Copy $KLIPPER_DIR/out/klipper.bin manually."
            fi
            exit 0
            ;;
        skip)
            info "Skipping flash. Firmware is at: $KLIPPER_DIR/out/klipper.bin"
            exit 0
            ;;
    esac
fi

# --- Flash via USB serial ---
if [ -n "$FLASH_DEVICE" ]; then
    info "Flashing to $FLASH_DEVICE..."

    # Stop Klipper service before flashing
    if systemctl is-active --quiet klipper 2>/dev/null; then
        info "Stopping Klipper service..."
        sudo systemctl stop klipper
        RESTART_KLIPPER=1
    else
        RESTART_KLIPPER=0
    fi

    if make -C "$KLIPPER_DIR" flash FLASH_DEVICE="$FLASH_DEVICE"; then
        success "Firmware flashed successfully!"
    else
        error "Flash failed! Check your device connection and permissions."
        if [ $RESTART_KLIPPER -eq 1 ]; then
            sudo systemctl start klipper
        fi
        exit 1
    fi

    # Restart Klipper
    if [ $RESTART_KLIPPER -eq 1 ]; then
        info "Restarting Klipper service..."
        sudo systemctl start klipper
        success "Klipper restarted"
    fi
fi

header "Done"
echo -e "Firmware built and flashed. ${GREEN}eddy-ng is ready!${NC}"
echo ""
echo "Next steps:"
echo "  1. Check your printer.cfg (see example-printer.cfg)"
echo "  2. Restart Klipper: sudo systemctl restart klipper"
echo "  3. Run: PROBE_EDDY_NG_SETUP"
echo "  4. Run: PROBE_EDDY_NG_OPTIMIZE_DRIVE_CURRENT"
echo "  5. Run: PROBE_EDDY_NG_CALIBRATE_THRESHOLD"
echo "  6. Run: SAVE_CONFIG"
