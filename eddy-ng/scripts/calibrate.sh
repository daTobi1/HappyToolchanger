#!/bin/bash
#
# eddy-ng fully automatic calibration script
#
# Runs all 7 calibration steps sequentially, handling Klipper restarts
# after each SAVE_CONFIG automatically. Only pauses for the manual Z
# positioning in step 1.
#
# Usage:
#   On the printer:  ~/eddy-ng/scripts/calibrate.sh
#   Via SSH:          ssh user@printer "~/eddy-ng/scripts/calibrate.sh"
#   Remote:           ~/eddy-ng/scripts/calibrate.sh --url http://printer-ip
#
# Options:
#   --url URL          Moonraker URL (default: http://localhost:7125)
#   --full             Run all 7 steps including advanced (default: base only, steps 1-4)
#   --bed-temp TEMP    Bed temperature for temp calibration (default: 90)
#   --min-temp TEMP    Minimum sensor temp (default: 40)
#   --max-temp TEMP    Maximum sensor temp (default: 60)
#   --hotend-fan       Enable hotend fan cooling during temp calibration
#   --twist-bed TEMP   Bed temperature for axis twist (default: 0 = cold)
#   --twist-hotend TEMP  Hotend temp for axis twist (default: 0 = cold)
#   --no-qgl           Skip QUAD_GANTRY_LEVEL (for printers without QGL)
#
set -e

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

# ─── Configuration ────────────────────────────────────────────────────────

MOONRAKER_URL="${MOONRAKER_URL:-http://localhost:7125}"
RESTART_TIMEOUT=60
COMMAND_POLL_INTERVAL=3
COMMAND_TIMEOUT=600
TEMP_CAL_TIMEOUT=3600  # 60 minutes for temp calibration

FULL_CALIBRATION=0
BED_TEMP=90
MIN_TEMP=40
MAX_TEMP=60
HOTEND_FAN=0
TWIST_BED_TEMP=0
TWIST_HOTEND_TEMP=0
LEVEL_CMD="QUAD_GANTRY_LEVEL"

# ─── Argument parsing ─────────────────────────────────────────────────────

while [[ $# -gt 0 ]]; do
    case "$1" in
        --url)           MOONRAKER_URL="$2"; shift 2 ;;
        --full)          FULL_CALIBRATION=1; shift ;;
        --bed-temp)      BED_TEMP="$2"; shift 2 ;;
        --min-temp)      MIN_TEMP="$2"; shift 2 ;;
        --max-temp)      MAX_TEMP="$2"; shift 2 ;;
        --hotend-fan)    HOTEND_FAN=1; shift ;;
        --twist-bed)     TWIST_BED_TEMP="$2"; shift 2 ;;
        --twist-hotend)  TWIST_HOTEND_TEMP="$2"; shift 2 ;;
        --level-cmd)     LEVEL_CMD="$2"; shift 2 ;;
        --no-level)      LEVEL_CMD=""; shift ;;
        -h|--help)
            echo "Usage: $0 [OPTIONS]"
            echo ""
            echo "Runs the eddy-ng calibration workflow."
            echo ""
            echo "Options:"
            echo "  --url URL            Moonraker URL (default: http://localhost:7125)"
            echo "  --full               Run all 7 steps (default: base only, steps 1-4)"
            echo "  --bed-temp TEMP      Bed temp for temp calibration (default: 90)"
            echo "  --min-temp TEMP      Min sensor temp (default: 40)"
            echo "  --max-temp TEMP      Max sensor temp (default: 60)"
            echo "  --hotend-fan         Use hotend fan for faster cooling in temp cal"
            echo "  --twist-bed TEMP     Bed temp for axis twist calibration (default: 0)"
            echo "  --twist-hotend TEMP  Hotend temp for axis twist (default: 0)"
            echo "  --level-cmd CMD      Leveling command (default: QUAD_GANTRY_LEVEL)"
            echo "  --no-level           Skip gantry leveling entirely"
            echo ""
            echo "Examples:"
            echo "  $0                                   # Base calibration (steps 1-4)"
            echo "  $0 --full --bed-temp 110             # Full 7-step, bed at 110C"
            echo "  $0 --full --bed-temp 110 --hotend-fan --twist-bed 60"
            echo "  $0 --full --level-cmd Z_TILT_ADJUST   # Voron Trident"
            echo "  $0 --full --no-level                  # Bedslingers"
            exit 0
            ;;
        *)  error "Unknown option: $1"; exit 1 ;;
    esac
