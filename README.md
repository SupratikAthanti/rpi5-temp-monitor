# PiStats — Live CLI Dashboard, Power & Cooler Decision Engine

PiStats is a lightweight temperature **and** power monitoring system for
single-board computers (Raspberry Pi, Arduino, etc.). It answers two questions:

**Do I actually need a passive heatsink or an active cooler — and what is my
board costing me to run?**

---

## One command opens everything

```bash
python3 pistats.py
```

That's it: **one command, no flags, no numbered menus**. PiStats opens a
full-screen dashboard that renders every second. It is a single command that
opens everything — live temperature, power, cost, battery, throttling and the
full analysis — over SSH or on the console.

On startup the app immediately begins sampling **SoC temperature and wall
power once per second** and records every sample. The default Overview screen
shows, from top to bottom:

1. current temperature (°C) and a labelled, multi-line graph of the temperature
   history (with min/max labels and time span),
2. current power (W) and a matching multi-line graph of the power history,
3. session energy used (Wh) and its cost at the configured rate
   (default **$0.13/kWh** = 13.0 cents/kWh), plus elapsed time and sample count.

Both graphs autoscale to the terminal, cover the last five minutes once that
much data exists, and fall back to a compact one-line sparkline on very short
terminals. No extra packages are required: the dashboard uses only the Python
standard library (`curses`), so it works over SSH.

To print a saved report instead of opening the dashboard:

```bash
python3 pistats.py view temp_log.csv --power-log power_log.csv --range 7d
```

### No PATH setup needed

`pistats.py` is a normal Python script, so `python3 pistats.py` works without putting
anything on your `PATH` and from any directory. Run it with the absolute path
if you like:

```bash
python3 /home/berry5/projects/temp/pistats.py
python3 /home/berry5/projects/temp/pistats.py view temp_log.csv --power-log power_log.csv
```

It resolves its own location, so it always finds its companion modules. Log
paths given to `view` are still interpreted relative to your current directory.

---

## The five tabs

The dashboard has exactly five tabs, always in this order:

1. **Overview** — live temperature graph, power graph and session energy/cost,
   followed by the compact verdict and log summary.
2. **Temperature** — min / average (± std dev) / median / p95 / max SoC temp,
   PMIC temperature, CPU load, ARM frequency, temperature distribution and ARM
   frequency distribution (all in one tab).
3. **Performance** — performance impact, throttling flags decoded from
   `vcgencmd get_throttled`, top processes, spike attribution and the final
   verdict with every reason.
4. **Battery** — live PMIC rail breakdown, wall estimate, energy (Wh), cost
   ($/kWh) and battery runtime estimates.
5. **Config** — **the fifth and last tab**; edit intervals, mode, rate, battery
   capacity and wall factor live.

`←` / `→` switch tabs, `↑` / `↓` move the highlighted row (the view
auto-scrolls to keep it visible), `Enter` opens a row's detail / provenance
overlay (or edits a setting in Config), and `Esc` closes overlays, cancels an
edit, closes the Sampling sub-view or the command palette — at the main screen
(with nothing open) it exits PiStats. `Home` / `End` jump to the first / last
row, `PgUp` / `PgDn` jump a page.

The dashboard uses a cyan/blue/yellow colour theme (with red for critical
verdicts). Monochrome terminals automatically fall back to plain text.

---

## Command palette (`?`)

Press **`?`** (or `/`) to open the command palette. Type to filter, `↑` / `↓`
to select, `Enter` to run, `Esc` to close.

| Command | Effect |
|---|---|
| `/help` | Show all commands |
| `/config` | Jump to the Config tab; `/config set <key> <value>` edits a setting |
| `/reset` | Confirm, then clear all collected logs and start fresh |
| `/report` | Show the full saved text report |
| `/range 7d` | Show a scrollable historical report (`2h`, `24h`, `7d`, dates) |
| `/test 60` | Run a timed test in minutes (replaces `run-test.sh` from inside the CLI) |
| `/cron start` / `/cron stop` / `/cron status` | Manage the background collector |
| `/mode temp` / `/mode power` / `/mode both` | Switch what is sampled |
| `/decision` | Show the cooler verdict with all reasons |
| `/pause` / `/resume` | Freeze / unfreeze live rendering |
| `/quit` | Exit PiStats (cron is left untouched) |

**Example session**

