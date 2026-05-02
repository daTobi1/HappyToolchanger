#!/bin/bash
#
# eddy-ng Eddy Duo firmware flash script
#
# Flashes pre-built firmware images to the Eddy Duo's RP2040 MCU.
# No Klipper source patching required.
#
# Usage:
#   ./flash-duo.sh                     # Interactive
#   ./flash-duo.sh --build             # Build from source instead of using pre-built
#   ./flash-duo.sh /path/to/klipper    # Specify Klipper dir (for --build mode)
#
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
FIRMWARE_DIR="$REPO_DIR/firmware"

# ── BTT Eddy Duo default CAN GPIO pins ─────────────────────────────
# Override with environment variables if your hardware differs.
CAN_TX_GPIO="${EDDY_CAN_TX_GPIO:-1}"
CAN_RX_GPIO="${EDDY_CAN_RX_GPIO:-0}"

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

BUILD_MODE=0
KLIPPER_DIR=""

while [[ $# -gt 0 ]]; do
    case "$1" in
        --build)    BUILD_MODE=1; shift ;;
        -h|--help)
            echo "Usage: $0 [OPTIONS] [KLIPPER_DIR]"
            echo ""
            echo "Options:"
            echo "  --build   Build firmware from source (requires Klipper source tree)"
            echo "  -h        Show this help"
            echo ""
            echo "Environment variables:"
            echo "  EDDY_CAN_TX_GPIO   CAN TX GPIO pin (default: 1)"
            echo "  EDDY_CAN_RX_GPIO   CAN RX GPIO pin (default: 0)"
            exit 0
            ;;
        -*) error "Unknown option: $1"; exit 1 ;;
        *)  KLIPPER_DIR="$1"; shift ;;
    esac
done

# ─── Build from source ──────────────────────────────────────────────────────

