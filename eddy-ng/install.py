#!/usr/bin/env python3

import os
import sys
import argparse
import shutil
from pathlib import Path

IS_MAC = os.path.isdir("/System/Library")

SED_IN_PLACE_ARG = "-i ''" if IS_MAC else "-i"

# Files/directories to install into Klipper
# Format: source (relative to repo) -> destination (relative to klipper dir)
FILES_TO_INSTALL = {
    "eddy-ng/sensor_ldc1612_ng.c": ("src", "file"),
    "ldc1612_ng.py": ("klippy/extras", "file"),
    "probe_eddy_ng": ("klippy/extras", "dir"),  # Package directory
}

# Legacy single-file layout for cleanup
LEGACY_FILES = {
    "probe_eddy_ng.py": "klippy/extras",
}


def get_script_dir():
    return os.path.dirname(os.path.realpath(__file__))


def uninstall_klipper(target_dir: str):
    # Remove new package-style installation
    for src_name, (dest_dir, kind) in FILES_TO_INSTALL.items():
        dest_path = os.path.join(target_dir, dest_dir)
        dest_file = os.path.join(dest_path, os.path.basename(src_name))
        if os.path.islink(dest_file) or os.path.isfile(dest_file):
            print(f"Removing {dest_file}")
            os.remove(dest_file)
        elif os.path.isdir(dest_file):
            print(f"Removing directory {dest_file}")
            shutil.rmtree(dest_file)
        else:
            print(f"File {dest_file} does not exist. Skipping.")

    # Remove legacy single-file installations
    for src_name, dest_dir in LEGACY_FILES.items():
        dest_file = os.path.join(target_dir, dest_dir, src_name)
        if os.path.islink(dest_file) or os.path.isfile(dest_file):
            print(f"Removing legacy file {dest_file}")
            os.remove(dest_file)

    print("Unpatching src/Makefile...")
    makefile_path = os.path.join(target_dir, "src/Makefile")
    os.system(f"sed {SED_IN_PLACE_ARG} 's, sensor_ldc1612_ng.c,,' '{makefile_path}'")

    # Legacy cleanup: remove old bed_mesh.py patch if present
    bed_mesh_path = os.path.join(target_dir, "klippy/extras/bed_mesh.py")
    if os.path.isfile(bed_mesh_path):
        with open(bed_mesh_path, "r") as f:
            content = f.read()
        if '"eddy" in probe_name #eddy-ng' in content:
            print("Reverting legacy bed_mesh.py patch...")
            os.system(
                f"sed {SED_IN_PLACE_ARG} 's,\"eddy\" in probe_name #eddy-ng,probe_name.startswith(\"probe_eddy_current\"),' '{bed_mesh_path}'"
            )
    return


def install_kalico(target_dir: str, uninstall: bool, copy: bool):
    if not uninstall:
        print("Congrats, you're running Kalico!")
        print("================================")

    python_module_path = os.path.join(target_dir, "klippy/plugins/probe_eddy_ng")
    firmware_module_path = os.path.join(target_dir, "src/extras/eddy-ng")

    # Clean up any old installation
    old_module_path = os.path.join(target_dir, "klippy/extras/probe_eddy_ng.py")
    if os.path.islink(old_module_path) or os.path.isfile(old_module_path):
        print("Uninstalling old installation...")
        uninstall_klipper(target_dir)

    if os.path.exists(python_module_path) or os.path.islink(python_module_path):
        if not os.path.islink(python_module_path):
            print(f"{python_module_path} exists, but is not a symlink. Please remove it and try again.")
            sys.exit(1)
        os.unlink(python_module_path)

    if os.path.exists(firmware_module_path) or os.path.islink(firmware_module_path):
        if not os.path.islink(firmware_module_path):
            print(f"{firmware_module_path} exists, but is not a symlink. Please remove it and try again.")
            sys.exit(1)
        os.unlink(firmware_module_path)

    if uninstall:
        print("Removed firmware and plugin module links.")
        sys.exit(0)

    if copy:
        shutil.copytree(get_script_dir(), python_module_path)
        shutil.copytree(os.path.join(get_script_dir(), "eddy-ng"), firmware_module_path)
    else:
        os.symlink(get_script_dir(), python_module_path)
        os.symlink(os.path.join(get_script_dir(), "eddy-ng"), firmware_module_path)

    print("Installed links to firmware and plugin modules.")
    print("When rebuilding firmware, make sure to select eddy-ng")
    print("from the firmware extras in menuconfig.")
    print("(There's no need to run install again after eddy-ng updates.)")