```
$ python3 pistats.py                 # dashboard opens, renders every 1 s
  ?  → type "/range 7d", Enter      # scrollable 7-day report
  ?  → type "/config set rate_per_kwh 0.22", Enter
  ?  → type "/cron start", Enter    # background collection begins
  ?  → type "/quit", Enter
```

---

## Configuration — `~/.config/pistats/config.json`

All settings live in `~/.config/pistats/config.json` (override the path with
`PISTATS_CONFIG_PATH`). The Config tab and `/config set` edit the same file:

| Key | Default | Meaning |
|---|---|---|
| `render_interval_s` | `1.0` | Dashboard redraw interval (seconds) |
| `sample_interval_s` | `1.0` | Live sampling + Temperature UI refresh interval (seconds) |
| `power_sample_interval_s` | `1.0` | Power / Battery UI refresh interval (seconds) |
| `log_cadence_s` | `60` | How often samples are persisted to CSV |
| `mode` | `both` | `temp`, `power` or `both` |
| `rate_per_kwh` | `0.13` | Electricity price in $/kWh (13.0 cents) |
| `battery_wh` | `45.0` | Battery capacity in Wh |
| `wall_factor` | `1.0` | PMIC → wall power multiplier |
| `wall_slope`, `wall_intercept` | `1.0`, `0.0` | Optional wall calibration |
| `energy_windows_min` | `[60, 120]` | Energy windows in minutes |

### Sampling sub-view (Config tab)

The Config tab has one **SAMPLING** row. Press `Enter` on it to open a compact
sub-view with two choices:

1. **Power (battery)** — edits `power_sample_interval_s` (default `1.0` s),
   which controls how often the Battery tab's cached context is refreshed.
2. **Temperature** — edits `sample_interval_s` (default `1.0` s), which drives
   the live per-second sampling loop and the Temperature / Performance tabs'
   cached context. The Overview tab merges both caches.

Use `↑` / `↓` to choose an option, `Enter` to edit its interval inline (type
the seconds and press `Enter` to save, `Esc` to cancel), and `Esc` on the
sub-view itself to go back to the Config tab. Edits apply to the dashboard
immediately and are written to the config file.

### Modes

- `temp` — sample temperature only (writes `temp_log.csv`).
- `power` — sample PMIC power only (writes `power_log.csv`).
- `both` — sample both (the default; recommended).

---

## Power, cost and battery

PiStats sums the PMIC rails (`watts = max(V) × ΣA`, excluding voltage-only rails
`EXT5V_V` and `BATT_V`) to report live **power** in watts and a wall estimate
using the configured wall factor. It integrates power over time into **energy**
in **Wh** (watt-hours), applies your **cost** rate in **$/kWh** (kilowatt-hours)
for $/day and $/30-day figures, and estimates **battery** runtime in hours from
the configured capacity in Wh. The Battery tab shows the rail breakdown, energy
windows, cost and conservative (p95-based) runtime.

---

## Repository files

- `pistats.py` — entry point: `python3 pistats.py` → TUI, `python3 pistats.py view` → report
  (delegates to `start.py`).
- `start.py` — launcher used by `pistats.py`; resolves its own directory so the
  companion modules are importable from any working directory.
- `pistats_tui.py` — the five tabs, command palette, detail overlays and 1 Hz loop.
- `pistats_commands.py` — command parsing and headless execution.
- `pistats_config.py` — configuration defaults, persistence and validation.
- `pistats_cli.py` — command-line argument handling.
- `collect.sh` — the cron collector / spike detector (`--mode temp|power|both`).
- `power_read.py` — PMIC rail-sum helper shared by the collector, TUI and CLI.
- `analyze.py` — statistics, distributions, power/energy math and verdict engine.
- `run-test.sh` — timed test with progress (also callable from `/test`).
- `setup-cron.sh` — install the background collector (`start|stop|status`).
- `stop.sh` — remove PiStats's cron entry.
- `reset.sh` — clear all collected data and state files.
- `tests/` — unit and simulation test suite.

---

## Legacy workflows (still available)

The original shell workflows still work unchanged; PiStats's commands simply
wrap them.

### Quick test (2 minutes)

```bash
./collect.sh --dry-run
./collect.sh temp_log.csv
python3 analyze.py temp_log.csv
```

### Run for 1 hour (recommended test)

