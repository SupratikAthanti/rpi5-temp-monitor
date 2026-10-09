#!/usr/bin/env bash
#
# Reset ThermalScope - Clear all collected data
#

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

echo "ThermalScope Reset"
echo "=================="
echo ""

# Delete main log file
if [[ -f "temp_log.csv" ]]; then
    echo "Removing temp_log.csv..."
    rm temp_log.csv
else
    echo "temp_log.csv not found (already clean)"
fi

# Delete spike log file
if [[ -f "spikes.csv" ]]; then
    echo "Removing spikes.csv..."
    rm spikes.csv
else
    echo "spikes.csv not found (already clean)"
fi

# Delete state file (user-specific)
UID_VAL="${UID:-0}"
STATE_FILE="/tmp/rpi5_temp_state_${UID_VAL}"
if [[ -f "$STATE_FILE" ]]; then
    echo "Removing state file ($STATE_FILE)..."
    rm "$STATE_FILE"
else
    echo "State file not found (already clean)"
fi

# Delete PID file
PID_FILE="/tmp/thermal-scope-collector.pid"
if [[ -f "$PID_FILE" ]]; then
    echo "Removing PID file ($PID_FILE)..."
    rm "$PID_FILE"
else
    echo "PID file not found (already clean)"
fi

# Delete test lock file
TEST_LOCK_FILE="/tmp/thermal-scope-test.lock"
if [[ -f "$TEST_LOCK_FILE" ]]; then
    echo "Removing test lock file ($TEST_LOCK_FILE)..."
    rm "$TEST_LOCK_FILE"
else
    echo "Test lock file not found (already clean)"
fi

echo ""
echo "Reset complete. ThermalScope will start fresh on next run."
