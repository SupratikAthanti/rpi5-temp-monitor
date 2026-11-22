# PiScope — Expected API contract (for the implementation subagent)

This file is the **authoritative interface** assumed by the tests in this directory.
The tests are written first (TDD) and currently FAIL because these modules/functions
do not exist yet. Implement them exactly as described here so the tests pass.

Nothing in this repo's existing public API may be removed or renamed. In particular
`analyze.py` must keep every function it already exports (`parse_csv_string`,
`parse_csv_file`, `calculate_stats`, `bucket_temperatures`, `bucket_frequencies`,
`calculate_performance_loss`, `decode_throttled_bits`, `summarize_throttling`,
`detect_culprit_app`, `parse_spikes_string`, `parse_spikes_file`,
`group_spikes_by_app`, `frequency_weighted_note`, `determine_verdict`, `main`).

Environment: Python 3.13 stdlib only. `pytest` and `textual` are **NOT** installed.
Pure logic must be importable without `textual`.

---

## 1. `power_read.py` (new)

```python
EXCLUDE_VOLTAGE_ONLY_RAILS = {"EXT5V_V", "BATT_V"}

def parse_pmic_adc(text: str) -> dict[str, float]: ...
def sum_pmic_watts(source) -> float: ...        # `source` is str (raw adc text) or dict from parse_pmic_adc
def read_power() -> dict: ...
```

`parse_pmic_adc` parses raw `vcgencmd pmic_read_adc` text. Real lines look like:

```
3V3_SYS A  0.50000
3V3_SYS V  3.3000
VDD_CORE A  1.00000
VDD_CORE V  0.8500
EXT5V_V V  5.0000
BATT_V  V  4.1000
```

Rules (all are tested):
* A rail contributes watts **only if it has at least one `A` (amps) reading and at
  least one `V` (volts) reading**. `watts = max(voltage_readings) * sum(current_readings)`.
* Rails with **no `A` reading are voltage-only and omitted** — this covers the
  explicit exclusions `EXT5V_V` and `BATT_V` (always omitted even if they have an A
  line).
* **Whitespace is arbitrary** (leading/trailing, multiple spaces/tabs between fields).
* **Malformed lines are skipped** (wrong field count, non-numeric value, unknown unit).
* **Exact duplicate lines are collapsed** (a `(rail, unit, value)` triple seen twice
  counts once). Distinct `A` readings for the same rail are summed (dual-phase rail).
* Case/format: rail name is the token before the unit; unit token is exactly `A` or `V`
  (uppercase; accept lowercase). Values may contain a trailing junk? — no: value must
  parse as `float`, else skip.
* Return value: `{"3V3_SYS": 1.65, "VDD_CORE": 0.85, ...}` (floats, watts).

`sum_pmic_watts` returns `sum(parse_pmic_adc(text).values())` for a string, or
`sum(d.values())` for a dict.

`read_power()` returns `{"rails": {..}, "pmic_w": float, "wall_w": float,
"wall_factor": float}`. It must call `vcgencmd pmic_read_adc` when available and fall
back to an empty reading safely. Honour env `FAKE_PMIC_ADC` (raw text) and config's
`wall_factor`.

---

## 2. `analyze.py` additions (modify existing file, keep all old functions)

```python
def pmic_to_wall(pmic_w: float, factor: float = 1.0) -> float: ...
def calibrated_wall_w(pmic_w: float, slope: float = 1.0, intercept: float = 0.0) -> float: ...

def parse_power_log_string(csv_text: str) -> list[dict]: ...
def parse_power_log(filepath: str) -> list[dict]: ...

def energy_wh(samples, window_minutes: float | None = None) -> float: ...
def cost_dollars(energy_wh_value: float, rate_per_kwh: float = 0.15) -> float: ...
def battery_runtime_hours(capacity_wh: float, power_w: float) -> float: ...
def battery_estimate(capacity_wh: float, avg_power_w: float, p95_power_w: float) -> dict: ...

def build_report(log_file: str, power_log_file: str | None = None,
                 config: dict | None = None, range_spec: str | None = None) -> str: ...
def main(argv=None) -> int: ...   # keep CLI behaviour; may add --range/--summary
```

