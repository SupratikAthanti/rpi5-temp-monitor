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

---

# NEW SECTIONS — command execution, cron installer, detail overlays, README

These interfaces are required by the newly added tests
(`test_command_execution.py`, `test_setup_cron.py`, `test_detail_overlay.py`,
`test_readme.py`, `test_design_gaps.py`). They currently FAIL because the
features are missing/unwired. Implement exactly as described; all other
existing APIs remain unchanged. Pure logic must stay importable without
`textual` and must never touch the real machine when a context is injected.

## 9. Command execution — `execute_command(cmd, context) -> result`

**Canonical location:** `piscope_commands.execute_command`. A re-export from
`piscope_tui` is also accepted (the test checks `piscope_commands` first, then
`piscope_tui`). The TUI's command handler must call this function; the old
"return status text only" behaviour of `/test`, `/reset`, `/report`, `/range`,
`/cron` must be replaced by real execution through the injected context.

```python
def execute_command(cmd, context) -> dict: ...
```

* `cmd` may be a **raw command string** (e.g. `'/test 60'`) **or** the dict
  returned by `parse_command`. Strings must be parsed with `parse_command`
  first; invalid commands return `{'ok': False, 'status': <error>, 'effects': []}`.

### Injected context (exact dict keys)

```python
context = {
    'dir': str,            # scratch data dir; default log names live here
    'config': dict,        # current config values (may be DEFAULT_CONFIG)
    'config_path': str,    # file path used to persist config changes
    'scripts': str,        # directory containing run-test.sh / setup-cron.sh / stop.sh
    'state': dict,         # mutable TUI state (paused / quit / detail ...)
    'confirm': callable(prompt: str) -> bool,
    'process_spawner': callable(argv: list[str], **kwargs) -> CompletedProcess-like,
    'crontab_runner': callable(argv: list[str], input: str | None = None, **kwargs)
                      -> CompletedProcess-like,
    'report_fn': callable(**kwargs) -> str,
}
```

A `CompletedProcess-like` object has `.returncode`, `.stdout`, `.stderr`.
All keys are optional except where a test uses them; when absent, fall back to
safe defaults (`confirm` → True for `/reset`? **No**: default to False so a
real reset is never destructive without explicit confirmation).

### Result (exact keys)

```python
{
    'ok': bool,                 # True when the command was carried out
    'status': str,              # human-readable line for the TUI status bar
    'effects': list[str],       # machine-readable effect markers (see below)
    ...command-specific keys...
}
```

### Per-command required behaviour

| Command | Required effect via injected context | Effect marker | Extra result keys |
|---|---|---|---|
| `/test N` | Call `process_spawner` **exactly once**, non-blocking, with an argv whose first element's basename is `run-test.sh` and which contains the string `str(N)` (e.g. `'/test 60'` → `... '60'`). Never sleep. Optionally pass `--mode <config['mode']>`. | `test_started` | `'minutes': int` |
| `/reset` | Only when `confirm(<prompt>)` returns True: delete `<dir>/temp_log.csv`, `<dir>/power_log.csv`, `<dir>/spikes.csv` (and `<dir>/collector.pid`, `<dir>/spike_state` if present). When confirm returns False: delete nothing, still return `ok=True`. | `logs_cleared` or `reset_cancelled` | — |
| `/report` | Call `report_fn(**kwargs)` exactly once with **no** `range_spec` (or `range_spec=None`). Put its returned string in `result['report']`. | `report_shown` | `'report': str` |
| `/range SPEC` | Call `report_fn(range_spec=SPEC)` exactly once (`SPEC='7d'` for `/range 7d`). Put returned text in `result['report']`. | `range_report_shown` | `'report': str`, `'scrollable': True` |
| `/cron start` | Use `crontab_runner`: read with `['crontab','-l']`, ensure exactly one collector line exists (contains `collect.sh` and `--mode both`), write full content via `['crontab','-']` with the new text as the **`input=` kwarg**. Preserve all unrelated lines. Idempotent across repeated starts. | `cron_started` | — |
| `/cron stop` | Read with `['crontab','-l']`, remove lines containing `collect.sh`, preserve unrelated lines, write via `['crontab','-']` with `input=`. | `cron_stopped` | — |
| `/cron status` | Only call `['crontab','-l']`; **never** call `['crontab','-']`. `status` must contain `running` (case-insensitive) when a collector line exists, else `stopped`. | `cron_status` | — |
| `/mode X` | Update `config['mode']` and persist to `context['config_path']` (via `piscope_config.save_config`). `X ∈ {temp,power,both}`. | `mode_changed` | `'config': dict` |
| `/config set K V` | Use `piscope_config.set_config_value`; persist to `config_path`. | `config_changed` | `'config': dict` |
| `/pause` / `/resume` | Set `state['paused']` True / False. | `paused` / `resumed` | — |
| `/quit` | Set `state['quit'] = True`. | `quit` | — |
| `/help`, `/decision`, `/config` (no args) | Keep prior status behaviour; harmless no-op effects. | `help_shown` etc. | — |

Notes:
* `crontab_runner` is called as `crontab_runner(argv, input=None)` for `-l` and
  `crontab_runner(['crontab','-'], input=<full crontab text>)` for writes.
  The fake in tests records every call and keeps its own crontab state.
* Never call the real `subprocess.crontab`, never write to a real home/crontab
  when a context is provided.
* `execute_command` must remain importable with `textual` absent.

## 10. Rich detail overlays — `piscope_tui.build_detail(row, tab, ctx) -> dict`

