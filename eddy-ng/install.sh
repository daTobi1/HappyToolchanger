#!/bin/bash
#
# eddy-ng installer wrapper
#
# Routes to the new interactive installer (scripts/install.sh) by default.
# Passes through to the legacy install.py for backward compatibility when
# called with legacy flags (--copy, -u, --uninstall).
#
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

# Check if using legacy flags
for arg in "$@"; do
    case "$arg" in
        --copy|-u|--uninstall|--firmware-only)
            exec python3 "$SCRIPT_DIR/install.py" "$@"
            ;;
    esac
done

# Default: new interactive installer
exec "$SCRIPT_DIR/scripts/install.sh" "$@"
