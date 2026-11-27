"""PiStats TUI: five tabs, command bar and auto-scrolling row selection.

The pure helpers at the top of this module (``build_tab_rows``,
``move_selection``, ``scroll_to_section``, ...) intentionally have **no**
dependency on any TUI toolkit — they work when ``textual`` is absent.  The
interactive frontend at the bottom uses the Python standard-library ``curses``
module so the dashboard runs on a stock RPi 5 with zero extra installs.  If
``textual`` happens to be installed it is imported defensively but is not
required.
"""

import os
import re
import subprocess
import sys
import time

try:  # optional; the curses frontend below is the dependency-free default
    import textual  # noqa: F401
    HAS_TEXTUAL = True
except Exception:  # pragma: no cover - depends on the environment
    textual = None
    HAS_TEXTUAL = False

try:
    import pistats_config as _config
    import pistats_commands as _commands
    import pistats_dashboard as _dashboard
    import power_read
except ImportError:  # pragma: no cover - only when run outside the repo
    _config = None
    _commands = None
    _dashboard = None
    power_read = None

TABS = ['Overview', 'Temperature', 'Performance', 'Battery', 'Config']
DEFAULT_RENDER_INTERVAL_S = 1.0

KEY_BINDINGS = {
    '?': 'commands',
    '/': 'commands',
    'left': 'prev_tab',
    'right': 'next_tab',
    'tab': 'next_tab',
    'shift_tab': 'prev_tab',
    'up': 'move_up',
    'down': 'move_down',
    'home': 'home',
    'end': 'end',
    'page_up': 'page_up',
    'page_down': 'page_down',
    'enter': 'activate',
    'esc': 'cancel',
}


# ---------------------------------------------------------------------------
# Pure helpers (no TUI toolkit required)
# ---------------------------------------------------------------------------

def _row(identifier, label, text, kind='data'):
    return {'id': identifier, 'label': label, 'text': text, 'kind': kind}


# The Sampling sub-view edits these two live config keys.  ``sample_interval_s``
# is kept as the temperature interval for backwards compatibility.
SAMPLING_KEYS = ('power_sample_interval_s', 'sample_interval_s')
SAMPLING_LABELS = ('Power (battery)', 'Temperature')


def build_sampling_rows(config=None):
    """Rows shown inside the Config tab's single Sampling sub-view."""
    cfg = config or {}
    rows = []
    for key, label in zip(SAMPLING_KEYS, SAMPLING_LABELS):
        rows.append(_row('sampling.' + key.split('_')[0], label,
                         f'{key}: [ {_fmt(cfg.get(key), "1.0")} ] s'))
    return rows


def start_sampling_edit(state, rows):
    """Seed the inline editor from the highlighted Sampling option."""
    out = dict(state or {})
    try:
        idx = int(out.get('sub_index', 0))
    except (TypeError, ValueError):
        idx = 0
    idx = max(0, min(len(rows or []) - 1, idx))
    out['sub_index'] = idx
    out['sub_editing'] = True
    text = str((rows[idx] or {}).get('text', '')) if rows else ''
    buffer = ''
    if '[' in text and ']' in text:
        buffer = text.split('[', 1)[1].split(']', 1)[0].strip()
    out['buffer'] = buffer
    out['cursor'] = len(buffer)
    return out


def close_sampling(state):
    """Leave the Sampling sub-view and return to the Config tab."""
    out = dict(state or {})
    out['submenu'] = None
    out['sub_index'] = 0
    out['sub_editing'] = False
    out['buffer'] = ''
    return out


def _interval_seconds(value, default=1.0):
    """Coerce a configured interval to a positive float (defensive)."""
    try:
        return max(0.1, float(value))
    except (TypeError, ValueError):
        return default


def _fmt(value, default='—'):
    return default if value in (None, '') else str(value)