```python
def build_detail(row: dict, tab: str, ctx: dict | None = None) -> dict: ...
```

`Enter` on a data row must show a structured overlay built by this helper
(wire it into the TUI loop: store `build_detail(row, tab, ctx)` in
`state['detail']`). The old behaviour of showing only `row['text']` is wrong.

### Injected `ctx` (exact keys)

```python
ctx = {
    'records':   [ {'timestamp': str, 'soc_temp': float, 'pmic_temp': float,
                    'load_1m': float, 'arm_freq': int, 'throttled': str,
                    'top_proc': str, 'top_cpu': float}, ... ],   # analyze.parse_csv_string records
    'raw_lines': [str, ...],   # raw temperature CSV data lines, parallel to records (no header)
    'spikes':    [ {'timestamp': str, 'soc_temp': float, 'baseline': float,
                    'delta': float, 'load': float, 'culprit_app': str,
                    'detail': str}, ... ],                       # analyze.parse_spikes_string records
}
```

### Overlay result (exact keys)

```python
{
    'title': str,                 # non-empty heading
    'value': str,                 # the row's displayed value
    'provenance': {
        'sample_index': int,      # index into ctx['records'] / ctx['raw_lines']
        'timestamp': str,         # ctx['records'][sample_index]['timestamp']
        'raw_line': str,          # ctx['raw_lines'][sample_index] (verbatim, whitespace-insensitive compare)
    },
    'spike_snapshot': None | [    # only for spike rows; None otherwise
        {'comm': str, 'cpu_pct': float | str, 'mem_pct': float | str,
         'args': str}, ...
    ],
}
```

Required behaviour:
* Always return a dict containing `'provenance'`; with `ctx=None` it may be an
  empty placeholder but must not raise.
* For `temp.min` / `temp.max` rows (ids must stay exactly these), `sample_index`
  must point at the record that actually produced the min / max `soc_temp`, so
  the test can verify `records[sample_index]['soc_temp']` equals 60.0 / 90.0
  respectively and `raw_line` matches the corresponding raw CSV line.
* For aggregate rows (e.g. `temp.avg`), any in-range `sample_index` is fine, but
  `raw_line` must be one of `ctx['raw_lines']`.
* Spike data rows have ids beginning with `perf.spike` (e.g. keep the existing
  `perf.spike.detail`). For those rows, parse every `spike['detail']` string of
  the form `opencode(80.0%|mem:12.3%|/usr/bin/opencode serve);ps(1.0%|mem:0.1%|ps -eo aux)`
  into `{'comm':'opencode','cpu_pct':80.0,'mem_pct':12.3,'args':'/usr/bin/opencode serve'}`
  and the second entry `{'comm':'ps', ... 'args':'ps -eo aux'}`. `cpu_pct` /
  `mem_pct` may be numbers or numeric strings (tests strip a trailing `%`).
  Snapshot must have at least two entries for such a detail string.
* Keep these row ids stable: `temp.min`, `temp.max`, `temp.avg`, and any
  `perf.spike*`.

## 11. `setup-cron.sh` (repo root, executable)

Counterpart to `stop.sh`, per design.md §13.

```bash
setup-cron.sh start|stop|status
```

* Must exist at the repo root, have a shebang, and be executable.
* Must invoke the `crontab` command **by name on PATH** or honour one of the env
  overrides `CRON_BIN` / `CRONTAB` / `CRON_CMD` pointing at an executable. The
  tests set all three to a fake and prepend its directory to `PATH`, so the real
  crontab is never touched.
* `start`: read `crontab -l`; if no collector line exists, append exactly one
  line containing `collect.sh` and `--mode both` (default log path is fine),
  then write the full content via `crontab -` (stdin). Must be idempotent and
  must preserve unrelated lines.
* `stop`: read `crontab -l`; remove only lines containing `collect.sh`; write
  remaining lines via `crontab -`; preserve unrelated lines. (May prompt; tests
  feed `y\n` on stdin.)
* `status`: read `crontab -l`, print collector status, **do not write** the
  crontab.
* All invocations of crontab must go through the injected command (never a
  hard-coded `/usr/bin/crontab`).

## 12. README.md (design.md §13)

The tests read `README.md` from the repo root. It must be rewritten so that:
* The **first heading** contains `PiScope` (case-insensitive).
* A **bare `piscope` line** appears (one-command workflow, no args/subcommand;
  an optional `$` prompt and `#` comment are accepted by the regex
  `^\$?\s*piscope\s*(#.*)?$`), and text describing "one command" / "no flags" /
  "single command" / "just run" / "opens everything" appears.
* All five tabs are mentioned, in order: Overview, Temperature, Performance,
  Battery, Config. Config must be explicitly described as last/fifth.
* `?` is documented as the command palette trigger; the literals `/test`,
  `/reset`, `/report`, `/range`, `/cron`, `/mode`, `/config`, `/quit` appear.
* `config.json` is documented (the `~/.config/piscope/config.json` path).
* All three modes `temp`, `power`, `both` are documented.
* `power`, `cost`, `battery` are documented, along with Wh / kWh units.

## 13. Audit coverage (already implemented; tests lock it in)

`test_design_gaps.py` verifies two design requirements that had no direct test:
* `power_read.read_power(env=...)` honours `wall_factor` from config at
  `PISCOPE_CONFIG_PATH` (returns `pmic_w`, `wall_w == pmic_w * wall_factor`,
  `wall_factor`).
* `piscope_cli.main(['view', LOG, '--range', '7d'])` calls
  `analyze.build_report(..., range_spec='7d')` (assert via mock.patch).
