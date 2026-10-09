# ThermalScope - Temperature & Cooler Decision Engine

A lightweight temperature logging and performance analysis system for single-board computers (Raspberry Pi, Arduino, etc.) running without active cooling.

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
   - Identifies real culprits rather than ambiguous generic process names.

3. **Intelligent Decision Engine (`analyze.py`)**
   - Computes statistical summaries (Min, Average, Median, Standard Deviation, 95th Percentile, Max).
   - Breaks down temperature distribution across thermal zones (`<60°C`, `60–70°C`, `70–80°C`, `80–85°C` soft throttle zone, `>85°C` hard throttle zone).
   - Frequency-weighted verdict logic: Distinguishes between rare spikes and frequent recurring heat to recommend **No Cooler**, **Passive Heatsink**, or **Active Cooler**.

4. **Performance Impact Analysis**
   - Tracks CPU frequency distribution (2400, 1500, 1000, 750, 600 MHz tiers).
   - Calculates performance loss percentage and effective speed factor.
   - Estimates time impact: "For a 1-hour task, thermal throttling added ~X minutes"

---

## Repository Files

- `collect.sh` — The lightweight cron collector and spike detector.
- `analyze.py` — The statistics calculator, spike parser, and cooler recommendation engine.
- `run-test.sh` — Test runner with progress indicator for timed tests.
- `reset.sh` — Clears all collected data and state files.
- `stop.sh` — Removes ThermalScope from crontab.
- `tests/` — Comprehensive unit and simulation test suite.

---

## Quick Start

### 1. Make the Script Executable
```bash
chmod +x collect.sh
```

### 2. Quick Test (2 minutes)
Run the collector manually a few times to verify it works:
```bash
./collect.sh --dry-run
./collect.sh temp_log.csv
./collect.sh temp_log.csv
python3 analyze.py temp_log.csv
```

### 3. Run for 1 Hour (Recommended Test)
Use the test runner with progress indicator:
```bash
./run-test.sh 60
```

This will:
- Show real-time progress with time remaining
- Display a progress bar
- Automatically run the analysis when complete
- Show the full report at the end

**To check progress from another terminal:**
```bash
./run-test.sh --progress
```

**To run in background with tmux:**
```bash
tmux new -s thermal-test
./run-test.sh 60
# Press Ctrl+B then D to detach
```

**Custom durations:**
```bash
./run-test.sh 30    # 30 minutes
./run-test.sh 480   # 8 hours
./run-test.sh 1440  # 24 hours
```

### 4. Run for 1 Week (Full Monitoring)
Set up cron to run automatically every minute:
```bash
crontab -e
```
Add these lines (replace paths with your actual path):
```cron
@reboot /home/berry5/projects/temp/collect.sh /home/berry5/projects/temp/temp_log.csv
* * * * * /home/berry5/projects/temp/collect.sh /home/berry5/projects/temp/temp_log.csv
```

**Note**: File locking prevents concurrent runs if a script takes longer than 1 minute.

---

## Usage

### View Results Anytime
You don't need to wait for the full test period. Inspect your accumulated stats live:
```bash
python3 analyze.py temp_log.csv
```

**Note**: This automatically looks for `spikes.csv` in the same directory as `temp_log.csv` for spike attribution.

### Stop the Test
If running with `run-test.sh`:
```bash
# Find the PID and kill it
./run-test.sh --progress  # Shows PID
kill <PID>

# Or just press Ctrl+C if running in foreground
```

If running in tmux:
```bash
tmux attach -s thermal-test
# Press Ctrl+C to stop
```

### Stop Cron (Stop Week-Long Monitoring)
```bash
./stop.sh
```
This will prompt you to remove ThermalScope entries from crontab.

### Where Results Are Stored
- `temp_log.csv` — Main temperature log (one row per minute)
- `spikes.csv` — Spike detection log (only created when 5°C+ jumps occur)
- `/tmp/rpi5_temp_state_${UID}` — Temporary EMA baseline state (in RAM, cleared on reboot)
- `/tmp/thermal-scope-collector.pid` — PID file to prevent concurrent runs

### Reset / Delete Results and Start Fresh
```bash
./reset.sh
```
This removes all data files and state files, giving you a clean slate.

**Manual reset (if needed):**
```bash
rm temp_log.csv spikes.csv
rm /tmp/rpi5_temp_state_${UID}
rm /tmp/thermal-scope-collector.pid
```

### Run Test Suite
```bash
python3 -m unittest discover tests
```

---

## Understanding the Report

The report provides three main recommendations:

- **No Cooler Needed** — Your bare-board setup is sufficient
- **Passive Heatsink Sufficient** — A heatsink will provide relief without fan noise
- **Active Cooler Strongly Recommended** — You need active cooling to prevent thermal throttling

Key metrics to watch:
- **Temperature Distribution** — What % of time you spend in each thermal zone
- **Frequency Distribution** — What % of time at each CPU speed (throttled vs full speed)
- **Performance Loss** — How much slower your system is due to thermal throttling
- **Spike Attribution** — Which processes are causing temperature spikes

**Important**: The report uses **p95 (95th percentile)** not average. This means idle time doesn't skew the verdict - if you're idle 23 hours/day but hit 85°C for 1 hour, the p95 will still show ~84°C.

