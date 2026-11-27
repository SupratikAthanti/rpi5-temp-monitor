# PiStats — Design: Live CLI Dashboard, Five Tabs & Full Feature Preservation

> Status: **design only.** Nothing described below has been implemented.
> Branch: `RPI-Power-Monitoring`.
> Proposed new name: **PiStats** (command: `python3 pistats.py`). Renaming the GitHub repo is a
> separate step and has not been done. Alternatives: `wattwatch`, `thermowatt`, `piwatch`.

---

## 1. Goal

One command opens everything:

```bash
python3 pistats.py
```

No flags, no numbered menus. A full-screen dashboard that **renders every second**, with:

- **Five tabs** — Overview, Temperature, Performance, Battery, Config (config last).
- **A command bar** — press **`?`** to see all commands; type to filter; `Enter` to run.
- **Consistent navigation everywhere** — `←`/`→` switch tabs, `↑`/`↓` move the
  highlighted row and auto-scroll the tab so the selected section stays visible,
  `Enter` opens a row's detail or edits a setting.

Every metric in the current README's example report is preserved. Distribution lives
**inside the Temperature tab** (no separate tab).

---

## 2. Is it possible?

**Yes.** A Python TUI does this cleanly:

- **Textual** (recommended) — native tabs, input with autocomplete, keybindings, mouse,
  smooth 1 s redraw. Runs over SSH. Requires `pip install textual`.
- **`curses`** (standard library) — zero dependencies, keyboard-only, more manual code.

A full PMIC read is ~25 ms and a temp read ~1 ms, so 1 Hz rendering is comfortable.
The 1 Hz samples live in memory for the display; persistent CSV logs stay at a
**configurable** cadence (default 1 sample/minute, ~750 KB/week).

---

## 3. Layout & navigation (shared by all tabs)

```
┌──────────────────────────────────────────────────────────────┐
│ header: mode · render every 1 s · session time · clock       ║
├──────────────────────────────────────────────────────────────┤
│ ◀ Overview │ Temperature │ Performance │ Battery │ Config ▶  ║
├──────────────────────────────────────────────────────────────┤
│              active tab content (updates every second)       ║
├──────────────────────────────────────────────────────────────┤
│ pistats › _     ? commands · ← → tabs · ↑ ↓ rows · Enter     ║
└──────────────────────────────────────────────────────────────┘
```

### How `↑` / `↓` work — auto-scrolling selection

Each tab is a vertical list of **selectable rows**: section titles and data rows.

- `↑`/`↓` moves the highlight from one row to the next.
- The visible area is a window into that list. When the highlight would fall below
  (or above) the visible area, the view shifts by the **minimum amount needed** to
  bring the highlighted row back into view — so you never lose your place.
- Section titles are selectable anchors. Highlighting a title scrolls its whole
  section into view; moving down then steps through that section's rows, and when
  you reach the bottom the next section's title slides in at the top. The highlight
  effectively travels "from the top section to the bottom section" while the title
  at the top updates to tell you which section you're in.
- `Enter` on a data row opens its detail overlay. In Config, `Enter` edits the
  highlighted value inline.
- `Home` / `End` jump to the first / last row; `PgUp` / `PgDn` jump a page.

| Key | Action |
|---|---|
| `←` / `→` | Switch tabs (when command bar is empty) |
| `Tab` / `Shift+Tab` | Also switch tabs |
| `↑` / `↓` | Move highlight through rows; view auto-scrolls to follow |
| `Enter` | Open detail for selected row / edit selected config value |
| `?` | Open the command list (also focuses the command bar) |
| typing | Filter the command list; in Config, edit the selected value |
| `Esc` | Close the list / cancel an edit / go back |
| `/` | Accepted as an alias for `?` |

Once you start typing in the command bar, arrow keys edit the text instead of tabs.
`?` is the primary command trigger; `/` still works if you're used to it.

---

## 4. Feature preservation map

Checklist against the README's "Example Report Output" and feature list.

