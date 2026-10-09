#!/usr/bin/env bash
#
# RPi 5 Lightweight Temperature & Metric Collector
# Designed for week-long logging with minimal resource usage.
#

set -uo pipefail

# Default log file path
LOG_FILE="${1:-/home/berry5/projects/temp/temp_log.csv}"
DRY_RUN=false

if [[ "${1:-}" == "--dry-run" ]] || [[ "${2:-}" == "--dry-run" ]]; then
    DRY_RUN=true
fi

# 1. Timestamp (ISO-8601 UTC)
TIMESTAMP="$(date -u +"%Y-%m-%dT%H:%M:%SZ")"

# 2. SoC Temperature (°C)
SOC_TEMP=""
if command -v vcgencmd &>/dev/null; then
    # e.g., "temp=54.2'C" -> extract 54.2
    SOC_TEMP="$(vcgencmd measure_temp 2>/dev/null | grep -oP '[0-9]+\.[0-9]+' || true)"
    if [[ -z "$SOC_TEMP" ]]; then
        SOC_TEMP="$(vcgencmd measure_temp 2>/dev/null | tr -cd '0-9.' || true)"
    fi
fi
if [[ -z "$SOC_TEMP" ]] && [[ -r /sys/class/thermal/thermal_zone0/temp ]]; then
    RAW_SYSFS="$(cat /sys/class/thermal/thermal_zone0/temp)"
    SOC_TEMP="$(awk "BEGIN {print $RAW_SYSFS / 1000}")"
fi
[[ -z "$SOC_TEMP" ]] && SOC_TEMP="0.0"

# Test hook: allow deterministic temp injection without hardware
if [[ -n "${FAKE_SOC_TEMP:-}" ]]; then
    SOC_TEMP="$FAKE_SOC_TEMP"
fi

# 3. PMIC Temperature (°C)
PMIC_TEMP="N/A"
if command -v vcgencmd &>/dev/null; then
    PMIC_TEMP="$(vcgencmd measure_temp pmic 2>/dev/null | grep -oP '[0-9]+\.[0-9]+' || true)"
    if [[ -z "$PMIC_TEMP" ]]; then
        PMIC_TEMP="$(vcgencmd measure_temp pmic 2>/dev/null | tr -cd '0-9.' || true)"
    fi
fi
[[ -z "$PMIC_TEMP" ]] && PMIC_TEMP="N/A"

# 4. Load average (1 min)
LOAD_1M="0.0"
if [[ -r /proc/loadavg ]]; then
    LOAD_1M="$(cut -d' ' -f1 /proc/loadavg)"
fi

# 5. ARM Frequency (MHz)
ARM_FREQ="0"
if command -v vcgencmd &>/dev/null; then
    # e.g. "frequency(48)=2400000000" -> 2400
    FREQ_HZ="$(vcgencmd measure_clock arm 2>/dev/null | grep -oP '[0-9]+$' || true)"
    if [[ -n "$FREQ_HZ" ]]; then
        ARM_FREQ="$(( FREQ_HZ / 1000000 ))"
    fi
fi
if [[ "$ARM_FREQ" -eq 0 ]] && [[ -r /sys/devices/system/cpu/cpu0/cpufreq/scaling_cur_freq ]]; then
    FREQ_KHZ="$(cat /sys/devices/system/cpu/cpu0/cpufreq/scaling_cur_freq)"
    ARM_FREQ="$(( FREQ_KHZ / 1000 ))"
fi

# 6. Throttled state
THROTTLED="0x0"
if command -v vcgencmd &>/dev/null; then
    THROTTLED_OUT="$(vcgencmd get_throttled 2>/dev/null || true)"
    if [[ "$THROTTLED_OUT" =~ (0x[0-9a-fA-F]+) ]]; then
        THROTTLED="${BASH_REMATCH[1]}"
    fi
fi

# 7. Top process by CPU (name and cpu%)
TOP_PROC="none"
TOP_CPU="0.0"
PS_LINE="$(ps -eo comm,%cpu --sort=-%cpu 2>/dev/null | sed -n '2p' || true)"
if [[ -n "$PS_LINE" ]]; then
    # ps output: "process_name   12.3"
    TOP_PROC="$(echo "$PS_LINE" | awk '{print $1}')"
    TOP_CPU="$(echo "$PS_LINE" | awk '{print $2}')"
fi

# Assemble CSV line
CSV_LINE="${TIMESTAMP},${SOC_TEMP},${PMIC_TEMP},${LOAD_1M},${ARM_FREQ},${THROTTLED},${TOP_PROC},${TOP_CPU}"

if [[ "$DRY_RUN" == "true" ]]; then
    echo "$CSV_LINE"
    exit 0
fi

# Ensure log directory exists
LOG_DIR="$(dirname "$LOG_FILE")"
mkdir -p "$LOG_DIR"

# Write header if file doesn't exist or is empty
if [[ ! -s "$LOG_FILE" ]]; then
    echo "timestamp,soc_temp_c,pmic_temp_c,load_1m,arm_freq_mhz,throttled_hex,top_proc,top_cpu_pct" > "$LOG_FILE"
fi

# Append entry atomically
echo "$CSV_LINE" >> "$LOG_FILE"