---

## Common Errors & Solutions

### Error: "Log file not found"
**Cause**: You tried to run `analyze.py` before collecting any data.

**Solution**: Run `./run-test.sh 60` for a 1-hour test, or manually collect data:
```bash
./collect.sh temp_log.csv
./collect.sh temp_log.csv
python3 analyze.py temp_log.csv
```

### Error: "No valid data in log file"
**Cause**: The file exists but contains only the header or malformed data.

**Solution**: Reset and start fresh:
```bash
./reset.sh
./run-test.sh 60
```

### Error: "Another instance is already running"
**Cause**: You tried to start a test while another test is already running.

**Solution**: Check progress or stop the running test:
```bash
./run-test.sh --progress  # Shows PID and status
kill <PID>               # Stop the running test
```

### Error: "Test Already Running" (run-test.sh)
**Cause**: A test is already in progress.

**Solution**: Check progress or stop it:
```bash
./run-test.sh --progress
kill <PID>
rm /tmp/thermal-scope-test.lock  # If process already stopped
```

---

## Edge Cases & Important Notes

### Reboot Behavior
- The state file (`/tmp/rpi5_temp_state_${UID}`) is stored in RAM and **cleared on reboot**
- After reboot, the EMA baseline resets to the current temperature
- This is expected behavior and not a problem

### Concurrent Runs
- PID-based locking prevents multiple instances from running simultaneously
- If you try to run the script while another instance is running, it will exit with an error: "Another instance is already running (PID: XXXXX). Exiting."
- This prevents data corruption from overlapping writes
- The PID file is automatically cleaned up when the script exits

### Stale Lock Files
- If a test crashes or is killed abnormally, the lock file may remain
- `run-test.sh` automatically detects and cleans up stale lock files
- `reset.sh` also removes all lock files

### Partial Data Handling
- If the system crashes during logging, the CSV may have incomplete lines
- The analyze script skips malformed rows automatically
- The reset script can clean up any corrupted data

### Idle Time Doesn't Skew Results
- Even if you're idle for 23 hours/day, the p95 and distribution metrics accurately show thermal stress
- The frequency-weighted verdict distinguishes between "bursty/experimental" and "frequent/recurring" heat

---

## Example Report Output

```text
=================================================================
 THERMALSCOPE - TEMPERATURE & COOLER DECISION REPORT
=================================================================
 Log File          : temp_log.csv
 Total Samples     : 63
 Time Span         : 2026-10-09T20:08:12Z to 2026-10-09T21:09:20Z
-----------------------------------------------------------------
 SoC TEMPERATURE STATS (°C):
   Min             : 65.3°C
   Average         : 79.1°C (± 6.7°C std dev)
   Median          : 82.3°C
   95th Percentile : 86.2°C
   Max             : 86.7°C
 PMIC TEMP (Avg)   : 69.8°C (Max: 74.4°C)
 CPU LOAD (1m Avg) : 0.97 (Max: 2.92)
 ARM FREQUENCY (Avg): 1801 MHz (Max: 2400 MHz)
-----------------------------------------------------------------
 TEMPERATURE DISTRIBUTION:
   Under 60°C (Cool)                   :     0 ( 0.00%)
   60°C - 70°C (Normal)                :    10 (15.87%) #######
   70°C - 80°C (Warm)                  :    19 (30.16%) ###############
   80°C - 85°C (Soft Throttle Zone)    :    19 (30.16%) ###############
   Over 85°C (Hard Throttle / Critical) :    15 (23.81%) ###########
-----------------------------------------------------------------
 ARM FREQUENCY DISTRIBUTION:
   2400 MHz (Max)                 :     9 (14.29%) #######
   1500 MHz (Throttled)           :    54 (85.71%) ##########################################
   1000 MHz (Throttled)           :     0 ( 0.00%)
   750 MHz (Throttled)            :     0 ( 0.00%)
   600 MHz (Min)                  :     0 ( 0.00%)
   Other/Unknown                  :     0 ( 0.00%)
-----------------------------------------------------------------
 PERFORMANCE IMPACT ANALYSIS:
   Average Frequency    : 1801 MHz / 2400 MHz max
   Performance Loss    : 24.9% slower than max capability
   Effective Speed     : 75.1% of maximum
   Time Impact         : For a 1-hour task, thermal throttling added ~20 minutes
-----------------------------------------------------------------
 TOP PROCESSES (Most frequently observed top CPU consumer):
   opencode             :    62 samples ( 98.4%)
   ps                   :     1 samples (  1.6%)
-----------------------------------------------------------------
 SPIKE ATTRIBUTION (5°C+ jumps, 2 events):
   opencode             :     2 spikes (peak 85.1°C, +12.7°C)
-----------------------------------------------------------------
 FINAL VERDICT & RECOMMENDATION:
   => Active Cooler Strongly Recommended (Prevent thermal throttling and performance degradation)
   Reasons:
      - Maximum temperature reached 86.7°C (>= 85°C thermal throttle threshold).
      - Recorded active throttling events (14 samples).
      - Spent time above 85°C.
      - Spikes are frequent: 2 spikes in 63 samples (3.17% of time). Culprits: opencode (2x, peak 85.1°C). Heat recurs during normal use — cooling is warranted.
=================================================================
```
