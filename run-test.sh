#!/usr/bin/env bash
#
# ThermalScope Test Runner with Progress Indicator
# Usage: ./run-test.sh [duration_in_minutes] [log_file]
#

set -uo pipefail

# Handle --progress flag first
if [[ "${1:-}" == "--progress" ]]; then
    LOCK_FILE="/tmp/thermal-scope-test.lock"
    DURATION_MINUTES="${2:-60}"

    if [[ ! -f "$LOCK_FILE" ]]; then
        echo "No test is currently running."
        echo "To start a test, run: ./run-test.sh [duration_in_minutes]"
        exit 0
    fi

    # Read lock file: PID DURATION START_TIME
    LOCK_DATA=$(cat "$LOCK_FILE" 2>/dev/null || echo "")
    LOCK_PID=$(echo "$LOCK_DATA" | awk '{print $1}')
    LOCK_DURATION=$(echo "$LOCK_DATA" | awk '{print $2}')
    LOCK_START=$(echo "$LOCK_DATA" | awk '{print $3}')

    if [[ -z "$LOCK_DURATION" ]]; then
        LOCK_DURATION=$DURATION_MINUTES
    fi

    LOCK_TOTAL_SECONDS=$((LOCK_DURATION * 60))
    LOCK_ELAPSED=$(( $(date +%s) - LOCK_START ))
    LOCK_REMAINING=$((LOCK_TOTAL_SECONDS - LOCK_ELAPSED ))

    echo "================================================================="
    echo " ThermalScope Test Progress"
    echo "================================================================="
    echo ""
    echo "Test PID: $LOCK_PID"
    echo "Total duration: ${LOCK_DURATION} minutes"
    echo ""

    if [[ $LOCK_ELAPSED -ge $LOCK_TOTAL_SECONDS ]]; then
        echo "Status: Test completed or nearly complete"
    else
        LOCK_MINUTES=$((LOCK_REMAINING / 60))
        LOCK_PROGRESS=$(( (LOCK_ELAPSED * 100) / LOCK_TOTAL_SECONDS ))
        echo "Time elapsed: $((LOCK_ELAPSED / 60))m"
        echo "Time remaining: ${LOCK_MINUTES}m"
        echo "Progress: ${LOCK_PROGRESS}%"
        echo ""
        # Progress bar
        BAR_WIDTH=50
        FILLED=$(( (LOCK_PROGRESS * BAR_WIDTH) / 100 ))
        EMPTY=$((BAR_WIDTH - FILLED))
        printf " ["
        printf "%${FILLED}s" | tr ' ' '='
        printf "%${EMPTY}s" | tr ' ' '-'
        printf "] ${LOCK_PROGRESS}%%\n"
    fi
    echo ""
    echo "To view partial results now, run: python3 analyze.py temp_log.csv"
    exit 0
fi

# Default values
DURATION_MINUTES="${1:-60}"
LOG_FILE="${2:-temp_log.csv}"
LOCK_FILE="/tmp/thermal-scope-test.lock"

# Validate duration
if ! [[ "$DURATION_MINUTES" =~ ^[0-9]+$ ]] || [[ "$DURATION_MINUTES" -lt 1 ]]; then
    echo "Error: Duration must be a positive integer (minutes)"
    echo "Usage: ./run-test.sh [duration_in_minutes] [log_file]"
    echo "Example: ./run-test.sh 60"
    echo "Example: ./run-test.sh 1440 temp_log.csv  # 24 hours"
    exit 1
fi

# Calculate total seconds
TOTAL_SECONDS=$((DURATION_MINUTES * 60))
START_TIME=$(date +%s)

# Check if a test is already running
if [[ -f "$LOCK_FILE" ]]; then
    LOCK_DATA=$(cat "$LOCK_FILE" 2>/dev/null || echo "")
    LOCK_PID=$(echo "$LOCK_DATA" | awk '{print $1}')
    LOCK_DURATION=$(echo "$LOCK_DATA" | awk '{print $2}')
    LOCK_START=$(echo "$LOCK_DATA" | awk '{print $3}')

    if [[ -n "$LOCK_PID" ]] && kill -0 "$LOCK_PID" 2>/dev/null; then
        echo "================================================================="
        echo " ThermalScope Test Already Running"
        echo "================================================================="
        echo ""
        echo "A test is currently running (PID: $LOCK_PID)"
        echo ""

        if [[ -n "$LOCK_DURATION" ]]; then
            LOCK_TOTAL_SECONDS=$((LOCK_DURATION * 60))
            LOCK_ELAPSED=$(( $(date +%s) - LOCK_START ))
            LOCK_REMAINING=$((LOCK_TOTAL_SECONDS - LOCK_ELAPSED))

            if [[ $LOCK_REMAINING -gt 0 ]]; then
                LOCK_MINUTES=$((LOCK_REMAINING / 60))
                echo "Time remaining: ${LOCK_MINUTES}m"
            else
                echo "Test should be completing soon..."
            fi
        fi
        echo ""
        echo "To view current progress, run: ./run-test.sh --progress"
        echo "To stop the running test, run: kill $LOCK_PID"
        echo "Then remove the lock file: rm $LOCK_FILE"
        echo ""
        echo "To view partial results now, run: python3 analyze.py $LOG_FILE"
        exit 1
    else
        # Stale lock file - process no longer running
        echo "Found stale lock file from previous test. Cleaning up..."
        rm -f "$LOCK_FILE"
    fi
