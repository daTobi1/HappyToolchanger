"""
Full eddy-ng installation on 192.168.178.60
Supports both Cartographer (STM32F042) and BTT Eddy Duo (RP2040).
Supports 250mm and 350mm printer sizes.

Usage:
    python install_eddy_ng.py                          # Interactive (auto-detect + confirm)
    python install_eddy_ng.py --btt-eddy --size 250    # Skip probe/size prompts
    python install_eddy_ng.py --cartographer --size 350
    python install_eddy_ng.py -y                       # Auto-detect, no confirmation

Steps: update repo, install python, patch firmware, build, create config, flash
"""
import paramiko, sys, io, re, time, argparse
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

parser = argparse.ArgumentParser(description="eddy-ng installer")
parser.add_argument("--btt-eddy", action="store_true", help="Force BTT Eddy Duo (RP2040)")
parser.add_argument("--cartographer", action="store_true", help="Force Cartographer (STM32F042)")
parser.add_argument("--size", type=int, choices=[250, 350], help="Printer size in mm (250 or 350)")
parser.add_argument("-y", "--yes", action="store_true", help="Skip interactive confirmation")
args = parser.parse_args()

ssh = paramiko.SSHClient()
ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
ssh.connect('192.168.178.60', username='biqu', password='biqu', timeout=10)

def run(cmd, timeout=60):
    stdin, stdout, stderr = ssh.exec_command(cmd, timeout=timeout)
    o = stdout.read().decode('utf-8', errors='replace').strip()
    e = stderr.read().decode('utf-8', errors='replace').strip()
    return o, e

def sudo(cmd):
    return run(f'echo biqu | sudo -S {cmd}')

def step(title):
    print(f"\n{'='*60}")
    print(f"  {title}")
    print(f"{'='*60}")

# ════════════════════════════════════════════════════════════
#  Printer geometry presets
# ════════════════════════════════════════════════════════════
GEOMETRY = {
    250: {
        "zero_reference_position": "125, 115",
        "mesh_min": "30, 30",
        "mesh_max": "220, 230",
        "probe_count": "10, 10",
        "qgl_gantry_corners": "   -60,-10\n   310, 320",
        "qgl_points": "  20,20\n  20,190\n  230,190\n  230,20",
        "qgl_horizontal_move_z": "10",
        "qgl_max_adjust": "20",
    },
    350: {
        "zero_reference_position": "175, 150",
        "mesh_min": "20, 30",
        "mesh_max": "320, 270",
        "probe_count": "9, 9",
        "qgl_gantry_corners": "   -60,-10\n   410, 420",
        "qgl_points": "  20,25\n  20,275\n  325,275\n  325,25",
        "qgl_horizontal_move_z": "3",
        "qgl_max_adjust": "60",
    },
}

# ════════════════════════════════════════════════════════════
#  Auto-detect + interactive selection
# ════════════════════════════════════════════════════════════

# --- Probe type ---
if args.btt_eddy:
    PROBE_TYPE = "btt_eddy"
elif args.cartographer:
    PROBE_TYPE = "cartographer"
else:
    cfg_out, _ = run("grep -s 'sensor_type' ~/printer_data/config/eddy-ng.cfg 2>/dev/null")
    if 'btt_eddy' in cfg_out:
        PROBE_TYPE = "btt_eddy"
    elif 'cartographer' in cfg_out:
        PROBE_TYPE = "cartographer"
    else:
        PROBE_TYPE = "btt_eddy"

# --- Printer size ---
if args.size:
    PRINTER_SIZE = args.size
else:
    # Try to detect from max_position in printer.cfg
    pos_out, _ = run("grep 'max_position' ~/printer_data/config/printer.cfg 2>/dev/null | head -3")
    if '350' in pos_out or '355' in pos_out:
        PRINTER_SIZE = 350
    else:
        PRINTER_SIZE = 250

# --- Show detection results + let user confirm/change ---
def mcu_label(ptype):
    return "RP2040 (BTT Eddy Duo)" if ptype == "btt_eddy" else "STM32F042 (Cartographer)"