* `pmic_to_wall(w, f) == w * f`. `calibrated_wall_w(w, s, b) == s * w + b`.
* `power_log.csv` header is exactly `timestamp,pmic_w,wall_w`.
  `parse_power_log*` returns records
  `{'timestamp': str, 'epoch': float, 'pmic_w': float, 'wall_w': float}`,
  skipping malformed rows. `epoch` is parsed from the ISO-8601 `Z` timestamp.
* `energy_wh(samples, window_minutes=None)`:
  * `samples` is a sequence of `(epoch_seconds: float, watts: float)` tuples **or**
    dict records with keys `epoch` and `wall_w`.
  * Uses **left Riemann sum**: `sum(watts[i] * (t[i+1] - t[i])) / 3600`, for
    consecutive pairs only. Last sample contributes no trailing interval.
  * `window_minutes`, when given, keeps only samples with
    `epoch >= last_epoch - window_minutes * 60`.
  * Return `0.0` for fewer than 2 usable samples.
* `cost_dollars(wh, rate) == wh / 1000.0 * rate`.
* `battery_runtime_hours(cap, watts) == cap / watts`, `0.0` when `watts <= 0`.
* `battery_estimate(...)` returns
  `{'capacity_wh', 'avg_power_w', 'p95_power_w', 'avg_hours', 'p95_hours',
  'conservative_hours'}` where `*_hours` use `battery_runtime_hours` and
  `conservative_hours == p95_hours` (p95 power is higher → shorter runtime).
* `build_report` returns the **full text report** as a string (does not print). It
  must preserve every existing README field/section heading and append a
  `POWER / ENERGY / BATTERY` section when `power_log_file` is given and readable.
  `piscope view` and `analyze.py <file>` call it so numbers always agree.
  `config` (when given) supplies `rate_per_kwh`, `battery_wh`, `energy_windows_min`.

---

## 3. `piscope_config.py` (new)

```python
CONFIG_REL_PATH = os.path.join('.config', 'piscope', 'config.json')
CONFIG_PATH_ENV = 'PISCOPE_CONFIG_PATH'
DEFAULT_CONFIG = {
    'render_interval_s': 1.0, 'sample_interval_s': 1.0, 'log_cadence_s': 60,
    'mode': 'both', 'rate_per_kwh': 0.15, 'battery_wh': 45.0,
    'wall_factor': 1.0, 'wall_slope': 1.0, 'wall_intercept': 0.0,
    'energy_windows_min': [60, 120],
}
LIVE_KEYS = {...}   # must include all 8 keys named in design.md §13

def config_path() -> str: ...
def load_config(path=None) -> dict: ...
def save_config(cfg: dict, path=None) -> None: ...
def validate_config(cfg: dict) -> dict: ...
def set_config_value(cfg: dict, key: str, value) -> dict: ...
```

* `config_path()` returns `os.environ[CONFIG_PATH_ENV]` when set, otherwise
  `os.path.join(os.path.expanduser('~'), CONFIG_REL_PATH)`.
* `load_config(path=None)` starts from a **copy** of `DEFAULT_CONFIG`, overlays the
  JSON file at `path or config_path()`, and runs `validate_config`. Missing/corrupt
  file → pure defaults (no exception).
* `save_config` creates parent dirs and writes indented JSON; path override honoured.
* `validate_config` clamps: render/sample interval to `[0.1, 60]`; log cadence to
  `[1, 86400]`; `mode` must be one of `temp|power|both` else `both`; `rate_per_kwh`
  and `battery_wh` clamped to `>= 0`; `wall_factor` clamped to `> 0`;
  `energy_windows_min` must be a non-empty list of positive numbers (else default).