build_from_source() {
    header "Building Eddy Duo firmware from source"

    # Find Klipper
    if [ -z "$KLIPPER_DIR" ]; then
        if [ -d "$HOME/klipper" ]; then
            KLIPPER_DIR="$HOME/klipper"
        elif [ -d "$HOME/kalico" ]; then
            KLIPPER_DIR="$HOME/kalico"
        else
            error "Klipper directory not found. Specify: $0 --build /path/to/klipper"
            exit 1
        fi
    fi

    info "Using Klipper at: $KLIPPER_DIR"

    # Temporarily patch Makefile if not already patched
    local makefile="$KLIPPER_DIR/src/Makefile"
    local was_patched=0
    local src_file="$KLIPPER_DIR/src/sensor_ldc1612_ng.c"

    if grep -q "sensor_ldc1612_ng.c" "$makefile" 2>/dev/null; then
        was_patched=1
    fi

    # Link C file if needed
    if [ ! -f "$src_file" ] && [ ! -L "$src_file" ]; then
        ln -s "$(realpath "$REPO_DIR/eddy-ng/sensor_ldc1612_ng.c")" "$src_file"
    fi

    # Patch Makefile if needed
    if [ $was_patched -eq 0 ]; then
        sed -i 's,sensor_ldc1612.c$,sensor_ldc1612.c sensor_ldc1612_ng.c,' "$makefile"
    fi

    # Use preselected values from select_prebuilt if available, otherwise ask
    local conn_choice="${PRESELECTED_CONN:-}"
    local can_freq="${PRESELECTED_CAN_FREQ:-}"
    local flash_offset="${PRESELECTED_FLASH_OFFSET:-0x10000100}"
    local preselected_pins=0
    if [[ -n "${PRESELECTED_CAN_TX:-}" ]]; then
        CAN_TX_GPIO="$PRESELECTED_CAN_TX"
        CAN_RX_GPIO="$PRESELECTED_CAN_RX"
        preselected_pins=1
    fi

    if [ -z "$conn_choice" ]; then
        # ── Step 1: Connection type ──
        echo ""
        echo -e "${BOLD}Connection type:${NC}"
        echo ""
        echo "  1) USB"
        echo "  2) CAN bus"
        echo ""
        read -p "Choose [1-2]: " conn_choice

        if [[ "$conn_choice" == "2" ]]; then
            # ── Step 2: CAN baud rate ──
            echo ""
            echo -e "${BOLD}CAN bus baud rate:${NC}"
            echo ""
            echo "  1) 500 kbit/s"
            echo "  2) 1 Mbit/s"
            echo "  3) Custom"
            echo ""
            read -p "Choose [1-3]: " baud_choice

            case "$baud_choice" in
                1) can_freq="500000" ;;
                2) can_freq="1000000" ;;
                3)
                    read -p "Enter baud rate in bit/s (e.g. 250000): " can_freq
                    if ! [[ "$can_freq" =~ ^[0-9]+$ ]]; then
                        error "Invalid baud rate: $can_freq"
                        exit 1
                    fi
                    ;;
                *) error "Invalid choice"; exit 1 ;;
            esac

            # ── Step 3: Bootloader offset ──
            echo ""
            echo -e "${BOLD}Bootloader offset:${NC}"
            echo ""
            echo "  1) No bootloader (0x10000100)"
            echo "  2) Katapult (0x10004000)"
            echo "  3) Custom"
            echo ""
            read -p "Choose [1-3]: " offset_choice

            case "$offset_choice" in
                1) flash_offset="0x10000100" ;;
                2) flash_offset="0x10004000" ;;
                3)
                    read -p "Enter flash application address (hex, e.g. 0x10008000): " flash_offset
                    if ! [[ "$flash_offset" =~ ^0x[0-9a-fA-F]+$ ]]; then
                        error "Invalid hex address: $flash_offset"
                        exit 1
                    fi
                    ;;
                *) error "Invalid choice"; exit 1 ;;
            esac
        else
            # USB: still ask for bootloader offset
            echo ""
            echo -e "${BOLD}Bootloader offset:${NC}"
            echo ""
            echo "  1) No bootloader (0x10000100)"
            echo "  2) Katapult (0x10004000)"
            echo "  3) Custom"
            echo ""
            read -p "Choose [1-3]: " offset_choice

            case "$offset_choice" in
                1) flash_offset="0x10000100" ;;
                2) flash_offset="0x10004000" ;;
                3)
                    read -p "Enter flash application address (hex, e.g. 0x10008000): " flash_offset
                    if ! [[ "$flash_offset" =~ ^0x[0-9a-fA-F]+$ ]]; then
                        error "Invalid hex address: $flash_offset"
                        exit 1
                    fi
                    ;;
                *) error "Invalid choice"; exit 1 ;;
            esac
        fi
    fi

    local config_file="$KLIPPER_DIR/.config"

    # Write base config for RP2040
    cat > "$config_file" << EOF
CONFIG_LOW_LEVEL_OPTIONS=y
CONFIG_MACH_RP2040=y
CONFIG_BOARD_DIRECTORY="rp2040"
CONFIG_MCU="rp2040"
CONFIG_CLOCK_FREQ=12000000
CONFIG_FLASH_SIZE=0x200000
CONFIG_RAM_START=0x20000000
CONFIG_RAM_SIZE=0x42000
CONFIG_STACK_SIZE=512
CONFIG_RP2040_HAVE_STAGE2=y
CONFIG_WANT_LDC1612=y
CONFIG_WANT_SENSOR_BULK=y
CONFIG_WANT_SOFTWARE_I2C=y
EOF

    case "$conn_choice" in
        1)
            info "Configuring for USB..."
            cat >> "$config_file" << EOF