if not args.yes and not (args.btt_eddy or args.cartographer) and not args.size:
    print(f"\n{'='*60}")
    print(f"  eddy-ng Installer — Konfiguration")
    print(f"{'='*60}")
    print(f"\n  Erkannte Einstellungen:")
    print(f"    [1] Probe:   {mcu_label(PROBE_TYPE)}")
    print(f"    [2] Groesse: {PRINTER_SIZE}mm")
    print()

    choice = input("  Stimmt das? [J/n] ").strip().lower()
    if choice in ('n', 'nein', 'no'):
        # Probe selection
        print(f"\n  Probe-Typ waehlen:")
        print(f"    1 = BTT Eddy Duo (RP2040)")
        print(f"    2 = Cartographer (STM32F042)")
        p = input(f"  Wahl [1/2] (aktuell: {'1' if PROBE_TYPE == 'btt_eddy' else '2'}): ").strip()
        if p == '1':
            PROBE_TYPE = "btt_eddy"
        elif p == '2':
            PROBE_TYPE = "cartographer"

        # Size selection
        print(f"\n  Druckergroesse waehlen:")
        print(f"    1 = 250mm")
        print(f"    2 = 350mm")
        s = input(f"  Wahl [1/2] (aktuell: {'1' if PRINTER_SIZE == 250 else '2'}): ").strip()
        if s == '1':
            PRINTER_SIZE = 250
        elif s == '2':
            PRINTER_SIZE = 350

MCU_NAME = mcu_label(PROBE_TYPE)
GEO = GEOMETRY[PRINTER_SIZE]

print(f"\n  >> Probe:   {MCU_NAME}")
print(f"  >> Groesse: {PRINTER_SIZE}mm")
print()

# ============================================================
step("1/8 — Update eddy-ng repo")
# ============================================================
o, e = run("cd ~/eddy-ng && git pull origin main 2>&1")
print(o)

# ============================================================
step("2/8 — Install Python dependencies")
# ============================================================
# Uninstall old pip package if any
o, e = run("~/klippy-env/bin/pip uninstall -y eddy-ng 2>/dev/null; echo done")
print(f"Uninstall old: {o}")

# Check numpy
o, e = run('~/klippy-env/bin/python -c "import numpy; print(numpy.__version__)" 2>&1')
print(f"numpy: {o}")
if 'Error' in o or 'error' in o:
    print("Installing numpy...")
    o, e = run("~/klippy-env/bin/pip install 'numpy>=1.16' 2>&1", timeout=120)
    print(o[-200:])

# ============================================================
step("3/8 — Create scaffolding (Python module loading)")
# ============================================================
# Remove any legacy files first
o, e = run("""
rm -f ~/klipper/klippy/extras/probe_eddy_ng.py
rm -f ~/klipper/klippy/extras/ldc1612_ng.py
rm -rf ~/klipper/klippy/extras/probe_eddy_ng
rm -f ~/klipper/src/sensor_ldc1612_ng.c
echo "Legacy cleaned"
""")
print(o)

REPO_DIR = "/home/biqu/eddy-ng"
SCAFF_DIR = "/home/biqu/klipper/klippy/extras"

# Create probe_eddy_ng.py scaffolding
o, e = run(f"""cat > {SCAFF_DIR}/probe_eddy_ng.py << 'PYEOF'
# eddy-ng scaffolding -- generated by installer
import sys
_repo_dir = "{REPO_DIR}"
if _repo_dir not in sys.path:
    sys.path.insert(0, _repo_dir)
from probe_eddy_ng import load_config_prefix  # noqa: F401
PYEOF
echo "probe_eddy_ng.py created"
""")
print(o)

# Create ldc1612_ng.py scaffolding
o, e = run(f"""cat > {SCAFF_DIR}/ldc1612_ng.py << 'PYEOF'
# eddy-ng scaffolding -- generated by installer
import sys
_repo_dir = "{REPO_DIR}"
if _repo_dir not in sys.path:
    sys.path.insert(0, _repo_dir)
from ldc1612_ng import load_config  # noqa: F401
PYEOF
echo "ldc1612_ng.py created"
""")
print(o)

# Verify
o, _ = run(f"ls -la {SCAFF_DIR}/probe_eddy_ng.py {SCAFF_DIR}/ldc1612_ng.py")
print(o)

# ============================================================
step("4/8 — Install firmware patch (Makefile + symlink)")
# ============================================================
# Symlink C source
o, e = run(f"""
ln -sf {REPO_DIR}/eddy-ng/sensor_ldc1612_ng.c ~/klipper/src/sensor_ldc1612_ng.c
# Also need printf.h for compilation (even though it's not linked without LDC_DEBUG)
ln -sf {REPO_DIR}/eddy-ng/printf.h ~/klipper/src/printf.h
ln -sf {REPO_DIR}/eddy-ng/printf_config.h ~/klipper/src/printf_config.h
echo "Symlinks created"
ls -la ~/klipper/src/sensor_ldc1612_ng.c ~/klipper/src/printf.h
""")
print(o)

