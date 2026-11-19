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
