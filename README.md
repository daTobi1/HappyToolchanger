# HappyToolchanger

Klipper Stealthchanger Monorepo: Toolchanger modules, Eddy-NG probe, Offset calibration webapp, and Mainsail with HTC Panel.

## Components

| Component | Description |
|---|---|
| `klippy/extras/` | Klipper Python modules (KTC Easy fork with z_probe routing) |
| `eddy-ng/` | BTT Eddy-NG probe with patches for toolchanger compatibility |
| `webapp/` | Offset calibration webapp (Flask, port 3000) |
| `mainsail/` | Mainsail fork with HappyHare/Toolchanger dashboard panel |
| `configs/` | Printer-specific configs (250mm/4T, 350mm/6T Stealthchanger) |

## Quick Install

```bash
cd ~
git clone https://github.com/daTobi1/HappyToolchanger.git
cd HappyToolchanger
./install.sh                    # Base install (extras, eddy-ng, mainsail, webapp)
./install.sh --printer 250      # Also deploy 250er configs
./install.sh --printer 350      # Also deploy 350er configs
```

## What install.sh does

1. Symlinks Klipper Python modules to `~/klipper/klippy/extras/`
2. Installs Eddy-NG probe loaders and package symlink
3. Deploys Mainsail dist to `~/mainsail/`
4. Sets up Offset webapp (venv, Flask, systemd service on port 3000)
5. Adds Moonraker Update Manager config for auto-updates
6. Restarts Klipper and Moonraker

## Uninstall

```bash
cd ~/HappyToolchanger
./uninstall.sh
```

## Updates

After install, updates appear in Mainsail's Update Manager. Or manually:

```bash
cd ~/HappyToolchanger
git pull
./install.sh
```

## z_probe Feature

Tools can reference an external Z probe (Eddy, Cartographer) via the `z_probe` config parameter. BED_MESH/QGL/PROBE automatically route to the best available probe. Tap remains for crash detection and tool detection. See `examples/z_probe_example.cfg`.

## htc_heater_fan

`[htc_heater_fan]` replaces `[heater_fan]` for hotend fans: separate speeds for the active tool, parked tools and cool-down, an optional ramp-up with chamber temperature, and `SET_HEATER_FAN` to change speeds at runtime. Pure Klipper extra, survives Klipper updates. See `docs/htc_heater_fan.md`.

## Input shaper per tool

Every toolhead resonates differently, so shaper type and frequency live per tool as `params_input_shaper_type_x / freq_x / damping_ratio_x` (and `_y`) in `[tool Tn]`. The toolchanger module applies them with `SET_INPUT_SHAPER` at every tool change; `[input_shaper]` in printer.cfg stays empty.

Measuring: Offset UI → *Input Shaper Calibration*, or `CALIBRATE_TOOL_SHAPER TOOLS=1,2,3 AXES=x,y X=130 Y=115 HEIGHT=20`. Each tool is picked up, moved to the point and measured with its own accelerometer: `params_accel_chip` in `[tool Tn]` if set, otherwise the chip on the same MCU as the tool's extruder (or a chip named `..._T<n>`). A `[resonance_tester]` section is required by Klipper (its `accel_chip` and `probe_points` are only placeholders here). Results are kept in `.offset_shaper_results.json`; *APPLY INPUT SHAPER* writes them into `T<n>.cfg`, adding missing keys. A tool without an accelerometer can take over the values of another tool in the UI. Defaults: `[offset] shaper_x / shaper_y` (bed centre), `shaper_height` (20), `shaper_max_smoothing`.

## License

Based on [klipper-toolchanger-easy](https://github.com/jwellman80/klipper-toolchanger-easy) and [Eddy-NG](https://github.com/Cartographer3D/eddy-ng).