| README item | Home tab |
|---|---|
| Log file, total samples, time span | **Temperature** (compact copy in header on every tab) |
| SoC temp: min, average (± std dev), median, p95, max | **Temperature** |
| PMIC temp (avg, max) | **Temperature** |
| CPU load 1m (avg, max) | **Temperature** |
| ARM frequency (avg, max) | **Temperature** |
| Temperature distribution (5 bands, counts, %, bars) | **Temperature** |
| ARM frequency distribution (2400/1500/1000/750/600/other) | **Temperature** |
| Performance impact: avg freq, loss %, effective speed, time impact | **Performance** |
| Throttling state (`vcgencmd get_throttled` decoded flags) | **Performance** |
| Top processes (samples, %) | **Performance** |
| Spike attribution (5 °C+ jumps, culprit, peak, delta) | **Performance** |
| Final verdict + all reasons | **Performance** (compact verdict on **Overview**) |
| Cooler recommendation logic (No / Passive / Active) | **Performance** |
| NEW: watts, per-rail W, energy Wh, cost, battery runtime | **Battery** |
| NEW: refresh / sample / log intervals, mode, rate, battery, wall factor | **Config** |

Old workflows preserved as commands:

| Old workflow | New command |
|---|---|
| `run-test.sh 60` (progress, tmux, custom durations) | `/test 60` (script still works) |
| `python3 analyze.py ...` anytime | `/report` and `pistats.py view` |
| `stop.sh` (remove cron) | `/cron stop` |
| `reset.sh` (fresh start) | `/reset` |
| 7-day / custom-range reports | `/range 7d` |

---

## 5. Tab 1 — Overview

```
╔═ PiStats  ·  OVERVIEW ─────────────────────────────────────────────────────────╗
║ ◀ Overview │ Temperature │ Performance │ Battery │ Config ▶                    ║
╟────────────────────────────────────────────────────────────────────────────────╢
║ Mode: BOTH      Render: 1 s      Session: 00:12:43     2026-11-21 14:32:07     ║
║                                                                                ║
║   78.3 °C  ▲                              5.42 W  ▲                            ║
║   SoC temperature                         PMIC power (wall est. 6.61 W)        ║
║   p95 (1 h): 84.1 °C                       Energy 1 h: 2.91 Wh                 ║
║                                                                                ║
║   Cost today $0.011  │  Battery 45 Wh → ~8.3 h  │  Cron: RUNNING               ║
║                                                                                ║
║   temp  ▁▂▃▅▇█▇▆▅▃▂▁▂▃▄▅▆▇█        watt  ▂▃▅▆█▇▆▅▃▂▁▂▃▅▆▇█▇▆                   ║
║                                                                                ║
║   VERDICT: ACTIVE COOLER  ·  max 86.7 °C  ·  14 throttles  ·  2 spikes         ║
║   Log span: 7 d 3 h       Samples: 10,080       → → see detail tabs            ║
╟────────────────────────────────────────────────────────────────────────────────╢
║ pistats › _     ? commands · ← → tabs · ↑ ↓ rows · Enter detail                ║
╚════════════════════════════════════════════════════════════════════════════════╝
```

The two history graphs use Unicode braille cells (2 x 4 subpixels per character)
instead of filled character rows. The displayed window follows a fixed ladder —
30 s, 1/2/5/10/15/30 min, 1/3/6/12 h, 1/2/3/7/14/30 d — starting at a 30 s
floor so startup noise is not magnified, and the header reports the chosen
window. Recent samples stay at 1 Hz for an hour; older readings become
one-minute min/max/avg buckets (30-day cap), which the renderer draws as
per-column envelope strokes so spikes are not interpolated away. Multiple time
ticks, dated when the window crosses days, sit under each plot, with unit-aware
min/max floors (2 °C / 1 W) for tiny early variations. Energy is integrated
from the compacted history with duration-weighted trapezoids.

---

## 6. Tab 2 — Temperature (stats **and** all distributions)

This tab is longer than the screen, so `↓` walks through it and the view auto-scrolls.
Two consecutive frames illustrate that behavior:

**Frame A — top of the tab:**