* `set_config_value` parses the string value according to the key's default type
  (float / int / mode / comma-separated numbers), validates, and returns an updated
  **copy** of `cfg`. Unknown key → `ValueError`.

---

## 4. `piscope_commands.py` (new)

```python
COMMANDS = [ {'name': '/reset', 'description': '...'}, ... ]  # >= 12 entries
ALIASES = {'?': '/help', '/': '/help'}

def normalize_command(text: str) -> str: ...
def parse_command(text: str) -> dict: ...
def filter_commands(query: str) -> list[dict]: ...
def autocomplete(query: str) -> list[str]: ...
def parse_config_set(args: list[str]) -> dict | None: ...
```

Required command names: `/help`, `/config`, `/reset`, `/report`, `/range`, `/test`,
`/cron`, `/mode`, `/pause`, `/resume`, `/quit`, `/decision`.

* `normalize_command` strips whitespace, maps `?` and `/` to `/help`, and lowercases
  the command token (not argument values).
* `parse_command` returns
  `{'raw': text, 'name': '/range' | None, 'args': [...], 'valid': bool,
  'error': str | None}`. `'/range 7d'` → name `/range`, args `['7d']`.
  `'/config set battery_wh 60'` → name `/config`, args `['set','battery_wh','60']`.
  Invalid: empty input; unknown command; `/mode` with not-in-{temp,power,both};
  `/cron` with not-in-{start,stop,status}; `/test` with non-positive-int.
* `filter_commands(query)` case-insensitive substring match on `name` and
  `description`; returns the full command dicts, preserving `COMMANDS` order.
* `autocomplete(query)` returns command `name`s that start with `query` (with a
  leading `/` added if absent).
* `parse_config_set(['set','battery_wh','60'])` → `{'key':'battery_wh','value':'60'}`;
  returns `None` when not a valid `set` invocation.

---

## 5. `piscope_tui.py` (new — MUST import with `textual` absent)

```python
TABS = ['Overview', 'Temperature', 'Performance', 'Battery', 'Config']
DEFAULT_RENDER_INTERVAL_S = 1.0
KEY_BINDINGS = { '?': 'commands', '/': 'commands', 'left': 'prev_tab', ... }

def build_tab_rows(tab: str, ctx: dict | None = None) -> list[dict]: ...
def move_selection(rows, index, scroll, viewport_height, direction) -> tuple[int, int]: ...
def scroll_to_section(rows, index, scroll, viewport_height) -> int: ...
def resolve_enter_action(row: dict, tab: str) -> str | None: ...
def cancel_edit(state: dict) -> dict: ...
```

* `TABS` is **exactly** the five names, in order, with `Config` last.
* Rows from `build_tab_rows` are dicts with at least `id`, `label`, `text`, `kind`;
  `kind` is `'section'` for titles and `'data'` for data rows. `ctx=None` still
  returns the full row set with placeholder values.
  * `Temperature` rows contain BOTH the stats section (`min`, `avg`, `median`,
    `p95`, `max`, `PMIC`, `CPU load`, `ARM MHz`) AND section titles for
    `TEMPERATURE DISTRIBUTION` and `ARM FREQUENCY DISTRIBUTION` plus all 5 temp
    bands and all 6 freq bands.
  * `Performance` rows contain sections for performance impact, throttling, top
    processes, spikes and final verdict.
  * `Battery` rows contain sections for live power (PMIC + wall), rail breakdown,
    energy, cost and battery runtime.
  * `Overview` contains a compact verdict; `Config` contains one row per live key.
  * Unknown tab → `[]` or `ValueError` (choose one; tests only use known tabs).
* `move_selection(rows, index, scroll, viewport_height, direction)`:
  * `direction` ∈ `up|down|home|end|page_up|page_down`.
  * Returns `(new_index, new_scroll)`.
  * Index is clamped to `[0, len(rows)-1]`.
  * Scrolling is the **minimum** required to keep `new_index` visible:
    `new_scroll <= new_index <= new_scroll + viewport_height - 1`.
    Going down past the bottom sets `scroll = new_index - viewport_height + 1`;
    going up past the top sets `scroll = new_index`.
  * `page_up`/`page_down` move by `viewport_height` rows.