CONFIG_FLASH_APPLICATION_ADDRESS=${flash_offset}
CONFIG_USB=y
CONFIG_USB_VENDOR_ID=0x1d50
CONFIG_USB_DEVICE_ID=0x614e
CONFIG_USB_SERIAL_NUMBER_CHIPID=y
EOF
            ;;
        2)
            info "Configuring for CAN bus (${can_freq} bit/s)..."

            # CAN GPIO pins -- only ask if not already set
            if [[ $preselected_pins -eq 0 ]]; then
                echo ""
                echo -e "${BOLD}CAN GPIO pins:${NC}"
                echo ""
                echo "  1) BTT Eddy Duo default (TX=GPIO1, RX=GPIO0)"
                echo "  2) Custom"
                echo ""
                read -p "Choose [1-2]: " pin_choice

                case "$pin_choice" in
                    1) CAN_TX_GPIO=1; CAN_RX_GPIO=0 ;;
                    2)
                        read -p "CAN TX GPIO pin: " CAN_TX_GPIO
                        read -p "CAN RX GPIO pin: " CAN_RX_GPIO
                        if ! [[ "$CAN_TX_GPIO" =~ ^[0-9]+$ && "$CAN_RX_GPIO" =~ ^[0-9]+$ ]]; then
                            error "Invalid GPIO pin numbers"
                            exit 1
                        fi
                        ;;
                    *) error "Invalid choice"; exit 1 ;;
                esac
            fi

            cat >> "$config_file" << EOF
CONFIG_FLASH_APPLICATION_ADDRESS=${flash_offset}
CONFIG_CANSERIAL=y
CONFIG_CANBUS_FREQUENCY=${can_freq}
CONFIG_RP2040_CANBUS_GPIO_TX=${CAN_TX_GPIO}
CONFIG_RP2040_CANBUS_GPIO_RX=${CAN_RX_GPIO}
EOF
            ;;
        *)
            error "Invalid choice"
            exit 1
            ;;
    esac

    # Show config summary
    echo ""
    info "Firmware configuration:"
    echo -e "  MCU:           ${BOLD}RP2040${NC}"
    if [[ "$conn_choice" == "1" ]]; then
        echo -e "  Connection:    ${BOLD}USB${NC}"
    else
        echo -e "  Connection:    ${BOLD}CAN bus (${can_freq} bit/s)${NC}"
        echo -e "  CAN TX pin:    ${BOLD}GPIO${CAN_TX_GPIO}${NC}"
        echo -e "  CAN RX pin:    ${BOLD}GPIO${CAN_RX_GPIO}${NC}"
    fi
    echo -e "  Flash offset:  ${BOLD}${flash_offset}${NC}"
    if [[ "$flash_offset" != "0x10000100" ]]; then
        echo -e "  Bootloader:    ${BOLD}yes ($flash_offset)${NC}"
    else
        echo -e "  Bootloader:    ${BOLD}none${NC}"
    fi
    echo ""

    # Resolve dependencies
    make -C "$KLIPPER_DIR" olddefconfig

    # Build
    local nproc
    nproc=$(nproc 2>/dev/null || echo 4)
    info "Building firmware..."
    make -C "$KLIPPER_DIR" -j"$nproc"
    success "Firmware built successfully"

    # Set firmware path for flashing
    if [ -f "$KLIPPER_DIR/out/klipper.uf2" ]; then
        FW_PATH="$KLIPPER_DIR/out/klipper.uf2"
        info "Output: $FW_PATH"
    elif [ -f "$KLIPPER_DIR/out/klipper.bin" ]; then
        FW_PATH="$KLIPPER_DIR/out/klipper.bin"
        info "Output: $FW_PATH"
    else
        error "Firmware output not found"
        exit 1
    fi

    # Clean up temporary patch if we applied it
    if [ $was_patched -eq 0 ]; then
        sed -i 's, sensor_ldc1612_ng.c,,' "$makefile"
        rm -f "$src_file"
        info "Cleaned up temporary Makefile patch"
    fi

    # If CAN was selected, ask for UUID now
    if [[ "$conn_choice" != "1" ]] && [ -z "$CAN_UUID" ]; then
        query_can_uuid || true
    fi
}

# ─── Pre-built firmware selection ────────────────────────────────────────────