done

# ─── Moonraker API helpers ────────────────────────────────────────────────

api_get() {
    curl -sf "$MOONRAKER_URL/$1" 2>/dev/null
}

api_post() {
    curl -sf -X POST "$MOONRAKER_URL/$1" -H "Content-Type: application/json" -d "$2" 2>/dev/null
}

get_printer_state() {
    api_get "printer/info" | python3 -c "
import sys, json
try:
    d = json.load(sys.stdin)
    print(d['result']['state'])
except:
    print('unknown')
" 2>/dev/null
}

get_last_responses() {
    local count="${1:-10}"
    api_get "server/gcode_store?count=$count" | python3 -c "
import sys, json
try:
    d = json.load(sys.stdin)['result']['gcode_store']
    for item in d:
        if item.get('type') == 'response' and item.get('message','').strip():
            print(item['message'])
except:
    pass
" 2>/dev/null
}

send_gcode() {
    local cmd="$1"
    info "Sending: $cmd"
    # Use POST with JSON body to avoid URL encoding issues
    api_post "printer/gcode/script" "{\"script\": \"$cmd\"}" > /dev/null 2>&1
    return $?
}

send_gcode_nowait() {
    local cmd="$1"
    info "Sending: $cmd"
    # Fire and forget - use timeout to avoid blocking on long commands
    curl -sf -X POST "$MOONRAKER_URL/printer/gcode/script" \
        -H "Content-Type: application/json" \
        -d "{\"script\": \"$cmd\"}" \
        --max-time 5 > /dev/null 2>&1 || true
}

# ─── Wait helpers ─────────────────────────────────────────────────────────

wait_for_ready() {
    local timeout="${1:-$RESTART_TIMEOUT}"
    local elapsed=0
    while [ $elapsed -lt $timeout ]; do
        local state
        state=$(get_printer_state)
        if [ "$state" = "ready" ]; then
            return 0
        fi
        sleep 2
        elapsed=$((elapsed + 2))
    done
    error "Timeout waiting for printer to become ready (${timeout}s)"
    return 1
}

wait_for_command() {
    # Wait for a long-running GCode command to complete.
    # Polls gcode_store for completion markers.
    local marker="$1"
    local timeout="${2:-$COMMAND_TIMEOUT}"
    local elapsed=0

    sleep 2  # Give the command time to start

    while [ $elapsed -lt $timeout ]; do
        local state
        state=$(get_printer_state)

        if [ "$state" = "error" ] || [ "$state" = "shutdown" ]; then
            error "Printer entered error/shutdown state"
            return 1
        fi

        # Check if the marker text appears in recent responses
        if [ -n "$marker" ]; then
            local responses
            responses=$(get_last_responses 5)
            if echo "$responses" | grep -qF "$marker"; then
                return 0
            fi
        fi

        sleep "$COMMAND_POLL_INTERVAL"
        elapsed=$((elapsed + COMMAND_POLL_INTERVAL))
    done

    error "Timeout waiting for command completion (${timeout}s)"
    return 1
}

save_config_and_wait() {
    info "Saving config (Klipper will restart)..."
    send_gcode "SAVE_CONFIG"
    sleep 5  # Give Klipper time to shut down
    wait_for_ready "$RESTART_TIMEOUT"
    success "Klipper restarted and ready"
}

home_and_wait() {
    send_gcode_nowait "G28"
    sleep 2
    wait_for_command "toolchanger initialized" 60 || {
        # Fallback: just wait for ready state
        sleep 15
        wait_for_ready 30
    }
    success "Homing complete"
}

home_xy_and_wait() {
    send_gcode_nowait "G28 X Y"
    sleep 2
    wait_for_command "toolchanger initialized" 60 || {
        sleep 15
        wait_for_ready 30
    }
    success "XY homing complete"
}