fi

# Handle --progress flag
if [[ "${1:-}" == "--progress" ]]; then
    if [[ ! -f "$LOCK_FILE" ]]; then
        echo "No test is currently running."
        echo "To start a test, run: ./run-test.sh [duration_in_minutes]"
        exit 0
    fi

    # Read lock file: PID DURATION START_TIME
    LOCK_DATA=$(cat "$LOCK_FILE" 2>/dev/null || echo "")
    LOCK_PID=$(echo "$LOCK_DATA" | awk '{print $1}')
    LOCK_DURATION=$(echo "$LOCK_DATA" | awk '{print $2}')
    LOCK_START=$(echo "$LOCK_DATA" | awk '{print $3}')

    if [[ -z "$LOCK_DURATION" ]]; then
        LOCK_DURATION=$DURATION_MINUTES
    fi

    LOCK_TOTAL_SECONDS=$((LOCK_DURATION * 60))
    LOCK_ELAPSED=$(( $(date +%s) - LOCK_START ))
    LOCK_REMAINING=$((LOCK_TOTAL_SECONDS - LOCK_ELAPSED ))

    echo "================================================================="
    echo " ThermalScope Test Progress"
    echo "================================================================="
    echo ""
    echo "Test PID: $LOCK_PID"
    echo "Total duration: ${LOCK_DURATION} minutes"
    echo ""

    if [[ $LOCK_ELAPSED -ge $LOCK_TOTAL_SECONDS ]]; then
        echo "Status: Test completed or nearly complete"
    else
        LOCK_MINUTES=$((LOCK_REMAINING / 60))
        LOCK_PROGRESS=$(( (LOCK_ELAPSED * 100) / LOCK_TOTAL_SECONDS ))
        echo "Time elapsed: $((LOCK_ELAPSED / 60))m"
        echo "Time remaining: ${LOCK_MINUTES}m"
        echo "Progress: ${LOCK_PROGRESS}%"
        echo ""
        # Progress bar
        BAR_WIDTH=50
        FILLED=$(( (LOCK_PROGRESS * BAR_WIDTH) / 100 ))
        EMPTY=$((BAR_WIDTH - FILLED))
        printf " ["
        printf "%${FILLED}s" | tr ' ' '='
        printf "%${EMPTY}s" | tr ' ' '-'
        printf "] ${LOCK_PROGRESS}%%\n"
    fi
    echo ""
    echo "To view partial results now, run: python3 analyze.py $LOG_FILE"
    exit 0
fi

# Create lock file with PID and duration
echo "$$ $DURATION_MINUTES $START_TIME" > "$LOCK_FILE"
trap 'rm -f "$LOCK_FILE"' EXIT

echo "================================================================="
echo " ThermalScope Test Starting"
echo "================================================================="
echo ""
echo "Duration: ${DURATION_MINUTES} minutes"
echo "Log file: $LOG_FILE"
echo "Start time: $(date)"
echo ""
echo "The test will run with progress updates."
echo "You can:"
echo "  - Check progress in another terminal: ./run-test.sh --progress"
echo "  - View partial results: python3 analyze.py $LOG_FILE"
echo "  - Stop the test: kill $$"
echo ""
echo "Starting collection..."
echo ""

# Run the collection loop with progress updates
for ((i=1; i<=DURATION_MINUTES; i++)); do
    CURRENT_TIME=$(date +%s)
    ELAPSED=$((CURRENT_TIME - START_TIME))
    REMAINING=$((TOTAL_SECONDS - ELAPSED))
    PROGRESS=$(( (ELAPSED * 100) / TOTAL_SECONDS ))

    # Progress bar
    BAR_WIDTH=40
    FILLED=$(( (PROGRESS * BAR_WIDTH) / 100 ))
    EMPTY=$((BAR_WIDTH - FILLED))

    # Clear line and show progress on single line
    printf "\r[%3d%%] [%s%s] Elapsed: %dm | Remaining: %dm | Samples: %d" \
        "$PROGRESS" \
        "$(printf '%0.s=' $(seq 1 $FILLED))" \
        "$(printf '%0.s-' $(seq 1 $EMPTY))" \
        "$((ELAPSED / 60))" \
        "$((REMAINING / 60))" \
        "$i"

    # Run collection
    ./collect.sh "$LOG_FILE" > /dev/null 2>&1

    # Sleep for 60 seconds (only if not last iteration)
    if [[ $i -lt $DURATION_MINUTES ]]; then
        sleep 60
    fi
done

# Clear the progress line
printf "\r%-100s\r" " "
printf "\n"

echo ""
echo "================================================================="
echo " Test Complete"
echo "================================================================="
echo ""
echo "Duration: ${DURATION_MINUTES} minutes"
echo "End time: $(date)"
echo "Total samples: ${DURATION_MINUTES}"
echo ""
echo "Generating analysis report..."
echo ""

# Run analysis
python3 analyze.py "$LOG_FILE"
