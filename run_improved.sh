#!/bin/bash
# TradingClaw Auto-Restart Script for Screen/Detached Mode
# Usage: ./run_improved.sh {start|stop|status|attach|logs}

set -e  # Exit on any error

# Configuration
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SESSION_NAME="tradingclaw"
LOG_DIR="$SCRIPT_DIR/logs"
WATCHDOG_LOG="$LOG_DIR/watchdog.log"
AUTONOMOUS_LOG_TODAY() { echo "$LOG_DIR/autonomous_$(date '+%Y-%m-%d').log"; }
AUTONOMOUS_LOG="$LOG_DIR/autonomous.log"  # symlink to today's log for backward compat
PID_FILE="$LOG_DIR/tradingclaw.pid"
MAX_RESTART=5
RESTART_DELAY=30
RESTART_COUNT_FILE="$LOG_DIR/restart_count"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Ensure log directory exists
mkdir -p "$LOG_DIR"

# Logging function
log_message() {
    local level=$1
    local message=$2
    local timestamp=$(date '+%Y-%m-%d %H:%M:%S')

    case $level in
        INFO)
            echo "[$timestamp] [INFO] $message" | tee -a "$WATCHDOG_LOG"
            ;;
        ERROR)
            echo -e "${RED}[$timestamp] [ERROR] $message${NC}" | tee -a "$WATCHDOG_LOG"
            ;;
        WARN)
            echo -e "${YELLOW}[$timestamp] [WARN] $message${NC}" | tee -a "$WATCHDOG_LOG"
            ;;
    esac
}

# Check if TradingClaw is already running
is_running() {
    local pid=""
    if [ -f "$PID_FILE" ]; then
        pid=$(cat "$PID_FILE")
        if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; then
            return 0  # Running
        fi
    fi

    # Also check screen session
    if screen -list | grep -q "$SESSION_NAME"; then
        return 0  # Running in screen
    fi

    return 1  # Not running
}

# Start TradingClaw in screen
start_claw() {
    log_message "INFO" "Starting TradingClaw in screen session '$SESSION_NAME'..."

    # Stop any existing instances
    stop_claw

    # Clean up old logs (>30 days old)
    find "$LOG_DIR" -name "*.log" -mtime +30 -delete 2>/dev/null || true
    find "$LOG_DIR" -name "autonomous_*.log" -mtime +30 -delete 2>/dev/null || true

    # Start in screen with detached mode — use daily log file
    cd "$SCRIPT_DIR"

    # Create start command
    local cmd="python3 -u main.py --daemon"

    # Determine today's log file
    local today_log=$(AUTONOMOUS_LOG_TODAY)
    
    # Update symlink for backward compatibility
    ln -sf "$today_log" "$AUTONOMOUS_LOG"
    
    # Start screen session (detached) — tee to daily-dated log
    screen -dmS "$SESSION_NAME" bash -c "cd $SCRIPT_DIR; $cmd 2>&1 | tee -a $today_log" &

    local screen_pid=$!

    # Save PID
    if [ -n "$screen_pid" ]; then
        echo "$screen_pid" > "$PID_FILE"
        log_message "INFO" "TradingClaw started with PID: $screen_pid"
    else
        log_message "ERROR" "Failed to get screen PID"
        return 1
    fi

    # Wait a bit and verify
    sleep 5

    if is_running; then
        log_message "INFO" "TradingClaw started successfully"
        log_message "INFO" "Use: $0 attach to monitor the session"
    else
        log_message "ERROR" "TradingClaw failed to start"
        return 1
    fi

    # Reset restart count
    echo "0" > "$RESTART_COUNT_FILE"
}

# Stop TradingClaw
stop_claw() {
    log_message "INFO" "Stopping TradingClaw..."

    # Kill screen session
    if screen -list | grep -q "$SESSION_NAME"; then
        screen -S "$SESSION_NAME" -X quit 2>/dev/null
        log_message "INFO" "Screen session '$SESSION_NAME' stopped"
    fi

    # Kill process by PID if exists
    if [ -f "$PID_FILE" ]; then
        local pid=$(cat "$PID_FILE")
        if [ -n "$pid" ]; then
            kill -TERM "$pid" 2>/dev/null || true
            sleep 2
            kill -KILL "$pid" 2>/dev/null || true
            log_message "INFO" "Process $pid killed"
        fi
        rm -f "$PID_FILE"
    fi

    # Kill any remaining python processes
    pkill -f "python3.*main.py" 2>/dev/null || true

    log_message "INFO" "TradingClaw stopped"
}