# Patch Makefile
o, e = run("""
cd ~/klipper
if grep -q sensor_ldc1612_ng src/Makefile; then
    echo "Makefile already patched"
else
    sed -i 's/src-$(CONFIG_WANT_LDC1612) += sensor_ldc1612.c$/src-$(CONFIG_WANT_LDC1612) += sensor_ldc1612.c sensor_ldc1612_ng.c/' src/Makefile
    echo "Makefile patched"
fi
grep sensor_ldc1612 src/Makefile
""")
print(o)

# ============================================================
step(f"5/8 — Write .config for {MCU_NAME}")
# ============================================================

# Common options (disabled features for minimal firmware)
COMMON_DISABLED = """CONFIG_WANT_ADC=n
CONFIG_WANT_SPI=n
CONFIG_WANT_SOFTWARE_SPI=n
CONFIG_WANT_HARD_PWM=n
CONFIG_WANT_BUTTONS=n
CONFIG_WANT_TMCUART=n
CONFIG_WANT_NEOPIXEL=n
CONFIG_WANT_PULSE_COUNTER=n
CONFIG_WANT_ST7920=n
CONFIG_WANT_HD44780=n
CONFIG_WANT_THERMOCOUPLE=n
CONFIG_WANT_ADXL345=n
CONFIG_WANT_LIS2DW=n
CONFIG_WANT_BMI160=n
CONFIG_WANT_MPU9250=n
CONFIG_WANT_ICM20948=n
CONFIG_WANT_HX71X=n
CONFIG_WANT_ADS1220=n
CONFIG_WANT_SENSOR_ANGLE=n
CONFIG_WANT_TRIGGER_ANALOG=n
CONFIG_WANT_STEPPER=n
CONFIG_USB_VENDOR_ID=0x1d50
CONFIG_USB_DEVICE_ID=0x614e
CONFIG_USB_SERIAL_NUMBER_CHIPID=y
CONFIG_INITIAL_PINS=""
"""

if PROBE_TYPE == "btt_eddy":
    # ── RP2040 (BTT Eddy Duo) ──────────────────────────────
    # CAN bus on GPIO4 (RX) / GPIO5 (TX)
    # 16KiB Katapult bootloader offset -> flash start 0x10004000
    minimal_config = f"""CONFIG_LOW_LEVEL_OPTIONS=y
CONFIG_MACH_RPXXXX=y
CONFIG_BOARD_DIRECTORY="rpxxxx"
CONFIG_MCU="rp2040"
CONFIG_RPXXXX_SELECT=y
CONFIG_MACH_RP2040=y
CONFIG_RPXXXX_CANBUS=y
CONFIG_CANBUS=y
CONFIG_CANBUS_FREQUENCY=1000000
CONFIG_RPXXXX_CANBUS_GPIO_RX=4
CONFIG_RPXXXX_CANBUS_GPIO_TX=5
CONFIG_RPXXXX_FLASH_START_4000=y
CONFIG_FLASH_APPLICATION_ADDRESS=0x10004000
CONFIG_WANT_GPIO_BITBANGING=y
CONFIG_WANT_I2C=y
CONFIG_WANT_SOFTWARE_I2C=y
CONFIG_WANT_LDC1612=y
CONFIG_NEED_SENSOR_BULK=y
{COMMON_DISABLED}"""
    FW_MAX_SIZE = 2 * 1024 * 1024 - 0x4000  # 2MB flash minus 16KiB Katapult
    FW_MAX_LABEL = "2MB - 16KiB Katapult"