def install_klipper(target_dir: str, uninstall: bool, copy: bool, firmware_only: bool = False):
    if uninstall:
        print("Uninstalling files...")
        uninstall_klipper(target_dir)
        return

    # Clean up legacy single-file installation
    for src_name, dest_dir in LEGACY_FILES.items():
        dest_file = os.path.join(target_dir, dest_dir, src_name)
        if os.path.islink(dest_file) or os.path.isfile(dest_file):
            print(f"Removing legacy file {dest_file}")
            os.remove(dest_file)

    # Determine which files to install
    if firmware_only:
        # Only install firmware C file and patch Makefile (Python handled by pip)
        files = {k: v for k, v in FILES_TO_INSTALL.items() if v[0] == "src"}
        print("Installing firmware files only (Python handled by pip)...")
    else:
        files = FILES_TO_INSTALL
        print("Installing files...")

    for src_name, (dest_dir, kind) in files.items():
        src_path = os.path.join(get_script_dir(), src_name)
        dest_path = os.path.join(target_dir, dest_dir)
        dest_file = os.path.join(dest_path, os.path.basename(src_name))

        # Clean existing
        if os.path.islink(dest_file):
            os.remove(dest_file)
        elif os.path.isdir(dest_file):
            shutil.rmtree(dest_file)
        elif os.path.isfile(dest_file):
            os.remove(dest_file)

        if copy:
            print(f"Copying {src_name} to {dest_dir}/")
            if kind == "dir":
                shutil.copytree(src_path, dest_file)
            else:
                shutil.copyfile(src_path, dest_file)
        else:
            link_path = os.path.relpath(os.path.realpath(src_path), dest_path)
            print(f"Linking {link_path} to {dest_dir}/")
            os.symlink(link_path, dest_file)

    print("Patching src/Makefile...")
    makefile_path = os.path.join(target_dir, "src/Makefile")
    os.system(f"sed {SED_IN_PLACE_ARG} 's,sensor_ldc1612.c$,sensor_ldc1612.c sensor_ldc1612_ng.c,' '{makefile_path}'")

    # Note: bed_mesh.py is no longer patched via sed. The runtime monkey-patch
    # in probe_eddy_ng handles rapid_scan support automatically.
    # Legacy patches are cleaned up during uninstall.


def main():
    parser = argparse.ArgumentParser(description="Install or uninstall components.")
    parser.add_argument("-u", "--uninstall", action="store_true", help="Uninstall files")
    parser.add_argument("--copy", action="store_true", help="Copy files instead of linking")
    parser.add_argument("--firmware-only", action="store_true", help="Only install firmware C files and patch Makefile (skip Python)")
    parser.add_argument("target_dir", nargs="?", help="Target directory")

    args = parser.parse_args()

    uninstall = args.uninstall
    copy = args.copy
    firmware_only = args.firmware_only
    target_dir = args.target_dir

    # If no target directory provided, try defaults
    if not target_dir:
        home_dir = str(Path.home())
        if os.path.isdir(os.path.join(home_dir, "klipper")):
            target_dir = os.path.join(home_dir, "klipper")
        elif os.path.isdir(os.path.join(home_dir, "kalico")):
            target_dir = os.path.join(home_dir, "kalico")
        else:
            print("Error: No target directory provided and no default directories found.")
            parser.print_help()
            sys.exit(1)

    if not os.path.isdir(target_dir):
        print(f"Error: Target directory '{target_dir}' does not exist.")
        sys.exit(1)

    if os.path.exists(os.path.join(target_dir, "klippy/extras/danger_options.py")):
        install_kalico(target_dir, uninstall, copy)
    else:
        install_klipper(target_dir, uninstall, copy, firmware_only)


if __name__ == "__main__":
    main()
