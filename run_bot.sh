#!/bin/bash
# run_bot.sh — Bot supervisor with auto-restart and crash recovery
#
# Usage:
#   ./run_bot.sh          # Run bot with auto-restart on crash
#   ./run_bot.sh --watch  # Also restart on code changes
#   ./run_bot.sh stop     # Stop the bot

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

VENV=".venv/bin/python3"
PID_FILE="data/bot.pid"
WATCH_MODE=false

# Parse args
case "${1:-}" in
    stop)
        if [ -f "$PID_FILE" ]; then
            PID=$(cat "$PID_FILE")
            if kill -0 "$PID" 2>/dev/null; then
                echo "Stopping bot (PID $PID)..."
                kill "$PID"
                sleep 3
                kill -0 "$PID" 2>/dev/null && kill -9 "$PID"
                echo "Bot stopped."
            else
                echo "Bot not running (stale PID file)."
                rm -f "$PID_FILE"
            fi
        else
            echo "No PID file found."
        fi
        exit 0
        ;;
    --watch)
        WATCH_MODE=true
        ;;
esac

# Check if already running
if [ -f "$PID_FILE" ]; then
    OLD_PID=$(cat "$PID_FILE")
    if kill -0 "$OLD_PID" 2>/dev/null; then
        echo "Bot already running (PID $OLD_PID). Use './run_bot.sh stop' first."
        exit 1
    fi
    rm -f "$PID_FILE"
fi

# Trap signals for clean shutdown
cleanup() {
    echo "Received shutdown signal..."
    if [ -n "${BOT_PID:-}" ] && kill -0 "$BOT_PID" 2>/dev/null; then
        kill "$BOT_PID"
        wait "$BOT_PID" 2>/dev/null
    fi
    if [ -n "${WATCH_PID:-}" ] && kill -0 "$WATCH_PID" 2>/dev/null; then
        kill "$WATCH_PID"
    fi
    rm -f "$PID_FILE"
    exit 0
}
trap cleanup SIGTERM SIGINT

echo "=== Bot Supervisor ==="
echo "Mode: $([ "$WATCH_MODE" = true ] && echo 'watch + auto-restart' || echo 'auto-restart')"
echo ""

# File watcher (optional)
if [ "$WATCH_MODE" = true ]; then
    if command -v inotifywait &>/dev/null; then
        (
            while true; do
                inotifywait -q -r -e modify,create,delete \
                    --include '\.py$|\.yaml$' \
                    bot/ config/ 2>/dev/null
                echo "[watcher] Code change detected — restarting bot..."
                if [ -f "$PID_FILE" ]; then
                    kill "$(cat "$PID_FILE")" 2>/dev/null
                fi
                sleep 2
            done
        ) &
        WATCH_PID=$!
        echo "File watcher started (PID $WATCH_PID)"
    else
        echo "WARNING: inotifywait not found. Install inotify-tools for --watch mode."
        echo "  sudo apt install inotify-tools"
    fi
fi

# Main restart loop
RESTART_COUNT=0
MAX_FAST_RESTARTS=5
LAST_START=0

while true; do
    NOW=$(date +%s)

    # Prevent rapid restart loops
    ELAPSED=$((NOW - LAST_START))
    if [ "$ELAPSED" -lt 10 ]; then
        RESTART_COUNT=$((RESTART_COUNT + 1))
        if [ "$RESTART_COUNT" -ge "$MAX_FAST_RESTARTS" ]; then
            echo "[supervisor] Too many fast restarts ($RESTART_COUNT). Waiting 60s..."
            sleep 60
            RESTART_COUNT=0
        fi
    else
        RESTART_COUNT=0
    fi
    LAST_START=$NOW

    echo "[supervisor] Starting bot... ($(date '+%Y-%m-%d %H:%M:%S'))"
    $VENV -m bot.main &
    BOT_PID=$!

    # bot/main.py writes its own PID file; supervisor must NOT pre-write it
    # or the bot's _acquire_pid_lock() sees its own PID and exits as a duplicate.
    set +e
    wait "$BOT_PID" 2>/dev/null
    EXIT_CODE=$?
    set -e
    rm -f "$PID_FILE"

    if [ $EXIT_CODE -eq 0 ]; then
        echo "[supervisor] Bot exited cleanly (code 0)."
        # Clean exit could be from file watcher restart — continue loop
    elif [ $EXIT_CODE -eq 1 ]; then
        echo "[supervisor] Bot exited with error (code 1). Check logs."
        sleep 5
    else
        echo "[supervisor] Bot crashed (code $EXIT_CODE). Restarting in 3s..."
        sleep 3
    fi
done
