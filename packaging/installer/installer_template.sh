#!/bin/sh
# ==============================================================================
# PaperAssist Self-Extracting Linux Installer
# Managed-By: PaperAssist-Linux-Installer
# ==============================================================================
set -e

APP_NAME="@@APP_NAME@@"
APP_VERSION="@@VERSION@@"
PAYLOAD_START_BYTE=@@PAYLOAD_START_BYTE@@
PAYLOAD_SIZE=@@PAYLOAD_SIZE@@
PAYLOAD_SHA256="@@PAYLOAD_SHA256@@"

show_help() {
    cat << EOF
$APP_NAME $APP_VERSION - Linux Self-Extracting Installer

Usage: $0 [OPTIONS]

Options:
  -h, --help            Show this help message and exit
  -v, --version         Print installer version and exit
  --verify              Verify package integrity (checksum) and exit
  --install-dir DIR     Install directory (default: \${XDG_DATA_HOME:-\$HOME/.local/share}/paperassist-app)
  --bin-dir DIR         CLI launcher directory (default: \${XDG_BIN_HOME:-\$HOME/.local/bin})
  --no-launcher         Do not install command-line launcher
  --no-desktop          Do not install desktop entry or icons
  --allow-root          Allow installation as root user (not recommended)
  --repair              Repair interrupted installation from backup
  --uninstall           Uninstall PaperAssist from the installation directory

PaperAssist installs per-user without administrator privileges (sudo).
User libraries, PDFs, annotations, and configuration files are never modified
or removed by installation, upgrades, or uninstallation.
EOF
}

