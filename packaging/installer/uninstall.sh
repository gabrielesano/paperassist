#!/bin/sh
# ==============================================================================
# PaperAssist Standalone Uninstaller
# Managed-By: PaperAssist-Linux-Installer
# ==============================================================================
set -e

SCRIPT_DIR="$(CDPATH= cd -- "$(dirname "$0")" && pwd)"
INSTALL_DIR="$SCRIPT_DIR"

# Allow overriding install directory via --install-dir
while [ $# -gt 0 ]; do
    case "$1" in
        -h|--help)
            echo "Usage: $0 [OPTIONS]"
            echo "Uninstall PaperAssist while preserving all user libraries and configuration."
            echo ""
            echo "Options:"
            echo "  --install-dir DIR   Target installation directory (default: current directory)"
            echo "  -h, --help          Show this help message and exit"
            exit 0
            ;;
        --install-dir)
            if [ -z "$2" ]; then
                echo "Error: --install-dir requires a directory path." >&2
                exit 1
            fi
            INSTALL_DIR="$2"
            shift 2
            ;;
        --install-dir=*)
            INSTALL_DIR="${1#*=}"
            shift
            ;;
        *)
            echo "Error: Unknown option: $1" >&2
            echo "Run with --help for usage information." >&2
            exit 1
            ;;
    esac
done

# Keep maintenance paths beside the installation, even with a trailing slash.
while [ "$INSTALL_DIR" != "/" ] && [ "${INSTALL_DIR%/}" != "$INSTALL_DIR" ]; do
    INSTALL_DIR="${INSTALL_DIR%/}"
done

is_pid_alive() {
    case "$1" in
        ''|*[!0-9]*) return 1 ;;
        *) kill -0 "$1" 2>/dev/null ;;
    esac
}

LOCK_DIR="$INSTALL_DIR.lock"
LOCK_ACQUIRED=0

acquire_lock() {
    LOCK_PARENT="$(dirname "$LOCK_DIR")"
    mkdir -p "$LOCK_PARENT" 2>/dev/null || true
    if ! command -v flock >/dev/null 2>&1; then
        echo "Error: flock is required for PaperAssist maintenance." >&2
        exit 1
    fi
    # This file must persist: unlinking it could split contenders across inodes.
    if ! { exec 9>>"$LOCK_DIR.flock"; }; then
        echo "Error: Cannot open maintenance lock ($LOCK_DIR.flock)." >&2
        exit 1
    fi
    if ! flock -n 9; then
        echo "Error: Another PaperAssist installer or maintenance process is currently operating on $INSTALL_DIR." >&2
        exit 1
    fi

    if mkdir "$LOCK_DIR" 2>/dev/null; then
        cat > "$LOCK_DIR/owner" << EOF_LOCK
# Managed-By: PaperAssist-Linux-Installer
PID=$$
EOF_LOCK
        LOCK_ACQUIRED=1
        return 0
    fi

    # Lock directory exists. Verify ownership before touching anything.
    if [ ! -e "$LOCK_DIR" ]; then
        echo "Error: Cannot create lock directory ($LOCK_DIR)." >&2
        exit 1
    fi

    if [ ! -f "$LOCK_DIR/owner" ]; then
        sleep 0.1 2>/dev/null || sleep 1 2>/dev/null || true
        if [ ! -f "$LOCK_DIR/owner" ]; then
            echo "Error: Lock directory ($LOCK_DIR) already exists and has no PaperAssist owner file." >&2
            exit 1
        fi
    fi

    if ! grep -q "Managed-By: PaperAssist-Linux-Installer" "$LOCK_DIR/owner" 2>/dev/null; then
        echo "Error: Lock directory ($LOCK_DIR) is not managed by PaperAssist-Linux-Installer." >&2
        exit 1
    fi

    OTHER_PID=$(grep '^PID=' "$LOCK_DIR/owner" 2>/dev/null | cut -d= -f2-)
    if [ -n "$OTHER_PID" ] && [ "$OTHER_PID" != "$$" ] && is_pid_alive "$OTHER_PID"; then
        echo "Error: Another PaperAssist installer or maintenance process (PID $OTHER_PID) is currently operating on $INSTALL_DIR." >&2
        exit 1
    fi

    if [ "$OTHER_PID" != "$$" ]; then
        rm -rf "$LOCK_DIR"
        if mkdir "$LOCK_DIR" 2>/dev/null; then
            cat > "$LOCK_DIR/owner" << EOF_LOCK
# Managed-By: PaperAssist-Linux-Installer
PID=$$
EOF_LOCK
            LOCK_ACQUIRED=1
            return 0
        else
            echo "Error: Failed to acquire lock directory ($LOCK_DIR) after removing stale lock." >&2
            exit 1
        fi
    else
        LOCK_ACQUIRED=1
        return 0
    fi
}