else:
    # ── STM32F042 (Cartographer) ────────────────────────────
    # CAN bus on PA11/PA12
    # 8KiB Katapult bootloader offset -> flash start 0x8002000
    minimal_config = f"""CONFIG_LOW_LEVEL_OPTIONS=y
CONFIG_MACH_STM32=y
CONFIG_BOARD_DIRECTORY="stm32"
CONFIG_MCU="stm32f042x6"
CONFIG_CLOCK_FREQ=48000000
CONFIG_FLASH_SIZE=0x8000
CONFIG_FLASH_BOOT_ADDRESS=0x8000000
CONFIG_RAM_START=0x20000000
CONFIG_RAM_SIZE=0x1800
CONFIG_STACK_SIZE=512
CONFIG_STM32_SELECT=y
CONFIG_MACH_STM32F042=y
CONFIG_MACH_STM32F0=y
CONFIG_MACH_STM32F0x2=y
CONFIG_STM32_DFU_ROM_ADDRESS=0x1fffc400
CONFIG_ARMCM_RAM_VECTORTABLE=y
CONFIG_STM32_CLOCK_REF_24M=y
CONFIG_CLOCK_REF_FREQ=24000000
CONFIG_STM32F0_TRIM=16
CONFIG_STM32_CANBUS_PA11_PA12=y
CONFIG_CANBUS=y
CONFIG_CANBUS_FREQUENCY=1000000
CONFIG_HAVE_STM32_CANBUS=y
CONFIG_HAVE_STM32_USBCANBUS=y
CONFIG_FLASH_APPLICATION_ADDRESS=0x8002000
CONFIG_STM32_FLASH_START_2000=y
CONFIG_WANT_GPIO_BITBANGING=y
CONFIG_WANT_I2C=y
CONFIG_WANT_SOFTWARE_I2C=y
CONFIG_WANT_LDC1612=y
CONFIG_NEED_SENSOR_BULK=y
{COMMON_DISABLED}"""
    FW_MAX_SIZE = 24576  # 24KB with Katapult
    FW_MAX_LABEL = "24KB (with Katapult)"

# Backup current config
o, _ = run("cd ~/klipper && cp .config .config.pre_eddy_ng 2>/dev/null; echo backed up")
print(o)

o, _ = run(f"cd ~/klipper && cat > .config << 'EOCONFIG'\n{minimal_config}\nEOCONFIG\necho 'Config written'")
print(o)

o, _ = run("cd ~/klipper && make olddefconfig 2>&1")
print(o[-300:])

# ============================================================
step("6/8 — Build firmware")
# ============================================================
o, e = run("cd ~/klipper && make clean 2>&1 && make -j4 2>&1", timeout=120)
print(o[-600:])

if 'error' in (o + e).lower():
    print(f"\n!!! BUILD FAILED !!!")
    print(e[-300:])
    ssh.close()
    sys.exit(1)

size_o, _ = run("stat -c%s ~/klipper/out/klipper.bin")
if size_o.isdigit():
    size = int(size_o)
    print(f"\nFirmware: {size} bytes ({size/1024:.1f} KB)")
    print(f"Available: {FW_MAX_SIZE} bytes ({FW_MAX_LABEL})")
    print(f"Free: {FW_MAX_SIZE - size} bytes ({(FW_MAX_SIZE-size)/1024:.1f} KB)")
    if size > FW_MAX_SIZE:
        print(f"\n!!! WARNING: Firmware exceeds available flash !!!")
else:
    print("Could not determine firmware size!")
    ssh.close()
    sys.exit(1)

# ============================================================
step(f"7/8 — Verify I2C / sensor support for {MCU_NAME}")
# ============================================================
if PROBE_TYPE == "btt_eddy":
    o, _ = run("ls -la ~/klipper/src/rpxxxx/ 2>/dev/null | head -20")
    print(f"RP2040 source files:\n{o}")
    o, _ = run("grep -l 'i2c' ~/klipper/src/rpxxxx/*.c 2>/dev/null")
    print(f"I2C support files: {o}")
else:
    o, _ = run("grep -r 'i2c.*pin' ~/klipper/src/stm32/stm32f0_i2c.c | head -20")
    print(f"I2C pin config:\n{o}")

# Verify LDC1612 sensor source is linked
o, _ = run("ls -la ~/klipper/src/sensor_ldc1612_ng.c 2>/dev/null")
print(f"sensor_ldc1612_ng.c: {o}")

# ============================================================
step(f"8/8 — Generate eddy-ng.cfg ({PRINTER_SIZE}mm / {MCU_NAME})")
# ============================================================

# Read current CAN UUID from existing config, or prompt
uuid_out, _ = run("grep 'canbus_uuid' ~/printer_data/config/eddy-ng.cfg 2>/dev/null")
if uuid_out:
    CAN_UUID = uuid_out.split(':')[-1].strip()
    print(f"Using existing CAN UUID: {CAN_UUID}")
else:
    CAN_UUID = "CHANGE_ME"
    print("No existing UUID found — set canbus_uuid in eddy-ng.cfg manually!")

# Sensor type for config
if PROBE_TYPE == "btt_eddy":
    SENSOR_TYPE = "btt_eddy"
    I2C_BUS = "i2c0f"