def _sparkline(values, width=12):
    """Tiny unicode bar sparkline for the Overview trend row."""
    bars = '▁▂▃▄▅▆▇█'
    nums = []
    for v in (values or []):
        try:
            nums.append(float(v))
        except (TypeError, ValueError):
            continue
    if not nums:
        return bars[0] * width
    lo, hi = min(nums), max(nums)
    if hi == lo:
        return bars[4] * width
    step = max(1, len(nums) // width)
    picked = nums[::step][:width]
    return ''.join(bars[min(7, int((v - lo) / (hi - lo) * 7))] for v in picked)


def build_tab_rows(tab, ctx=None):
    """Return the selectable rows for one tab.

    Every row is a dict with at least ``id``, ``label``, ``text`` and ``kind``
    (``'section'`` or ``'data'``).  ``ctx=None`` still returns the complete row
    set with placeholder values.
    """
    c = dict(ctx or {})
    rows = []
    add = rows.append

    if tab == 'Overview':
        # The main dashboard is drawn as two stacked history graphs followed
        # by the session energy/cost line.  These selectable rows carry the
        # values (and the per-second samples) used by the dedicated renderer.
        temp_now = _fmt(c.get('session_temp_now', c.get('soc_temp')), '—')
        power_now = _fmt(c.get('session_power_now', c.get('wall_w')), '—')
        add(_row('overview.temp', 'Temperature',
                 'SoC ' + temp_now + ' °C  ·  p95 (1 h) ' +
                 _fmt(c.get('p95_temp'), '—') + ' °C'))
        temp_graph = _row('overview.temp_graph', 'TEMPERATURE HISTORY',
                          'Temperature: ' + temp_now + ' °C · last 5 min')
        temp_graph['samples'] = list(c.get('session_temp_samples') or [])
        temp_graph['unit'] = '°C'
        add(temp_graph)
        add(_row('overview.power', 'Power',
                 'Wall ' + power_now +
                 ' W  ·  PMIC ' + _fmt(c.get('pmic_w'), '—') +
                 ' W  ·  Energy 1 h ' + _fmt(c.get('energy_1h'), '—') + ' Wh'))
        power_graph = _row('overview.power_graph', 'POWER HISTORY',
                           'Wall power: ' + power_now + ' W · last 5 min')
        power_graph['samples'] = list(c.get('session_power_samples') or [])
        power_graph['unit'] = 'W'
        add(power_graph)
        add(_row('overview.energy', 'Session energy / cost',
                 _fmt(c.get('session_energy_text'), '0.000 Wh') +
                 '  ·  cost ' + _fmt(c.get('session_cost'), '$0.000000') +
                 ' @ $' + _fmt(c.get('session_rate'), '0.13') + '/kWh' +
                 '  ·  ' + _fmt(c.get('session_elapsed'), '00:00:00') +
                 '  ·  ' + _fmt(c.get('session_samples'), '0') + ' samples'))
        add(_row('overview.status', 'OVERVIEW',
                 'Mode: ' + _fmt(c.get('mode'), 'both') +
                 '  ·  Render: ' + _fmt(c.get('render_interval_s'), '1 s') +
                 '  ·  Session: ' + _fmt(c.get('session_time'), '00:00:00'),
                 'section'))
        add(_row('overview.cost', 'Cost / Battery / Cron',
                 'Cost today ' + _fmt(c.get('cost'), '$0.000') +
                 '  ·  Battery ' + _fmt(c.get('battery_wh'), '45') +
                 ' Wh → ~' + _fmt(c.get('battery_hours'), '8.3') +
                 ' h  ·  Cron: ' + _fmt(c.get('cron_status'), 'STOPPED')))
        add(_row('overview.sparkline', 'Trend',
                 'temp  ' + _fmt(c.get('temp_spark'), '▁▂▃▅▇█▇▆▅▃▂') +
                 '     watt  ' + _fmt(c.get('watt_spark'), '▂▃▅▆█▇▆▅▃▂▁')))
        add(_row('overview.verdict', 'FINAL VERDICT',
                 'VERDICT: ' + _fmt(c.get('verdict'), 'No Cooler Needed') +
                 '  ·  max ' + _fmt(c.get('max'), '—') + ' °C  ·  ' +
                 _fmt(c.get('active_throttles'), '0') + ' throttles  ·  ' +
                 _fmt(c.get('spike_count'), '0') + ' spikes', 'section'))
        add(_row('overview.span', 'Log span',
                 'Log span: ' + _fmt(c.get('span'), '—') +
                 '  ·  Samples: ' + _fmt(c.get('samples'), '0')))
        return rows

    if tab == 'Temperature':
        add(_row('temp.stats', 'SoC TEMPERATURE STATS (°C)',
                 'SoC TEMPERATURE STATS (°C)  ·  Samples ' +
                 _fmt(c.get('samples'), '0'), 'section'))
        stats_labels = [
            ('min', 'Min', 'min'), ('avg', 'Average', 'average'),
            ('median', 'Median', 'median'), ('p95', '95th Percentile', '95'),
            ('max', 'Max', 'max'),
        ]
        for key, label, token in stats_labels:
            add(_row('temp.' + key, label,
                     f'{label:<16}: {_fmt(c.get(key), "—")} °C  ({token})'))
        add(_row('temp.derived', 'PMIC / CPU LOAD / ARM FREQUENCY',
                 'PMIC / CPU LOAD / ARM FREQUENCY', 'section'))
        add(_row('temp.pmic', 'PMIC',
                 'PMIC ' + _fmt(c.get('pmic_temp'), '—') +
                 ' °C (max ' + _fmt(c.get('pmic_max'), '—') + ')'))
        add(_row('temp.load', 'CPU load',
                 'CPU load ' + _fmt(c.get('load'), '—') +
                 ' (max ' + _fmt(c.get('load_max'), '—') + ')'))
        add(_row('temp.mhz', 'ARM MHz',
                 'ARM ' + _fmt(c.get('arm_freq'), '—') + ' MHz (max ' +
                 _fmt(c.get('arm_max'), '2400') + ')'))
        add(_row('temp.distribution', 'TEMPERATURE DISTRIBUTION',
                 'TEMPERATURE DISTRIBUTION', 'section'))
        for ident, label in [
            ('under_60', 'Under 60°C (Cool)'),
            ('60_70', '60°C - 70°C (Normal)'),
            ('70_80', '70°C - 80°C (Warm)'),
            ('80_85', '80°C - 85°C (Soft Throttle Zone)'),
            ('over_85', 'Over 85°C (Hard/Critical)'),
        ]:
            info = (c.get('temp_buckets') or {}).get(ident, {})
            add(_row('temp.band.' + ident, label,
                     f'{label:<34}: {_fmt(info.get("count"), "0"):>5} '
                     f'({_fmt(info.get("pct"), "0.00")}%)'))
        add(_row('temp.freq_distribution', 'ARM FREQUENCY DISTRIBUTION',
                 'ARM FREQUENCY DISTRIBUTION', 'section'))
        for ident, label in [
            ('2400_mhz', '2400 MHz (Max)'),
            ('1500_mhz', '1500 MHz (Throttled)'),
            ('1000_mhz', '1000 MHz (Throttled)'),
            ('750_mhz', '750 MHz (Throttled)'),
            ('600_mhz', '600 MHz (Min)'),
            ('other', 'Other/Unknown'),
        ]:
            info = (c.get('freq_buckets') or {}).get(ident, {})
            add(_row('temp.freq.' + ident, label,
                     f'{label:<30}: {_fmt(info.get("count"), "0"):>5} '
                     f'({_fmt(info.get("pct"), "0.00")}%)'))
        return rows

    if tab == 'Performance':
        add(_row('perf.impact', 'PERFORMANCE IMPACT ANALYSIS',
                 'PERFORMANCE IMPACT ANALYSIS', 'section'))
        add(_row('perf.avg_freq', 'Average Frequency',
                 'Average Frequency: ' + _fmt(c.get('avg_freq'), '—') +
                 ' MHz / 2400 MHz max'))
        add(_row('perf.loss', 'Performance Loss',
                 'Performance Loss: ' + _fmt(c.get('perf_loss'), '—') +
                 '% slower than max capability'))
        add(_row('perf.speed', 'Effective Speed',
                 'Effective Speed: ' + _fmt(c.get('effective_speed'), '—') +
                 '% of maximum'))
        add(_row('perf.time', 'Time Impact',
                 'Time Impact: 1-hour task → +~' +
                 _fmt(c.get('time_impact'), '—') + ' minutes'))
        add(_row('perf.throttling', 'THROTTLING (vcgencmd decoded)',
                 'THROTTLING (vcgencmd get_throttled decoded)', 'section'))
        add(_row('perf.active', 'Active throttled',
                 'Active throttled: ' + _fmt(c.get('active_throttles'), '0') +
                 '  Past throttled: ' + _fmt(c.get('past_throttles'), '0') +
                 '  Soft-temp limit: ' + _fmt(c.get('soft_temp'), '0')))
        add(_row('perf.uv', 'Under-voltage',
                 'Under-voltage now / past: ' +
                 _fmt(c.get('under_voltage'), '0 / 0')))
        add(_row('perf.procs', 'TOP PROCESSES', 'TOP PROCESSES', 'section'))
        for proc, info in (c.get('top_procs') or [('none', {})])[:5]:
            add(_row('perf.proc.' + str(proc), str(proc),
                     f'{proc:<20}: {_fmt(info.get("count"), "0")} samples '
                     f'({_fmt(info.get("pct"), "0.0")}%)'))
        add(_row('perf.spikes', 'SPIKE ATTRIBUTION',
                 'SPIKE ATTRIBUTION (5°C+ jumps, ' +
                 _fmt(c.get('spike_count'), '0') + ' events)', 'section'))
        add(_row('perf.spike.detail', 'Spikes',
                 _fmt(c.get('spike_note'), 'No spikes recorded yet.')))
        add(_row('perf.verdict', 'FINAL VERDICT', 'FINAL VERDICT', 'section'))
        add(_row('perf.verdict.text', 'Verdict',
                 _fmt(c.get('verdict'), 'No Cooler Needed')))
        for reason in (c.get('reasons') or [])[:6]:
            add(_row('perf.reason.' + reason[:12], 'Reason', '  - ' + reason))
        return rows

    if tab == 'Battery':
        add(_row('bat.live', 'LIVE POWER', 'LIVE POWER', 'section'))
        add(_row('bat.pmic', 'PMIC rail sum',
                 'PMIC rail sum ' + _fmt(c.get('pmic_w'), '—') +
                 ' W   wall est. ' + _fmt(c.get('wall_w'), '—') +
                 ' W  (factor ' + _fmt(c.get('wall_factor'), '1.0') + ')'))
        add(_row('bat.rails', 'RAIL BREAKDOWN (A × V per rail)',
                 'RAIL BREAKDOWN (A × V per rail)', 'section'))
        for rail, watts in (c.get('rails') or {}).items():
            add(_row('bat.rail.' + str(rail), str(rail),
                     f'{rail:<12} {watts:6.2f} W'))
        add(_row('bat.total', 'TOTAL (PMIC)',
                 'TOTAL (PMIC) ' + _fmt(c.get('pmic_w'), '—') +
                 ' W  (EXT5V_V / BATT_V excluded: voltage-only)'))
        add(_row('bat.energy', 'ENERGY', 'ENERGY', 'section'))
        add(_row('bat.energy.detail', 'Energy',
                 'Energy 1 h: ' + _fmt(c.get('energy_1h'), '—') +
                 ' Wh   2 h: ' + _fmt(c.get('energy_2h'), '—') +
                 ' Wh   full log: ' + _fmt(c.get('energy_full'), '—') + ' Wh'))
        add(_row('bat.cost', 'COST', 'COST', 'section'))
        add(_row('bat.cost.detail', 'Cost',
                 'Cost @ $' + _fmt(c.get('rate'), '0.13') + '/kWh → ' +
                 _fmt(c.get('cost'), '$0.000')))
        add(_row('bat.runtime', 'BATTERY RUNTIME',
                 'BATTERY RUNTIME', 'section'))
        add(_row('bat.runtime.detail', 'Battery',
                 'Battery ' + _fmt(c.get('battery_wh'), '45') + ' Wh → ~' +
                 _fmt(c.get('avg_hours'), '—') + ' h @ avg · ~' +
                 _fmt(c.get('conservative_hours'), '—') +
                 ' h @ p95 (conservative)'))
        return rows

    if tab == 'Config':
        add(_row('config.header', 'CONFIG', 'CONFIG — Enter edits · Esc cancels',
                 'section'))
        add(_row('config.sampling', 'SAMPLING',
                 f'SAMPLING  power_sample_interval_s='
                 f'{_fmt(c.get("power_sample_interval_s"), "1.0")}s · '
                 f'sample_interval_s={_fmt(c.get("sample_interval_s"), "1.0")}s'
                 f'  → Enter', 'data'))
        keys = sorted(k for k in (_config.LIVE_KEYS if _config else [])
                      if k not in SAMPLING_KEYS)
        for key in keys:
            value = c.get(key, (_config.DEFAULT_CONFIG.get(key)
                                if _config else '—'))
            add(_row('config.' + key, key,
                     f'{key:<22}: [ {_fmt(value)} ]'))
        add(_row('config.cron.status', 'Cron status',
                 'Cron status: ' + _fmt(c.get('cron_status'), 'STOPPED') +
                 '  → /cron stop to disable', 'section'))
        return rows

    return []


def move_selection(rows, index, scroll, viewport_height, direction):
    """Move the highlighted row and scroll the minimum amount to keep it visible.

    Returns ``(new_index, new_scroll)``.  The selected row always satisfies
    ``new_scroll <= new_index <= new_scroll + viewport_height - 1``.
    """
    count = len(rows)
    if count == 0:
        return 0, 0
    height = max(1, int(viewport_height))
    idx = max(0, min(count - 1, int(index)))
    top = max(0, min(max(0, count - height), int(scroll)))

    if direction == 'up':
        idx -= 1
    elif direction == 'down':
        idx += 1
    elif direction == 'home':
        idx = 0
    elif direction == 'end':
        idx = count - 1
    elif direction == 'page_up':
        idx -= height
    elif direction == 'page_down':
        idx += height
    idx = max(0, min(count - 1, idx))

    if idx < top:
        top = idx
    elif idx > top + height - 1:
        top = idx - height + 1
    top = max(0, min(max(0, count - height), top))
    return idx, top


def scroll_to_section(rows, index, scroll, viewport_height):
    """Anchor ``index``'s section at the top, but keep the row visible.

    The section title is placed at the top of the viewport when that does not
    hide the selection.  If it would, the smallest scroll that still shows the
    selected row is used instead (so PgUp/PgDn can never anchor the row away).
    """
    count = len(rows)
    if count == 0:
        return 0
    height = max(1, int(viewport_height))
    idx = max(0, min(count - 1, int(index)))
    anchored = 0
    if rows[idx].get('kind') == 'section':
        anchored = idx
    else:
        for pos in range(idx - 1, -1, -1):
            if rows[pos].get('kind') == 'section':
                anchored = pos
                break
    # Minimum legal scroll that still displays the selected row.
    anchored = max(anchored, idx - height + 1)
    anchored = max(0, min(max(0, count - height), anchored))
    return anchored


def resolve_enter_action(row, tab):
    """Return ``'edit'`` (Config data row), ``'detail'`` or ``None``."""
    if not row or row.get('kind') != 'data':
        return None
    if row.get('id') == 'config.sampling':
        return 'submenu'
    return 'edit' if tab == 'Config' else 'detail'


def cancel_edit(state):
    """Clear an in-progress edit while preserving all other state keys."""
    out = dict(state or {})
    out['editing'] = False
    out['buffer'] = ''
    return out


def resolve_palette_enter(buffer, filtered=None, cmd_index=0, parse_fn=None):
    """Decide which command Enter runs in the command palette.

    A complete, valid command typed in the buffer is honoured verbatim (design
    §12).  Otherwise the buffer is treated as a filter fragment and the
    highlighted entry of ``filtered`` is returned.  The index is always
    clamped, so an empty filter can never raise ``IndexError``.
    """
    text = str(buffer or '').strip()
    parser = parse_fn
    if parser is None and _commands is not None:
        parser = _commands.parse_command
    if text and parser is not None:
        try:
            parsed = parser(text)
        except Exception:
            parsed = None
        if parsed is True or (isinstance(parsed, dict) and parsed.get('valid')):
            return text

    shown = list(filtered or [])
    if not shown:
        return '?'
    try:
        idx = int(cmd_index)
    except (TypeError, ValueError):
        idx = 0
    idx = max(0, min(len(shown) - 1, idx))
    entry = shown[idx]
    name = entry.get('name') if isinstance(entry, dict) else entry
    return str(name or '?')


def resolve_key_action(key, curses_mod=None):
    """Map a raw key code or symbolic key name to a ``KEY_BINDINGS`` action.

    ``curses_mod`` may be a stub exposing the ``KEY_*`` constants, so this
    helper is testable without importing curses.  TAB and Shift+Tab are kept
    distinct (``next_tab`` / ``prev_tab``) instead of being lumped in with the
    left-arrow action.
    """
    mod = curses_mod if curses_mod is not None else globals().get('_curses')
    if key is None:
        return None

    if isinstance(key, str):
        # Literal control characters must be matched against the raw string
        # first: `.strip()` collapses '\t', '\n' and '\r' to '' (§14.5).
        if key == '\t':
            return KEY_BINDINGS['tab']
        if key in ('\n', '\r'):
            return KEY_BINDINGS['enter']
        token = key.strip().lower()
        if token in ('\t', 'tab'):
            return KEY_BINDINGS['tab']
        if token in ('shift_tab', 'btab', 'shift+tab'):
            return KEY_BINDINGS['shift_tab']
        if token in ('enter', 'newline', 'return', '\n', '\r'):
            return KEY_BINDINGS['enter']
        if token in ('esc', 'escape'):
            return KEY_BINDINGS['esc']
        return KEY_BINDINGS.get(token)

    if key == 9:
        return KEY_BINDINGS['tab']

    if mod is not None:
        def code(name):
            return getattr(mod, name, None)

        if code('KEY_BTAB') is not None and key == code('KEY_BTAB'):
            return KEY_BINDINGS['shift_tab']
        for attr, action in (
            ('KEY_LEFT', 'left'), ('KEY_RIGHT', 'right'),
            ('KEY_UP', 'up'), ('KEY_DOWN', 'down'),
            ('KEY_HOME', 'home'), ('KEY_END', 'end'),
            ('KEY_PPAGE', 'page_up'), ('KEY_NPAGE', 'page_down'),
        ):
            value = code(attr)
            if value is not None and key == value:
                return KEY_BINDINGS[action]

    if key in (10, 13):
        return KEY_BINDINGS['enter']
    if key == 27:
        return KEY_BINDINGS['esc']
    if key in (ord('?'), ord('/')):
        return KEY_BINDINGS[chr(key)]
    return None


def build_palette_view(filtered, cmd_index=0, limit=4):
    """Return the drawable command lines and the visible selection window."""
    commands = list(filtered or [])
    if not commands:
        return {'lines': [], 'index': 0, 'start': 0}
    try:
        idx = int(cmd_index)
    except (TypeError, ValueError):
        idx = 0
    idx = max(0, min(len(commands) - 1, idx))
    window = max(1, int(limit))
    start = min(max(0, idx - window + 1),
                max(0, len(commands) - window))
    lines = []
    for pos in range(start, min(len(commands), start + window)):
        cmd = commands[pos]
        if isinstance(cmd, dict):
            name = cmd.get('name', '')
            description = cmd.get('description', '')
        else:
            name, description = str(cmd), ''
        marker = '>' if pos == idx else ' '
        lines.append(f'{marker} {name}  {description}'.rstrip())
    return {'lines': lines, 'index': idx, 'start': start}


def start_config_edit(state, rows):
    """Seed an inline Config editor with the selected row's current value."""
    out = dict(state or {})
    out['editing'] = True
    text = ''
    try:
        idx = int(out.get('index', 0))
    except (TypeError, ValueError):
        idx = 0
    if 0 <= idx < len(rows or []):
        text = str((rows[idx] or {}).get('text', ''))
    buffer = ''
    if '[' in text and ']' in text:
        buffer = text.split('[', 1)[1].split(']', 1)[0].strip()
    out['buffer'] = buffer
    out['cursor'] = len(buffer)
    return out


def handle_escape(state):
    """Esc opens/focuses the command bar as a real state transition."""
    out = dict(state or {})
    out['palette'] = True
    out['command_focus'] = True
    out['buffer'] = ''
    out['cmd_index'] = 0
    return out


# ---------------------------------------------------------------------------
# Rich detail overlays (see EXPECTED_API.md §10)
# ---------------------------------------------------------------------------

def _parse_spike_detail(detail):
    """Parse ``comm(cpu%|mem:x%|args);...`` into process snapshot dicts."""
    entries = []
    for chunk in str(detail or '').split(';'):
        chunk = chunk.strip()
        if not chunk or '(' not in chunk:
            continue
        comm, rest = chunk.split('(', 1)
        fields = rest.rstrip(')').split('|')

        def _numeric(text):
            text = str(text or '').strip()
            try:
                return float(text)
            except (TypeError, ValueError):
                return text

        cpu = _numeric(fields[0].strip().rstrip('%')) if fields else ''
        mem = ''
        if len(fields) > 1:
            token = fields[1].strip()
            if token.lower().startswith('mem:'):
                token = token[4:]
            mem = _numeric(token.rstrip('%'))
        args = '|'.join(fields[2:]).strip() if len(fields) > 2 else ''
        entries.append({'comm': comm.strip(), 'cpu_pct': cpu,
                        'mem_pct': mem, 'args': args})
    return entries


def _provenance(row, ctx):
    """Point at the sample that produced a row's value (plus its raw line)."""
    records = (ctx or {}).get('records') or []
    raw_lines = (ctx or {}).get('raw_lines') or []
    count = len(records)
    idx = 0
    if count:
        row_id = str((row or {}).get('id', ''))
        if row_id == 'temp.min':
            idx = min(range(count), key=lambda i: float(
                records[i].get('soc_temp') or 0))
        elif row_id == 'temp.max':
            idx = max(range(count), key=lambda i: float(
                records[i].get('soc_temp') or 0))
        else:
            idx = count - 1  # aggregates resolve to the most recent sample
        idx = max(0, min(count - 1, idx))
    raw_line = raw_lines[min(idx, len(raw_lines) - 1)] if raw_lines else ''
    timestamp = str(records[idx].get('timestamp', '')) if count else ''
    return {'sample_index': idx, 'timestamp': timestamp, 'raw_line': raw_line}


def build_detail(row, tab, ctx=None):
    """Build the structured ``Enter`` overlay for a selectable row."""
    row = row or {}
    label = str(row.get('label') or '')
    title = label or (f'{tab} detail' if tab else 'PiStats detail')
    value = str(row.get('text', ''))

    snapshot = None
    if str(row.get('id', '')).startswith('perf.spike'):
        snapshot = []
        for spike in (ctx or {}).get('spikes') or []:
            snapshot.extend(_parse_spike_detail(spike.get('detail')))

    return {
        'title': title,
        'value': value,
        'provenance': _provenance(row, ctx),
        'spike_snapshot': snapshot,
    }


# ---------------------------------------------------------------------------
# Context gathering (shared by the interactive frontend and detail overlays)
# ---------------------------------------------------------------------------

DEFAULT_TEMP_LOG = os.path.join(
    os.path.expanduser('~'), 'projects', 'temp', 'temp_log.csv')
DEFAULT_POWER_LOG = os.path.join(
    os.path.expanduser('~'), 'projects', 'temp', 'power_log.csv')


def _first_log():
    return DEFAULT_TEMP_LOG if os.path.exists(DEFAULT_TEMP_LOG) else 'temp_log.csv'


def build_context(config=None):
    """Build a best-effort snapshot used to populate tab rows.

    Never raises: missing logs / hardware simply produce placeholder values.
    """
    cfg = dict(config or {})
    if _config:
        try:
            cfg = _config.load_config()
        except Exception:
            pass
    ctx = dict(cfg)
    try:
        import analyze
        import power_read

        log_path = _first_log()
        records = analyze.parse_csv_file(log_path)
        # Detail overlays need the producing samples and their raw CSV lines.
        ctx['records'] = records
        try:
            with open(log_path, 'r', newline='') as f:
                ctx['raw_lines'] = [line for line in f.read().splitlines()[1:]
                                    if line.strip()]
        except OSError:
            ctx['raw_lines'] = []
        if records:
            temps = [r['soc_temp'] for r in records]
            ctx['temp_spark'] = _sparkline(temps)
            stats = analyze.calculate_stats(temps) or {}
            ctx.update({
                'samples': stats.get('count', 0),
                'min': f"{stats.get('min', 0):.1f}",
                'avg': f"{stats.get('avg', 0):.1f}",
                'median': f"{stats.get('median', 0):.1f}",
                'p95': f"{stats.get('p95', 0):.1f}",
                'p95_temp': f"{stats.get('p95', 0):.1f}",
                'max': f"{stats.get('max', 0):.1f}",
                'soc_temp': f"{records[-1]['soc_temp']:.1f}",
                'pmic_temp': f"{records[-1]['pmic_temp']:.1f}" if records[-1]['pmic_temp'] else '—',
                'load': f"{records[-1]['load_1m']:.2f}",
                'arm_freq': records[-1]['arm_freq'],
            })
            pmics = [r['pmic_temp'] for r in records if r['pmic_temp'] is not None]
            loads = [r['load_1m'] for r in records]
            freqs = [r['arm_freq'] for r in records]
            if pmics:
                ctx['pmic_max'] = f'{max(pmics):.1f}'
            ctx['load_max'] = f'{max(loads):.2f}' if loads else '—'
            ctx['arm_max'] = max(freqs) if freqs else '2400'
            ctx['temp_buckets'] = analyze.bucket_temperatures(temps)
            ctx['freq_buckets'] = analyze.bucket_frequencies(freqs)
            perf = analyze.calculate_performance_loss(freqs)
            if perf:
                ctx['avg_freq'] = f"{perf['avg_freq_mhz']:.0f}"
                ctx['perf_loss'] = f"{perf['performance_loss_pct']:.1f}"
                ctx['effective_speed'] = f"{perf['effective_speed_factor']:.1%}"
                ctx['time_impact'] = f"{60 * (1 / perf['effective_speed_factor'] - 1):.0f}"
            ctx['active_throttles'] = analyze.summarize_throttling(records)['active_throttle_count']
            counter = {}
            for r in records:
                counter[r['top_proc']] = counter.get(r['top_proc'], 0) + 1
            ctx['top_procs'] = [
                (p, {'count': n, 'pct': f'{n / len(records) * 100:.1f}'})
                for p, n in sorted(counter.items(), key=lambda kv: -kv[1])[:5]
            ]
            verdict = analyze.determine_verdict(
                stats, analyze.summarize_throttling(records),
                analyze.bucket_temperatures(temps))
            ctx['verdict'] = verdict['decision']
            ctx['reasons'] = verdict['reasons']
            ctx['span'] = f"{records[0]['timestamp']} → {records[-1]['timestamp']}"

        power_path = os.environ.get('PISTATS_POWER_LOG') or DEFAULT_POWER_LOG
        precords = analyze.parse_power_log(power_path)
        live = power_read.read_power()
        ctx['rails'] = live['rails']
        ctx['pmic_w'] = f"{live['pmic_w']:.2f}"
        ctx['wall_w'] = f"{live['wall_w']:.2f}"
        ctx['wall_factor'] = cfg.get('wall_factor', 1.0)
        ctx['rate'] = cfg.get('rate_per_kwh', 0.13)
        ctx['battery_wh'] = cfg.get('battery_wh', 45.0)
        ctx['cron_status'] = os.environ.get('PISTATS_CRON_STATUS', 'STOPPED')
        if precords:
            ctx['energy_full'] = f"{analyze.energy_wh(precords):.2f}"
            wall_w = [r['wall_w'] for r in precords]
            ctx['watt_spark'] = _sparkline(wall_w)
            wstats = analyze.calculate_stats(wall_w)
            estimate = analyze.battery_estimate(
                cfg.get('battery_wh', 45.0), wstats['avg'], wstats['p95'])
            ctx['energy_1h'] = f"{analyze.energy_wh(precords, 60):.2f}"
            ctx['energy_2h'] = f"{analyze.energy_wh(precords, 120):.2f}"
            ctx['battery_hours'] = f"{estimate['avg_hours']:.1f}"
            ctx['cost'] = f"${analyze.cost_dollars(analyze.energy_wh(precords), cfg.get('rate_per_kwh', 0.13)):.5f}"
            ctx['avg_hours'] = f"{estimate['avg_hours']:.1f}"
            ctx['conservative_hours'] = f"{estimate['conservative_hours']:.1f}"
        spikes = analyze.parse_spikes_file(
            os.path.join(os.path.dirname(log_path), 'spikes.csv'))
        ctx['spikes'] = spikes
        ctx['spike_count'] = len(spikes)
        ctx['spike_note'] = analyze.frequency_weighted_note(spikes, len(records))
    except Exception:
        pass
    return ctx


# ---------------------------------------------------------------------------
# Interactive curses frontend (stdlib only)
# ---------------------------------------------------------------------------

def _command_context(state, cfg):
    """Build the injected context used by the real TUI command handler.

    Every collaborator is the real (stdlib) implementation here; the headless
    ``execute_command`` tests inject fakes instead, so no test ever spawns a
    real process or edits a real crontab.
    """
    cfgmod = _config
    data_dir = (os.environ.get('PISTATS_DATA_DIR') or
                os.path.dirname(DEFAULT_TEMP_LOG))

    def confirm(prompt):
        # Two-step confirmation keeps /reset from ever being destructive by
        # accident while still allowing the real TUI to clear the logs.
        if not state.get('confirm_reset'):
            state['confirm_reset'] = True
            return False
        state.pop('confirm_reset', None)
        return True

    def spawner(argv, **kwargs):
        return subprocess.Popen(
            argv, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            start_new_session=True)

    def crontab_runner(argv, input=None, **kwargs):
        return subprocess.run(argv, input=input, capture_output=True,
                              text=True, timeout=10)

    def report_fn(**kwargs):
        import analyze
        power = os.environ.get('PISTATS_POWER_LOG') or DEFAULT_POWER_LOG
        if not os.path.exists(power):
            power = None
        return analyze.build_report(_first_log(), power_log_file=power,
                                    **kwargs)

    return {
        'dir': data_dir,
        'config': cfg,
        'config_path': cfgmod.config_path() if cfgmod else None,
        'scripts': os.path.dirname(os.path.abspath(__file__)),
        'state': state,
        'confirm': confirm,
        'process_spawner': spawner,
        'crontab_runner': crontab_runner,
        'report_fn': report_fn,
    }


def _command_action(command, state, cfg):
    """Execute a parsed command through ``execute_command``; return status text."""
    if not command or not command.get('valid'):
        return command.get('error', 'Invalid command') if command else 'Invalid command'
    if _commands is None or not hasattr(_commands, 'execute_command'):
        return command.get('name', 'Unknown command')

    result = _commands.execute_command(command, _command_context(state, cfg))
    if not result.get('ok'):
        return result.get('status') or 'Command failed'

    updated = result.get('config')
    if isinstance(updated, dict):
        cfg.update(updated)

    name = command.get('name')
    report = result.get('report')
    if isinstance(report, str):
        state['report'] = report
        state['detail'] = {
            'title': 'Report' + (f" ({result.get('range_spec')})"
                                 if result.get('range_spec') else ''),
            'value': report[:400],
            'provenance': {'sample_index': 0, 'timestamp': '', 'raw_line': ''},
            'spike_snapshot': None,
        }
    if name == '/decision':
        state['detail'] = {
            'title': 'Verdict',
            'value': build_context(cfg).get('verdict', 'No Cooler Needed'),
            'provenance': {'sample_index': 0, 'timestamp': '', 'raw_line': ''},
            'spike_snapshot': None,
        }
    if name == '/config':
        state['tab_index'] = TABS.index('Config')
        state['index'] = state['scroll'] = 0
    return result.get('status', '')


def _render(stdscr, state, rows, cfg, filtered=None, status=''):
    """Draw a single, width-aware frame with one consistent right edge.

    Layout (top to bottom)::

        ╔═ PiStats · TAB ───────────────────────────────────────╗
        ║ ◀ Overview │ … │ Config ▶                            ║
        ╟────────────────────────────────────────────────────────╢
        ║ header · … · clock (right-aligned)                     ║
        ║ content rows …                                        ║
        ╟────────────────────────────────────────────────────────╢
        ╚ pistats › …                 shortcut hint (right)     ╝

    Every row uses the exact same right-edge column.  Curses cannot safely
    paint the very last column of a terminal (writing there wraps the cursor),
    so the frame occupies columns ``0 .. width-2`` and the right border sits
    at column ``width-2``.  That is also the widest a line may be for the
    stdscr contract used by the alignment tests.
    """
    height, width = stdscr.getmaxyx()
    if height < 4 or width < 20:
        return
    stdscr.erase()

    right_x = width - 2          # the one and only right-edge column
    text_x = 2                   # one blank cell after the left border
    text_max = right_x - text_x  # usable columns for a row's text

    def _put(y, x, text, n, attr=0):
        if 0 <= y < height and 0 <= x < width and n > 0:
            try:
                stdscr.addnstr(y, x, str(text)[:max(0, n)], max(0, n), attr)
            except Exception:
                pass

    def _clip(text, limit):
        """Truncate with an ellipsis, preferring a word boundary when possible."""
        text = str(text)
        if limit <= 0:
            return ''
        if len(text) <= limit:
            return text
        if limit == 1:
            return '…'
        head = text[:limit - 1]
        boundary = head.rfind(' ')
        if boundary >= max(1, limit // 2):
            return head[:boundary].rstrip() + '…'
        return head + '…'

    def _lined(y, text, attr=0, left='║', right='║', pad=True):
        """Draw one bordered row: left frame, text, right frame."""
        if not (0 <= y < height):
            return
        text = _clip(text, text_max)
        if pad:
            text = text.ljust(text_max)
        _put(y, 0, left, 1, _style('frame'))
        _put(y, text_x, text, text_max, attr)
        _put(y, right_x, right, 1, _style('frame'))

    def _separator(y):
        if 0 <= y < height:
            _put(y, 0, '╟' + '─' * max(0, width - 3) + '╢', width - 1,
                 _style('frame'))

    # --- row 0: top border, with the active tab as its title ---------------
    tab_index = state['tab_index']
    tab_name = TABS[tab_index] if 0 <= tab_index < len(TABS) else TABS[0]
    title = _clip(f'╔═ PiStats · {tab_name.upper()} ', width - 2)
    _put(0, 0, title + '─' * max(0, width - 2 - len(title)) + '╗', width - 1,
         _style('header', bold=True))

    # --- row 1: tab bar (active tab keeps its leading ◀; add a trailing ▶) -
    active = [('◀ ' + t if i == tab_index else t) for i, t in enumerate(TABS)]
    chosen_sep = ' │ '
    for sep in (' │ ', '│'):
        candidate = ' ' + sep.join(active) + ' ▶ '
        if len(candidate) <= text_max:
            chosen_sep = sep
            break
    else:
        chosen_sep = '│'
    # Draw the bar in segments so the active tab can be highlighted without
    # writing any character twice.
    _put(1, 0, '║', 1, _style('frame'))
    bar_parts = []
    for i, label in enumerate(active):
        if i:
            bar_parts.append((chosen_sep, 'tab'))
        bar_parts.append((label, 'tab_active' if i == tab_index else 'tab'))
    bar_parts.append((' ▶ ', 'tab'))
    bar_x = 0
    for text, role in bar_parts:
        if bar_x >= text_max:
            break
        n = min(len(text), text_max - bar_x)
        _put(1, text_x + bar_x, text, n,
             _style(role, bold=(role == 'tab_active')))
        bar_x += len(text)
    _put(1, right_x, '║', 1, _style('frame'))

    _separator(2)

    # --- row 3: header, with the clock right-aligned at the right border ---
    interval = cfg.get('render_interval_s', DEFAULT_RENDER_INTERVAL_S)
    mode = cfg.get('mode', 'both')
    live = 'PAUSED' if state.get('paused') else 'LIVE'
    session = state.get('session_time', '')
    lefts = [
        f'PiStats · {live} · mode={mode} · render {interval}s'
        + (f' · session {session}' if session else ''),
        f'PiStats · {live} · mode={mode} · render {interval}s',
        f'PiStats · {live} · mode={mode}',
        f'PiStats · {live}',
        'PiStats',
    ]
    clocks = [time.strftime('%Y-%m-%d %H:%M:%S'),
              time.strftime('%H:%M:%S'), time.strftime('%H:%M')]
    header = _clip('PiStats · ' + clocks[-1], text_max)
    for clock in clocks:
        for left in lefts:
            if len(left) + 2 + len(clock) <= text_max:
                header = left + ' ' * (text_max - len(left) - len(clock)) + clock
                break
        else:
            continue
        break
    _lined(3, header, _style('header', bold=True), pad=False)

    # --- rows 4 .. height-3: content ---------------------------------------
    top = state['scroll']
    visible = max(1, height - 6)

    def _draw_data_row(y, pos, row):
        """Draw one selectable row (shared by the list and dashboard views)."""
        marker = '>' if pos == state['index'] else ' '
        prefix = f"{marker} {row['label']:<24} "
        text = str(row['text'])
        avail = text_max - len(prefix)
        if avail > 1:
            content = prefix + _clip(text, avail).rjust(avail)
        else:
            content = _clip(prefix + text, text_max)
        if pos == state['index']:
            reverse = 1 if _curses_A_REVERSE is None else _curses_A_REVERSE
            _lined(y, content, reverse)
        elif avail > 1:
            _lined(y, content, _style('label'))
            value = content[len(prefix):]
            if value:
                _put(y, text_x + len(prefix), value, len(value),
                     _style(_value_style(row), bold=True))
        else:
            _lined(y, content, _style('value'))

    if state.get('submenu') == 'sampling':
        # Config → SAMPLING: a compact panel replaces the tab's content rows.
        sub_rows = build_sampling_rows(cfg)
        try:
            sub_index = max(0, min(len(sub_rows) - 1,
                                   int(state.get('sub_index', 0))))
        except (TypeError, ValueError):
            sub_index = 0
        _lined(4, ' SAMPLING · Enter edit · Esc back',
               _style('header', bold=True))
        editing = bool(state.get('sub_editing'))
        shown = str(state.get('buffer', ''))
        try:
            caret = max(0, min(len(shown), int(state.get('cursor', len(shown)))))
        except (TypeError, ValueError):
            caret = len(shown)
        if editing:
            shown = shown[:caret] + '_' + shown[caret:]
        for offset, sub_row in enumerate(sub_rows):
            y = 5 + offset
            if y >= height - 1:
                break
            marker = '>' if offset == sub_index else ' '
            prefix = f"{marker} {sub_row['label']:<16} "
            text = str(sub_row['text'])
            if editing and offset == sub_index:
                # Swap the displayed value for "[ buffer_ ] s" while editing.
                head, sep, tail = text.partition('[ ')
                if sep:
                    _old, close_sep, suffix = tail.partition(' ]')
                    if close_sep:
                        text = head + '[ ' + shown + ' ]' + suffix
            avail = text_max - len(prefix)
            if avail > 1:
                content = prefix + _clip(text, avail).rjust(avail)
            else:
                content = _clip(prefix + text, text_max)
            if offset == sub_index:
                reverse = 1 if _curses_A_REVERSE is None else _curses_A_REVERSE
                _lined(y, content, reverse)
            else:
                _lined(y, content, _style('label'))
    elif tab_name == 'Overview' and _dashboard is not None:
        # Dedicated main dashboard: current temp + history graph, then the
        # same for power, then the session energy / cost line.  Two stacked
        # plots share the available height and shrink (or collapse to a
        # labelled sparkline) on small terminals.
        by_id = {row.get('id'): (pos, row) for pos, row in enumerate(rows)}
        drawn = set()
        cursor = 4
        bottom = height - 2
        plot_h = max(0, min(8, (visible - 7) // 2))
        compact = plot_h < 2

        def _draw_row(identifier):
            nonlocal cursor
            entry = by_id.get(identifier)
            if entry and cursor < bottom:
                _draw_data_row(cursor, entry[0], entry[1])
                drawn.add(identifier)
                cursor += 1

        def _draw_graph(identifier):
            nonlocal cursor
            entry = by_id.get(identifier)
            if not entry or cursor >= bottom:
                return
            pos, row = entry
            samples = row.get('samples') or []
            unit = row.get('unit', '')
            label = ('> ' if pos == state['index'] else '  ') + row['label']
            if compact:
                values = [v for _t, v in samples if v is not None]
                spark = _sparkline(values, width=max(6, text_max // 3))
                text = (label + '  ' + spark + '  ' +
                        ('last 5m' if values else 'waiting for samples'))
                _lined(cursor, _clip(text, text_max), _style('label'))
                cursor += 1
                return
            lines = _dashboard.render_graph(
                samples, width=text_max - 1, height=plot_h,
                title=label, unit=unit,
                current=(samples[-1][1] if samples else None),
                window_s=_dashboard.GRAPH_WINDOW_S,
                now=(state.get('session_now') or
                     (samples[-1][0] if samples else None)),
                nonnegative=(unit == 'W'))
            for line in lines:
                if cursor >= bottom:
                    break
                _lined(cursor, line, _style('value'))
                cursor += 1
            drawn.add(identifier)

        _draw_row('overview.temp')
        _draw_graph('overview.temp_graph')
        _draw_row('overview.power')
        _draw_graph('overview.power_graph')
        _draw_row('overview.energy')
        for pos in range(top, len(rows)):
            if cursor >= bottom:
                break
            row = rows[pos]
            if row.get('id') not in drawn:
                _draw_data_row(cursor, pos, row)
                cursor += 1
    else:
        for offset in range(visible):
            pos = top + offset
            if pos >= len(rows):
                break
            _draw_data_row(4 + offset, pos, rows[pos])

    if filtered is not None:
        view = build_palette_view(filtered, state.get('cmd_index', 0))
        base = max(4, height - 6)
        _lined(base, ' COMMANDS (Esc to close):', _style('header', bold=True))
        for i, line in enumerate(view['lines']):
            if base + 1 + i < height - 1:
                _lined(base + 1 + i, line, _style('value'))

    detail = state.get('detail')
    if isinstance(detail, dict):
        prov = detail.get('provenance') or {}
        lines = [
            ' DETAIL: ' + str(detail.get('title', '')),
            '   ' + str(detail.get('value', '')),
            f"   sample #{prov.get('sample_index', '?')} "
            f"@ {prov.get('timestamp', '')}",
            '   raw: ' + str(prov.get('raw_line', '')),
        ]
        for proc in (detail.get('spike_snapshot') or [])[:6]:
            lines.append('   process: {comm} cpu={cpu}% mem={mem}% {args}'.format(
                comm=proc.get('comm', ''), cpu=proc.get('cpu_pct', ''),
                mem=proc.get('mem_pct', ''), args=proc.get('args', '')))
        base = max(4, height - 4 - len(lines))
        for i, line in enumerate(lines):
            _lined(base + i, line,
                   _style('header', bold=True) if i == 0 else _style('value'))

    _separator(height - 2)

    # --- last row: command bar, fused with the bottom frame edge -----------
    if state.get('editing'):
        edit_buffer = str(state.get('buffer', ''))
        try:
            caret = max(0, min(len(edit_buffer),
                               int(state.get('cursor', len(edit_buffer)))))
        except (TypeError, ValueError):
            caret = len(edit_buffer)
        # Design §9 shows the caret inline, e.g. "[ 2_ ]" while editing.
        shown = edit_buffer[:caret] + '_' + edit_buffer[caret:]
        left = 'pistats › edit [' + shown + ']'
        hints = ['Enter save · Esc cancel']
    else:
        left = 'pistats › ' + state.get('buffer', '')
        hints = [
            '? commands · ← → tabs · ↑ ↓ rows · Enter detail',
            '? commands · ← → tabs · ↑ ↓ rows',
            '? commands · ← → tabs',
            '? commands',
            '',
        ]
    prefix_status = status + '  |  ' if status else ''
    bar = None
    for hint in hints:
        right_text = prefix_status + hint
        if len(left) + 2 + len(right_text) <= text_max:
            bar = (left
                   + ' ' * (text_max - len(left) - len(right_text))
                   + right_text)
            break
    if bar is None:
        room = max(1, text_max - len(prefix_status) - 2)
        left = _clip(left, room)
        right_text = prefix_status[:max(0, text_max - len(left) - 2)]
        bar = left + ' ' * max(0, text_max - len(left) - len(right_text)) + right_text
    _lined(height - 1, bar, _style('bar'), left='╚', right='╝')
    stdscr.refresh()


try:
    import curses as _curses
    _curses_A_REVERSE = _curses.A_REVERSE
except Exception:  # pragma: no cover
    _curses = None
    _curses_A_REVERSE = None


# --- colour theme ---------------------------------------------------------
# Colours must be registered from inside curses.wrapper(), so the pairs are
# built at startup.  On monochrome terminals (or in tests) every role simply
# resolves to attribute 0 and the old plain rendering is preserved.
_THEME_COLORS = (
    ('frame', 'COLOR_BLUE'),
    ('tab', 'COLOR_CYAN'),
    ('tab_active', 'COLOR_WHITE'),
    ('header', 'COLOR_CYAN'),
    ('label', 'COLOR_CYAN'),
    ('value', 'COLOR_WHITE'),
    ('good', 'COLOR_GREEN'),
    ('warn', 'COLOR_YELLOW'),
    ('critical', 'COLOR_RED'),
    ('bar', 'COLOR_YELLOW'),
)
_COLOR_ATTRS = {}


def _init_colors():
    """Register curses colour pairs; a no-op when colour is unavailable."""
    if _curses is None:
        return
    try:
        _curses.start_color()
        try:
            _curses.use_default_colors()
        except Exception:  # pragma: no cover - older curses implementations
            pass
        for pair, (name, color_name) in enumerate(_THEME_COLORS, start=1):
            color = getattr(_curses, color_name, None)
            if color is None:
                continue
            _curses.init_pair(pair, color, -1)
            _COLOR_ATTRS[name] = _curses.color_pair(pair)
    except Exception:  # pragma: no cover - unusual / monochrome terminals
        _COLOR_ATTRS.clear()


def _style(name, bold=False):
    """Return the curses attribute for a theme role (0 without colour)."""
    attr = _COLOR_ATTRS.get(name, 0)
    if bold and _curses is not None:
        attr |= getattr(_curses, 'A_BOLD', 0)
    return attr


def _value_style(row):
    """Choose a semantic colour for a row's value from its label and text."""
    label = str(row.get('label', '')).lower()
    text = str(row.get('text', '')).lower()
    if any(word in label for word in ('verdict', 'recommend', 'throttl')):
        if any(word in text for word in (
                'throttl', 'critical', 'active cooler', 'over', 'hot')):
            return 'critical'
        if any(word in text for word in ('heatsink', 'passive', 'warm')):
            return 'warn'
        return 'good'
    if 'p95' in label or 'max' in label:
        return 'warn'
    return 'value'


def read_soc_temp():
    """Read the live SoC temperature in °C (``None`` when unavailable).

    ``vcgencmd measure_temp`` is preferred, then Linux thermal sysfs.  The
    ``FAKE_SOC_TEMP`` hook keeps tests and SSH sessions hardware-free.
    """
    fake = os.environ.get('FAKE_SOC_TEMP')
    if fake not in (None, ''):
        try:
            value = float(fake)
            return value if value == value and abs(value) != float('inf') else None
        except (TypeError, ValueError):
            return None
    try:
        proc = subprocess.run(
            ['vcgencmd', 'measure_temp'], stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, text=True, timeout=2)
        match = re.search(r'(-?\d+(?:\.\d+)?)', proc.stdout or '')
        if match:
            return float(match.group(1))
    except Exception:
        pass
    try:
        with open('/sys/class/thermal/thermal_zone0/temp', 'r') as handle:
            return float(handle.read().strip()) / 1000.0
    except (OSError, ValueError):
        return None


def _persist_sample(cfg):
    """Periodically persist one sample through collect.sh's shared PID lock.

    ``--skip-if-locked`` makes this a no-op whenever the background cron
    collector is already writing, so the TUI never creates duplicate rows.
    """
    collector = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                             'collect.sh')
    if not os.path.exists(collector):
        return
    argv = [collector, '--skip-if-locked']
    mode = cfg.get('mode')
    if mode:
        argv += ['--mode', str(mode)]
    argv.append(_first_log())
    env = dict(os.environ)
    env.setdefault('PISTATS_POWER_LOG', DEFAULT_POWER_LOG)
    try:
        subprocess.Popen(
            argv, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            start_new_session=True, env=env)
    except OSError:
        pass


def _tui_loop(stdscr, cfg):
    _init_colors()
    try:
        _curses.curs_set(0)
    except Exception:
        pass
    timeout_ms = max(100, int(float(cfg.get('render_interval_s', 1.0)) * 1000))
    stdscr.timeout(timeout_ms)

    state = {
        'tab_index': 0, 'index': 0, 'scroll': 0, 'buffer': '',
        'palette': False, 'editing': False, 'detail': None,
        'paused': False, 'quit': False, 'cmd_index': 0,
        'command_focus': False,
        'submenu': None, 'sub_index': 0, 'sub_editing': False,
    }
    status = ''
    filtered = None
    temp_ctx = {}  # Temperature / Performance (legacy sample_interval_s)
    power_ctx = {}  # Battery (power_sample_interval_s)
    last_temp_time = 0.0
    last_power_time = 0.0
    last_log_time = time.monotonic()
    started = last_log_time

    # Live 1 Hz sampling: every observation is kept in a ring buffer for the
    # graphs / energy integral and appended to its own session CSV.  The
    # shared collector logs are left to _persist_sample (log_cadence_s).
    session_log = (os.path.join(
        os.path.dirname(os.path.abspath(_first_log())),
        _dashboard.SESSION_LOG_FILENAME) if _dashboard else None)
    recorder = (_dashboard.SessionRecorder(session_log) if _dashboard else None)
    last_sample_time = float('-inf')
    last_sample = None

    while not state['quit']:
        now = time.monotonic()
        elapsed = int(now - started)
        state['session_time'] = (
            f'{elapsed // 3600:02d}:{(elapsed % 3600) // 60:02d}:'
            f'{elapsed % 60:02d}')

        # Sample temperature and wall power together, once per configured
        # interval (1 second out of the box), and record every observation.
        if (recorder is not None and
                now - last_sample_time >= _interval_seconds(
                    cfg.get('sample_interval_s'))):
            temp = read_soc_temp()
            watts = None
            if power_read is not None:
                try:
                    watts = (power_read.read_power() or {}).get('wall_w')
                except Exception:
                    watts = None
            last_sample = recorder.add(time.time(), temp, watts)
            last_sample_time = now

        # The two caches refresh independently; read the live cfg every pass so
        # an edit through the Sampling sub-view takes effect immediately.
        if now - last_temp_time >= _interval_seconds(
                cfg.get('sample_interval_s')):
            temp_ctx = build_context(cfg)
            last_temp_time = now
        if now - last_power_time >= _interval_seconds(
                cfg.get('power_sample_interval_s')):
            power_ctx = build_context(cfg)
            last_power_time = now
        tab = TABS[state['tab_index']]
        if tab == 'Battery':
            ctx = power_ctx
        elif tab in ('Temperature', 'Performance'):
            ctx = temp_ctx
        elif tab == 'Overview':
            ctx = dict(temp_ctx)
            ctx.update(power_ctx)
            if recorder is not None:
                wall_now = time.time()
                session = recorder.context(
                    cfg.get('rate_per_kwh', 0.13), elapsed, now=wall_now)
                ctx.update(session)
                state['session_now'] = session['session_now']
                if last_sample is not None:
                    if last_sample.temp is not None:
                        ctx['session_temp_now'] = f'{last_sample.temp:.1f}'
                    if last_sample.watts is not None:
                        ctx['session_power_now'] = f'{last_sample.watts:.2f}'
        else:  # Config: reflect config saves without waiting for a refresh
            ctx = dict(temp_ctx)
            ctx.update(cfg)
        if now - last_log_time >= float(cfg.get('log_cadence_s', 60)):
            _persist_sample(cfg)
            last_log_time = now
        rows = build_tab_rows(tab, ctx)
        state['index'], state['scroll'] = move_selection(
            rows, state['index'], state['scroll'],
            max(1, stdscr.getmaxyx()[0] - 4), 'none')
        if state['palette'] and _commands:
            filtered = _commands.filter_commands(state['buffer'])
            # Keep the highlighted command on-screen and in range.
            state['cmd_index'] = build_palette_view(
                filtered, state.get('cmd_index', 0))['index']
        else:
            filtered = None
        _render(stdscr, state, rows, cfg, filtered, status)

        try:
            key = stdscr.getch()
        except Exception:
            key = -1
        if key == -1:
            continue

        # Sampling sub-view: consume every key so nothing leaks to the tab/row
        # handlers underneath.  ``cfg`` is the live dict (reassigned on save).
        if state.get('submenu') == 'sampling':
            sub_rows = build_sampling_rows(cfg)
            try:
                sub_index = max(0, min(len(sub_rows) - 1,
                                       int(state.get('sub_index', 0))))
            except (TypeError, ValueError):
                sub_index = 0
            state['sub_index'] = sub_index
            if state.get('sub_editing'):
                if key in (10, 13):
                    key_name = SAMPLING_KEYS[sub_index]
                    try:
                        updated = _config.set_config_value(
                            _config.load_config(), key_name, state['buffer'])
                        _config.save_config(updated)
                        cfg = updated
                        status = f'Saved {key_name} = {updated[key_name]}'
                    except Exception as exc:
                        status = str(exc)
                    state['sub_editing'] = False
                    state['buffer'] = ''
                elif key in (27,):
                    state['sub_editing'] = False
                    state['buffer'] = ''
                    status = 'Edit cancelled'
                elif key in (8, 127, _curses.KEY_BACKSPACE if _curses else 8):
                    state['buffer'] = state['buffer'][:-1]
                    state['cursor'] = len(state['buffer'])
                elif 32 <= key < 127:
                    state['buffer'] += chr(key)
                    state['cursor'] = len(state['buffer'])
                continue
            sub_action = resolve_key_action(key, _curses)
            if sub_action == 'cancel':
                state = close_sampling(state)
                status = ''
            elif sub_action in ('move_up', 'move_down'):
                step = -1 if sub_action == 'move_up' else 1
                state['sub_index'] = max(0, min(len(sub_rows) - 1,
                                                sub_index + step))
            elif sub_action == 'activate':
                state = start_sampling_edit(state, sub_rows)
            continue

        # A detail overlay closes on any key.
        if state['detail'] is not None:
            state['detail'] = None
            status = ''
            continue

        # Inline config edit.
        if state['editing']:
            if key in (10, 13):
                key_name = rows[state['index']].get('id', '').split('.', 1)[-1] \
                    if state['index'] < len(rows) else ''
                try:
                    updated = _config.set_config_value(
                        _config.load_config(), key_name, state['buffer'])
                    _config.save_config(updated)
                    cfg = updated
                    status = f'Saved {key_name} = {updated[key_name]}'
                except Exception as exc:
                    status = str(exc)
                state = cancel_edit(state)
            elif key in (27,):
                state = cancel_edit(state)
                status = 'Edit cancelled'
            elif key in (8, 127, _curses.KEY_BACKSPACE if _curses else 8):
                state['buffer'] = state['buffer'][:-1]
                state['cursor'] = len(state['buffer'])
            elif 32 <= key < 127:
                state['buffer'] += chr(key)
                state['cursor'] = len(state['buffer'])
            continue

        # Command palette.
        if state['palette']:
            if key == 27:
                state['palette'] = False
                state['buffer'] = ''
                status = ''
            elif key in (10, 13):
                text = resolve_palette_enter(
                    state['buffer'], filtered, state.get('cmd_index', 0))
                result = _commands.parse_command(text) if _commands else None
                status = _command_action(result, state, cfg)
                state['palette'] = False
                state['buffer'] = ''
            elif key == _curses.KEY_UP:
                state['cmd_index'] = max(0, state['cmd_index'] - 1)
            elif key == _curses.KEY_DOWN:
                state['cmd_index'] += 1
            elif key in (8, 127, _curses.KEY_BACKSPACE if _curses else 8):
                state['buffer'] = state['buffer'][:-1]
                state['cmd_index'] = 0
            elif 32 <= key < 127:
                state['buffer'] += chr(key)
                state['cmd_index'] = 0
            continue

        # Normal navigation: route every key through resolve_key_action so
        # TAB / Shift+Tab and the arrow keys keep their distinct meanings.
        action = resolve_key_action(key, _curses)
        if action == 'commands':
            state['palette'] = True
            state['buffer'] = ''
            state['cmd_index'] = 0
        elif action in ('prev_tab', 'next_tab'):
            step = -1 if action == 'prev_tab' else 1
            state['tab_index'] = (state['tab_index'] + step) % len(TABS)
            state['index'] = state['scroll'] = 0
        elif action in ('move_up', 'move_down', 'home', 'end',
                        'page_up', 'page_down'):
            direction = {
                'move_up': 'up', 'move_down': 'down', 'home': 'home',
                'end': 'end', 'page_up': 'page_up',
                'page_down': 'page_down',
            }[action]
            height = max(1, stdscr.getmaxyx()[0] - 4)
            state['index'], state['scroll'] = move_selection(
                rows, state['index'], state['scroll'], height, direction)
            if direction in ('page_up', 'page_down'):
                state['scroll'] = scroll_to_section(
                    rows, state['index'], state['scroll'], height)
        elif action == 'activate':
            if state['index'] < len(rows):
                row_action = resolve_enter_action(
                    rows[state['index']], TABS[state['tab_index']])
                if row_action == 'edit':
                    state = start_config_edit(state, rows)
                elif row_action == 'submenu':
                    state['submenu'] = 'sampling'
                    state['sub_index'] = 0
                    state['sub_editing'] = False
                    state['buffer'] = ''
                elif row_action == 'detail':
                    state['detail'] = build_detail(
                        rows[state['index']], TABS[state['tab_index']], ctx)
        elif action == 'cancel':
            # Esc at the main screen exits; handle_escape() stays available for
            # API compatibility but is no longer wired to this bare-Esc path.
            state['quit'] = True
        elif key in (ord('q'),):
            state['quit'] = True


def run_tui(config=None):
    """Launch the full-screen curses dashboard.  Returns an exit code."""
    if _curses is None:
        print('PiStats TUI requires the stdlib curses module.', file=sys.stderr)
        return 1
    cfg = dict(config or {})
    if _config:
        try:
            cfg = _config.load_config()
        except Exception:
            cfg = dict(_config.DEFAULT_CONFIG)
    try:
        _curses.wrapper(_tui_loop, cfg)
    except KeyboardInterrupt:
        return 0
    except Exception as exc:  # pragma: no cover - defensive
        print(f'PiStats TUI error: {exc}', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(run_tui())