select_prebuilt() {
    header "Select Eddy Duo firmware"

    # ── Step 1: Connection type ──
    echo -e "${BOLD}Connection type:${NC}"
    echo ""
    echo "  1) USB"
    echo "  2) CAN bus"
    echo ""
    read -p "Choose [1-2]: " conn_choice

    local can_freq=""
    local custom_baud=""

    case "$conn_choice" in
        1) ;;
        2)
            # ── Step 2: CAN baud rate ──
            echo ""
            echo -e "${BOLD}CAN bus baud rate:${NC}"
            echo ""
            echo "  1) 500 kbit/s"
            echo "  2) 1 Mbit/s"
            echo "  3) Custom"
            echo ""
            read -p "Choose [1-3]: " baud_choice

            case "$baud_choice" in
                1) can_freq="500000" ;;
                2) can_freq="1000000" ;;
                3)
                    read -p "Enter baud rate in bit/s (e.g. 250000): " custom_baud
                    can_freq="$custom_baud"
                    if ! [[ "$can_freq" =~ ^[0-9]+$ ]]; then
                        error "Invalid baud rate: $can_freq"
                        exit 1
                    fi
                    ;;
                *) error "Invalid choice"; exit 1 ;;
            esac
            ;;
        *) error "Invalid choice"; exit 1 ;;
    esac

    # ── Step 3: Bootloader offset ──
    echo ""
    echo -e "${BOLD}Bootloader offset:${NC}"
    echo ""
    echo "  1) No bootloader (0x10000100)"
    echo "  2) Katapult (0x10004000)"
    echo "  3) Custom"
    echo ""
    read -p "Choose [1-3]: " offset_choice

    local flash_offset="0x10000100"
    local use_katapult=0

    case "$offset_choice" in
        1) flash_offset="0x10000100" ;;
        2) flash_offset="0x10004000"; use_katapult=1 ;;
        3)
            read -p "Enter flash application address (hex, e.g. 0x10008000): " flash_offset
            if ! [[ "$flash_offset" =~ ^0x[0-9a-fA-F]+$ ]]; then
                error "Invalid hex address: $flash_offset"
                exit 1
            fi
            use_katapult=1
            ;;
        *) error "Invalid choice"; exit 1 ;;
    esac

    # ── Determine if pre-built firmware is available ──
    local needs_build=0

    # Custom baud rate → must build from source
    if [[ -n "$custom_baud" ]]; then
        info "Custom baud rate ${can_freq} -- building from source required."
        needs_build=1
    fi

    # Custom bootloader offset (not 0x10000100 and not 0x10004000) → must build
    if [[ "$flash_offset" != "0x10000100" && "$flash_offset" != "0x10004000" ]]; then
        info "Custom bootloader offset ${flash_offset} -- building from source required."
        needs_build=1
    fi

    # USB + Katapult → no pre-built available
    if [[ "$conn_choice" == "1" && $use_katapult -eq 1 ]]; then
        info "USB with bootloader offset -- building from source required."
        needs_build=1
    fi

    if [[ $needs_build -eq 1 ]]; then
        # Pass collected settings to build_from_source
        PRESELECTED_CONN="$conn_choice"
        PRESELECTED_CAN_FREQ="$can_freq"
        PRESELECTED_FLASH_OFFSET="$flash_offset"
        build_from_source
        return
    fi

    # ── Step 4: CAN GPIO pins (CAN only) ──
    local custom_pins=0
    if [[ "$conn_choice" == "2" ]]; then
        echo ""
        echo -e "${BOLD}CAN GPIO pins:${NC}"
        echo ""
        echo "  1) BTT Eddy Duo default (TX=GPIO1, RX=GPIO0)"
        echo "  2) Custom"
        echo ""
        read -p "Choose [1-2]: " pin_choice

        case "$pin_choice" in
            1) CAN_TX_GPIO=1; CAN_RX_GPIO=0 ;;
            2)
                read -p "CAN TX GPIO pin: " CAN_TX_GPIO
                read -p "CAN RX GPIO pin: " CAN_RX_GPIO
                if ! [[ "$CAN_TX_GPIO" =~ ^[0-9]+$ && "$CAN_RX_GPIO" =~ ^[0-9]+$ ]]; then
                    error "Invalid GPIO pin numbers"
                    exit 1
                fi
                custom_pins=1
                ;;
            *) error "Invalid choice"; exit 1 ;;
        esac

        # Custom pins → must build from source
        if [[ $custom_pins -eq 1 ]]; then
            info "Custom CAN pins -- building from source required."
            PRESELECTED_CONN="$conn_choice"
            PRESELECTED_CAN_FREQ="$can_freq"
            PRESELECTED_FLASH_OFFSET="$flash_offset"
            PRESELECTED_CAN_TX="$CAN_TX_GPIO"
            PRESELECTED_CAN_RX="$CAN_RX_GPIO"
            build_from_source
            return
        fi
    fi

    # ── Select pre-built firmware file ──
    if [[ "$conn_choice" == "1" ]]; then
        FW_FILE="eddy-duo-usb.uf2"
    else
        if [[ "$can_freq" == "500000" ]]; then
            if [[ $use_katapult -eq 1 ]]; then
                FW_FILE="eddy-duo-katapult-canbus-500k.bin"
            else
                FW_FILE="eddy-duo-canbus-500k.uf2"
            fi
        else
            if [[ $use_katapult -eq 1 ]]; then
                FW_FILE="eddy-duo-katapult-canbus-1m.bin"
            else
                FW_FILE="eddy-duo-canbus-1m.uf2"
            fi
        fi

        if [[ $use_katapult -eq 1 ]]; then
            warn "Using Katapult bootloader offset: $flash_offset"
            warn "Make sure Katapult is already flashed on the RP2040."
        fi
    fi

    # ── Show summary ──
    echo ""
    info "Firmware configuration:"
    if [[ "$conn_choice" == "1" ]]; then
        echo -e "  Connection:    ${BOLD}USB${NC}"
    else
        local freq_display="500k"
        [[ "$can_freq" == "1000000" ]] && freq_display="1M"
        echo -e "  Connection:    ${BOLD}CAN bus ${freq_display}${NC}"
        echo -e "  CAN TX pin:    ${BOLD}GPIO${CAN_TX_GPIO}${NC}"
        echo -e "  CAN RX pin:    ${BOLD}GPIO${CAN_RX_GPIO}${NC}"
    fi
    echo -e "  Bootloader:    ${BOLD}$([ $use_katapult -eq 1 ] && echo "Katapult ($flash_offset)" || echo "none")${NC}"
    echo -e "  Firmware:      ${BOLD}${FW_FILE}${NC}"
    echo ""

    FW_PATH="$FIRMWARE_DIR/$FW_FILE"

    # Check if firmware exists locally
    if [ ! -f "$FW_PATH" ]; then
        # Also try .uf2 <-> .bin
        local alt_ext
        if [[ "$FW_FILE" == *.uf2 ]]; then
            alt_ext="${FW_FILE%.uf2}.bin"
        else
            alt_ext="${FW_FILE%.bin}.uf2"
        fi
        if [ -f "$FIRMWARE_DIR/$alt_ext" ]; then
            FW_FILE="$alt_ext"
            FW_PATH="$FIRMWARE_DIR/$FW_FILE"
            info "Using alternative format: $FW_FILE"
        else
            warn "Pre-built firmware '$FW_FILE' not found locally."
            echo ""
            echo "Options:"
            echo "  1) Build from source"
            echo "  2) Cancel"
            echo ""
            read -p "Choose [1-2]: " fallback

            case "$fallback" in
                1) build_from_source; return ;;
                *) exit 0 ;;
            esac
        fi
    fi

    info "Selected firmware: $FW_PATH"
}