level_and_wait() {
    if [ -z "$LEVEL_CMD" ]; then
        info "Skipping gantry leveling (--no-level)"
        return 0
    fi
    info "Running $LEVEL_CMD..."
    send_gcode_nowait "$LEVEL_CMD"
    wait_for_command "Retries:" 120 || {
        sleep 30
        wait_for_ready 30
    }
    success "$LEVEL_CMD complete"
}

# ─── Calibration steps ────────────────────────────────────────────────────

step_1_setup() {
    header "Step 1/7: Initial Sensor Setup"
    echo ""
    echo -e "  This step requires ${BOLD}manual interaction${NC}."
    echo "  After homing, the nozzle moves to the bed center."
    echo "  Use your Klipper console (Mainsail/Fluidd) to:"
    echo ""
    echo -e "    ${BOLD}TESTZ Z=-10${NC}   (large steps first)"
    echo -e "    ${BOLD}TESTZ Z=-1${NC}    (then smaller)"
    echo -e "    ${BOLD}TESTZ Z=-0.1${NC}  (until nozzle barely touches bed)"
    echo -e "    ${BOLD}ACCEPT${NC}        (when position is correct)"
    echo ""

    home_xy_and_wait
    send_gcode_nowait "PROBE_EDDY_NG_SETUP"

    echo ""
    echo -e "${YELLOW}>>> Complete the manual Z probe in your Klipper console now.${NC}"
    echo -e "${YELLOW}>>> Press Enter here AFTER you have typed ACCEPT.${NC}"
    echo ""
    read -p "Press Enter when done..."

    # Verify the setup completed successfully
    local responses
    responses=$(get_last_responses 10)
    if echo "$responses" | grep -qF "Setup success"; then
        success "Setup completed successfully"
    else
        warn "Could not verify setup success. Check your Klipper console."
    fi

    save_config_and_wait
    success "Step 1/7 complete"
}

step_2_optimize_dc() {
    header "Step 2/7: Optimize Drive Current"
    info "This is fully automatic and takes ~2 minutes."

    home_and_wait
    send_gcode_nowait "PROBE_EDDY_NG_OPTIMIZE_DRIVE_CURRENT"
    wait_for_command "SAVE_CONFIG to persist" "$COMMAND_TIMEOUT"

    # Show results
    echo ""
    get_last_responses 5 | grep -E "(Best for|Results saved)" | while read -r line; do
        success "$line"
    done
    echo ""

    save_config_and_wait
    success "Step 2/7 complete"
}

step_3_threshold() {
    header "Step 3/7: Calibrate Tap Threshold"
    info "This is fully automatic and takes ~2 minutes."

    home_and_wait
    send_gcode_nowait "PROBE_EDDY_NG_CALIBRATE_THRESHOLD"
    wait_for_command "SAVE_CONFIG to persist" "$COMMAND_TIMEOUT"

    # Show results
    echo ""
    get_last_responses 5 | grep -E "(Optimal threshold|Verification)" | while read -r line; do
        success "$line"
    done
    echo ""

    save_config_and_wait
    success "Step 3/7 complete"
}

step_4_verify() {
    header "Step 4/7: Verification"
    info "Testing homing, tap, and probe accuracy."

    home_and_wait

    info "Testing tap..."
    send_gcode_nowait "PROBE_EDDY_NG_TAP"
    wait_for_command "tap" 60 || sleep 15

    info "Testing probe accuracy..."
    send_gcode_nowait "PROBE_EDDY_NG_PROBE_ACCURACY"
    wait_for_command "probe accuracy" 120 || sleep 30

    # Show results
    echo ""
    get_last_responses 15 | grep -iE "(range|standard deviation|median|maximum|minimum)" | while read -r line; do
        info "$line"
    done
    echo ""

    success "Step 4/7 complete — base calibration done"
}

