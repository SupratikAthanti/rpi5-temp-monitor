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