# ─── Katapult lookup ─────────────────────────────────────────────────────────

find_katapult() {
    KATAPULT_DIR=""
    for dir in "$HOME/katapult" "$HOME/Katapult" "$HOME/CanBoot" "$HOME/canboot"; do
        if [ -d "$dir" ]; then
            KATAPULT_DIR="$dir"
            break
        fi
    done

    if [ -z "$KATAPULT_DIR" ]; then
        return 1
    fi

    FLASH_SCRIPT=""
    for script in "scripts/flashtool.py" "scripts/flash_can.py"; do
        if [ -f "$KATAPULT_DIR/$script" ]; then
            FLASH_SCRIPT="$KATAPULT_DIR/$script"
            break
        fi
    done

    if [ -z "$FLASH_SCRIPT" ]; then
        return 1
    fi
    return 0
}

# ─── Query CAN UUID ─────────────────────────────────────────────────────────

query_can_uuid() {
    echo ""
    echo -e "${BOLD}CAN bus UUID:${NC}"
    echo ""
    echo "  Your Eddy Duo has a unique CAN UUID. You need this for both"
    echo "  flashing (via Katapult) and for your printer.cfg."
    echo ""

    # Try to auto-detect via Katapult
    if find_katapult; then
        echo "  You can find it with:"
        echo "    $FLASH_SCRIPT -i can0 -q"
        echo ""
        read -p "  Scan for CAN devices now? [Y/n]: " do_scan
        if [[ ! "$do_scan" =~ ^[nN] ]]; then
            read -p "  CAN interface [can0]: " scan_iface
            scan_iface="${scan_iface:-can0}"
            echo ""
            info "Scanning for CAN devices on $scan_iface..."

            # Stop Klipper temporarily for scan
            local klipper_was_running=0
            if systemctl is-active --quiet klipper 2>/dev/null; then
                info "Stopping Klipper for CAN scan..."
                sudo systemctl stop klipper
                klipper_was_running=1
                sleep 1
            fi

            python3 "$FLASH_SCRIPT" -i "$scan_iface" -q 2>&1 || true

            if [ $klipper_was_running -eq 1 ]; then
                info "Restarting Klipper..."
                sudo systemctl start klipper
            fi
            echo ""
        fi
    else
        echo "  Katapult not found -- cannot scan automatically."
        echo "  Install from: https://github.com/Arksine/katapult"
        echo ""
    fi

    read -p "  Enter CAN UUID: " CAN_UUID

    if [ -z "$CAN_UUID" ]; then
        warn "No CAN UUID entered. You can flash later or enter it during flash."
        return 1
    fi

    read -p "  CAN interface [can0]: " CAN_IFACE
    CAN_IFACE="${CAN_IFACE:-can0}"

    success "CAN UUID: $CAN_UUID (interface: $CAN_IFACE)"
    return 0
}

