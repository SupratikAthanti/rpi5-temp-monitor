"""PiScope TUI: five tabs, command bar and auto-scrolling row selection.

The pure helpers at the top of this module (``build_tab_rows``,
``move_selection``, ``scroll_to_section``, ...) intentionally have **no**
dependency on any TUI toolkit — they work when ``textual`` is absent.  The
interactive frontend at the bottom uses the Python standard-library ``curses``
module so the dashboard runs on a stock RPi 5 with zero extra installs.  If
``textual`` happens to be installed it is imported defensively but is not
required.
"""

import os
import sys
import time

try:  # optional; the curses frontend below is the dependency-free default
    import textual  # noqa: F401
    HAS_TEXTUAL = True
except Exception:  # pragma: no cover - depends on the environment
    textual = None
    HAS_TEXTUAL = False

try:
    import piscope_config as _config
    import piscope_commands as _commands
except ImportError:  # pragma: no cover - only when run outside the repo
    _config = None
    _commands = None

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


def _fmt(value, default='—'):
    return default if value in (None, '') else str(value)


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
        add(_row('overview.status', 'OVERVIEW',
                 'Mode: ' + _fmt(c.get('mode'), 'both') +
                 '  ·  Render: ' + _fmt(c.get('render_interval_s'), '1 s'),
                 'section'))
        add(_row('overview.temp', 'Temperature',
                 'SoC ' + _fmt(c.get('soc_temp'), '—') +
                 ' °C  ·  p95 (1 h) ' + _fmt(c.get('p95_temp'), '—') + ' °C'))
        add(_row('overview.power', 'Power',
                 'PMIC ' + _fmt(c.get('pmic_w'), '—') +
                 ' W  ·  wall est. ' + _fmt(c.get('wall_w'), '—') + ' W'))
        add(_row('overview.cost', 'Cost / Battery',
                 'Cost today ' + _fmt(c.get('cost'), '$0.000') +
                 '  ·  Battery ' + _fmt(c.get('battery_wh'), '45') +
                 ' Wh → ~' + _fmt(c.get('battery_hours'), '8.3') + ' h'))
        add(_row('overview.verdict', 'FINAL VERDICT',
                 'VERDICT: ' + _fmt(c.get('verdict'), 'No Cooler Needed'),
                 'section'))
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
                 ' Wh   full log: ' + _fmt(c.get('energy_full'), '—') + ' Wh'))
        add(_row('bat.cost', 'COST', 'COST', 'section'))
        add(_row('bat.cost.detail', 'Cost',
                 'Cost @ $' + _fmt(c.get('rate'), '0.15') + '/kWh → ' +
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
        keys = sorted(_config.LIVE_KEYS) if _config else []
        for key in keys:
            value = c.get(key, (_config.DEFAULT_CONFIG.get(key)
                                if _config else '—'))
            add(_row('config.' + key, key,
                     f'{key:<22}: [ {_fmt(value)} ]'))
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
    """Scroll ``index``'s section title to the top of the viewport."""
    count = len(rows)
    if count == 0:
        return 0
    idx = max(0, min(count - 1, int(index)))
    if rows[idx].get('kind') == 'section':
        return idx
    for pos in range(idx - 1, -1, -1):
        if rows[pos].get('kind') == 'section':
            return pos
    return 0


def resolve_enter_action(row, tab):
    """Return ``'edit'`` (Config data row), ``'detail'`` or ``None``."""
    if not row or row.get('kind') != 'data':
        return None
    return 'edit' if tab == 'Config' else 'detail'


def cancel_edit(state):
    """Clear an in-progress edit while preserving all other state keys."""
    out = dict(state or {})
    out['editing'] = False
    out['buffer'] = ''
    return out


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

        records = analyze.parse_csv_file(_first_log())
        if records:
            temps = [r['soc_temp'] for r in records]
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

        power_path = os.environ.get('PISCOPE_POWER_LOG') or DEFAULT_POWER_LOG
        precords = analyze.parse_power_log(power_path)
        live = power_read.read_power()
        ctx['rails'] = live['rails']
        ctx['pmic_w'] = f"{live['pmic_w']:.2f}"
        ctx['wall_w'] = f"{live['wall_w']:.2f}"
        ctx['wall_factor'] = cfg.get('wall_factor', 1.0)
        ctx['rate'] = cfg.get('rate_per_kwh', 0.15)
        ctx['battery_wh'] = cfg.get('battery_wh', 45.0)
        if precords:
            ctx['energy_full'] = f"{analyze.energy_wh(precords):.2f}"
            wall_w = [r['wall_w'] for r in precords]
            wstats = analyze.calculate_stats(wall_w)
            estimate = analyze.battery_estimate(
                cfg.get('battery_wh', 45.0), wstats['avg'], wstats['p95'])
            ctx['energy_1h'] = f"{analyze.energy_wh(precords, 60):.2f}"
            ctx['cost'] = f"${analyze.cost_dollars(analyze.energy_wh(precords), cfg.get('rate_per_kwh', 0.15)):.5f}"
            ctx['avg_hours'] = f"{estimate['avg_hours']:.1f}"
            ctx['conservative_hours'] = f"{estimate['conservative_hours']:.1f}"
        spikes = analyze.parse_spikes_file(
            os.path.join(os.path.dirname(_first_log()), 'spikes.csv'))
        ctx['spike_count'] = len(spikes)
        ctx['spike_note'] = analyze.frequency_weighted_note(spikes, len(records))
    except Exception:
        pass
    return ctx


# ---------------------------------------------------------------------------
# Interactive curses frontend (stdlib only)
# ---------------------------------------------------------------------------

def _command_action(command, state, cfg):
    """Apply a parsed command to the mutable TUI state; return status text."""
    if not command or not command.get('valid'):
        return command.get('error', 'Invalid command') if command else 'Invalid command'
    name, args = command['name'], command['args']
    if name == '/help':
        return 'Commands: ' + ', '.join(c['name'] for c in (_commands.COMMANDS if _commands else []))
    if name == '/quit':
        state['quit'] = True
        return 'Exiting PiScope...'
    if name == '/pause':
        state['paused'] = True
        return 'Rendering paused'
    if name == '/resume':
        state['paused'] = False
        return 'Rendering resumed'
    if name == '/config':
        state['tab_index'] = TABS.index('Config')
        state['index'] = state['scroll'] = 0
        setting = _commands.parse_config_set(args) if _commands else None
        if setting and _config:
            try:
                updated = _config.set_config_value(
                    _config.load_config(), setting['key'], setting['value'])
                _config.save_config(updated)
                return f"Set {setting['key']} = {updated[setting['key']]}"
            except ValueError as exc:
                return str(exc)
        return 'Config tab'
    if name == '/mode' and _config:
        updated = _config.set_config_value(_config.load_config(), 'mode', args[0])
        _config.save_config(updated)
        return f'Mode = {updated["mode"]}'
    if name == '/decision':
        state['detail'] = build_context(cfg).get('verdict', 'No Cooler Needed')
        return 'Verdict shown'
    if name == '/report':
        return 'Report: use "piscope view" in another terminal'
    if name == '/reset':
        return 'Reset: run ./reset.sh from the shell'
    if name == '/test':
        return f'Test {args[0]} min: run ./run-test.sh {args[0]}'
    if name == '/range':
        return f'Range: {args[0] if args else "full log"}'
    if name == '/cron':
        return f'Cron {args[0] if args else "status"}: manage with setup-cron.sh / stop.sh'
    return name


def _render(stdscr, state, rows, cfg, filtered=None, status=''):
    height, width = stdscr.getmaxyx()
    if height < 4 or width < 20:
        return
    stdscr.erase()

    def put(y, text, attr=0):
        if 0 <= y < height:
            try:
                stdscr.addnstr(y, 0, str(text), max(0, width - 1), attr)
            except Exception:
                pass

    interval = cfg.get('render_interval_s', DEFAULT_RENDER_INTERVAL_S)
    header = (f" PiScope · mode={cfg.get('mode', 'both')} · render {interval}s"
              f" · {'PAUSED' if state.get('paused') else 'LIVE'}"
              f" · {time.strftime('%Y-%m-%d %H:%M:%S')}")
    put(0, header)
    tabs = ' │ '.join(
        ('◀ ' + t if i == state['tab_index'] else t)
        for i, t in enumerate(TABS))
    put(1, ' ' + tabs)

    top = state['scroll']
    visible = max(1, height - 4)
    for offset in range(visible):
        pos = top + offset
        if pos >= len(rows):
            break
        row = rows[pos]
        marker = '>' if pos == state['index'] else ' '
        text = f"{marker} {row['label']:<24} {row['text']}"
        attr = 0
        if pos == state['index']:
            attr = 1 if _curses_A_REVERSE is None else _curses_A_REVERSE
        put(2 + offset, text, attr)

    if filtered is not None:
        base = max(2, height - 6)
        put(base, ' COMMANDS (Esc to close):')
        for i, cmd in enumerate(filtered[:4]):
            put(base + 1 + i, '   ' + cmd['name'] + '  ' + cmd['description'])

    if state.get('editing'):
        bar = f" piscope › edit [{state.get('buffer', '')}]  Enter save · Esc cancel"
    else:
        bar = ' piscope › ' + state.get('buffer', '') + '     ? commands · ← → tabs · ↑ ↓ rows · Enter detail'
    if status:
        bar += '  |  ' + status
    put(height - 1, bar[:max(0, width - 1)])
    stdscr.refresh()


try:
    import curses as _curses
    _curses_A_REVERSE = _curses.A_REVERSE
except Exception:  # pragma: no cover
    _curses = None
    _curses_A_REVERSE = None


def _tui_loop(stdscr, cfg):
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
    }
    status = ''
    filtered = None
    last_ctx_time = 0.0
    ctx = {}

    while not state['quit']:
        now = time.monotonic()
        if now - last_ctx_time >= float(cfg.get('sample_interval_s', 1.0)):
            ctx = build_context(cfg)
            last_ctx_time = now
        rows = build_tab_rows(TABS[state['tab_index']], ctx)
        state['index'], state['scroll'] = move_selection(
            rows, state['index'], state['scroll'],
            max(1, stdscr.getmaxyx()[0] - 4), 'none')
        if state['palette'] and _commands:
            filtered = _commands.filter_commands(state['buffer'])
        else:
            filtered = None
        _render(stdscr, state, rows, cfg, filtered, status)

        try:
            key = stdscr.getch()
        except Exception:
            key = -1
        if key == -1:
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
            elif 32 <= key < 127:
                state['buffer'] += chr(key)
            continue

        # Command palette.
        if state['palette']:
            if key == 27:
                state['palette'] = False
                state['buffer'] = ''
                status = ''
            elif key in (10, 13):
                if state['buffer'].strip():
                    text = state['buffer']
                elif filtered:
                    pick = min(state['cmd_index'], len(filtered) - 1)
                    text = filtered[pick]['name']
                else:
                    text = '?'
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

        # Normal navigation.
        if key in (ord('?'), ord('/')):
            state['palette'] = True
            state['buffer'] = ''
            state['cmd_index'] = 0
        elif key in (_curses.KEY_LEFT, 9):  # left arrow / Tab
            state['tab_index'] = (state['tab_index'] - 1) % len(TABS)
            state['index'] = state['scroll'] = 0
        elif key in (_curses.KEY_RIGHT,):
            state['tab_index'] = (state['tab_index'] + 1) % len(TABS)
            state['index'] = state['scroll'] = 0
        elif key == _curses.KEY_UP:
            state['index'], state['scroll'] = move_selection(
                rows, state['index'], state['scroll'],
                max(1, stdscr.getmaxyx()[0] - 4), 'up')
        elif key == _curses.KEY_DOWN:
            state['index'], state['scroll'] = move_selection(
                rows, state['index'], state['scroll'],
                max(1, stdscr.getmaxyx()[0] - 4), 'down')
        elif key in (_curses.KEY_HOME,):
            state['index'], state['scroll'] = move_selection(
                rows, state['index'], state['scroll'],
                max(1, stdscr.getmaxyx()[0] - 4), 'home')
        elif key in (_curses.KEY_END,):
            state['index'], state['scroll'] = move_selection(
                rows, state['index'], state['scroll'],
                max(1, stdscr.getmaxyx()[0] - 4), 'end')
        elif key in (_curses.KEY_PPAGE,):
            state['index'], state['scroll'] = move_selection(
                rows, state['index'], state['scroll'],
                max(1, stdscr.getmaxyx()[0] - 4), 'page_up')
            state['scroll'] = scroll_to_section(rows, state['index'],
                                                state['scroll'],
                                                stdscr.getmaxyx()[0] - 4)
        elif key in (_curses.KEY_NPAGE,):
            state['index'], state['scroll'] = move_selection(
                rows, state['index'], state['scroll'],
                max(1, stdscr.getmaxyx()[0] - 4), 'page_down')
            state['scroll'] = scroll_to_section(rows, state['index'],
                                                state['scroll'],
                                                stdscr.getmaxyx()[0] - 4)
        elif key in (10, 13):
            if state['index'] < len(rows):
                action = resolve_enter_action(
                    rows[state['index']], TABS[state['tab_index']])
                if action == 'edit':
                    state['editing'] = True
                    state['buffer'] = ''
                elif action == 'detail':
                    state['detail'] = rows[state['index']]['text']
        elif key in (27,):
            status = 'Esc: command bar focused (type ? for help)'
        elif key in (ord('q'),):
            state['quit'] = True


def run_tui(config=None):
    """Launch the full-screen curses dashboard.  Returns an exit code."""
    if _curses is None:
        print('PiScope TUI requires the stdlib curses module.', file=sys.stderr)
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
        print(f'PiScope TUI error: {exc}', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(run_tui())