# Watchdog loop - auto-restart on crash
run_watchdog() {
    local restart_count=0
    local sleep_time=10

    # Initialize restart count
    if [ ! -f "$RESTART_COUNT_FILE" ]; then
        echo "0" > "$RESTART_COUNT_FILE"
    else
        restart_count=$(cat "$RESTART_COUNT_FILE")
    fi

    log_message "INFO" "Watchdog started. Current restart count: $restart_count"

    while true; do
        # Check if running
        if is_running; then
            log_message "INFO" "TradingClaw is running (PID: $(cat $PID_FILE 2>/dev/null)). Monitoring..."

            # Wait and monitor
            local timeout=60
            while [ $timeout -gt 0 ]; do
                sleep 1
                timeout=$((timeout - 1))

                # Check if still running
                if ! is_running; then
                    log_message "WARN" "TradingClaw stopped unexpectedly!"
                    break
                fi

                # Check for critical errors in logs
                if [ -f "$AUTONOMOUS_LOG" ]; then
                    if grep -qi "critical\|exception\|traceback\|fatal error" "$AUTONOMOUS_LOG"; then
                        log_message "ERROR" "CRITICAL ERROR detected in logs!"
                        break
                    fi
                fi
            done
        else
            log_message "ERROR" "TradingClaw is NOT running!"
            sleep 5
        fi

        # If we're here, TradingClaw stopped or crashed
        restart_count=$((restart_count + 1))
        echo "$restart_count" > "$RESTART_COUNT_FILE"

        # Check for critical errors
        if [ -f "$AUTONOMOUS_LOG" ]; then
            if grep -qi "critical\|exception\|traceback\|fatal error" "$AUTONOMOUS_LOG"; then
                log_message "ERROR" "CRITICAL ERROR detected. Will not restart."
                sleep 300  # Wait 5 minutes
                continue
            fi
        fi

        # Max restart limit check
        if [ $restart_count -ge $MAX_RESTART ]; then
            log_message "ERROR" "MAX RESTART LIMIT ($MAX_RESTART) reached. Waiting 10 minutes..."
            sleep 600  # Wait 10 minutes
            restart_count=0
            echo "0" > "$RESTART_COUNT_FILE"
        else
            # Restart
            log_message "WARN" "TradingClaw exited. Restarting (attempt #$restart_count)..."
            sleep $sleep_time
            start_claw
        fi
    done
}

# Show status
show_status() {
    if is_running; then
        local pid=$(cat "$PID_FILE" 2>/dev/null)
        echo -e "${GREEN}TradingClaw is RUNNING${NC}"
        echo "PID: $pid"
        echo "Screen Session: $SESSION_NAME"

        if screen -list | grep -q "$SESSION_NAME"; then
            echo "Screen Status: Active"
        else
            echo "Screen Status: Process only (screen may have detached)"
        fi

        # Show recent logs
        if [ -f "$AUTONOMOUS_LOG" ]; then
            echo ""
            echo "=== Recent 10 lines of autonomous.log ==="
            tail -10 "$AUTONOMOUS_LOG"
        fi
    else
        echo -e "${RED}TradingClaw is NOT running${NC}"

        if [ -f "$PID_FILE" ]; then
            local last_pid=$(cat "$PID_FILE")
            echo "Last known PID: $last_pid"
        fi

        if [ -f "$WATCHDOG_LOG" ]; then
            echo ""
            echo "=== Recent 10 lines of watchdog.log ==="
            tail -10 "$WATCHDOG_LOG"
        fi
    fi
}

# Attach to screen session
attach_screen() {
    if ! is_running; then
        echo -e "${RED}TradingClaw is NOT running. Use '$0 start' to start it first.${NC}"
        return 1
    fi

    if screen -list | grep -q "$SESSION_NAME"; then
        echo "Attaching to screen session '$SESSION_NAME'..."
        echo "Press Ctrl+A, D to detach (without stopping)"
        screen -r "$SESSION_NAME"
    else
        echo -e "${YELLOW}Warning: Screen session not found. Starting new session...${NC}"
        start_claw
        sleep 2
        screen -r "$SESSION_NAME"
    fi
}

# Show logs
show_logs() {
    if [ -f "$AUTONOMOUS_LOG" ]; then
        echo "=== Autonomous Log (last 30 lines) ==="
        tail -30 "$AUTONOMOUS_LOG"
    else
        echo "No autonomous log found."
    fi

    echo ""

    if [ -f "$WATCHDOG_LOG" ]; then
        echo "=== Watchdog Log (last 30 lines) ==="
        tail -30 "$WATCHDOG_LOG"
    else
        echo "No watchdog log found."
    fi
}

# Main command handler
case "${1:-start}" in
    start)
        start_claw
        ;;
    stop)
        stop_claw
        ;;
    status)
        show_status
        ;;
    attach)
        attach_screen
        ;;
    logs)
        show_logs
        ;;
    watchdog)
        run_watchdog
        ;;
    restart)
        stop_claw
        sleep 3
        start_claw
        ;;
    *)
        echo "Usage: $0 {start|stop|status|attach|logs|watchdog|restart}"
        echo ""
        echo "Commands:"
        echo "  start     - Start TradingClaw daemon in screen"
        echo "  stop      - Stop TradingClaw daemon"
        echo "  status    - Check if TradingClaw is running"
        echo "  attach    - Attach to TradingClaw screen session"
        echo "  logs      - Show recent logs"
        echo "  watchdog  - Run watchdog (auto-restart on crash)"
        echo "  restart   - Stop and restart TradingClaw"
        exit 1
        ;;
esac