else:
    SENSOR_TYPE = "cartographer"
    I2C_BUS = "i2c0f"

eddy_cfg = f"""# ============================================================================
# eddy-ng.cfg -- {SENSOR_TYPE} on {PRINTER_SIZE}mm printer
# Generated by install_eddy_ng.py
#
# Ersteinrichtung:
#   EDDY_NG_CALIBRATE_1VON4   (manuelles Z, dann ACCEPT + SAVE_CONFIG)
#   EDDY_NG_CALIBRATE_2VON4   (automatisch, ~2 min)
#   EDDY_NG_CALIBRATE_3VON4   (automatisch, ~2 min)
#   EDDY_NG_CALIBRATE_4VON4   (Verifikation)
# ============================================================================

[include calibrate_macros.cfg]

# -- Eddy MCU (CAN) --
[mcu eddy]
canbus_uuid: {CAN_UUID}

# -- Eddy-NG Probe --
[probe_eddy_ng my_eddy]
sensor_type: {SENSOR_TYPE}
i2c_mcu: eddy
i2c_bus: {I2C_BUS}
x_offset: 0.0
y_offset: 16.0

# Tap-Erkennung
tap_speed: 3.0
tap_start_z: 3.0
tap_target_z: -0.250

# Tap-Sampling
tap_samples: 3
tap_max_samples: 5
tap_samples_stddev: 0.020

# Scanning / Bed Mesh
scan_sample_time: 0.100
scan_sample_time_delay: 0.050
mesh_path: snake
mesh_direction: x
mesh_runs: 1
mesh_height: 3.0

# Z Homing
home_trigger_height: 2.0

# Geschwindigkeiten
probe_speed: 5.0
lift_speed: 10.0
move_speed: 50.0

# Kalibrierung
calibration_z_max: 15.0
calibration_points: 150

# Debug (nach Ersteinrichtung auf False setzen)
debug: True

# -- Temperaturueberwachung --
[temperature_sensor btt_eddy_mcu]
sensor_type: temperature_mcu
sensor_mcu: eddy
min_temp: 10
max_temp: 100

[temperature_sensor btt_eddy]
sensor_type: Generic 3950
sensor_pin: eddy:gpio26

# -- Bed Mesh ({PRINTER_SIZE}mm) --
[bed_mesh]
zero_reference_position: {GEO['zero_reference_position']}
speed: 200
horizontal_move_z: 3.5
mesh_min: {GEO['mesh_min']}
mesh_max: {GEO['mesh_max']}
probe_count: {GEO['probe_count']}
adaptive_margin: 10
algorithm: bicubic
fade_start: 0.6
fade_end: 10.0

# -- Quad Gantry Level ({PRINTER_SIZE}mm) --
[quad_gantry_level]
gantry_corners:
{GEO['qgl_gantry_corners']}
points:
{GEO['qgl_points']}
speed: 200
horizontal_move_z: {GEO['qgl_horizontal_move_z']}
max_adjust: {GEO['qgl_max_adjust']}
retries: 10
retry_tolerance: 0.005

# ============================================================================
# Makros
# ============================================================================

# -- QGL Wrapper: 2-stufig (coarse + fine) --
[gcode_macro QUAD_GANTRY_LEVEL]
description: 2-stufiges QGL (coarse @10mm, fine @3mm)
rename_existing: _QUAD_GANTRY_LEVEL
gcode:
  {{% set coarse_z   = params.COARSE_Z|default(10.0)|float %}}
  {{% set fine_z     = params.FINE_Z|default(3.0)|float %}}
  {{% set coarse_tol = params.COARSE_TOL|default(0.05)|float %}}
  {{% set fine_tol   = params.FINE_TOL|default(0.005)|float %}}
  {{% set coarse_r   = params.COARSE_RETRIES|default(10)|int %}}
  {{% set fine_r     = params.FINE_RETRIES|default(10)|int %}}

  {{% if params.RAW|default(0)|int == 1 %}}
    _QUAD_GANTRY_LEVEL
  {{% else %}}
    {{% set homed = printer.toolhead.homed_axes %}}
    {{% if 'x' not in homed or 'y' not in homed %}}
      G28 X Y
    {{% endif %}}

    {{% if 'z' in homed %}}
      G90
      G0 Z{{coarse_z}} F1000
    {{% endif %}}

    M117 QGL coarse
    _QUAD_GANTRY_LEVEL HORIZONTAL_MOVE_Z={{coarse_z}} RETRY_TOLERANCE={{coarse_tol}} RETRIES={{coarse_r}}

    G90
    G0 Z{{coarse_z}} F1000

    M117 QGL fine
    _QUAD_GANTRY_LEVEL HORIZONTAL_MOVE_Z={{fine_z}} RETRY_TOLERANCE={{fine_tol}} RETRIES={{fine_r}}

    M117 QGL done
  {{% endif %}}

# -- Homing Routine --
[gcode_macro HOMING_ROUTINE]
description: Vollstaendiges Homing: XY -> QGL -> Z Tap -> Mesh
gcode:
  ENSURE_TOOL_MOUNTED

  {{% set cfg = printer.configfile.settings %}}
  {{% if 'probe_eddy_ng my_eddy' in cfg %}}
    {{% set px = cfg['probe_eddy_ng my_eddy'].x_offset|default(0.0)|float %}}
    {{% set py = cfg['probe_eddy_ng my_eddy'].y_offset|default(0.0)|float %}}
  {{% elif 'probe' in cfg %}}
    {{% set px = cfg.probe.x_offset|default(0.0)|float %}}
    {{% set py = cfg.probe.y_offset|default(0.0)|float %}}
  {{% else %}}
    {{% set px = 0.0 %}}
    {{% set py = 0.0 %}}
  {{% endif %}}

  {{% set axmin = printer.toolhead.axis_minimum %}}
  {{% set axmax = printer.toolhead.axis_maximum %}}
  {{% set bed_min_x = (0.0 if axmin.x < 0 else axmin.x)|float %}}
  {{% set bed_min_y = (0.0 if axmin.y < 0 else axmin.y)|float %}}
  {{% set bed_max_x = axmax.x|float %}}
  {{% set bed_max_y = axmax.y|float %}}
  {{% set cx = ((bed_min_x + bed_max_x) / 2.0 - px)|float %}}
  {{% set cy = ((bed_min_y + bed_max_y) / 2.0 - py)|float %}}

  # 1) Home XYZ
  SET_GCODE_OFFSET Z=0
  G28
  G90

  # 2) QGL (2-stufig)
  QUAD_GANTRY_LEVEL

  # 3) Z fein via Tap an Bettmitte
  CLEAN_NOZZLE
  G1 Z10 F5000
  G1 X{{ '%.3f' % cx }} Y{{ '%.3f' % cy }} F5000
  PROBE_EDDY_NG_TAP
  G0 Z3 F5000

  # 4) Bed Mesh
  BED_MESH_CLEAR
  BED_MESH_CALIBRATE

  # 5) Zurueck zur Mitte
  G1 X{{ '%.3f' % cx }} Y{{ '%.3f' % cy }} F2000

# -- Utility --
[gcode_macro ENSURE_TOOL_MOUNTED]
description: Laedt T0, falls kein Tool aktiv ist
gcode:
  {{% if not printer.toolhead.extruder %}}
    T0
  {{% endif %}}
"""

