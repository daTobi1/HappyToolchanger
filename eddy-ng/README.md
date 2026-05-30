# eddy-ng

Enhanced Eddy current probe support for [Klipper](https://github.com/Klipper3d/klipper) and [Kalico](https://github.com/KalicoCrew/kalico).

eddy-ng adds accurate Z-offset setting by physically making contact with the build surface ("tap"). Eddy current probes are very accurate, but suffer from drift due to temperature changes and surface conductivity variations. Instead of guesswork temperature compensation, eddy-ng takes a physical approach:

1. **Cold calibration** -- Calibrate at any temperature. The frequency-to-height mapping is stored as a JSON polynomial fit with full double precision.
2. **Coarse Z-home** -- Z-homing via the sensor uses this calibration regardless of current temperatures. Not accurate enough for printing, but sufficient for homing, gantry leveling, and other preparation.
3. **Precise tap** -- Just before printing (bed at print temp, nozzle warm but not hot), a precise Z-offset is determined by physically tapping the build surface. The nozzle touches the bed and the exact height is measured.
4. **Temperature-compensated mesh** -- The difference between sensor reading and actual tap height is saved as an offset. This offset compensates for thermal drift during bed mesh scanning.

---

## Table of Contents

- [Supported Hardware](#supported-hardware)
- [Features](#features)
- [Requirements](#requirements)
- [Installation](#installation)
  - [Remote Installation (from Windows)](#remote-installation-from-windows)
  - [BTT Eddy Duo (RP2040)](#installation-btt-eddy-duo-rp2040)
  - [Other Eddy Sensors](#installation-other-eddy-sensors)
- [Configuration](#configuration)
  - [Minimal Configuration](#minimal-configuration)
  - [Full Configuration Reference](#full-configuration-reference)
  - [Z Homing](#z-homing-configuration)
  - [Bed Mesh](#bed-mesh-configuration)
  - [Example Print Start Macro](#example-print-start-macro)
  - [Eddy Duo via USB](#eddy-duo-via-usb)
  - [Eddy Duo via CAN Bus](#eddy-duo-via-can-bus)
  - [Cartographer via CAN Bus](#cartographer-via-can-bus)
  - [Advanced: Multi-Pass Mesh with Spiral Path](#advanced-multi-pass-mesh-with-spiral-path)
  - [Advanced: Temperature Compensation + Backlash](#advanced-temperature-compensation--backlash)
- [GCode Commands](#gcode-commands)
  - [Calibration](#calibration)
  - [Probing](#probing)
  - [Configuration Commands](#configuration-commands)
  - [Advanced Features](#advanced-features)
  - [Command Details](#command-details)
- [Typical Workflow](#typical-workflow)
  - [First-Time Setup](#first-time-setup)
    - [Guided Macros](#option-a-guided-macros-recommended)
    - [Automatic Script](#option-b-automatic-script-via-ssh)
    - [Manual Commands](#option-c-manual-commands)
  - [Before Every Print](#before-every-print)
- [Updating](#updating)
- [Uninstallation](#uninstallation)
- [Project Structure](#project-structure)
- [Troubleshooting](#troubleshooting)
  - [QGL fails with "Probed points range is increasing"](#qgl-fails-with-probed-points-range-is-increasing)
- [Support](#support)
- [License](#license)

---

## Supported Hardware

| Sensor Type | Description | Installation Mode |
|---|---|---|
| `btt_eddy` | BigTreeTech Eddy Coil (I2C to main MCU) | Traditional (Klipper patching) |
| `btt_eddy` | BigTreeTech Eddy Duo (own RP2040 MCU) | Patchless (pip + pre-built firmware) |
| `ldc1612` | TI LDC1612 inductive sensor | Traditional |
| `cartographer` | Cartographer probe (own RP2040 MCU) | Patchless (pip only) |
| `mellow_fly` | Mellow Fly probe | Traditional |
| `ldc1612_internal_clk` | LDC1612 with internal oscillator | Traditional |

**BTT Eddy Duo** has its own RP2040 microcontroller and connects via USB or CAN bus as a separate Klipper MCU. This allows a patchless installation -- no Klipper source modifications are needed, just like the Cartographer.

**BTT Eddy Coil** and other sensors connect via I2C to the printer's main MCU and require a Klipper Makefile patch to compile the custom sensor driver into the firmware.

## Features

### Core
- **Butterworth bandpass filter** for tap detection (MCU-side, real-time)
- **Auto-threshold calibration** -- automatically finds the optimal tap sensitivity
- **Rapid bed mesh scanning** with lookahead motion planning
- **JSON calibration format** -- human-readable, exact precision via hex floats
- **Klipper + Kalico compatible** -- auto-detects and adapts to both firmware forks

### Advanced (Cartographer-inspired)
- **Temperature compensation** -- quadratic drift model, calibrated across multiple heights
- **Named calibration models** -- save/load/switch calibrations for different build plates
- **Advanced mesh path algorithms** -- snake, alternating snake, spiral, random traversal
- **Multi-pass mesh scanning** -- multiple passes with median averaging for higher accuracy
- **Z-axis backlash estimation** -- statistical measurement with Welch's t-test
- **Alpha-beta tracking filter** -- recursive smoothing of height measurements
- **Axis twist compensation** -- automatic integration with Klipper's axis_twist_compensation
- **Data streaming with CSV export** -- session-based raw data capture for analysis
- **Pre-built firmware images** -- for Eddy Duo (USB, CAN 500k, CAN 1M)

### Installation
- **Dual installation mode** -- patchless for Eddy Duo/Cartographer, traditional for other sensors
- **Interactive installer** -- auto-detects Klipper/Kalico, guides through sensor selection
- **Scaffolding-based loading** -- thin import bridges in `klippy/extras/` that load eddy-ng directly from the cloned repo
- **CI-built firmware** -- GitHub Actions pipeline builds RP2040 firmware automatically

## Requirements

- Python 3.8+
- NumPy
- Klipper or Kalico firmware
- An LDC1612-based eddy current probe

Optional:
- `scipy` -- for custom Butterworth filter parameters and temperature compensation model fitting
- `plotly` -- for calibration and tap diagnostic plots

---

## Installation

### Interactive Installation (Recommended)

The installer auto-detects your sensor type and chooses the right installation method:

```bash
cd ~
git clone https://github.com/daTobi1/neweddy.git eddy-ng
cd ~/eddy-ng
./install.sh
```

The installer will:
1. Ask which sensor you have (Eddy Duo, Cartographer, or other)
2. Install Python dependencies and scaffolding files
3. Copy `calibrate_macros.cfg` and `eddy-ng.cfg` to your Klipper config directory
4. Tell you which `[include]` lines to add to `printer.cfg`
5. For Eddy Duo: offer to flash firmware
6. For other sensors: patch the Klipper Makefile for firmware rebuild

If Klipper is in a non-standard location:

```bash
./install.sh /path/to/klipper
```

### Remote Installation (from Windows)

If you prefer to run the installation from a Windows PC (no SSH terminal on the printer needed), use the remote installer:

```bash
pip install paramiko
python install_eddy_ng.py
```

The script connects to the printer via SSH (default: `192.168.178.60`, user `biqu`) and performs all 8 steps remotely: repo update, Python dependencies, scaffolding, firmware patch, `.config` generation, firmware build, verification, and `eddy-ng.cfg` generation.

**Features:**
- Auto-detects probe type (BTT Eddy / Cartographer) and printer size (250mm / 350mm)
- Interactive confirmation menu — lets you override auto-detected values
- Generates correct Klipper `.config` for RP2040 or STM32F042
- Generates `eddy-ng.cfg` with geometry presets matching your printer size

**CLI flags:**

| Flag | Description |
|---|---|
| `--btt-eddy` | Force BTT Eddy Duo (RP2040) |
| `--cartographer` | Force Cartographer (STM32F042) |
| `--size 250` or `--size 350` | Set printer size |
| `-y` / `--yes` | Skip interactive confirmation |

**Examples:**

```bash
python install_eddy_ng.py                          # Interactive (auto-detect + confirm)
python install_eddy_ng.py --btt-eddy --size 250    # BTT Eddy on 250mm printer
python install_eddy_ng.py --cartographer --size 350 # Cartographer on 350mm printer
python install_eddy_ng.py -y                       # Auto-detect, no confirmation
```

After installation, flash the firmware via Katapult CAN and run the calibration macros (see [First-Time Setup](#first-time-setup)).

### Installation: BTT Eddy Duo (RP2040)

The Eddy Duo has its own RP2040 microcontroller. No Klipper source patching is needed.

**What the installer does:**

| Step | Action |
|---|---|
| 1 | Installs Python dependencies (numpy) into Klipper's virtualenv |
| 2 | Creates scaffolding files in `klippy/extras/` (thin import bridges that load eddy-ng from the repo) |
| 3 | Copies `calibrate_macros.cfg` and `eddy-ng.cfg` (example) to Klipper config directory |
| 4 | Offers to flash firmware to the RP2040 (pre-built or build from source) |

**Firmware flashing:**

The installer will offer to flash the RP2040. You can also flash later:

```bash
./scripts/flash-duo.sh
```

Pre-built firmware variants are available:

| File | Connection | Bootloader | CAN Pins |
|---|---|---|---|
| `eddy-duo-usb.uf2` | USB | none | -- |
| `eddy-duo-canbus-500k.uf2` | CAN 500k | none | TX=GPIO5, RX=GPIO4 |
| `eddy-duo-canbus-1m.uf2` | CAN 1M | none | TX=GPIO5, RX=GPIO4 |
| `eddy-duo-katapult-canbus-500k.bin` | CAN 500k | Katapult | TX=GPIO5, RX=GPIO4 |
| `eddy-duo-katapult-canbus-1m.bin` | CAN 1M | Katapult | TX=GPIO5, RX=GPIO4 |

> **CAN GPIO pins:** Pre-built CAN firmware uses the BTT Eddy Duo defaults (TX=GPIO5, RX=GPIO4). The standard (non-Duo) Eddy uses TX=GPIO1, RX=GPIO0. If your board has different CAN pins, use `./scripts/flash-duo.sh --build` to configure them manually.

> **Katapult bootloader:** Variants with "katapult" in the name use flash offset `0x10004000` (16KB reserved for the Katapult bootloader). The Katapult bootloader must be flashed on the RP2040 **before** using these images.

**Flash methods:**

| Method | Format | Bootloader | Use case |
|--------|--------|------------|----------|
| BOOTSEL | `.uf2` | **Overwrites** bootloader | First-time flash, USB connection |
| Katapult (CAN) | `.bin` | **Preserved** | CAN bus updates without physical access |
| Manual copy | any | depends | Custom setups |

> **Important:** BOOTSEL mode overwrites the entire flash including any bootloader. If you have Katapult installed and want to keep it, use Katapult flash instead. The script warns about this automatically.

> **Important:** Katapult expects the firmware at a specific offset (`0x10004000`). Flashing a firmware built without bootloader offset via Katapult will likely not boot. The script warns about this mismatch.

**Build from source** (for custom CAN pins or Klipper version matching):

```bash
./scripts/flash-duo.sh --build
```

The build script asks for connection type, CAN pins, and bootloader offset interactively. For CAN connections, it also offers to scan for the CAN UUID via Katapult. You can override CAN pins via environment variables:

```bash
EDDY_CAN_TX_GPIO=5 EDDY_CAN_RX_GPIO=4 ./scripts/flash-duo.sh --build
```

### Installation: Cartographer

The Cartographer probe has its own RP2040 MCU. No Klipper source patching or firmware flashing is needed -- the Cartographer already runs its own Klipper firmware.

**What the installer does:**

| Step | Action |
|---|---|
| 1 | Installs Python dependencies (numpy) into Klipper's virtualenv |
| 2 | Creates scaffolding files in `klippy/extras/` (thin import bridges that load eddy-ng from the repo) |
| 3 | Copies `calibrate_macros.cfg` and `eddy-ng.cfg` (example) to Klipper config directory |

Set `sensor_type: cartographer` in your config. See [Configuration](#configuration).

### Installation: Other Eddy Sensors

Sensors without their own MCU (Eddy Coil, bare LDC1612, etc.) require the custom sensor driver to be compiled into the printer's main MCU firmware.

**What the installer does:**

| Step | Action |
|---|---|
| 1 | Installs Python dependencies (numpy) into Klipper's virtualenv |
| 2 | Creates scaffolding files in `klippy/extras/` (thin import bridges that load eddy-ng from the repo) |
| 3 | Copies `calibrate_macros.cfg` and `eddy-ng.cfg` (example) to Klipper config directory |
| 3 | Links the C firmware module into Klipper's `src/` directory |
| 4 | Patches Klipper's `src/Makefile` to compile the sensor driver |

**After installation, rebuild and flash the MCU firmware:**

```bash
cd ~/eddy-ng
./flash.sh
```

The flash script:
- Detects your Klipper/Kalico installation
- Opens `menuconfig` if needed (enable LDC1612 support under Sensors)
- Builds the firmware
- Auto-detects connected devices and offers flash options

**Common flash options:**

```bash
./flash.sh                              # Full build + interactive flash
./flash.sh --build-only                 # Build only, no flash
./flash.sh --menuconfig                 # Force menuconfig before build
./flash.sh --clean                      # Clean build first
./flash.sh --flash-device /dev/ttyACM0  # Specify flash device directly
```

**Supported flash methods:** USB Serial, DFU, CAN bus (Katapult/CANboot), SD card, manual device path.

### Command-Line Flags

```bash
./install.sh              # Interactive mode (recommended)
./install.sh --duo        # Skip prompt, install for Eddy Duo
./install.sh --cartographer  # Skip prompt, install for Cartographer
./install.sh --other      # Skip prompt, install for other sensors
./install.sh --uninstall  # Uninstall everything
```

### Legacy Installation

The old `install.py` is still available for backward compatibility:

```bash
python3 install.py                # Symlink install (traditional)
python3 install.py --copy         # Copy files instead of symlinking
python3 install.py --firmware-only  # Only install C firmware + patch Makefile
python3 install.py --uninstall    # Uninstall
```

---

## Configuration

### Standalone Config File

The installer automatically creates `eddy-ng.cfg` and `calibrate_macros.cfg` in your Klipper config directory. Add these includes to your `printer.cfg`:

```ini
[include eddy-ng.cfg]
[include calibrate_macros.cfg]
```

Edit `eddy-ng.cfg` with your MCU UUID, probe offsets, and other settings. See the [example-printer.cfg](example-printer.cfg) for all available options. The `calibrate_macros.cfg` can be removed after initial calibration is complete.

### Minimal Configuration

For most users, only a few settings are needed:

```ini
[probe_eddy_ng my_eddy]
sensor_type: btt_eddy
i2c_mcu: mcu
i2c_bus: i2c3a
x_offset: -38.0
y_offset: -22.0
```

### Full Configuration Reference

All available options with their defaults. Only set values that differ from defaults.

```ini
[probe_eddy_ng my_eddy]

# ── Sensor ──────────────────────────────────────────────────────────
# Options: ldc1612, btt_eddy, cartographer, mellow_fly, ldc1612_internal_clk
sensor_type: btt_eddy

# I2C bus configuration (depends on your board wiring)
i2c_mcu: mcu
i2c_bus: i2c3a
i2c_speed: 400000

# ── Probe Offsets ───────────────────────────────────────────────────
# Physical offset between probe coil center and nozzle tip (mm).
# MUST be non-zero unless allow_unsafe: True.
x_offset: -38.0
y_offset: -22.0

# ── Movement Speeds ────────────────────────────────────────────────
#probe_speed: 5.0           # Z probing speed (mm/s)
#lift_speed: 10.0           # Z lift speed (mm/s)
#move_speed: 50.0           # XY positioning speed (mm/s)

# ── Z Homing ───────────────────────────────────────────────────────
#home_trigger_height: 2.0             # Homing trigger height (mm)
#home_trigger_safe_start_offset: 1.0  # Extra height above trigger (mm)

# ── Tap Detection ──────────────────────────────────────────────────
# Mode: "butter" (Butterworth bandpass, recommended) or "wma" (weighted moving average)
#tap_mode: butter
# Tap threshold -- lower = more sensitive, higher = less sensitive.
# Use PROBE_EDDY_NG_CALIBRATE_THRESHOLD to auto-find the optimal value.
#tap_threshold: 250.0       # Default for butter (1000.0 for wma)
#tap_speed: 3.0             # Tap speed (mm/s)
#tap_start_z: 3.0           # Z height to start tap from (mm)
#tap_target_z: -0.250       # Z target for tap (mm, slightly below bed)
#tap_adjust_z: 0.0          # Additional Z adjustment after tap (mm)
#tap_time_position: 0.3     # Time position for trigger detection (0.0-1.0)

# ── Tap Sampling ───────────────────────────────────────────────────
#tap_samples: 3             # Consistent tap samples required
#tap_max_samples: 5         # Max tap attempts before failure
#tap_samples_stddev: 0.020  # Max standard deviation between samples (mm)
#tap_use_median: False      # Use median instead of mean

# ── Butterworth Filter (advanced) ──────────────────────────────────
# Custom values require scipy. Default coefficients are built-in.
#tap_butter_lowcut: 5.0     # Low cutoff frequency (Hz)
#tap_butter_highcut: 25.0   # High cutoff frequency (Hz)
#tap_butter_order: 2        # Filter order

# ── Drive Current ──────────────────────────────────────────────────
# LDC1612 drive current register (0-31). 0 = sensor default.
#reg_drive_current: 0       # For regular probing
#tap_drive_current: 0       # For tap detection

# ── Scanning / Bed Mesh ───────────────────────────────────────────
#scan_sample_time: 0.100        # Sample time per point (s)
#scan_sample_time_delay: 0.050  # Delay between samples (s)

# ── Mesh Path (advanced) ──────────────────────────────────────────
# Algorithm for traversing mesh probe points during scanning.
# Options: snake, alternating_snake, spiral, random
#mesh_path: snake
# Primary direction for snake paths: x or y
#mesh_direction: x
# Number of scan passes (>1 uses median averaging)
#mesh_runs: 1
# Height above bed for mesh scanning (mm)
#mesh_height: 3.0

# ── Calibration ────────────────────────────────────────────────────
#calibration_z_max: 15.0    # Max Z height during calibration (mm)
#calibration_points: 150    # Number of calibration sample points

# ── Backlash Compensation ─────────────────────────────────────────
# Z-axis backlash compensation (mm). Use PROBE_EDDY_NG_ESTIMATE_BACKLASH
# to measure automatically.
#z_backlash: 0.0

# ── Alpha-Beta Filter ─────────────────────────────────────────────
# Recursive tracking filter for smoothing height measurements.
# alpha: position smoothing (0.0 = no filter, 1.0 = no smoothing)
# beta: velocity tracking (small values = slow response to velocity changes)
#filter_alpha: 0.5
#filter_beta: 1e-6

# ── Temperature Compensation ──────────────────────────────────────
# Calibrated via PROBE_EDDY_NG_TEMPERATURE_CALIBRATE. Saved automatically.
#temperature_compensation:

# ── Safety & Debug ─────────────────────────────────────────────────
#allow_unsafe: False         # Allow x_offset=0 y_offset=0
#debug: True                 # Debug logging
#max_errors: 0               # Max sensor errors (0=unlimited)
#write_tap_plot: False       # Write HTML plot of final tap
#write_every_tap_plot: False # Write HTML plot of every tap
```

### Z Homing Configuration

Use the eddy probe as Z endstop (virtual endstop):

```ini
[stepper_z]
# ... your existing stepper_z config ...
endstop_pin: probe:z_virtual_endstop
# Comment out or remove any existing position_endstop:
#position_endstop: 0
homing_speed: 5
position_min: -2    # Allow slight negative Z for tap target
```

### Bed Mesh Configuration

Standard Klipper bed mesh -- eddy-ng integrates automatically:

```ini
[bed_mesh]
speed: 200
horizontal_move_z: 3
mesh_min: 30, 30
mesh_max: 320, 320
probe_count: 15, 15
algorithm: bicubic
```

### Safe Z Home

Home Z in the center of the bed:

```ini
[safe_z_home]
home_xy_position: 175, 175
speed: 100
z_hop: 10
z_hop_speed: 10
```

> **Toolchanger users:** If your toolchanger setup already defines `[homing_override]` (e.g. in a homing.cfg), you **cannot** use `[safe_z_home]` -- Klipper does not allow both simultaneously. Remove the `[safe_z_home]` section and handle Z homing inside your existing `[homing_override]` instead.

### Example Print Start Macro

```ini
[gcode_macro PRINT_START]
gcode:
    {% set bed_temp = params.BED_TEMP|default(60)|float %}
    {% set extruder_temp = params.EXTRUDER_TEMP|default(200)|float %}

    # Home all axes
    G28

    # Heat bed and wait
    M190 S{bed_temp}

    # Warm nozzle to 150C (no oozing, no build plate damage)
    M109 S150

    # Precise Z offset via tap at print temperature
    PROBE_EDDY_NG_TAP

    # Temperature-compensated bed mesh
    BED_MESH_CALIBRATE

    # Full nozzle temperature
    M109 S{extruder_temp}

    # Ready to print
    G0 Z5 F3000
```

Call from your slicer's start G-Code:
```
PRINT_START BED_TEMP=60 EXTRUDER_TEMP=200
```

### Eddy Duo via USB

```ini
# The Eddy Duo appears as a separate MCU
[mcu eddy_duo]
serial: /dev/serial/by-id/usb-Klipper_rp2040_XXXXXXXXXXXX-if00

[probe_eddy_ng my_eddy]
sensor_type: btt_eddy
i2c_mcu: eddy_duo
i2c_bus: i2c0e
x_offset: -38.0
y_offset: -22.0
```

### Eddy Duo via CAN Bus

```ini
[mcu eddy_duo]
canbus_uuid: XXXXXXXXXXXX

[probe_eddy_ng my_eddy]
sensor_type: btt_eddy
i2c_mcu: eddy_duo
i2c_bus: i2c0f
x_offset: -38.0
y_offset: -22.0
```

> **I2C bus note:** The Eddy Duo typically uses `i2c0e` when connected via USB and `i2c0f` when connected via CAN bus. Check the BTT pinout documentation for your specific board revision.

### Cartographer via CAN Bus

```ini
[mcu cartographer]
canbus_uuid: XXXXXXXXXXXX

[probe_eddy_ng my_eddy]
sensor_type: cartographer
i2c_mcu: cartographer
i2c_bus: i2c0f
x_offset: 0.0
y_offset: 16.0
```

> **Cartographer note:** The Cartographer probe already runs its own Klipper firmware. No firmware flashing is needed -- just configure the MCU and probe section.

### Advanced: Multi-Pass Mesh with Spiral Path

For maximum accuracy at the cost of time:

```ini
[probe_eddy_ng my_eddy]
sensor_type: btt_eddy
i2c_mcu: eddy_duo
i2c_bus: i2c0e
x_offset: -38.0
y_offset: -22.0

# Use spiral path with 3 passes (median averaged)
mesh_path: spiral
mesh_runs: 3
mesh_height: 3.0

# Smooth readings with alpha-beta filter
filter_alpha: 0.6
filter_beta: 1e-5
```

### Advanced: Temperature Compensation + Backlash

For environments with large temperature swings:

```ini
[probe_eddy_ng my_eddy]
sensor_type: btt_eddy
i2c_mcu: eddy_duo
i2c_bus: i2c0e
x_offset: -38.0
y_offset: -22.0

# Backlash compensation (measured with PROBE_EDDY_NG_ESTIMATE_BACKLASH)
z_backlash: 0.015

# Temperature compensation is auto-saved after running
# PROBE_EDDY_NG_TEMPERATURE_CALIBRATE
# temperature_compensation: (auto-saved)
```

### Axis Twist Compensation

eddy-ng automatically integrates with Klipper's `axis_twist_compensation` module and includes a **fully automatic tap-based calibration** -- no manual probing required.

#### Configuration

Add to your printer.cfg:

```ini
[axis_twist_compensation]
calibrate_start_x: 30
calibrate_end_x: 320
calibrate_y: 175
# Optional: Y-axis twist calibration
calibrate_start_y: 30
calibrate_end_y: 270
calibrate_x: 175
```

#### Calibration

Unlike Klipper's built-in `AXIS_TWIST_COMPENSATION_CALIBRATE` (which requires manual TESTZ/ACCEPT at every point), eddy-ng uses tap for fully automatic calibration:

```
# Calibrate X-axis twist (default)
PROBE_EDDY_NG_AXIS_TWIST_CALIBRATE

# Calibrate Y-axis twist
PROBE_EDDY_NG_AXIS_TWIST_CALIBRATE AXIS=Y

# Calibrate both axes in one run
PROBE_EDDY_NG_AXIS_TWIST_CALIBRATE AXIS=BOTH

# Custom parameters
PROBE_EDDY_NG_AXIS_TWIST_CALIBRATE AXIS=BOTH SAMPLE_COUNT=9 SAMPLES=5

SAVE_CONFIG
```

Or use the macro (homes + calibrates + saves automatically):
```
EDDY_NG_AXIS_TWIST_CALIBRATE AXIS=BOTH
```

| Parameter | Default | Description |
|-----------|---------|-------------|
| AXIS | X | Which axis to calibrate: X, Y, or BOTH |
| SAMPLE_COUNT | 7 | Number of probe points per axis |
| SAMPLES | 3 | Tap samples per point (median) |
| START_X/END_X | from config | X-axis range |
| START_Y/END_Y | from config | Y-axis range |
| X/Y | bed center | Fixed position on the other axis |

Once calibrated, the correction is applied automatically during bed mesh scanning.

---

## GCode Commands

### Calibration

| Command | Description |
|---|---|
| `PROBE_EDDY_NG_SETUP` | Interactive first-time setup wizard |
| `PROBE_EDDY_NG_CALIBRATE` | Calibrate frequency-to-height mapping |
| `PROBE_EDDY_NG_CALIBRATE_THRESHOLD` | Auto-find optimal tap threshold |
| `PROBE_EDDY_NG_CALIBRATION_STATUS` | Display calibration information |
| `PROBE_EDDY_NG_CLEAR_CALIBRATION` | Clear all calibration data |
| `PROBE_EDDY_NG_TEST_DRIVE_CURRENT` | Test a specific drive current |
| `PROBE_EDDY_NG_OPTIMIZE_DRIVE_CURRENT` | Test all DCs, find optimal for homing + tap |
| `PROBE_EDDY_NG_TEMPERATURE_CALIBRATE` | Calibrate temperature compensation model |
| `PROBE_EDDY_NG_ESTIMATE_BACKLASH` | Measure Z-axis backlash statistically |
| `PROBE_EDDY_NG_AXIS_TWIST_CALIBRATE` | Auto-calibrate axis twist using tap (X, Y, or BOTH) |

### Probing

| Command | Description |
|---|---|
| `PROBE_EDDY_NG_PROBE` | Probe height (moves to trigger height) |
| `PROBE_EDDY_NG_PROBE_STATIC` | Probe height at current position (no move) |
| `PROBE_EDDY_NG_PROBE_ACCURACY` | Test probe repeatability |
| `PROBE_EDDY_NG_TAP` | Precise Z-offset by touching the bed |

### Configuration Commands

| Command | Description |
|---|---|
| `PROBE_EDDY_NG_STATUS` | Show status and last readings |
| `PROBE_EDDY_NG_SET_TAP_OFFSET` | Set/clear tap offset for scanning |
| `PROBE_EDDY_NG_SET_TAP_ADJUST_Z` | Set additional tap Z adjustment |
| `Z_OFFSET_APPLY_PROBE` | Apply current G-Code Z offset to `tap_adjust_z` |

### Advanced Features

| Command | Description |
|---|---|
| `PROBE_EDDY_NG_MODEL` | Manage named calibration models |
| `PROBE_EDDY_NG_STREAM` | Manage data streaming sessions |

### Quick Aliases

| Alias | Full Command |
|---|---|
| `PES` | `PROBE_EDDY_NG_STATUS` |
| `PEP` | `PROBE_EDDY_NG_PROBE` |
| `PEPS` | `PROBE_EDDY_NG_PROBE_STATIC` |
| `PETAP` | `PROBE_EDDY_NG_TAP` |

### Command Details

#### PROBE_EDDY_NG_CALIBRATE_THRESHOLD

Performs an ascending search to find the optimal tap threshold automatically:

```
PROBE_EDDY_NG_CALIBRATE_THRESHOLD [MODE=butter] [START=50] [MAX=2000] [SPEED=3.0]
    [SCREENING_SAMPLES=5] [VERIFICATION_SAMPLES=10] [SAMPLE_RANGE=0.010]
```

| Parameter | Default (butter) | Default (wma) | Description |
|---|---|---|---|
| `MODE` | `butter` | - | Detection mode |
| `START` | `50` | `200` | Starting threshold |
| `MAX` | `2000` | `10000` | Maximum threshold to test |
| `SPEED` | `3.0` | `3.0` | Tap speed (mm/s) |
| `SCREENING_SAMPLES` | `5` | `5` | Quick screening taps per threshold |
| `VERIFICATION_SAMPLES` | `10` | `10` | Full verification taps |
| `SAMPLE_RANGE` | `0.010` | `0.010` | Required Z range (mm) |

Tests thresholds with adaptive step sizes. Each threshold goes through screening, then full verification. The first passing threshold is saved. Run `SAVE_CONFIG` afterwards.

#### PROBE_EDDY_NG_OPTIMIZE_DRIVE_CURRENT

Tests all drive currents and selects the optimal one for homing and tap:

```
PROBE_EDDY_NG_OPTIMIZE_DRIVE_CURRENT [START_DC=1] [END_DC=31] [TAP_VERIFY=5]
    [TOP_CANDIDATES=3] [MODE=butter] [SAVE=1] [DEBUG=0]
```

| Parameter | Default | Description |
|---|---|---|
| `START_DC` | `1` | First drive current to test |
| `END_DC` | `31` | Last drive current to test |
| `TAP_VERIFY` | `5` | Real test taps per top candidate |
| `TOP_CANDIDATES` | `3` | How many top DCs to verify |
| `MODE` | `butter` | Tap mode for verification |
| `SAVE` | `1` | Auto-save results |
| `DEBUG` | `0` | Write debug files |

**Phase 1 -- Calibration sweep:** Full Z sweep per DC, scored on RMSE, frequency spread, height range.

**Phase 2 -- Tap verification:** Top candidates tested with real taps, scored on range, stddev, success rate.

Run `SAVE_CONFIG` afterwards.

#### PROBE_EDDY_NG_TEMPERATURE_CALIBRATE

Calibrates a temperature drift compensation model. Requires scipy.

```
PROBE_EDDY_NG_TEMPERATURE_CALIBRATE [MIN_TEMP=40] [MAX_TEMP=60] [BED_TEMP=90]
```

| Parameter | Default | Description |
|---|---|---|
| `MIN_TEMP` | `40` | Start collecting data at this temperature (C) |
| `MAX_TEMP` | `60` | Stop collecting data at this temperature (C) |
| `BED_TEMP` | `90` | Bed heater target temperature (C) |

The procedure:
1. For each calibration height (1, 2, 3 mm):
   - Cools down to `MIN_TEMP` (fan on, heater off)
   - Heats bed to `BED_TEMP` (fan off)
   - Collects frequency-temperature pairs while heating through `MIN_TEMP` to `MAX_TEMP`
2. Fits a quadratic compensation model from the collected data
3. Saves the model. Run `SAVE_CONFIG` to persist.

**This takes 30-60 minutes.** Do not touch the printer during calibration.

Once calibrated, temperature compensation is applied automatically to all probe readings.

#### PROBE_EDDY_NG_ESTIMATE_BACKLASH

Measures Z-axis backlash by probing from both directions and applying Welch's t-test:

```
PROBE_EDDY_NG_ESTIMATE_BACKLASH [ITERATIONS=10] [DELTA=0.5] [SPEED=3.0] [CALIBRATE=0]
```

| Parameter | Default | Description |
|---|---|---|
| `ITERATIONS` | `10` | Number of measurement pairs (up+down) |
| `DELTA` | `0.5` | Distance to travel in each direction (mm) |
| `SPEED` | `3.0` | Measurement speed (mm/s) |
| `CALIBRATE` | `0` | Set to 1 to auto-save `z_backlash` if significant |

Reports:
- Mean height from above vs. from below
- Standard deviation for each direction
- t-statistic and degrees of freedom
- Whether the backlash is statistically significant (|t| >= 2.0, approximately p <= 0.05)

If `CALIBRATE=1` and the result is significant, `z_backlash` is updated. Run `SAVE_CONFIG` afterwards.

#### PROBE_EDDY_NG_MODEL

Manage named calibration models -- useful for switching between different build plates:

```
PROBE_EDDY_NG_MODEL ACTION=SAVE NAME=pei_sheet
PROBE_EDDY_NG_MODEL ACTION=LOAD NAME=textured_pei
PROBE_EDDY_NG_MODEL ACTION=LIST
PROBE_EDDY_NG_MODEL ACTION=DELETE NAME=old_model
```

| Action | Description |
|---|---|
| `SAVE` | Save current calibration under a name |
| `LOAD` | Load a saved model as active calibration |
| `LIST` | List all saved model names |
| `DELETE` | Delete a saved model |

Models are stored in the printer config. Run `SAVE_CONFIG` after SAVE or DELETE.

**Example workflow with multiple build plates:**

```gcode
; Calibrate with PEI sheet installed
PROBE_EDDY_NG_CALIBRATE
PROBE_EDDY_NG_MODEL ACTION=SAVE NAME=smooth_pei
SAVE_CONFIG

; Later, swap to textured PEI and recalibrate
PROBE_EDDY_NG_CALIBRATE
PROBE_EDDY_NG_MODEL ACTION=SAVE NAME=textured_pei
SAVE_CONFIG

; Switch between plates without recalibrating
PROBE_EDDY_NG_MODEL ACTION=LOAD NAME=smooth_pei
; or
PROBE_EDDY_NG_MODEL ACTION=LOAD NAME=textured_pei
```

#### PROBE_EDDY_NG_STREAM

Manage data streaming sessions for diagnostic data capture:

```
PROBE_EDDY_NG_STREAM ACTION=START [FILE=/tmp/my_data.csv]
PROBE_EDDY_NG_STREAM ACTION=STOP
PROBE_EDDY_NG_STREAM ACTION=CANCEL
PROBE_EDDY_NG_STREAM ACTION=STATUS
```

| Action | Description |
|---|---|
| `START` | Begin recording sensor data. Optional `FILE` parameter for output path. |
| `STOP` | Stop recording and write CSV file. |
| `CANCEL` | Stop recording and discard data. |
| `STATUS` | Show current streaming status (sample count, duration). |

CSV output columns: `time, frequency, temperature, position_x, position_y, position_z`

---

## Typical Workflow

### First-Time Setup

#### Option A: Guided Macros (recommended)

Include the calibration macros in your printer.cfg:

```ini
[include calibrate_macros.cfg]
```

Copy the file from the eddy-ng repo to your config directory, or symlink it:

```bash
cp ~/eddy-ng/calibrate_macros.cfg ~/printer_data/config/
```

Then run each macro in order. After each `SAVE_CONFIG` restart, Klipper tells you which macro to run next:

**Base calibration (required):**

| Step | Macro | What it does | Time |
|------|-------|-------------|------|
| 1/7 | `EDDY_NG_STEP1_SETUP` | Initial setup (manual Z positioning) | ~2 min |
| 2/7 | `EDDY_NG_STEP2_OPTIMIZE_DC` | Optimize drive current | ~2 min |
| 3/7 | `EDDY_NG_STEP3_CALIBRATE_THRESHOLD` | Calibrate tap threshold | ~2 min |
| 4/7 | `EDDY_NG_STEP4_VERIFY` | Verify homing, tap, accuracy | ~1 min |

**Advanced calibration (optional, for best accuracy):**

| Step | Macro | What it does | Time |
|------|-------|-------------|------|
| 5/7 | `EDDY_NG_STEP5_TEMP_CALIBRATE` | Temperature compensation (requires scipy) | 30-60 min |
| 6/7 | `EDDY_NG_STEP6_AXIS_TWIST` | Axis twist compensation via tap | ~6 min |
| 7/7 | `EDDY_NG_STEP7_BACKLASH` | Z-axis backlash estimation | ~2 min |

> **Note:** Step 1 requires manual interaction (lowering the nozzle with `TESTZ`). All other steps are fully automatic.

**Configuring defaults:** Edit `_EDDY_NG_SETTINGS` in `calibrate_macros.cfg` to set your printer's default values. These are used when no parameter is given:

```ini
[gcode_macro _EDDY_NG_SETTINGS]
# Gantry leveling before advanced calibrations (Steps 5-7)
# "QUAD_GANTRY_LEVEL" for Voron 2.4, "Z_TILT_ADJUST" for Trident, "" for none
variable_level_command: "QUAD_GANTRY_LEVEL"

# Temperature Compensation (Step 5)
variable_bed_temp: 110          # Bed temperature
variable_min_temp: 40           # Min sensor temp to measure
variable_max_temp: 70           # Max sensor temp to measure
variable_hotend_fan: 0          # 1 = use hotend fan for faster cooling

# Axis Twist (Step 6)
variable_twist_axis: "BOTH"     # X, Y, or BOTH
variable_twist_sample_count: 7  # Points per axis
variable_twist_bed_temp: 60     # Bed temp (0 = cold)
variable_twist_hotend_temp: 150 # Hotend temp (0 = cold)
```

You can always override individual values per call, e.g. `EDDY_NG_STEP5_TEMP_CALIBRATE BED_TEMP=110 HOTEND_FAN=1`. Run `_EDDY_NG_SETTINGS` to display the current defaults.

#### Option B: Automatic Script (via SSH)

Run the calibration from the command line. The script handles all Klipper restarts automatically and only pauses for the manual Z positioning in step 1:

```bash
# Base calibration (steps 1-4):
~/eddy-ng/scripts/calibrate.sh

# Full calibration (all 7 steps):
~/eddy-ng/scripts/calibrate.sh --full --bed-temp 110

# Full calibration with all options:
~/eddy-ng/scripts/calibrate.sh --full --bed-temp 110 --min-temp 40 --max-temp 70 \
    --hotend-fan --twist-bed 60 --twist-hotend 150

# Remote:
~/eddy-ng/scripts/calibrate.sh --url http://printer-ip:7125
```

#### Cartographer (native firmware)

If using a Cartographer probe with its native firmware (not eddy-ng firmware), use the Cartographer-specific macros instead:

```ini
[include calibrate_macros_cartographer.cfg]
```

| Step | Macro | What it does | Time |
|------|-------|-------------|------|
| 1/7 | `CARTO_STEP1_SCAN_CALIBRATE` | Scan calibration | ~2 min |
| 2/7 | `CARTO_STEP2_TOUCH_CALIBRATE` | Touch calibration | ~2 min |
| 3/7 | `CARTO_STEP3_VERIFY` | Verify touch + scan | ~1 min |
| 4/7 | `CARTO_STEP4_TEMP_CALIBRATE` | Temperature compensation | 30-60 min |
| 5/7 | `CARTO_STEP5_AXIS_TWIST` | Axis twist via touch | ~6 min |
| 6/7 | `CARTO_STEP6_BACKLASH` | Backlash estimation | ~2 min |
| 7/7 | `CARTO_STEP7_FULL_VERIFY` | Final verification | ~3 min |

Configure defaults in `_CARTO_SETTINGS` (same concept as `_EDDY_NG_SETTINGS`).

#### Option C: Manual Commands

1. Install eddy-ng and configure `printer.cfg`
2. `G28 X Y`
3. `PROBE_EDDY_NG_SETUP` -- lower nozzle with `TESTZ`, then `ACCEPT`
4. `SAVE_CONFIG`
5. `G28` then `PROBE_EDDY_NG_OPTIMIZE_DRIVE_CURRENT`
6. `SAVE_CONFIG`
7. `G28` then `PROBE_EDDY_NG_CALIBRATE_THRESHOLD`
8. `SAVE_CONFIG`

#### Optional Advanced Calibration

After the base setup (steps 1-4), continue with steps 5-7 for best accuracy. These can also be run individually:

- **Temperature compensation (step 5):** `EDDY_NG_STEP5_TEMP_CALIBRATE BED_TEMP=110 MIN_TEMP=40 MAX_TEMP=70` -- calibrate temperature drift across 3 heights (requires scipy, 30-60 min). Add `HOTEND_FAN=1` to use the hotend fan for faster cooling between phases.
- **Axis twist (step 6):** `EDDY_NG_STEP6_AXIS_TWIST BED_TEMP=60 HOTEND_TEMP=150` -- fully automatic tap-based twist calibration for X and Y axes (~6 min). Best done at print temperature. Requires `[axis_twist_compensation]` in printer.cfg.
- **Z backlash (step 7):** `EDDY_NG_STEP7_BACKLASH` -- measure and compensate Z backlash (~2 min)

### Before Every Print

1. `G28` -- Home all axes (uses eddy for Z)
2. Heat bed to print temperature
3. `PROBE_EDDY_NG_TAP` -- Precise Z-offset at temperature
4. `BED_MESH_CALIBRATE` -- Bed mesh with thermal compensation
5. Start printing

See the [Print Start Macro](#example-print-start-macro) for an automated version.

---

## Updating

### Updating eddy-ng only

Since eddy-ng loads directly from the cloned repo, updating is just a `git pull`:

```bash
cd ~/eddy-ng
git pull
sudo systemctl restart klipper
```

If the scaffolding format changed (rare, noted in release notes), re-run the installer:

```bash
cd ~/eddy-ng
./install.sh
sudo systemctl restart klipper
```

### Updating Klipper (important!)

A Klipper update (`git pull` in `~/klipper`) **overwrites `src/Makefile`**, which removes the eddy-ng firmware patch (only relevant for non-Duo sensors). Use the update script:

```bash
cd ~/eddy-ng
./update-klipper.sh
```

The script:
1. Updates eddy-ng (`git pull`)
2. Updates Klipper (`git pull`) -- stashes local changes if needed
3. Re-installs eddy-ng (re-patches `src/Makefile`, re-links files)
4. **Asks** whether to rebuild firmware
5. **Asks** whether to flash the rebuilt firmware
6. **Asks** whether to restart Klipper service

```
=== Step 4: Firmware ===

[WARN] Klipper was updated (a1b2c3d -> e4f5g6h).
[WARN] Detected 3 changed files in src/ -- firmware rebuild likely needed!

Rebuild MCU firmware? [y/N] y
[OK] Firmware rebuilt successfully

Flash firmware to MCU? [y/N] n
[INFO] Run ./flash.sh later to flash.
```

Use `--yes` to skip all prompts:

```bash
./update-klipper.sh --yes    # Non-interactive: update + rebuild + flash + restart
```

**Eddy Duo users:** Klipper updates do not affect the Duo firmware. You only need to re-flash the Duo if the eddy-ng firmware module changes (which is rare and will be noted in release notes).

---

## Uninstallation

### Interactive Uninstallation

```bash
cd ~/eddy-ng
./install.sh --uninstall
```

### Using the uninstall script

```bash
cd ~/eddy-ng
./uninstall.sh
```

With a custom Klipper path:

```bash
./uninstall.sh /path/to/klipper
```

### Manual uninstallation via install.py

```bash
cd ~/eddy-ng
python3 install.py --uninstall
```

### What the uninstaller removes

| Component | Location |
|---|---|
| Python package | pip uninstall from Klipper virtualenv (if previously installed) |
| Scaffolding files | `klippy/extras/probe_eddy_ng.py`, `klippy/extras/ldc1612_ng.py` |
| Calibration macros | `calibrate_macros.cfg` in Klipper config directory |
| Plugin directory | `klippy/extras/probe_eddy_ng/` (or `klippy/plugins/` for Kalico) |
| Sensor driver | `klippy/extras/ldc1612_ng.py` |
| Firmware module | `src/sensor_ldc1612_ng.c` |
| Makefile patch | Reverted in `src/Makefile` |
| Legacy files | `probe_eddy_ng.py` (old single-file install) |
| Legacy patches | `bed_mesh.py` patches from older versions |

> **Note:** `eddy-ng.cfg` is **not** removed on uninstall since it contains user customizations. Delete it manually if desired.

After uninstalling, remove the `[probe_eddy_ng ...]` section from your `printer.cfg` and restart Klipper.

**Note:** Calibration data stored in `printer.cfg` (under `#*# [probe_eddy_ng ...]`) is not removed automatically. Delete it manually if desired.

---

## Project Structure

```
eddy-ng/
├── probe_eddy_ng/               # Python package (Klipper plugin)
│   ├── __init__.py              #   Package entry point & exports
│   ├── _compat.py               #   Klipper/Kalico compatibility layer
│   ├── probe.py                 #   Main ProbeEddy class & GCode commands
│   ├── params.py                #   Configuration parameters & validation
│   ├── frequency_map.py         #   Calibration data & named models
│   ├── sampler.py               #   Sensor sample collection & filtering
│   ├── endstop.py               #   Virtual endstop for Z homing
│   ├── scanning.py              #   Rapid bed mesh scanning
│   ├── bed_mesh_helper.py       #   Bed mesh integration & path planning
│   ├── alpha_beta_filter.py     #   Alpha-beta tracking filter
│   ├── temperature_compensation.py  # Temperature drift compensation
│   ├── backlash.py              #   Z-axis backlash estimation
│   ├── mesh_paths.py            #   Mesh path algorithms (snake, spiral, ...)
│   └── streaming.py             #   Data streaming with CSV export
├── ldc1612_ng.py                # LDC1612 sensor driver
├── eddy-ng/                     # MCU firmware module (C)
│   ├── sensor_ldc1612_ng.c      #   Sensor driver with tap detection
│   ├── Kconfig                  #   Firmware build options
│   └── Makefile                 #   Build integration
├── src/eddy_ng/                 # pip package structure
│   ├── __init__.py              #   Package skeleton
│   └── scaffolding/             #   Import bridges for klippy/extras/
├── calibrate_macros.cfg          # Calibration macro set (7-step workflow, eddy-ng)
├── calibrate_macros_cartographer.cfg # Calibration macros for native Cartographer firmware
├── scripts/
│   ├── install.sh               #   Interactive installer
│   ├── flash-duo.sh             #   Eddy Duo firmware flash utility
│   └── calibrate.sh             #   Automatic calibration via Moonraker API
├── firmware/                    # Pre-built firmware images
│   └── README.md
├── tests/                       # Test suite
├── install.py                   # Legacy installer
├── install.sh                   # Install wrapper
├── uninstall.sh                 # Uninstall wrapper
├── flash.sh                     # Firmware build & flash automation
├── update-klipper.sh            # Safe Klipper update with re-patching
├── pyproject.toml               # pip/hatchling build configuration
├── .github/workflows/
│   ├── ci.yml                   #   CI pipeline
│   └── build-firmware.yml       #   RP2040 firmware build pipeline
└── LICENSE                      # GPLv3
```

---

## Troubleshooting

### "x_offset and y_offset are both 0.0"

You must set the physical offset between the probe coil and the nozzle. Measure carefully. If the probe is genuinely at the nozzle position (unusual), set `allow_unsafe: True`.

### "Calibration required first"

Run `PROBE_EDDY_NG_SETUP` or `PROBE_EDDY_NG_CALIBRATE` before using probe/tap/mesh commands.

### "butter mode with custom filter parameters requires scipy"

Install scipy: `~/klippy-env/bin/pip install scipy`

Or use default Butterworth parameters (no scipy needed) or switch to `tap_mode: wma`.

### Tap triggers too early / too late

Run `PROBE_EDDY_NG_CALIBRATE_THRESHOLD` to auto-tune the threshold. Lower threshold = more sensitive (triggers earlier), higher = less sensitive.

### Bed mesh has poor accuracy

- Increase `mesh_runs` to 2 or 3 for multi-pass averaging
- Try `mesh_path: alternating_snake` for cross-direction averaging
- Run `PROBE_EDDY_NG_ESTIMATE_BACKLASH CALIBRATE=1` to measure and compensate backlash
- Calibrate temperature compensation if the bed heats significantly during scanning

### Klipper won't start after installation

Check the Klipper log (`/tmp/klippy.log` or `~/printer_data/logs/klippy.log`) for import errors. Common causes:
- numpy not installed in Klipper virtualenv: `~/klippy-env/bin/pip install numpy`
- Sensor driver not compiled: re-run `./flash.sh` to rebuild firmware
- Old eddy-ng pip package conflicting with repo imports: `~/klippy-env/bin/pip uninstall eddy-ng`

### "homing_override and safe_z_homing cannot be used simultaneously"

Klipper does not allow `[safe_z_home]` and `[homing_override]` in the same config. This commonly happens with **toolchanger setups**, where the toolchanger config already defines `[homing_override]` (e.g. in `homing.cfg`).

**Fix:** Remove the `[safe_z_home]` section from your eddy-ng config and handle Z homing inside your existing `[homing_override]` macro instead. Example:

```ini
# In your homing_override, add Z homing via the eddy probe:
[homing_override]
gcode:
    # ... existing XY homing logic ...
    # Home Z using the eddy probe
    G28 Z
```

### "attempted relative import with no known parent package"

This usually means the eddy-ng pip package is installed alongside the repo-based scaffolding. The pip package's import paths can conflict with Klipper's module loading.

**Fix:**
```bash
~/klippy-env/bin/pip uninstall eddy-ng
sudo systemctl restart klipper
```

The installer already handles this automatically, but if you previously did a manual `pip install`, uninstall it.

### "No module named 'probe'" / "No module named 'bus'"

Klipper's extras modules (probe.py, bus.py, etc.) use relative imports and must be loaded as part of the `extras` package. This is handled automatically by eddy-ng's compatibility layer. If you see this error:

1. Make sure you are using the latest eddy-ng version: `cd ~/eddy-ng && git pull`
2. Re-run the installer: `./scripts/install.sh`
3. Restart Klipper: `sudo systemctl restart klipper`

### "Unknown pin chip name 'probe'"

Your config still references a different probe plugin (e.g. `cartographer_eddy` or `probe_eddy_current`). The virtual endstop pin name follows the config section name.

**Fix:** Make sure your `stepper_z` uses:
```ini
endstop_pin: probe:z_virtual_endstop
```

And that your config has `[probe_eddy_ng my_eddy]` (not `[cartographer_eddy ...]` or similar).

### QGL fails with "Probed points range is increasing"

This is **not** a Z motor wiring issue. Eddy current probes have reduced accuracy at higher distances from the bed. The default coarse phase of a 2-stage QGL (typically at 10mm `HORIZONTAL_MOVE_Z`) may produce readings that oscillate by ~0.1mm, which is too much for a tight tolerance like 0.05mm.

**Fix:** Increase the coarse tolerance so the coarse phase passes quickly, then let the fine phase (at 3mm) do the precision work. Example 2-stage QGL macro:

```ini
[gcode_macro QUAD_GANTRY_LEVEL]
rename_existing: _QUAD_GANTRY_LEVEL
gcode:
  {% set coarse_z   = params.COARSE_Z|default(10.0)|float %}
  {% set fine_z     = params.FINE_Z|default(3.0)|float %}
  {% set coarse_tol = params.COARSE_TOL|default(0.15)|float %}
  {% set fine_tol   = params.FINE_TOL|default(0.005)|float %}
  {% set coarse_r   = params.COARSE_RETRIES|default(10)|int %}
  {% set fine_r     = params.FINE_RETRIES|default(10)|int %}

  M117 QGL coarse
  _QUAD_GANTRY_LEVEL HORIZONTAL_MOVE_Z={coarse_z} RETRY_TOLERANCE={coarse_tol} RETRIES={coarse_r}
  G90
  G0 Z{coarse_z} F1000
  M117 QGL fine
  _QUAD_GANTRY_LEVEL HORIZONTAL_MOVE_Z={fine_z} RETRY_TOLERANCE={fine_tol} RETRIES={fine_r}
  M117 QGL done
```

A coarse tolerance of 0.15mm works well -- the fine phase at 3mm height reliably converges to <0.005mm.

**Note:** If you use `axis_twist_compensation`, keep it **enabled** during QGL. The compensation corrects systematic probe errors and helps QGL converge more accurately.

### Eddy Duo not detected

1. Check USB connection: `ls /dev/serial/by-id/`
2. Ensure the Duo is flashed with Klipper firmware (not stock BTT firmware)
3. Try reflashing: `./scripts/flash-duo.sh`
4. For CAN bus: verify `canbus_uuid` and CAN bus configuration

### Pre-built firmware not available

The `firmware/` directory may not contain pre-built binaries for your configuration. In that case, build from source:

```bash
./scripts/flash-duo.sh
# Choose option 4: "Build from source"
```

The build script guides you through connection type, CAN baud rate, bootloader offset, and GPIO pin selection interactively.

### Permission denied running install.sh

If you cloned the repo on Windows and copied it to Linux, the execute permission bits may be missing:

```bash
chmod +x install.sh scripts/*.sh flash.sh uninstall.sh update-klipper.sh
```

### Checking logs remotely via Moonraker API

If you don't have SSH access, you can read the Klipper log via Moonraker's HTTP API:

```bash
# Read the last 100 lines of klippy.log
curl -s "http://<printer-ip>/server/files/klippy.log" | tail -100

# Check printer status
curl -s "http://<printer-ip>/printer/info"

# List config files
curl -s "http://<printer-ip>/server/files/list?root=config"
```

---

## Support

- **Discord:** [Sovol 3D Printers Discord](https://discord.gg/Zg45rA52G7) -- eddy-ng forum (not Sovol-specific)
- **Issues:** [GitHub Issues](https://github.com/vvuk/eddy-ng/issues)
- **Wiki:** [Full setup guide](https://github.com/vvuk/eddy-ng/wiki)

## License

This project is licensed under the [GNU General Public License v3](LICENSE).

Based on original `probe_eddy_current` code by Kevin O'Connor. Maintained by Vladimir Vukicevic.