```
╔═ PiStats  ·  TEMPERATURE ──────────────────────────────────────────────────────╗
║  Overview │ ◀ Temperature ▶ │ Performance │ Battery │ Config                   ║
╟────────────────────────────────────────────────────────────────────────────────╢
║ > SoC TEMPERATURE STATS (°C)          Samples 63 · span 20:08:12Z→21:09:20Z    ║
║     Min             :  55.2 °C                                                 ║
║     Average         :  79.1 °C   (± 6.7 °C std dev)                            ║
║     Median          :  82.3 °C                                                 ║
║     95th Percentile :  86.2 °C                                                 ║
║     Max             :  86.7 °C                                                 ║
║   PMIC / CPU LOAD / ARM FREQUENCY                                              ║
║     PMIC 69.8 °C (max 74.4) · load 0.97 (max 2.92) · 1801 MHz (max 2400)       ║
║   TEMPERATURE DISTRIBUTION                                                     ║
║     Under 60°C (Cool)   :   0 ( 0.00%)                                         ║
╟────────────────────────────────────────────────────────────────────────────────╢
║ pistats › _     ? commands · ↑ ↓ rows · Enter detail                           ║
╚════════════════════════════════════════════════════════════════════════════════╝
```

**Frame B — after pressing `↓` several times; the view scrolled to follow the highlight:**

```
╔═ PiStats  ·  TEMPERATURE ──────────────────────────────────────────────────────╗
║  Overview │ ◀ Temperature ▶ │ Performance │ Battery │ Config                   ║
╟────────────────────────────────────────────────────────────────────────────────╢
║   TEMPERATURE DISTRIBUTION                                                     ║
║     60°C - 70°C (Normal)              :  10 (15.87%) #######                   ║
║  > 70°C - 80°C (Warm)                 :  19 (30.16%) ###############           ║
║     80°C - 85°C (Soft Throttle Zone)  :  19 (30.16%) ###############           ║
║     Over 85°C (Hard/Critical)         :  15 (23.81%) ###########               ║
║   ARM FREQUENCY DISTRIBUTION                                                   ║
║     2400 MHz (Max)        :   9 (14.29%) #######                               ║
║     1500 MHz (Throttled)  :  54 (85.71%) ######################################║
║     1000 / 750 / 600 MHz  :   0 ( 0.00%)                                       ║
║     Other/Unknown         :   0 ( 0.00%)                                       ║
╟────────────────────────────────────────────────────────────────────────────────╢
║ pistats › _     ? commands · ↑ ↓ rows · Enter detail                           ║
╚════════════════════════════════════════════════════════════════════════════════╝
```

`Enter` on a highlighted row shows provenance — which sample produced the value and
the raw source line.

---

## 7. Tab 3 — Performance / Throttling / Spikes

```
╔═ PiStats  ·  PERFORMANCE ──────────────────────────────────────────────────────╗
║  Overview │ Temperature │ ◀ Performance ▶ │ Battery │ Config                   ║
╟────────────────────────────────────────────────────────────────────────────────╢
║ > PERFORMANCE IMPACT ANALYSIS                                                  ║
║     Average Frequency   :  1801 MHz / 2400 MHz max                             ║
║     Performance Loss    :  24.9 % slower than max capability                   ║
║     Effective Speed     :  75.1 % of maximum                                   ║
║     Time Impact         :  1-hour task → +~20 minutes from throttling          ║
║   THROTTLING (vcgencmd get_throttled decoded)                                  ║
║     Active throttled: 14   Past throttled: 45   Soft-temp limit: 120           ║
║     Under-voltage now / past: 0 / 0                                            ║
║   TOP PROCESSES                                                                ║
║     opencode   62 samples (98.4 %)                                             ║
║     ps          1 samples ( 1.6 %)                                             ║
║   SPIKE ATTRIBUTION (5°C+ jumps, 2 events)                                     ║
║     opencode   2 spikes (peak 85.1 °C, +12.7 °C)                               ║
║   FINAL VERDICT                                                                ║
║     Active Cooler Strongly Recommended                                         ║
║       - max 86.7 °C (≥ 85 °C); 14 active throttles; 23.81 % above 85 °C        ║
║       - frequent spikes: 2 in 63 samples (3.17 %), culprit opencode            ║
╟────────────────────────────────────────────────────────────────────────────────╢
║ pistats › _     ? commands · ↑ ↓ rows · Enter detail                           ║
╚════════════════════════════════════════════════════════════════════════════════╝
```