step_5_temp_calibrate() {
    header "Step 5/7: Temperature Compensation"
    info "BED_TEMP=$BED_TEMP MIN_TEMP=$MIN_TEMP MAX_TEMP=$MAX_TEMP HOTEND_FAN=$HOTEND_FAN"
    info "This takes 30-60 minutes. Do not touch the printer."

    home_and_wait
    level_and_wait

    local cmd="PROBE_EDDY_NG_TEMPERATURE_CALIBRATE BED_TEMP=$BED_TEMP MIN_TEMP=$MIN_TEMP MAX_TEMP=$MAX_TEMP HOTEND_FAN=$HOTEND_FAN"
    send_gcode_nowait "$cmd"
    wait_for_command "SAVE_CONFIG to persist" "$TEMP_CAL_TIMEOUT"

    save_config_and_wait
    success "Step 5/7 complete"
}

step_6_axis_twist() {
    header "Step 6/7: Axis Twist Compensation"
    info "AXIS=BOTH BED_TEMP=$TWIST_BED_TEMP HOTEND_TEMP=$TWIST_HOTEND_TEMP"

    home_and_wait
    level_and_wait
    # Re-home Z after QGL for accurate tap
    send_gcode_nowait "G28 Z"
    sleep 5

    local cmd="PROBE_EDDY_NG_AXIS_TWIST_CALIBRATE AXIS=BOTH SAMPLE_COUNT=7 BED_TEMP=$TWIST_BED_TEMP HOTEND_TEMP=$TWIST_HOTEND_TEMP"
    send_gcode_nowait "$cmd"
    wait_for_command "SAVE_CONFIG to persist" "$COMMAND_TIMEOUT"

    save_config_and_wait
    success "Step 6/7 complete"
}

step_7_backlash() {
    header "Step 7/7: Backlash Estimation"
    info "This is fully automatic and takes ~2 minutes."

    home_and_wait
    level_and_wait
    # Re-home Z after QGL
    send_gcode_nowait "G28 Z"
    sleep 5

    send_gcode_nowait "PROBE_EDDY_NG_ESTIMATE_BACKLASH CALIBRATE=1"
    wait_for_command "SAVE_CONFIG to persist" "$COMMAND_TIMEOUT"

    # Show results
    echo ""
    get_last_responses 10 | grep -iE "(backlash|measured|compensation)" | while read -r line; do
        info "$line"
    done
    echo ""

    save_config_and_wait
    success "Step 7/7 complete"
}

# ─── Main ─────────────────────────────────────────────────────────────────

main() {
    if [ "$FULL_CALIBRATION" -eq 1 ]; then
        header "eddy-ng Full Calibration (7 steps)"
    else
        header "eddy-ng Base Calibration (4 steps)"
    fi
    info "Moonraker: $MOONRAKER_URL"

    # Check connection
    local state
    state=$(get_printer_state)
    if [ "$state" != "ready" ]; then
        error "Printer is not ready (state: $state)"
        error "Make sure Klipper is running and the printer is connected."
        exit 1
    fi
    success "Printer is ready"
    echo ""

    # Base calibration (steps 1-4)
    step_1_setup
    step_2_optimize_dc
    step_3_threshold
    step_4_verify

    if [ "$FULL_CALIBRATION" -eq 1 ]; then
        # Advanced calibration (steps 5-7)
        step_5_temp_calibrate
        step_6_axis_twist
        step_7_backlash

        header "Full Calibration Complete! (7/7)"
    else
        header "Base Calibration Complete! (4/7)"
    fi

    echo ""
    echo "Your eddy-ng probe is calibrated and ready to use."
    echo ""
    echo "Next steps:"
    echo "  - G28               Home all axes"
    echo "  - PROBE_EDDY_NG_TAP Precise Z-offset before printing"
    echo "  - BED_MESH_CALIBRATE Scan the bed mesh"
    echo ""

    if [ "$FULL_CALIBRATION" -eq 0 ]; then
        echo "Optional advanced calibration (steps 5-7):"
        echo "  $0 --full --bed-temp 110"
        echo "  Or run individual macros:"
        echo "  - EDDY_NG_TEMP_CALIBRATE_5VON7 BED_TEMP=110 MIN_TEMP=40 MAX_TEMP=70"
        echo "  - EDDY_NG_AXIS_TWIST_6VON7 BED_TEMP=60 HOTEND_TEMP=150"
        echo "  - EDDY_NG_BACKLASH_7VON7"
        echo ""
    fi
}

main