# ─── Flash RP2040 ────────────────────────────────────────────────────────────

flash_rp2040() {
    header "Flashing Eddy Duo"

    if [ -z "$FW_PATH" ] || [ ! -f "$FW_PATH" ]; then
        error "No firmware file to flash"
        exit 1
    fi

    info "Firmware: $FW_PATH"

    # If we have CAN UUID from earlier, suggest Katapult flash
    if [ -n "$CAN_UUID" ] && [ -n "$CAN_IFACE" ]; then
        echo ""
        echo -e "CAN UUID already configured: ${BOLD}${CAN_UUID}${NC} on ${BOLD}${CAN_IFACE}${NC}"
        echo ""
        echo "Flash method:"
        echo ""
        echo -e "  1) Katapult/CANboot (CAN bus) ${GREEN}<-- recommended${NC}"
        echo "  2) BOOTSEL mode (RP2040 USB mass storage)"
        echo "  3) Copy firmware file to a specific path"
        echo "  4) Skip flashing"
        echo ""
        read -p "Choose [1-4]: " flash_method

        case "$flash_method" in
            1) _flash_via_katapult ;;
            2) _flash_via_bootsel ;;
            3) _flash_via_copy ;;
            4) info "Skipping flash. Firmware is at: $FW_PATH" ;;
            *) error "Invalid choice"; exit 1 ;;
        esac
    else
        echo ""
        echo "Flash method:"
        echo ""
        echo "  1) BOOTSEL mode (RP2040 USB mass storage)"
        echo "  2) Katapult/CANboot (CAN bus)"
        echo "  3) Copy firmware file to a specific path"
        echo "  4) Skip flashing"
        echo ""
        read -p "Choose [1-4]: " flash_method

        case "$flash_method" in
            1) _flash_via_bootsel ;;
            2)
                # Need to collect CAN info now
                if [ -z "$CAN_UUID" ]; then
                    query_can_uuid || {
                        error "CAN UUID required for Katapult flash"
                        exit 1
                    }
                fi
                _flash_via_katapult
                ;;
            3) _flash_via_copy ;;
            4) info "Skipping flash. Firmware is at: $FW_PATH" ;;
            *) error "Invalid choice"; exit 1 ;;
        esac
    fi
}