`Enter` on a spike opens the captured top-process snapshot (CPU %, memory %, arguments)
from `spikes.csv`.

---

## 8. Tab 4 — Battery & Cost

```
╔═ PiStats  ·  BATTERY ──────────────────────────────────────────────────────────╗
║  Overview │ Temperature │ Performance │ ◀ Battery ▶ │ Config                   ║
╟────────────────────────────────────────────────────────────────────────────────╢
║ > LIVE POWER                                                                   ║
║     PMIC rail sum      5.42 W      wall est. 6.61 W  (factor 1.0)              ║
║   RAIL BREAKDOWN (A × V per rail)                                              ║
║     VDD_CORE    2.61 W  48 %  ████████████████                                 ║
║     0V8_SW      0.27 W   5 %  ██                                               ║
║     3V3_SYS     0.20 W   4 %  █                                                ║
║     1V1_SYS     0.19 W   4 %  █                                                ║
║     … other rails                                                              ║
║     TOTAL (PMIC) 5.42 W     (EXT5V_V / BATT_V excluded: voltage-only)          ║
║   ENERGY   1 h: 2.91 Wh   2 h: 5.74 Wh   full log: 21.4 Wh                     ║
║   COST     @ $0.13/kWh → 1 h $0.00038 · today $0.010 · 30 d $0.28              ║
║   BATTERY  45 Wh → ~8.3 h @ avg · ~7.5 h @ p95 (conservative)                  ║
║            NOTE: PMIC excludes USB/fan/regulator losses; set wall factor       ║
╟────────────────────────────────────────────────────────────────────────────────╢
║ pistats › _     ? commands · ↑ ↓ rows · Enter detail                           ║
╚════════════════════════════════════════════════════════════════════════════════╝
```

---

## 9. Tab 5 — Config (last tab)

Navigate with `↑`/`↓`, press `Enter` on a row to edit it, type the new value,
`Enter` to save, `Esc` to cancel. Changes apply immediately and are saved to
`~/.config/pistats/config.json`.

```
╔═ PiStats  ·  CONFIG ───────────────────────────────────────────────────────────╗
║  Overview │ Temperature │ Performance │ Battery │ ◀ Config ▶                   ║
╟────────────────────────────────────────────────────────────────────────────────╢
║ > Render dashboard every    [ 1 s ]      ← → tabs · ↑ ↓ select · Enter edit    ║
║   Live sample interval      [ 1 s ]                                            ║
║   Persist to log every      [ 60 s ]                                           ║
║   Mode                      [ both ]     temp | power | both                   ║
║   Electricity rate          [ $0.13 / kWh ]                                    ║
║   Battery capacity          [ 45 Wh ]                                          ║
║   PMIC → wall factor        [ 1.00 ]                                           ║
║   Energy windows            [ 60,120 min ]                                     ║
║   Cron status               [ RUNNING ]   → /cron stop to disable              ║
╟────────────────────────────────────────────────────────────────────────────────╢
║ pistats › _     ? commands · ↑ ↓ select · Enter edit · Esc cancel              ║
╚════════════════════════════════════════════════════════════════════════════════╝
```

Editing in place:

```
║   Render dashboard every    [ 2_ ]   ← type value · Enter save · Esc cancel    ║
```

The same edit pattern is used in every tab: `↑`/`↓` selects a row and `Enter` opens
its detail (or the editor in Config).

---

## 10. Command bar (`?`)

Press `?` to open the list; typing filters it; `↑`/`↓` selects, `Enter` runs, `Esc` closes.