* `scroll_to_section`: when the selected row is a section, return its index (title
  at top of viewport); otherwise return the index of the nearest preceding section.
* `resolve_enter_action(row, tab)` → `'edit'` for data rows on `Config`, `'detail'`
  for data rows elsewhere, `None` for section rows.
* `cancel_edit(state)` clears an in-progress edit: returns a dict with
  `{'editing': False, 'buffer': ''}` plus any other keys preserved.

---

## 6. `piscope_cli.py` + `piscope` entry script (new)

```python
# piscope_cli.py
def build_parser() -> argparse.ArgumentParser: ...
def main(argv=None) -> int: ...
def run_view(args) -> int: ...
def launch_tui(args) -> int: ...

# piscope (repo root, executable, shebang python3)
# -> imports main() from piscope_cli and calls sys.exit(main())
```

* `piscope --help` exits 0 and prints usage mentioning `piscope` and `view`.
* `piscope view [LOG] [--power-log P] [--range 7d]` prints the report from
  `analyze.build_report` and exits 0.
* `piscope` (no args) calls `launch_tui` exactly once. Tests patch
  `piscope_cli.launch_tui`.
* `piscope view` on a missing log file exits non-zero with an informative message.

---

## 7. Changes to shell scripts

* `collect.sh [--mode temp|power|both] [--skip-if-locked] [LOG_FILE]`
  * default mode `both`; flag may appear before or after the log path.
  * `temp` writes only `temp_log.csv`; `power` writes only `power_log.csv`;
    `both` writes both.
  * power row: `timestamp,pmic_w,wall_w` (wall = pmic × `wall_factor`, default 1).
  * env overrides (all optional, tests use them so no real paths are touched):
    `POWER_LOG=<path>`, `COLLECTOR_PID_FILE=<path>`, `FAKE_PMIC_ADC=<raw text>`,
    `WALL_FACTOR=<float>`.
  * `--skip-if-locked`: if another live collector holds the PID lock, exit **0**
    without writing any log rows. Without the flag, behaviour is as today (exit 1).
  * `--dry-run` still prints one 8-field temperature CSV line to stdout.
* `run-test.sh [--mode MODE]` passes `--mode` through to `collect.sh`.
* `reset.sh` also deletes `power_log.csv` next to the other logs.

---

## 8. Fixtures / integration expectations

`tests/test_integration.py` writes these files to a temp dir:

`temp_log.csv` (4 rows): soc `[60,70,80,90]`, pmic `[50,55,60,65]`,
load `[0.5,1,2,3]`, freq `[2400,1500,1500,2400]`, throttled `0x0,0x0,0x4,0x4`,
top procs `opencode,opencode,opencode,ps`, timestamps one minute apart starting
`2026-11-21T10:00:00Z`.

Derived expected values: count 4; min 60.0; avg 75.0; median 75.0; p95 90.0;
max 90.0; std 11.2; PMIC avg 57.5 / max 65.0; load avg 1.62 / max 3.00;
ARM freq avg 1950 / max 2400; every temperature bucket 25%; freq buckets
2400→2, 1500→2; perf loss 18.8%; effective speed 81.2%; time impact ~14 minutes;
active throttles 2; verdict = ACTIVE COOLER with reasons naming 90.0°C and 2 throttles.

`power_log.csv` (4 rows): wall_w `[2.4,4.8,7.2,9.6]` at the same 1-minute
timestamps. Left-Riemann energy = `(2.4+4.8+7.2)*60/3600 = 0.24 Wh`.
At `$0.15/kWh` cost = `$0.00004`. Avg wall = 6.0 W, p95 wall = 9.6 W.
With `battery_wh=45`: avg runtime 7.5 h, conservative (p95) 4.7 h.
