# Pre-built Firmware Images for Eddy Duo (RP2040)

This directory contains pre-built Klipper firmware images for the BTT Eddy Duo
sensor board (RP2040-based). These images include the `sensor_ldc1612_ng.c`
module, so **no Klipper source patching is required**.

## Available Variants

| File | Connection | Bootloader | CAN Pins |
|------|-----------|------------|----------|
| `eddy-duo-usb.uf2` | USB | none | -- |
| `eddy-duo-canbus-500k.uf2` | CAN bus 500k | none | TX=GPIO1, RX=GPIO0 |
| `eddy-duo-canbus-1m.uf2` | CAN bus 1M | none | TX=GPIO1, RX=GPIO0 |
| `eddy-duo-katapult-canbus-500k.bin` | CAN bus 500k | Katapult | TX=GPIO1, RX=GPIO0 |
| `eddy-duo-katapult-canbus-1m.bin` | CAN bus 1M | Katapult | TX=GPIO1, RX=GPIO0 |

### Flash address offsets

| Variant | `CONFIG_FLASH_APPLICATION_ADDRESS` |
|---------|-----------------------------------|
| Without bootloader (USB, CAN) | `0x10000100` |
| With Katapult bootloader | `0x10004000` (16KB reserved for bootloader) |

### CAN GPIO Pin Configuration

The pre-built CAN firmware uses the **BTT Eddy Duo default** CAN pin assignment:

- **CAN TX:** GPIO1
- **CAN RX:** GPIO0

If your hardware uses different CAN pins, build from source instead:

```bash
./scripts/flash-duo.sh --build
```

Or override via environment variables:

```bash
EDDY_CAN_TX_GPIO=5 EDDY_CAN_RX_GPIO=4 ./scripts/flash-duo.sh --build
```

## How to Flash

Use the flash script for an interactive guided experience:

```bash
./scripts/flash-duo.sh
```

The script will:
1. Ask for connection type (USB / CAN 500k / CAN 1M)
2. For CAN: ask for bootloader offset (default `0x10000100`, `0x10004000` for Katapult)
3. For CAN: scan for CAN UUID (optional, via Katapult tools)
4. Choose flash method and flash

### Flash Methods

| Method | Format | Bootloader | Use case |
|--------|--------|------------|----------|
| BOOTSEL | `.uf2` | **Overwrites** bootloader | First-time flash, USB connection |
| Katapult (CAN) | `.bin` | **Preserved** | CAN bus updates without physical access |
| Manual copy | any | depends | Custom setups |

### BOOTSEL Mode (USB)

1. Hold the **BOOT** button on your Eddy Duo
2. Press and release **RESET** (or unplug/replug USB)
3. Release the BOOT button
4. Copy the `.uf2` file to the `RPI-RP2` USB drive that appears

> **Warning:** BOOTSEL flashing overwrites the **entire** flash, including
> any bootloader (Katapult). If you want to keep Katapult for future CAN
> updates, use Katapult flash instead. The flash script warns about this
> if you select BOOTSEL with a Katapult-built firmware.

### CAN Bus with Katapult Bootloader

**Prerequisites:**
- Katapult bootloader already flashed on the RP2040
- Katapult tools installed (`~/katapult`)
- CAN interface configured (e.g. `can0`)
- CAN UUID known (the flash script can scan for it automatically)

```bash
./scripts/flash-duo.sh
# Choose CAN 500k or CAN 1M
# Enter bootloader offset: 0x10004000
# Let the script scan for CAN UUID
# Choose Katapult flash method
```

> **Warning:** Katapult expects the firmware at a specific flash offset
> (usually `0x10004000`). If you try to flash a firmware built without
> bootloader offset (`0x10000100`) via Katapult, it will likely not boot.
> The flash script warns about this mismatch.

### CAN Bus without Bootloader

Flash via BOOTSEL mode first. If you install Katapult later,
subsequent updates can be done over CAN bus.

## Building from Source

If pre-built images are not available, you need custom CAN pins, or you
want to match your exact Klipper version:

```bash
./scripts/flash-duo.sh --build
```

The script will:
1. Ask for your connection type (USB / CAN 500k / CAN 1M)
2. Ask for CAN TX/RX GPIO pins (if CAN selected, defaults to GPIO1/GPIO0)
3. Ask for bootloader flash offset (with common values listed)
4. Build the firmware
5. For CAN: scan for CAN UUID
6. Offer to flash

## Version Compatibility

These firmware images are built against a specific Klipper version.
Check the GitHub release notes for the compatible Klipper version.
If you experience MCU protocol errors after a Klipper update, rebuild
from source with `--build` to match your current Klipper version.