# --- Spike-triggered detailed process snapshot (5C jump over daily baseline) ---
# Configurable via env (sane defaults for bare RPi5 VPS use):
#   SPIKE_STATE_FILE  default /tmp/rpi5_temp_state (RAM, zero disk wear)
#   SPIKE_LOG         default <logdir>/spikes.csv
#   SPIKE_DELTA       default 5.0 (°C jump over baseline to trigger)
#   SPIKE_MIN_TEMP    default 60.0 (°C absolute floor to avoid cold-room noise)
#   SPIKE_COOLDOWN_SEC default 600 (max 1 snapshot per 10 min)
#   SPIKE_EMA_ALPHA   default 0.15 (baseline adapts through the day)
# Test hooks: FAKE_SOC_TEMP (above), FAKE_TOP_DETAIL ("comm|cpu|mem|args")
SPIKE_STATE_FILE="${SPIKE_STATE_FILE:-/tmp/rpi5_temp_state}"
SPIKE_LOG="${SPIKE_LOG:-$(dirname "$LOG_FILE")/spikes.csv}"
SPIKE_DELTA="${SPIKE_DELTA:-5.0}"
SPIKE_MIN_TEMP="${SPIKE_MIN_TEMP:-60.0}"
SPIKE_COOLDOWN_SEC="${SPIKE_COOLDOWN_SEC:-600}"
SPIKE_EMA_ALPHA="${SPIKE_EMA_ALPHA:-0.15}"

NOW_EPOCH="$(date +%s)"
BASELINE=""
LAST_SPIKE="0"
if [[ -r "$SPIKE_STATE_FILE" ]]; then
    # shellcheck disable=SC2162
    read BASELINE LAST_SPIKE < "$SPIKE_STATE_FILE" || true
fi
if [[ -z "$BASELINE" ]]; then
    BASELINE="$SOC_TEMP"
fi
if [[ -z "$LAST_SPIKE" ]]; then
    LAST_SPIKE="0"
fi

IS_HOT="$(awk -v c="$SOC_TEMP" -v m="$SPIKE_MIN_TEMP" 'BEGIN{print (c+0 >= m+0)}')"
DELTA="$(awk -v c="$SOC_TEMP" -v b="$BASELINE" 'BEGIN{printf "%.1f", (c+0)-(b+0)}')"
IS_JUMP="$(awk -v d="$DELTA" -v t="$SPIKE_DELTA" 'BEGIN{print (d+0 >= t+0)}')"
COOLDOWN_OK="$([ $(( NOW_EPOCH - LAST_SPIKE )) -ge "$SPIKE_COOLDOWN_SEC" ] && echo 1 || echo 0)"

if [[ "$IS_HOT" == "1" && "$IS_JUMP" == "1" && "$COOLDOWN_OK" == "1" ]]; then
    # Build compact top-process detail (commas stripped to keep CSV valid)
    if [[ -n "${FAKE_TOP_DETAIL:-}" ]]; then
        TOP_DETAIL="$(echo "$FAKE_TOP_DETAIL" | tr ',' ';' | cut -c1-400)"
    else
        TOP_DETAIL="$(ps -eo comm,%cpu,%mem,args --sort=-%cpu 2>/dev/null | sed -n '2,6p' | awk '{c=$1; cpu=$2; mem=$3; $1=$2=$3=""; sub(/^ +/,""); args=substr($0,1,80); gsub(/,/,";"); printf "%s(%s%%|mem:%s%%|%s);", c, cpu, mem, args}' | tr ',' ';' | cut -c1-500 || true)"
        [[ -z "$TOP_DETAIL" ]] && TOP_DETAIL="${TOP_PROC}(${TOP_CPU}%)"
    fi
    # Culprit guess: first known app keyword in detail (lowercased), else top comm
    LOWER_DETAIL="$(echo "$TOP_DETAIL" | tr '[:upper:]' '[:lower:]')"
    CULPRIT="$TOP_PROC"
    for _app in ollama hermes opencode tailscaled docker apt; do
        if [[ "$LOWER_DETAIL" == *"$_app"* ]]; then CULPRIT="$_app"; break; fi
    done
    SPIKE_DIR="$(dirname "$SPIKE_LOG")"
    mkdir -p "$SPIKE_DIR"
    if [[ ! -s "$SPIKE_LOG" ]]; then
        echo "timestamp,soc_temp_c,baseline_temp_c,delta_c,load_1m,culprit_app,top_processes_detail" > "$SPIKE_LOG"
    fi
    echo "${TIMESTAMP},${SOC_TEMP},${BASELINE},${DELTA},${LOAD_1M},${CULPRIT},${TOP_DETAIL}" >> "$SPIKE_LOG"
    # Freeze baseline during spike (don't let spike drag baseline up), record trigger time
    echo "$BASELINE $NOW_EPOCH" > "$SPIKE_STATE_FILE"
else
    # Normal sample: drift EMA baseline toward current temp (adapts through the day)
    BASELINE="$(awk -v c="$SOC_TEMP" -v b="$BASELINE" -v a="$SPIKE_EMA_ALPHA" 'BEGIN{printf "%.2f", a*(c+0)+(1-a)*(b+0)}')"
    STATE_DIR="$(dirname "$SPIKE_STATE_FILE")"
    mkdir -p "$STATE_DIR" 2>/dev/null || true
    echo "$BASELINE $LAST_SPIKE" > "$SPIKE_STATE_FILE" 2>/dev/null || true
fi
