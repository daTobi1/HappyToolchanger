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

## License

Based on [klipper-toolchanger-easy](https://github.com/jwellman80/klipper-toolchanger-easy) and [Eddy-NG](https://github.com/Cartographer3D/eddy-ng).