# Check if eddy-ng.cfg already exists
existing, _ = run("cat ~/printer_data/config/eddy-ng.cfg 2>/dev/null | head -5")
if existing:
    print(f"Existing eddy-ng.cfg found on printer.")
    if not args.yes:
        overwrite = input("  Ueberschreiben? [j/N] ").strip().lower()
        if overwrite not in ('j', 'ja', 'y', 'yes'):
            print("  -> eddy-ng.cfg nicht ueberschrieben")
            print(f"\n--- Installation complete for {MCU_NAME} / {PRINTER_SIZE}mm ---")
            ssh.close()
            sys.exit(0)

o, _ = run(f"cat > ~/printer_data/config/eddy-ng.cfg << 'CFGEOF'\n{eddy_cfg}\nCFGEOF\necho 'eddy-ng.cfg written'")
print(o)

# Verify
o, _ = run("head -5 ~/printer_data/config/eddy-ng.cfg")
print(f"Verify:\n{o}")

print(f"\n--- Installation complete for {MCU_NAME} / {PRINTER_SIZE}mm ---")
print("Next steps:")
print("  1. Flash firmware via Katapult CAN:")
print(f"     python3 ~/katapult/scripts/flashtool.py -i can0 -u {CAN_UUID} -f ~/klipper/out/klipper.bin")
print("  2. Restart Klipper")
print("  3. Run calibration (EDDY_NG_CALIBRATE_1VON4 .. 4VON4)")

ssh.close()