release_lock() {
    if [ "$LOCK_ACQUIRED" -eq 1 ] && [ -d "$LOCK_DIR" ]; then
        OWNER_PID=$(grep '^PID=' "$LOCK_DIR/owner" 2>/dev/null | cut -d= -f2-)
        if [ "$OWNER_PID" = "$$" ]; then
            rm -rf "$LOCK_DIR"
        fi
        LOCK_ACQUIRED=0
    fi
}

validate_target() {
# Step A: Refuse immediately if target is a PaperAssist library catalog
if [ -f "$INSTALL_DIR/library.sqlite3" ]; then
    echo "Error: Target directory ($INSTALL_DIR) contains a PaperAssist library catalog (library.sqlite3)." >&2
    echo "PaperAssist application files must be kept separate from user research libraries." >&2
    exit 1
fi

if [ ! -d "$INSTALL_DIR" ]; then
    echo "Error: Installation directory does not exist: $INSTALL_DIR" >&2
    exit 1
fi

MANIFEST="$INSTALL_DIR/.paperassist-install-manifest"
if [ ! -f "$MANIFEST" ]; then
    echo "Error: No installation manifest found in $INSTALL_DIR." >&2
    echo "Refusing to uninstall unmanaged directory." >&2
    exit 1
fi

if ! grep -q "Managed-By: PaperAssist-Linux-Installer" "$MANIFEST" 2>/dev/null; then
    echo "Error: Manifest in $INSTALL_DIR is not managed by PaperAssist-Linux-Installer." >&2
    echo "Refusing to uninstall unmanaged directory." >&2
    exit 1
fi

}

validate_target
acquire_lock
trap 'release_lock' EXIT
trap 'exit 1' HUP INT TERM
validate_target

# Locate hash command
if command -v sha256sum >/dev/null 2>&1; then
    SHA_CMD="sha256sum"
elif command -v shasum >/dev/null 2>&1; then
    SHA_CMD="shasum -a 256"
else
    SHA_CMD=""
fi

# 1. Remove owned launcher
LAUNCHER_PATH=$(grep '^LAUNCHER_PATH=' "$MANIFEST" 2>/dev/null | cut -d= -f2-)
if [ -n "$LAUNCHER_PATH" ] && { [ -e "$LAUNCHER_PATH" ] || [ -L "$LAUNCHER_PATH" ]; }; then
    if grep -q "Managed-By: PaperAssist-Linux-Installer" "$LAUNCHER_PATH" 2>/dev/null; then
        rm -f "$LAUNCHER_PATH"
        echo "Removed launcher: $LAUNCHER_PATH"
    else
        echo "Preserving launcher (modified or unmanaged): $LAUNCHER_PATH"
    fi
fi

# 2. Remove owned desktop file
DESKTOP_PATH=$(grep '^DESKTOP_PATH=' "$MANIFEST" 2>/dev/null | cut -d= -f2-)
if [ -n "$DESKTOP_PATH" ] && { [ -e "$DESKTOP_PATH" ] || [ -L "$DESKTOP_PATH" ]; }; then
    if grep -q "Managed-By: PaperAssist-Linux-Installer" "$DESKTOP_PATH" 2>/dev/null; then
        rm -f "$DESKTOP_PATH"
        echo "Removed desktop entry: $DESKTOP_PATH"
    else
        echo "Preserving desktop entry (modified or unmanaged): $DESKTOP_PATH"
    fi
fi

# 3. Remove owned icons
if [ -n "$DESKTOP_PATH" ] && command -v update-desktop-database >/dev/null 2>&1; then
    update-desktop-database "$(dirname "$DESKTOP_PATH")" >/dev/null 2>&1 || true
fi

grep '^ICON_ENTRY=' "$MANIFEST" 2>/dev/null | cut -d= -f2- | while IFS='|' read -r ICON_PATH RECORDED_HASH; do
    if [ -n "$ICON_PATH" ] && { [ -e "$ICON_PATH" ] || [ -L "$ICON_PATH" ]; }; then
        if [ -n "$SHA_CMD" ] && [ -n "$RECORDED_HASH" ]; then
            CURR_HASH=$($SHA_CMD "$ICON_PATH" 2>/dev/null | awk '{print $1}')
            if [ "$CURR_HASH" = "$RECORDED_HASH" ]; then
                rm -f "$ICON_PATH"
                echo "Removed icon: $ICON_PATH"
            else
                echo "Preserving icon (modified or hash mismatch): $ICON_PATH"
            fi
        else
            rm -f "$ICON_PATH"
            echo "Removed icon: $ICON_PATH"
        fi
    fi
done

# The script remains loaded after its file is removed, and retains the lock.
rm -rf "$INSTALL_DIR"
echo "PaperAssist uninstalled successfully from $INSTALL_DIR."
echo "Your user libraries, PDFs, annotations, and configuration files were preserved."