_flash_via_bootsel() {
    # Warn if the selected firmware was built for a bootloader
    if [[ "$FW_FILE" == *katapult* ]] || [[ "$FW_PATH" == *katapult* ]]; then
        echo ""
        warn "The selected firmware was built for use WITH a bootloader (Katapult)."
        warn "Flashing via BOOTSEL will OVERWRITE the Katapult bootloader!"
        warn "After this, CAN bus firmware updates will no longer work until"
        warn "you re-flash Katapult via BOOTSEL first."
        echo ""
        echo "Options:"
        echo "  1) Continue anyway (Katapult will be overwritten)"
        echo "  2) Cancel and use Katapult flash method instead"
        echo ""
        read -p "Choose [1-2]: " overwrite_choice
        if [[ "$overwrite_choice" != "1" ]]; then
            info "Cancelled. Use Katapult flash method instead."
            return
        fi
    fi

    echo ""
    echo -e "${BOLD}Put your Eddy Duo in BOOTSEL mode:${NC}"
    echo "  1. Hold the BOOT button on your Eddy Duo"
    echo "  2. Press and release RESET (or unplug/replug USB)"
    echo "  3. Release the BOOT button"
    echo "  4. The device should appear as a USB drive (RPI-RP2)"
    echo ""
    read -p "Press Enter when the device is in BOOTSEL mode..." _

    # Try to find RP2040 mass storage
    RP2_MOUNT=""
    for mount_point in /media/*/RPI-RP2 /media/*/*/RPI-RP2 /mnt/*/RPI-RP2; do
        if [ -d "$mount_point" ]; then
            RP2_MOUNT="$mount_point"
            break
        fi
    done

    if [ -z "$RP2_MOUNT" ]; then
        RP2_MOUNT=$(mount 2>/dev/null | grep -i "rpi-rp2\|rp2" | awk '{print $3}' | head -1)
    fi

    # BOOTSEL needs .uf2 format
    local flash_file="$FW_PATH"
    if [[ "$FW_PATH" == *.bin ]]; then
        local uf2_path="${FW_PATH%.bin}.uf2"
        if [ -f "$uf2_path" ]; then
            flash_file="$uf2_path"
            info "Using .uf2 format for BOOTSEL: $uf2_path"
        else
            warn "BOOTSEL mode requires .uf2 format, but only .bin is available."
            warn "Try 'Build from source' or use a different flash method."
        fi
    fi

    if [ -n "$RP2_MOUNT" ]; then
        info "Found RP2040 at: $RP2_MOUNT"
        cp "$flash_file" "$RP2_MOUNT/"
        sync
        success "Firmware flashed! The device will reboot automatically."
    else
        warn "RP2040 mass storage not detected automatically."
        echo ""
        read -p "Enter mount path (e.g. /media/$USER/RPI-RP2): " RP2_MOUNT
        if [ -n "$RP2_MOUNT" ] && [ -d "$RP2_MOUNT" ]; then
            cp "$flash_file" "$RP2_MOUNT/"
            sync
            success "Firmware flashed!"
        else
            error "Invalid path: $RP2_MOUNT"
            echo "You can manually copy: cp $flash_file /path/to/RPI-RP2/"
            exit 1
        fi
    fi
}

_flash_via_katapult() {
    if ! find_katapult; then
        error "Katapult not found. Install from: https://github.com/Arksine/katapult"
        exit 1
    fi

    # Warn if the firmware was NOT built for a bootloader
    if [[ "$FW_FILE" != *katapult* ]] && [[ "$FW_PATH" != *katapult* ]]; then
        echo ""
        warn "The selected firmware was built WITHOUT a bootloader offset."
        warn "It uses flash offset 0x10000100, but Katapult expects the"
        warn "application at 0x10004000. The firmware will likely NOT boot!"
        echo ""
        echo "Options:"
        echo "  1) Continue anyway (firmware may not work)"
        echo "  2) Cancel and choose a Katapult-compatible firmware"
        echo ""
        read -p "Choose [1-2]: " mismatch_choice
        if [[ "$mismatch_choice" != "1" ]]; then
            info "Cancelled. Select a firmware built with Katapult bootloader offset."
            return
        fi
    fi

    # CAN flash needs .bin format
    local flash_file="$FW_PATH"
    if [[ "$FW_PATH" == *.uf2 ]]; then
        local bin_path="${FW_PATH%.uf2}.bin"
        if [ -f "$bin_path" ]; then
            flash_file="$bin_path"
            info "Using .bin format for CAN flash"
        else
            warn "CAN flash typically needs .bin format, trying with .uf2..."
        fi
    fi

    # Stop Klipper
    local restart_klipper=0
    if systemctl is-active --quiet klipper 2>/dev/null; then
        info "Stopping Klipper..."
        sudo systemctl stop klipper
        restart_klipper=1
        sleep 1
    fi

    info "Flashing via CAN ($CAN_IFACE, UUID: $CAN_UUID)..."
    echo ""

    if python3 "$FLASH_SCRIPT" -i "$CAN_IFACE" -u "$CAN_UUID" -f "$flash_file"; then
        success "CAN flash complete!"
    else
        error "CAN flash failed!"
        echo ""
        echo "Troubleshooting:"
        echo "  - Check that the CAN interface is up: ip link show $CAN_IFACE"
        echo "  - Verify UUID: $FLASH_SCRIPT -i $CAN_IFACE -q"
        echo "  - Make sure Katapult bootloader is on the RP2040"
        echo "  - Try BOOTSEL mode instead"
    fi

    if [ $restart_klipper -eq 1 ]; then
        info "Restarting Klipper..."
        sudo systemctl start klipper
    fi
}

_flash_via_copy() {
    read -p "Enter destination path: " dest_path
    if [ -n "$dest_path" ]; then
        cp "$FW_PATH" "$dest_path"
        success "Firmware copied to $dest_path"
    fi
}

# ─── Main ────────────────────────────────────────────────────────────────────

header "eddy-ng Eddy Duo Firmware Flash"

FW_PATH=""
FW_FILE=""
CAN_UUID=""
CAN_IFACE=""
PRESELECTED_CONN=""
PRESELECTED_CAN_FREQ=""
PRESELECTED_FLASH_OFFSET=""
PRESELECTED_CAN_TX=""
PRESELECTED_CAN_RX=""

if [ $BUILD_MODE -eq 1 ]; then
    build_from_source
else
    select_prebuilt
fi

# If CAN was selected and no UUID yet, ask now
if [[ "$FW_FILE" == *canbus* || "$FW_FILE" == *katapult* ]] && [ -z "$CAN_UUID" ]; then
    query_can_uuid || true
fi

flash_rp2040

header "Done"
echo -e "${GREEN}Eddy Duo firmware ready!${NC}"
echo ""
echo "Next steps:"
echo "  1. Check your printer.cfg (see $REPO_DIR/example-printer.cfg)"
echo "  2. Restart Klipper: sudo systemctl restart klipper"
echo "  3. Run: PROBE_EDDY_NG_SETUP"