```bash
./run-test.sh 60
./run-test.sh --progress       # check progress from another terminal
```

Or, from inside PiStats, type `?` then `/test 60`. Custom durations: 30 / 480 /
1440 minutes are all accepted. For a background run use
`tmux new -s pistats-test`, then `./run-test.sh 60` and `Ctrl+B`, `D` to detach.

### Run for 1 week (full monitoring)

```bash
./setup-cron.sh start          # installs one collector line; idempotent
./setup-cron.sh status
./setup-cron.sh stop           # removes only PiStats's line
```

You can still edit the crontab by hand:

```cron
@reboot /home/berry5/projects/temp/collect.sh --mode both /home/berry5/projects/temp/temp_log.csv
* * * * * /home/berry5/projects/temp/collect.sh --mode both /home/berry5/projects/temp/temp_log.csv
```

File locking prevents concurrent runs if a script takes longer than one minute.

### View results anytime

```bash
python3 analyze.py temp_log.csv
# or, with power data:
python3 pistats.py view temp_log.csv --power-log power_log.csv
```

Both routes call the same `analyze.build_report`, so the numbers always agree.

### Stop / reset

```bash
./stop.sh     # remove PiStats from crontab (or /cron stop in the TUI)
./reset.sh    # clear all data files and state (or /reset in the TUI)
```

Manual reset (if needed):

```bash
rm -f temp_log.csv power_log.csv spikes.csv
rm -f /tmp/rpi5_temp_state_${UID} /tmp/thermal-scope-collector.pid
```

### Where results are stored

- `temp_log.csv` — temperature log (one row per persisted sample).
- `power_log.csv` — `timestamp,pmic_w,wall_w` power log.
- `pistats_session.csv` — dedicated live session log, written once per second
  by the dashboard (`timestamp,temp_c,wall_w`). It lives next to
  `temp_log.csv`; override the path with `PISTATS_SESSION_LOG=/path/to/file.csv`.
  The shared historical logs keep their existing collector format and cadence,
  so starting the dashboard never adds duplicate rows to them.
- `spikes.csv` — spike detection log (only created on 5°C+ jumps).
- `/tmp/rpi5_temp_state_${UID}` — temporary EMA baseline state (cleared on reboot).
- `/tmp/thermal-scope-collector.pid` — PID lock preventing concurrent runs.

### Run the test suite

```bash
python3 tests/run_tests.py
```

---

## Understanding the report

The report provides three main recommendations:

- **No Cooler Needed** — your bare-board setup is sufficient
- **Passive Heatsink Sufficient** — a heatsink will provide relief without fan noise
- **Active Cooler Strongly Recommended** — you need active cooling to prevent
  thermal throttling and performance degradation

Key metrics to watch: temperature distribution; frequency distribution;
performance loss; spike attribution; energy (Wh), cost ($/kWh) and battery
runtime.

**Important**: the report uses **p95 (95th percentile)**, not the average, so
idle time doesn't skew the verdict — if you're idle 23 hours/day but hit 85 °C
for 1 hour, p95 still shows ~84 °C.

---

## Common errors & solutions

- **"Log file not found"** — run `./run-test.sh 60` or collect a few samples first.
- **"No valid data in log file"** — the file exists but has no valid rows;
  `./reset.sh` then recollect.
- **"Another instance is already running"** — a collector holds the PID lock;
  check `./run-test.sh --progress`, stop it, or clear the stale lock file.
- **Stale lock files** — `run-test.sh` and `reset.sh` clean these up.
- **Partial data** — malformed CSV rows are skipped automatically.

### Edge cases

- The EMA state file lives in RAM and is **cleared on reboot** (expected).
- PID locking prevents overlapping writes; a stale lock is auto-detected.
- Even with 23 h/day idle, p95 and distributions accurately show thermal stress.

---

## Example report output

```text
=================================================================
 PISTATS - TEMPERATURE, POWER & COOLER DECISION REPORT
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
 POWER / ENERGY / BATTERY:
   PMIC rail sum        : 5.42 W   (wall est. 6.61 W, factor 1.0)
   ENERGY   1 h: 2.91 Wh   full log: 21.4 Wh
   COST     @ $0.13/kWh → 1 h $0.00038 · today $0.010 · 30 d $0.28
   BATTERY  45 Wh → ~8.3 h @ avg · ~7.5 h @ p95 (conservative)
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
