# Raspberry Pi 5 Temperature & Cooler Decision Engine

An ultra-lightweight, production-grade temperature logging and spike-attribution system for the **Raspberry Pi 5 (4GB)** running VPS agents, background tasks, and experimental local LLMs. 

Designed to answer the fundamental question: **Do I actually need a passive heatsink or an active cooler, or is my bare-board setup fine?**

---

## Features

1. **Passive 1-Minute Logging (`collect.sh`)**
   - Records SoC temperature, PMIC temperature, 1-minute load average, ARM clock frequency, and firmware throttling bitmask (`vcgencmd get_throttled`).
   - Gracefully falls back to `/sys/class/thermal/thermal_zone0/temp` if `vcgencmd` is unavailable.
   - Storage footprint: **~750 KB per week** (10,080 samples in a single flat CSV).

2. **Spike-Triggered Process Attribution (`spikes.csv`)**
   - Tracks a rolling Exponential Moving Average (EMA) baseline of your daily temperature.
   - Automatically captures a detailed snapshot (top processes with CPU/mem % and command-line arguments) whenever a **5°C+ jump** occurs above baseline (rate-limited with a 10-minute cooldown).
   - Identifies real culprits (`ollama`, `hermes`, `opencode`, `tailscaled`, `docker`, etc.) rather than ambiguous generic process names like `python3`.

3. **Intelligent Decision Engine (`analyze.py`)**
   - Computes statistical summaries (Min, Average, Median, Standard Deviation, 95th Percentile, Max).
   - Breaks down temperature distribution across thermal zones (`<60°C`, `60–70°C`, `70–80°C`, `80–85°C` soft throttle zone, `>85°C` hard throttle zone).
   - Frequency-weighted verdict logic: Distinguishes between rare experimental LLM bursts and heavy everyday agent workloads to recommend **No Cooler**, **Passive Heatsink**, or **Active Cooler**.

---

## Repository Files

- `collect.sh` — The lightweight cron collector and spike detector.
- `analyze.py` — The statistics calculator, spike parser, and cooler recommendation engine.
- `tests/` — Comprehensive unit and 7-day simulation test suite (`test_analyzer.py`, `test_collector.py`, `test_spike_detection.py`, `test_simulation.py`).

---

## Installation & Setup

### 1. Clone & Set Permissions
```bash
git clone https://github.com/SupratikAthanti/rpi5-temp-monitor.git
cd rpi5-temp-monitor
chmod +x collect.sh
```

### 2. Configure Cron (Every 60 Seconds)
Open your crontab editor:
```bash
crontab -e
```
Add these two lines:
```cron
@reboot /home/berry5/projects/temp/collect.sh /home/berry5/projects/temp/temp_log.csv
* * * * * /home/berry5/projects/temp/collect.sh /home/berry5/projects/temp/temp_log.csv
```

---

## Usage

### Run Analysis Anytime
You don't need to wait 7 days. Inspect your accumulated stats and spike attribution live:
```bash
python3 analyze.py temp_log.csv
```

### Run Test Suite
```bash
python3 -m unittest discover tests
```

---

## Example Report Output

```text
=================================================================
 RPi 5 TEMPERATURE & COOLER DECISION REPORT
=================================================================
 Log File          : temp_log.csv
 Total Samples     : 10080
 Time Span         : 2026-10-01T00:00:00Z to 2026-10-07T23:59:00Z
-----------------------------------------------------------------
 SoC TEMPERATURE STATS (°C):
   Min             : 51.2°C
   Average         : 58.4°C (± 4.2°C std dev)
   Median          : 57.0°C
   95th Percentile : 68.5°C
   Max             : 82.1°C
 PMIC TEMP (Avg)   : 54.1°C (Max: 68.2°C)
 CPU LOAD (1m Avg) : 0.65 (Max: 3.85)
-----------------------------------------------------------------
 TEMPERATURE DISTRIBUTION:
   Under 60°C (Cool)                   :  7200 ( 71.43%) ######################################
   60°C - 70°C (Normal)                :  2400 ( 23.81%) ############
   70°C - 80°C (Warm)                  :   430 (  4.27%) ##
   80°C - 85°C (Soft Throttle Zone)    :    50 (  0.50%) 
   Over 85°C (Hard Throttle / Critical):     0 (  0.00%) 
-----------------------------------------------------------------
 SPIKE ATTRIBUTION (5°C+ jumps, 4 events):
   ollama               :     3 spikes (peak 82.1°C, +11.2°C)
   opencode             :     1 spikes (peak 72.5°C, +6.1°C)
-----------------------------------------------------------------
 FINAL VERDICT & RECOMMENDATION:
   => Passive Heatsink Sufficient (Great for silent, light-to-moderate thermal relief)
   Reasons:
      - 95th percentile temperature is 68.5°C (approaching/entering 80°C throttle range).
      - Spent 0.50% of time in the warm zone (>=80°C).
      - Passive heatsink will effectively shave 5-10°C off spikes without adding fan noise.
      - Spikes are rare: 4 spikes in 10080 samples (0.04% of time). Culprits: ollama (3x, peak 82.1°C). Daily use is fine; heat is bursty/experimental.
=================================================================
```