```
║ pistats › ?re_
║   /reset            Delete all collected data and start fresh
║   /report           Show the full saved report
║   /range 7d         Show results for a time range (2h, 24h, 7d, dates)
║   /test 60          Run a timed test with progress (minutes)
║   /cron start       Start / stop / check the cron job
║   /mode power       Switch mode: temp | power | both
║   /pause            Pause live rendering
║   /quit             Exit PiStats
```

| Command | Effect |
|---|---|
| `?` or `/help` | Show all commands |
| `/config` | Jump to Config tab (`/config set <key> <value>` also works) |
| `/reset` | Confirm, then clear all logs and state |
| `/cron start` / `stop` / `status` | Manage the background collector |
| `/mode temp` / `power` / `both` | Switch what is sampled |
| `/test <minutes>` | Timed test with progress (replaces `run-test.sh` from inside the CLI) |
| `/range 2h` / `7d` / `2026-11-20..21` | Scrollable historical report |
| `/report` | Full current text report (same as `python3 pistats.py view`) |
| `/decision` | Cooler verdict with all reasons |
| `/pause` / `/resume` | Freeze / unfreeze rendering |
| `/quit` | Exit; cron is untouched |

---

## 11. How the two halves work together

- **Cron is optional.** It samples once a minute in the background
  (`collect.sh --mode both`) for always-on history; live CLI readings do *not*
  depend on it.
- **`python3 pistats.py`** renders at 1 Hz from in-memory samples and reads the
  PMIC ADC itself. Only its configurable 1/min persistence writes to CSV, using
  the same collector lock, so no duplicate rows. Readings stop when the CLI
  closes, by design.
- The real ADC format is `RAIL_A current(N)=<float>A` / `RAIL_V volt(N)=<float>V`
  (two tokens per line); `power_read.parse_pmic_adc` also accepts the legacy
  `RAIL A <float>` fixture format and combines `_A` currents with `_V` voltages.
- When `power_log.csv` is missing, empty, all-zero or stale (>5 min), the other
  UI paths (Battery tab, `/report`, `view`) fall back to live session samples
  via `power_source.resolve()` and surface the source label `live (no cron)`.
  Explicit `--power-log` files keep their historical behaviour. With no ADC and
  no log the state is `unavailable` plus a hint — never invented zeros.
- Both the TUI and `python3 pistats.py view` call the **same** `analyze.py` functions — numbers
  always agree.
- The tabs are a different presentation of the existing report data, not a replacement.

---

## 12. Example sessions

```bash
$ python3 pistats.py               # dashboard opens, renders every 1 s
# → once: Temperature tab; ↓ repeatedly → highlight walks stats into the
#   distribution sections, view auto-scrolls to keep the section visible
# → again: Performance tab; ↓ to spike rows, Enter for process snapshot
# ? then type "/range 7d", Enter → scrollable 7-day report
# ? then type "/config set render_interval_s 2", Enter → now renders every 2 s
# ? then type "/cron start", Enter → background collection begins
# ? then type "/quit", Enter
```

---

## 13. Implementation notes (for later, not done now)

**New files**
- `pistats.py` — single entry point; `python3 pistats.py` with no args → TUI
- `pistats_tui.py` — five tabs, command bar, `?` palette, 1 Hz render loop,
  auto-scrolling row selection, detail overlays
- `setup-cron.sh` — cron installer (counterpart to `stop.sh`)

**Modified**
- `collect.sh` — `--mode temp|power|both` (default `both`), `power_log.csv`,
  `--skip-if-locked` for TUI persistence
- `run-test.sh` — `--mode` pass-through, still callable directly
- `analyze.py` — all existing stats/distribution/verdict functions kept **unchanged**;
  adds power parsing, energy/cost/battery math, `--range`, `--summary`
- `power_read.py` — PMIC rail-sum helper shared by collector and TUI
- `reset.sh` — also clears `power_log.csv`
- `README.md` — new name, one-command workflow, tab/command guide

**Config:** `~/.config/pistats/config.json` — render interval, sample interval, log
cadence, mode, rate, battery Wh, wall factor, energy windows.

**Data files:** `temp_log.csv`, `power_log.csv`, `spikes.csv` — already in `.gitignore`.