# ------------------------------------------------------------------------------
# Locate installer file
# ------------------------------------------------------------------------------
SELF="$0"
case "$SELF" in
    /*) SCRIPT_FILE="$SELF" ;;
    */*) SCRIPT_FILE="$(pwd)/$SELF" ;;
    *)
        SCRIPT_FILE="$(command -v "$SELF" 2>/dev/null || which "$SELF" 2>/dev/null || true)"
        [ -z "$SCRIPT_FILE" ] && SCRIPT_FILE="$(pwd)/$SELF"
        case "$SCRIPT_FILE" in
            /*) ;;
            *) SCRIPT_FILE="$(pwd)/$SCRIPT_FILE" ;;
        esac
        ;;
esac

SCRIPT_DIR="$(CDPATH= cd -- "$(dirname "$SCRIPT_FILE")" 2>/dev/null && pwd)"
SCRIPT_BASE="$(basename "$SCRIPT_FILE")"
SCRIPT_FILE="$SCRIPT_DIR/$SCRIPT_BASE"

if [ ! -f "$SCRIPT_FILE" ]; then
    echo "Error: Cannot locate installer file ($SCRIPT_FILE)." >&2
    exit 1
fi

# Locate hash command
if command -v sha256sum >/dev/null 2>&1; then
    SHA_CMD="sha256sum"
elif command -v shasum >/dev/null 2>&1; then
    SHA_CMD="shasum -a 256"
else
    echo "Error: Neither sha256sum nor shasum is available on this system." >&2
    exit 1
fi

# ------------------------------------------------------------------------------
# Parse arguments
# ------------------------------------------------------------------------------
INSTALL_DIR=""
BIN_DIR=""
NO_LAUNCHER=0
NO_DESKTOP=0
ALLOW_ROOT=0
VERIFY_ONLY=0
REPAIR_ONLY=0
UNINSTALL_ACTION=0

while [ $# -gt 0 ]; do
    case "$1" in
        -h|--help)
            show_help
            exit 0
            ;;
        -v|--version)
            echo "$APP_NAME installer version $APP_VERSION"
            exit 0
            ;;
        --verify)
            VERIFY_ONLY=1
            shift
            ;;
        --repair)
            REPAIR_ONLY=1
            shift
            ;;
        --uninstall)
            UNINSTALL_ACTION=1
            shift
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
        --bin-dir)
            if [ -z "$2" ]; then
                echo "Error: --bin-dir requires a directory path." >&2
                exit 1
            fi
            BIN_DIR="$2"
            shift 2
            ;;
        --bin-dir=*)
            BIN_DIR="${1#*=}"
            shift
            ;;
        --no-launcher)
            NO_LAUNCHER=1
            shift
            ;;
        --no-desktop)
            NO_DESKTOP=1
            shift
            ;;
        --allow-root)
            ALLOW_ROOT=1
            shift
            ;;
        *)
            echo "Error: Unknown option: $1" >&2
            echo "Run '$0 --help' for usage." >&2
            exit 1
            ;;
    esac
done

# ------------------------------------------------------------------------------
# Target paths
# ------------------------------------------------------------------------------
XDG_DATA_HOME="${XDG_DATA_HOME:-$HOME/.local/share}"
XDG_CONFIG_HOME="${XDG_CONFIG_HOME:-$HOME/.config}"
XDG_BIN_HOME="${XDG_BIN_HOME:-$HOME/.local/bin}"

if [ -z "$INSTALL_DIR" ]; then
    INSTALL_DIR="$XDG_DATA_HOME/paperassist-app"
fi
# Keep maintenance paths beside the installation, even with a trailing slash.
while [ "$INSTALL_DIR" != "/" ] && [ "${INSTALL_DIR%/}" != "$INSTALL_DIR" ]; do
    INSTALL_DIR="${INSTALL_DIR%/}"
done

if [ -z "$BIN_DIR" ]; then
    BIN_DIR="$XDG_BIN_HOME"
fi

LAUNCHER_PATH="$BIN_DIR/paperassist"
APPLICATIONS_DIR="$XDG_DATA_HOME/applications"
DESKTOP_PATH="$APPLICATIONS_DIR/paperassist.desktop"
ICONS_BASE_DIR="$XDG_DATA_HOME/icons/hicolor"
ICON_SVG_TARGET="$ICONS_BASE_DIR/scalable/apps/paperassist.svg"
ICON_PNG_TARGET="$ICONS_BASE_DIR/256x256/apps/paperassist.png"

# ------------------------------------------------------------------------------
# Helper functions
# ------------------------------------------------------------------------------
verify_package_integrity() {
    TOTAL_FILE_SIZE=$(wc -c < "$SCRIPT_FILE" | tr -d ' \t\r\n')
    EXPECTED_TOTAL_SIZE=$((PAYLOAD_START_BYTE + PAYLOAD_SIZE))

    if [ "$TOTAL_FILE_SIZE" -lt "$EXPECTED_TOTAL_SIZE" ]; then
        echo "Error: Installer package is truncated (expected $EXPECTED_TOTAL_SIZE bytes, found $TOTAL_FILE_SIZE bytes)." >&2
        exit 1
    fi
    if [ "$TOTAL_FILE_SIZE" -gt "$EXPECTED_TOTAL_SIZE" ]; then
        echo "Error: Installer package contains unexpected trailing data (expected $EXPECTED_TOTAL_SIZE bytes, found $TOTAL_FILE_SIZE bytes)." >&2
        exit 1
    fi

    CALCULATED_HASH=$(tail -c +$((PAYLOAD_START_BYTE + 1)) "$SCRIPT_FILE" | head -c "$PAYLOAD_SIZE" | $SHA_CMD | awk '{print $1}')
    if [ "$CALCULATED_HASH" != "$PAYLOAD_SHA256" ]; then
        echo "Error: Checksum verification failed for installer payload." >&2
        echo "Expected: $PAYLOAD_SHA256" >&2
        echo "Actual:   $CALCULATED_HASH" >&2
        exit 1
    fi
}

is_pid_alive() {
    PID_CHECK="$1"
    case "$PID_CHECK" in
        ''|*[!0-9]*) return 1 ;;
        *) kill -0 "$PID_CHECK" 2>/dev/null ;;
    esac
}

is_committed_install_dir() {
    CHECK_DIR="$1"
    [ -d "$CHECK_DIR" ] || return 1
    [ ! -f "$CHECK_DIR/library.sqlite3" ] || return 1
    [ -f "$CHECK_DIR/.paperassist-install-manifest" ] || return 1
    grep -q "Managed-By: PaperAssist-Linux-Installer" "$CHECK_DIR/.paperassist-install-manifest" 2>/dev/null
}

is_owned_staging_dir() {
    CHECK_DIR="$1"
    [ -d "$CHECK_DIR" ] || return 1
    [ ! -f "$CHECK_DIR/library.sqlite3" ] || return 1
    [ -f "$CHECK_DIR/.paperassist-staging-marker" ] || return 1
    grep -q "Managed-By: PaperAssist-Linux-Installer" "$CHECK_DIR/.paperassist-staging-marker" 2>/dev/null
}

is_incomplete_paperassist_dir() {
    CHECK_DIR="$1"
    [ -d "$CHECK_DIR" ] || return 1
    [ ! -f "$CHECK_DIR/library.sqlite3" ] || return 1
    if is_committed_install_dir "$CHECK_DIR"; then
        return 1
    fi
    if is_owned_staging_dir "$CHECK_DIR"; then
        return 0
    fi
    if [ -f "$CHECK_DIR/PaperAssist" ] && [ -d "$CHECK_DIR/_internal" ] && [ -f "$CHECK_DIR/LICENSE" ]; then
        return 0
    fi
    return 1
}

find_owned_backup() {
    for b in "$INSTALL_DIR.backup."*; do
        if is_committed_install_dir "$b"; then
            BPID=""
            if [ -f "$b/.paperassist-backup-marker" ]; then
                BPID=$(grep '^PID=' "$b/.paperassist-backup-marker" 2>/dev/null | cut -d= -f2-)
            fi
            if [ -z "$BPID" ]; then
                SUFFIX="${b##*.backup.}"
                case "$SUFFIX" in
                    ''|*[!0-9]*) ;;
                    *) BPID="$SUFFIX" ;;
                esac
            fi
            if [ -n "$BPID" ] && is_pid_alive "$BPID"; then
                continue
            fi
            echo "$b"
            return 0
        fi
    done
    return 1
}

check_fault_inject() {
    HOOK_NAME="$1"
    MATCH=0
    case "$__PAPERASSIST_INSTALLER_FAULT_INJECT" in
        "$HOOK_NAME") MATCH=1 ;;
        "before_manifest")
            if [ "$HOOK_NAME" = "after_swap_before_manifest" ]; then MATCH=1; fi
            ;;
        "launcher_write")
            if [ "$HOOK_NAME" = "during_integration" ]; then MATCH=1; fi
            ;;
        "before_cleanup")
            if [ "$HOOK_NAME" = "after_commit_before_cleanup" ]; then MATCH=1; fi
            ;;
    esac
    if [ "$MATCH" -eq 1 ]; then
        echo "Fault injected: $__PAPERASSIST_INSTALLER_FAULT_INJECT" >&2
        if [ "$__PAPERASSIST_INSTALLER_CRASH_SIGNAL" = "KILL" ] || [ "$__PAPERASSIST_INSTALLER_SIMULATE_CRASH" = "1" ]; then
            kill -9 $$
        else
            exit 1
        fi
    fi
}

# ------------------------------------------------------------------------------
# Early checks without side effects
# ------------------------------------------------------------------------------
# Root privileges check
if [ "$(id -u)" -eq 0 ] && [ "$ALLOW_ROOT" -ne 1 ]; then
    echo "Error: PaperAssist is designed to be installed per-user without sudo or administrator privileges." >&2
    echo "Running as root may misplace user libraries and configuration in /root." >&2
    echo "To force installation as root anyway, pass --allow-root." >&2
    exit 1
fi

# Verify-only mode
if [ "$VERIFY_ONLY" -eq 1 ]; then
    verify_package_integrity
    echo "Package integrity verified: SHA-256 digest matches ($PAYLOAD_SHA256)."
    exit 0
fi

# Step A: Immediate Destination Rejections (Zero Side Effects)
if [ -f "$INSTALL_DIR/library.sqlite3" ]; then
    echo "Error: Target install directory ($INSTALL_DIR) contains a PaperAssist library catalog (library.sqlite3)." >&2
    echo "PaperAssist application files must be kept separate from user research libraries." >&2
    exit 1
fi

if [ -e "$INSTALL_DIR" ] && [ ! -d "$INSTALL_DIR" ]; then
    echo "Error: Target install path ($INSTALL_DIR) exists and is not a directory." >&2
    exit 1
fi

# Step C: Check Foreign Unmanaged INSTALL_DIR Before Touching Anything
OWNED_BACKUP=$(find_owned_backup || true)

if [ -d "$INSTALL_DIR" ] && [ "$(ls -A "$INSTALL_DIR" 2>/dev/null)" ]; then
    if ! is_committed_install_dir "$INSTALL_DIR"; then
        IS_RECOVERABLE=0
        if is_incomplete_paperassist_dir "$INSTALL_DIR"; then
            if is_owned_staging_dir "$INSTALL_DIR" || [ -n "$OWNED_BACKUP" ]; then
                IS_RECOVERABLE=1
            fi
        fi
        if [ "$IS_RECOVERABLE" -eq 0 ]; then
            MANIFEST="$INSTALL_DIR/.paperassist-install-manifest"
            if [ ! -f "$MANIFEST" ]; then
                echo "Error: Target install directory ($INSTALL_DIR) exists, is not empty, and has no PaperAssist install manifest." >&2
                echo "Refusing to overwrite unmanaged files." >&2
                exit 1
            else
                echo "Error: Target install directory ($INSTALL_DIR) is not managed by PaperAssist-Linux-Installer." >&2
                exit 1
            fi
        fi
    fi
fi

# ------------------------------------------------------------------------------
# Step D: Concurrency Lock ($INSTALL_DIR.lock)
# ------------------------------------------------------------------------------
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

acquire_lock

STAGE=""
ROLLBACK_DIR=""
BACKUP_DIR=""
SWAPPED=0
ROLLBACK_ACTIVE=0

# A fresh install has no backup. Record the exact bytes before publishing each
# integration file so interrupted recovery removes only files we installed.
record_fresh_integration() {
    [ "$IS_UPGRADE" -eq 0 ] || return 0
    mkdir -p "$INSTALL_DIR/.paperassist-integration"
    printf '%s\n' "$2" > "$INSTALL_DIR/.paperassist-integration/$1.path"
    if [ -f "$2" ] && [ ! -L "$2" ]; then
        cp -a "$2" "$INSTALL_DIR/.paperassist-integration/$1.previous"
    fi
    cp -a "$3" "$INSTALL_DIR/.paperassist-integration/$1.expected"
}

remove_fresh_integration() {
    JOURNAL="$INSTALL_DIR/.paperassist-integration"
    for ITEM in launcher desktop icon.svg icon.png; do
        [ -f "$JOURNAL/$ITEM.path" ] && [ -f "$JOURNAL/$ITEM.expected" ] || continue
        TARGET=$(cat "$JOURNAL/$ITEM.path")
        case "$ITEM:$TARGET" in
            launcher:/*/paperassist|desktop:/*/applications/paperassist.desktop|\
            icon.svg:/*/icons/hicolor/scalable/apps/paperassist.svg|\
            icon.png:/*/icons/hicolor/256x256/apps/paperassist.png) ;;
            *) continue ;;
        esac
        if [ -f "$TARGET" ] && [ ! -L "$TARGET" ] &&
           cmp -s "$JOURNAL/$ITEM.expected" "$TARGET"; then
            if [ -f "$JOURNAL/$ITEM.previous" ]; then
                cp -a "$JOURNAL/$ITEM.previous" "$TARGET"
            else
                rm -f "$TARGET"
            fi
        fi
    done
}

cleanup_on_exit() {
    EXIT_STATUS=$?
    trap - EXIT HUP INT TERM
    if [ "$ROLLBACK_ACTIVE" -eq 1 ]; then
        echo "Installation failed. Rolling back changes..." >&2
        if [ "$SWAPPED" -eq 1 ]; then
            if [ "$IS_UPGRADE" -eq 0 ]; then
                remove_fresh_integration
            fi
            rm -rf "$INSTALL_DIR"
        fi
        if [ -n "$BACKUP_DIR" ] && [ -d "$BACKUP_DIR" ]; then
            mv "$BACKUP_DIR" "$INSTALL_DIR"
            rm -rf "$INSTALL_DIR/.paperassist-rollback" 2>/dev/null || true
        fi
        if [ -f "$ROLLBACK_DIR/launcher" ]; then
            cp -a "$ROLLBACK_DIR/launcher" "$LAUNCHER_PATH"
        fi
        if [ -f "$ROLLBACK_DIR/desktop" ]; then
            cp -a "$ROLLBACK_DIR/desktop" "$DESKTOP_PATH"
        fi
        if [ -f "$ROLLBACK_DIR/icon.svg" ]; then
            cp -a "$ROLLBACK_DIR/icon.svg" "$ICON_SVG_TARGET"
        fi
        if [ -f "$ROLLBACK_DIR/icon.png" ]; then
            cp -a "$ROLLBACK_DIR/icon.png" "$ICON_PNG_TARGET"
        fi
        echo "Rollback complete. Previous installation state preserved." >&2
    fi
    if [ -n "$STAGE" ] && [ -d "$STAGE" ]; then
        rm -rf "$STAGE"
    fi
    if [ -n "$ROLLBACK_DIR" ] && [ -d "$ROLLBACK_DIR" ]; then
        rm -rf "$ROLLBACK_DIR"
    fi
    release_lock
    exit "$EXIT_STATUS"
}
trap 'cleanup_on_exit' EXIT HUP INT TERM

# ------------------------------------------------------------------------------
# Auto-recovery and Repair
# ------------------------------------------------------------------------------
auto_recover() {
    # 1. Clean stale owned staging directories
    for s in "$INSTALL_DIR.staging."*; do
        if is_owned_staging_dir "$s"; then
            STAGE_PID=$(grep '^PID=' "$s/.paperassist-staging-marker" 2>/dev/null | cut -d= -f2-)
            if [ -z "$STAGE_PID" ]; then
                SUFFIX="${s##*.staging.}"
                case "$SUFFIX" in
                    ''|*[!0-9]*) ;;
                    *) STAGE_PID="$SUFFIX" ;;
                esac
            fi
            if ! is_pid_alive "$STAGE_PID"; then
                rm -rf "$s"
            fi
        fi
    done

    # 2. Recover incomplete or missing INSTALL_DIR from a valid owned backup
    if ! is_committed_install_dir "$INSTALL_DIR"; then
        RESTORE_BACKUP=""
        for b in "$INSTALL_DIR.backup."*; do
            if is_committed_install_dir "$b"; then
                BPID=""
                if [ -f "$b/.paperassist-backup-marker" ]; then
                    BPID=$(grep '^PID=' "$b/.paperassist-backup-marker" 2>/dev/null | cut -d= -f2-)
                fi
                if [ -z "$BPID" ]; then
                    SUFFIX="${b##*.backup.}"
                    case "$SUFFIX" in
                        ''|*[!0-9]*) ;;
                        *) BPID="$SUFFIX" ;;
                    esac
                fi
                if [ -n "$BPID" ] && is_pid_alive "$BPID"; then
                    continue
                fi
                RESTORE_BACKUP="$b"
                break
            fi
        done

        if [ -n "$RESTORE_BACKUP" ]; then
            if [ -d "$INSTALL_DIR" ] && is_incomplete_paperassist_dir "$INSTALL_DIR"; then
                rm -rf "$INSTALL_DIR"
            fi
            if [ ! -e "$INSTALL_DIR" ]; then
                echo "Interrupted installation detected: restoring $RESTORE_BACKUP -> $INSTALL_DIR" >&2
                mv "$RESTORE_BACKUP" "$INSTALL_DIR"
                if [ -d "$INSTALL_DIR/.paperassist-rollback" ]; then
                    if [ -f "$INSTALL_DIR/.paperassist-rollback/launcher" ]; then
                        mkdir -p "$(dirname "$LAUNCHER_PATH")" 2>/dev/null || true
                        cp -a "$INSTALL_DIR/.paperassist-rollback/launcher" "$LAUNCHER_PATH"
                    fi
                    if [ -f "$INSTALL_DIR/.paperassist-rollback/desktop" ]; then
                        mkdir -p "$(dirname "$DESKTOP_PATH")" 2>/dev/null || true
                        cp -a "$INSTALL_DIR/.paperassist-rollback/desktop" "$DESKTOP_PATH"
                    fi
                    if [ -f "$INSTALL_DIR/.paperassist-rollback/icon.svg" ]; then
                        mkdir -p "$(dirname "$ICON_SVG_TARGET")" 2>/dev/null || true
                        cp -a "$INSTALL_DIR/.paperassist-rollback/icon.svg" "$ICON_SVG_TARGET"
                    fi
                    if [ -f "$INSTALL_DIR/.paperassist-rollback/icon.png" ]; then
                        mkdir -p "$(dirname "$ICON_PNG_TARGET")" 2>/dev/null || true
                        cp -a "$INSTALL_DIR/.paperassist-rollback/icon.png" "$ICON_PNG_TARGET"
                    fi
                    rm -rf "$INSTALL_DIR/.paperassist-rollback"
                fi
            fi
        elif [ -d "$INSTALL_DIR" ] && is_owned_staging_dir "$INSTALL_DIR"; then
            remove_fresh_integration
            rm -rf "$INSTALL_DIR"
            rm -rf "$INSTALL_DIR/.paperassist-rollback" 2>/dev/null || true
        fi
    fi

    # 3. Clean up leftover owned backups ONLY when INSTALL_DIR is a valid committed installation
    if is_committed_install_dir "$INSTALL_DIR"; then
        rm -f "$INSTALL_DIR/.paperassist-staging-marker"
        rm -rf "$INSTALL_DIR/.paperassist-rollback" "$INSTALL_DIR/.paperassist-integration"
        for b in "$INSTALL_DIR.backup."*; do
            if is_committed_install_dir "$b"; then
                BPID=""
                if [ -f "$b/.paperassist-backup-marker" ]; then
                    BPID=$(grep '^PID=' "$b/.paperassist-backup-marker" 2>/dev/null | cut -d= -f2-)
                fi
                if [ -z "$BPID" ]; then
                    SUFFIX="${b##*.backup.}"
                    case "$SUFFIX" in
                        ''|*[!0-9]*) ;;
                        *) BPID="$SUFFIX" ;;
                    esac
                fi
                if [ -n "$BPID" ] && is_pid_alive "$BPID"; then
                    continue
                fi
                rm -rf "$b"
            fi
        done
    fi
}

auto_recover

if [ "$REPAIR_ONLY" -eq 1 ]; then
    if is_committed_install_dir "$INSTALL_DIR"; then
        echo "Repair successful: $INSTALL_DIR restored."
        release_lock
        trap - EXIT HUP INT TERM
        exit 0
    else
        echo "Error: No restorable PaperAssist installation found at $INSTALL_DIR." >&2
        release_lock
        trap - EXIT HUP INT TERM
        exit 1
    fi
fi

# ------------------------------------------------------------------------------
# Uninstall action
# ------------------------------------------------------------------------------
if [ "$UNINSTALL_ACTION" -eq 1 ]; then
    if [ ! -d "$INSTALL_DIR" ]; then
        echo "Error: Installation directory does not exist: $INSTALL_DIR" >&2
        release_lock
        trap - EXIT HUP INT TERM
        exit 1
    fi

    MANIFEST="$INSTALL_DIR/.paperassist-install-manifest"
    if [ ! -f "$MANIFEST" ]; then
        echo "Error: No installation manifest found in $INSTALL_DIR." >&2
        echo "Refusing to uninstall unmanaged directory." >&2
        release_lock
        trap - EXIT HUP INT TERM
        exit 1
    fi

    if ! grep -q "Managed-By: PaperAssist-Linux-Installer" "$MANIFEST" 2>/dev/null; then
        echo "Error: Manifest in $INSTALL_DIR is not managed by PaperAssist-Linux-Installer." >&2
        echo "Refusing to uninstall unmanaged directory." >&2
        release_lock
        trap - EXIT HUP INT TERM
        exit 1
    fi

    LAUNCHER_TO_REMOVE=$(grep '^LAUNCHER_PATH=' "$MANIFEST" 2>/dev/null | cut -d= -f2-)
    if [ -n "$LAUNCHER_TO_REMOVE" ] && { [ -e "$LAUNCHER_TO_REMOVE" ] || [ -L "$LAUNCHER_TO_REMOVE" ]; }; then
        if grep -q "Managed-By: PaperAssist-Linux-Installer" "$LAUNCHER_TO_REMOVE" 2>/dev/null; then
            rm -f "$LAUNCHER_TO_REMOVE"
            echo "Removed launcher: $LAUNCHER_TO_REMOVE"
        fi
    fi

    DESKTOP_TO_REMOVE=$(grep '^DESKTOP_PATH=' "$MANIFEST" 2>/dev/null | cut -d= -f2-)
    if [ -n "$DESKTOP_TO_REMOVE" ] && { [ -e "$DESKTOP_TO_REMOVE" ] || [ -L "$DESKTOP_TO_REMOVE" ]; }; then
        if grep -q "Managed-By: PaperAssist-Linux-Installer" "$DESKTOP_TO_REMOVE" 2>/dev/null; then
            rm -f "$DESKTOP_TO_REMOVE"
            echo "Removed desktop entry: $DESKTOP_TO_REMOVE"
        fi
    fi

    grep '^ICON_ENTRY=' "$MANIFEST" 2>/dev/null | cut -d= -f2- | while IFS='|' read -r ICON_PATH RECORDED_HASH; do
        if [ -n "$ICON_PATH" ] && { [ -e "$ICON_PATH" ] || [ -L "$ICON_PATH" ]; }; then
            CURR_HASH=$($SHA_CMD "$ICON_PATH" 2>/dev/null | awk '{print $1}')
            if [ -z "$RECORDED_HASH" ] || [ "$CURR_HASH" = "$RECORDED_HASH" ]; then
                rm -f "$ICON_PATH"
                echo "Removed icon: $ICON_PATH"
            fi
        fi
    done

    if [ -n "$DESKTOP_TO_REMOVE" ] && command -v update-desktop-database >/dev/null 2>&1; then
        update-desktop-database "$(dirname "$DESKTOP_TO_REMOVE")" >/dev/null 2>&1 || true
    fi

    rm -rf "$INSTALL_DIR"
    release_lock
    trap - EXIT HUP INT TERM
    echo "PaperAssist uninstalled successfully."
    echo "Your user libraries, PDFs, annotations, and configuration files were preserved."
    exit 0
fi

# ------------------------------------------------------------------------------
# Pre-installation checks
# ------------------------------------------------------------------------------
# Verify payload integrity before staging or modifying target
verify_package_integrity

# Check INSTALL_DIR ownership if it exists and is non-empty
IS_UPGRADE=0
if is_committed_install_dir "$INSTALL_DIR"; then
    IS_UPGRADE=1
fi

# Check LAUNCHER_PATH ownership if it exists or is a symlink
if [ "$NO_LAUNCHER" -ne 1 ]; then
    if [ -L "$LAUNCHER_PATH" ]; then
        echo "Error: Refusing to overwrite launcher symlink: $LAUNCHER_PATH" >&2
        exit 1
    fi
    if [ -e "$LAUNCHER_PATH" ]; then
        if ! grep -q "Managed-By: PaperAssist-Linux-Installer" "$LAUNCHER_PATH" 2>/dev/null; then
            echo "Error: Launcher ($LAUNCHER_PATH) already exists and is not managed by PaperAssist-Linux-Installer." >&2
            echo "Refusing to overwrite foreign launcher. Choose a different --bin-dir or pass --no-launcher." >&2
            exit 1
        fi
    fi
fi

# Check DESKTOP_PATH ownership if it exists or is a symlink
if [ "$NO_DESKTOP" -ne 1 ]; then
    if [ -L "$DESKTOP_PATH" ]; then
        echo "Error: Refusing to overwrite desktop entry symlink: $DESKTOP_PATH" >&2
        exit 1
    fi
    if [ -e "$DESKTOP_PATH" ]; then
        if ! grep -q "Managed-By: PaperAssist-Linux-Installer" "$DESKTOP_PATH" 2>/dev/null; then
            echo "Error: Desktop entry ($DESKTOP_PATH) already exists and is not managed by PaperAssist-Linux-Installer." >&2
            echo "Refusing to overwrite foreign desktop file. Pass --no-desktop to skip desktop integration." >&2
            exit 1
        fi
    fi
fi

# Check pre-existing icons
check_icon_ownership() {
    ICON_FILE="$1"
    if [ -e "$ICON_FILE" ] || [ -L "$ICON_FILE" ]; then
        if [ "$IS_UPGRADE" -eq 1 ]; then
            RECORDED_ENTRY=$(grep "^ICON_ENTRY=$ICON_FILE|" "$INSTALL_DIR/.paperassist-install-manifest" 2>/dev/null || true)
            if [ -z "$RECORDED_ENTRY" ]; then
                echo "Error: Pre-existing icon ($ICON_FILE) is not recorded in PaperAssist install manifest." >&2
                echo "Refusing to overwrite unowned icon." >&2
                exit 1
            fi
            REC_HASH=$(printf '%s' "$RECORDED_ENTRY" | cut -d'|' -f2)
            CURR_HASH=$($SHA_CMD "$ICON_FILE" 2>/dev/null | awk '{print $1}')
            if [ "$REC_HASH" != "$CURR_HASH" ]; then
                echo "Error: Pre-existing icon ($ICON_FILE) was modified from its installed state." >&2
                echo "Refusing to overwrite modified icon." >&2
                exit 1
            fi
        else
            echo "Error: Pre-existing icon ($ICON_FILE) already exists and was not installed by PaperAssist." >&2
            echo "Refusing to overwrite unowned icon." >&2
            exit 1
        fi
    fi
}

if [ "$NO_DESKTOP" -ne 1 ]; then
    check_icon_ownership "$ICON_SVG_TARGET"
    check_icon_ownership "$ICON_PNG_TARGET"
fi

# ------------------------------------------------------------------------------
# Staging and payload extraction
# ------------------------------------------------------------------------------
INSTALL_PARENT="$(dirname "$INSTALL_DIR")"
mkdir -p "$INSTALL_PARENT" || {
    echo "Error: Cannot create parent directory: $INSTALL_PARENT" >&2
    exit 1
}

STAGE="$INSTALL_DIR.staging.$$"
mkdir -p "$STAGE" || {
    echo "Error: Cannot create staging directory: $STAGE" >&2
    exit 1
}
cat > "$STAGE/.paperassist-staging-marker" << EOF_STAGE_MARKER
# Managed-By: PaperAssist-Linux-Installer
PID=$$
EOF_STAGE_MARKER

# Fault injection hook: during_extraction
if [ "$__PAPERASSIST_INSTALLER_FAULT_INJECT" = "during_extraction" ]; then
    echo "partial_corrupt_data" > "$STAGE/partial.tmp"
    check_fault_inject "during_extraction"
fi

# Extract payload into STAGE
tail -c +$((PAYLOAD_START_BYTE + 1)) "$SCRIPT_FILE" | head -c "$PAYLOAD_SIZE" | tar -xz -C "$STAGE" || {
    rm -rf "$STAGE"
    echo "Error: Failed to extract payload archive into staging directory." >&2
    exit 1
}

# Validate completeness of extracted bundle
if [ ! -f "$STAGE/PaperAssist" ] || [ ! -x "$STAGE/PaperAssist" ]; then
    rm -rf "$STAGE"
    echo "Error: Extracted bundle validation failed: PaperAssist executable is missing or not executable." >&2
    exit 1
fi
if [ ! -d "$STAGE/_internal" ]; then
    rm -rf "$STAGE"
    echo "Error: Extracted bundle validation failed: _internal directory is missing." >&2
    exit 1
fi
if [ ! -f "$STAGE/LICENSE" ]; then
    rm -rf "$STAGE"
    echo "Error: Extracted bundle validation failed: LICENSE file is missing." >&2
    exit 1
fi

# Run execution sanity test
if "$STAGE/PaperAssist" --version >/dev/null 2>&1; then
    :
elif "$STAGE/PaperAssist" --help >/dev/null 2>&1; then
    :
else
    rm -rf "$STAGE"
    echo "Error: Extracted PaperAssist binary failed execution sanity check." >&2
    exit 1
fi

# Fault injection hook: after_stage
check_fault_inject "after_stage"

# ------------------------------------------------------------------------------
# Rollback handler and atomic swap
# ------------------------------------------------------------------------------
ROLLBACK_DIR="$(mktemp -d -t paperassist-rb.XXXXXX 2>/dev/null || mktemp -d /tmp/paperassist-rb.XXXXXX)"
BACKUP_DIR=""
SWAPPED=0
ROLLBACK_ACTIVE=1

# Save existing launcher, desktop file, and icons to rollback backup
if [ -f "$LAUNCHER_PATH" ]; then
    cp -a "$LAUNCHER_PATH" "$ROLLBACK_DIR/launcher"
fi
if [ -f "$DESKTOP_PATH" ]; then
    cp -a "$DESKTOP_PATH" "$ROLLBACK_DIR/desktop"
fi
if [ -f "$ICON_SVG_TARGET" ]; then
    cp -a "$ICON_SVG_TARGET" "$ROLLBACK_DIR/icon.svg"
fi
if [ -f "$ICON_PNG_TARGET" ]; then
    cp -a "$ICON_PNG_TARGET" "$ROLLBACK_DIR/icon.png"
fi

# Atomic move of existing installation to backup
if [ "$IS_UPGRADE" -eq 1 ]; then
    mkdir -p "$INSTALL_DIR/.paperassist-rollback"
    cat > "$INSTALL_DIR/.paperassist-rollback/marker" << EOF_RB_MARKER
# Managed-By: PaperAssist-Linux-Installer
PID=$$
EOF_RB_MARKER
    if [ -f "$LAUNCHER_PATH" ]; then
        cp -a "$LAUNCHER_PATH" "$INSTALL_DIR/.paperassist-rollback/launcher"
    fi
    if [ -f "$DESKTOP_PATH" ]; then
        cp -a "$DESKTOP_PATH" "$INSTALL_DIR/.paperassist-rollback/desktop"
    fi
    if [ -f "$ICON_SVG_TARGET" ]; then
        cp -a "$ICON_SVG_TARGET" "$INSTALL_DIR/.paperassist-rollback/icon.svg"
    fi
    if [ -f "$ICON_PNG_TARGET" ]; then
        cp -a "$ICON_PNG_TARGET" "$INSTALL_DIR/.paperassist-rollback/icon.png"
    fi

    BACKUP_DIR="$INSTALL_DIR.backup.$$"
    mv "$INSTALL_DIR" "$BACKUP_DIR"
    cat > "$BACKUP_DIR/.paperassist-backup-marker" << EOF_BK_MARKER
# Managed-By: PaperAssist-Linux-Installer
PID=$$
EOF_BK_MARKER
fi

# Fault injection hook: after_backup
check_fault_inject "after_backup"

# An existing empty target must be removed so mv renames instead of nesting the stage.
if [ "$IS_UPGRADE" -eq 0 ] && [ -d "$INSTALL_DIR" ]; then
    rmdir "$INSTALL_DIR"
fi
mv "$STAGE" "$INSTALL_DIR"
SWAPPED=1

# Fault injection hook: after_swap_before_manifest (also before_manifest)
check_fault_inject "after_swap_before_manifest"

# ------------------------------------------------------------------------------
# Install components (Uninstall script, Launcher, Desktop Entry, Icons)
# ------------------------------------------------------------------------------
# 1. Standalone uninstall script
if [ ! -f "$INSTALL_DIR/uninstall.sh" ]; then
    cat > "$INSTALL_DIR/uninstall.sh" << 'EOF_UNINSTALL_SCRIPT'
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
EOF_UNINSTALL_SCRIPT
fi
chmod 755 "$INSTALL_DIR/uninstall.sh"

# 2. CLI Launcher
RECORDED_LAUNCHER=""
if [ "$NO_LAUNCHER" -ne 1 ]; then
    mkdir -p "$BIN_DIR" || {
        echo "Error: Cannot create bin directory: $BIN_DIR" >&2
        exit 1
    }
    TARGET_BIN="$INSTALL_DIR/PaperAssist"
    ESCAPED_TARGET=$(printf '%s' "$TARGET_BIN" | sed -e 's/\\/\\\\/g' -e 's/"/\\"/g' -e 's/\$/\\\$/g' -e 's/`/\\`/g')
    LAUNCHER_TMP="$BIN_DIR/.paperassist.launcher.tmp.$$"
    cat > "$LAUNCHER_TMP" << EOF_LAUNCHER
#!/bin/sh
# Managed-By: PaperAssist-Linux-Installer
exec "$ESCAPED_TARGET" "\$@"
EOF_LAUNCHER
    chmod 755 "$LAUNCHER_TMP"
    record_fresh_integration launcher "$LAUNCHER_PATH" "$LAUNCHER_TMP"
    mv "$LAUNCHER_TMP" "$LAUNCHER_PATH"
    RECORDED_LAUNCHER="$LAUNCHER_PATH"
fi

# 3. Desktop Entry and Icons
RECORDED_DESKTOP=""
RECORDED_ICONS=""

if [ "$NO_DESKTOP" -ne 1 ]; then
    if [ -n "$RECORDED_LAUNCHER" ]; then
        EXEC_TARGET="$RECORDED_LAUNCHER"
    else
        EXEC_TARGET="$INSTALL_DIR/PaperAssist"
    fi
    ESCAPED_EXEC=$(printf '%s' "$EXEC_TARGET" | sed -e 's/\\/\\\\/g' -e 's/"/\\"/g' -e 's/\$/\\\$/g' -e 's/`/\\`/g' -e 's/%/%%/g')

    mkdir -p "$APPLICATIONS_DIR" || {
        echo "Error: Cannot create applications directory: $APPLICATIONS_DIR" >&2
        exit 1
    }
    DESKTOP_TMP="$APPLICATIONS_DIR/.paperassist.desktop.tmp.$$"

    if [ -f "$INSTALL_DIR/desktop/paperassist.desktop" ]; then
        EXEC_LINE="Exec=\"$ESCAPED_EXEC\" %F" awk '
            BEGIN { header_printed = 0 }
            /^\[Desktop Entry\]/ {
                print $0
                print "# Managed-By: PaperAssist-Linux-Installer"
                print "MimeType=application/pdf;"
                header_printed = 1
                next
            }
            /^# Managed-By: PaperAssist-Linux-Installer/ { next }
            /^MimeType=/ { next }
            /^Exec=/ {
                print ENVIRON["EXEC_LINE"]
                next
            }
            { print $0 }
        ' "$INSTALL_DIR/desktop/paperassist.desktop" > "$DESKTOP_TMP"
    else
        cat > "$DESKTOP_TMP" << EOF_DESKTOP
[Desktop Entry]
# Managed-By: PaperAssist-Linux-Installer
Version=1.0
Type=Application
Name=PaperAssist
GenericName=Research Paper Reader
Comment=Read research PDFs locally and consult cited references
Exec="$ESCAPED_EXEC" %F
MimeType=application/pdf;
Icon=paperassist
Terminal=false
Categories=Office;Viewer;Science;Literature;
StartupNotify=true
StartupWMClass=PaperAssist
EOF_DESKTOP
    fi
    chmod 644 "$DESKTOP_TMP"
    record_fresh_integration desktop "$DESKTOP_PATH" "$DESKTOP_TMP"
    mv "$DESKTOP_TMP" "$DESKTOP_PATH"
    RECORDED_DESKTOP="$DESKTOP_PATH"

    # Install scalable SVG icon if present in payload desktop/
    if [ -f "$INSTALL_DIR/desktop/paperassist.svg" ]; then
        mkdir -p "$(dirname "$ICON_SVG_TARGET")"
        record_fresh_integration icon.svg "$ICON_SVG_TARGET" "$INSTALL_DIR/desktop/paperassist.svg"
        ICON_TMP="$(dirname "$ICON_SVG_TARGET")/.paperassist.svg.tmp.$$"
        cp -a "$INSTALL_DIR/desktop/paperassist.svg" "$ICON_TMP"
        mv "$ICON_TMP" "$ICON_SVG_TARGET"
        SVG_HASH=$($SHA_CMD "$ICON_SVG_TARGET" 2>/dev/null | awk '{print $1}')
        RECORDED_ICONS="$RECORDED_ICONS
ICON_ENTRY=$ICON_SVG_TARGET|$SVG_HASH"
    fi

    # Install 256x256 PNG icon if present in payload desktop/
    if [ -f "$INSTALL_DIR/desktop/paperassist.png" ]; then
        mkdir -p "$(dirname "$ICON_PNG_TARGET")"
        record_fresh_integration icon.png "$ICON_PNG_TARGET" "$INSTALL_DIR/desktop/paperassist.png"
        ICON_TMP="$(dirname "$ICON_PNG_TARGET")/.paperassist.png.tmp.$$"
        cp -a "$INSTALL_DIR/desktop/paperassist.png" "$ICON_TMP"
        mv "$ICON_TMP" "$ICON_PNG_TARGET"
        PNG_HASH=$($SHA_CMD "$ICON_PNG_TARGET" 2>/dev/null | awk '{print $1}')
        RECORDED_ICONS="$RECORDED_ICONS
ICON_ENTRY=$ICON_PNG_TARGET|$PNG_HASH"
    fi
fi

# Fault injection hook: during_integration (also launcher_write)
check_fault_inject "during_integration"

# 4. Manifest (written atomically)
TIMESTAMP=$(date -u +"%Y-%m-%dT%H:%M:%SZ" 2>/dev/null || date +"%Y-%m-%d")
cat > "$INSTALL_DIR/.paperassist-install-manifest.tmp.$$" << EOF_MANIFEST
# Managed-By: PaperAssist-Linux-Installer
PACKAGE_NAME=$APP_NAME
VERSION=$APP_VERSION
INSTALL_TIMESTAMP=$TIMESTAMP
INSTALL_DIR=$INSTALL_DIR
LAUNCHER_PATH=$RECORDED_LAUNCHER
DESKTOP_PATH=$RECORDED_DESKTOP$RECORDED_ICONS
EOF_MANIFEST
mv "$INSTALL_DIR/.paperassist-install-manifest.tmp.$$" "$INSTALL_DIR/.paperassist-install-manifest"
rm -f "$INSTALL_DIR/.paperassist-staging-marker"
rm -rf "$INSTALL_DIR/.paperassist-rollback" "$INSTALL_DIR/.paperassist-integration"
ROLLBACK_ACTIVE=0

# Fault injection hook: after_commit_before_cleanup (also before_cleanup)
check_fault_inject "after_commit_before_cleanup"

# ------------------------------------------------------------------------------
# Finalize and Success
# ------------------------------------------------------------------------------
if [ -n "$BACKUP_DIR" ] && [ -d "$BACKUP_DIR" ]; then
    rm -rf "$BACKUP_DIR"
fi
rm -rf "$ROLLBACK_DIR"
release_lock
trap - EXIT HUP INT TERM

echo "PaperAssist $APP_VERSION installed successfully to:"
echo "  $INSTALL_DIR"

if [ -n "$RECORDED_LAUNCHER" ]; then
    echo "Launcher installed to:"
    echo "  $RECORDED_LAUNCHER"

    case ":$PATH:" in
        *":$BIN_DIR:"*) ;;
        *)
            echo ""
            echo "Note: $BIN_DIR is not currently in your PATH."
            echo "To run 'paperassist' from the command line, add it to your PATH:"
            echo "  export PATH=\"$BIN_DIR:\$PATH\""
            ;;
    esac
fi

if [ -n "$RECORDED_DESKTOP" ]; then
    echo "Desktop entry installed to:"
    echo "  $RECORDED_DESKTOP"
    if command -v update-desktop-database >/dev/null 2>&1; then
        update-desktop-database "$APPLICATIONS_DIR" >/dev/null 2>&1 || true
    fi
fi

exit 0
__PAYLOAD_BELOW__
