"""Pure command-palette parsing (and headless execution) for the PiStats TUI.

No TUI / textual dependency: all helpers are plain functions over strings and
dicts so they can be unit-tested (and reused by the curses frontend) directly.
``execute_command`` performs the *real* side effects of a command through an
injected context, so tests never touch a real crontab, process or home config.
"""

import os
import subprocess

COMMANDS = [
    {'name': '/help', 'description': 'Show the list of available commands'},
    {'name': '/config', 'description': 'Open the Config tab; /config set <key> <value> edits a setting'},
    {'name': '/reset', 'description': 'Delete all collected data and start fresh'},
    {'name': '/report', 'description': 'Show the full saved text report'},
    {'name': '/range', 'description': 'Show results for a time range (2h, 24h, 7d, dates)'},
    {'name': '/test', 'description': 'Run a timed test with progress (minutes)'},
    {'name': '/cron', 'description': 'Start / stop / check the background collector'},
    {'name': '/mode', 'description': 'Switch sampling mode: temp | power | both'},
    {'name': '/pause', 'description': 'Pause live rendering'},
    {'name': '/resume', 'description': 'Resume live rendering'},
    {'name': '/quit', 'description': 'Exit PiStats (cron is left untouched)'},
    {'name': '/decision', 'description': 'Show the cooler verdict with all reasons'},
]

ALIASES = {'?': '/help', '/': '/help'}
_COMMAND_NAMES = {c['name'] for c in COMMANDS}
_MODES = {'temp', 'power', 'both'}
_CRON_ACTIONS = {'start', 'stop', 'status'}


def normalize_command(text):
    """Strip whitespace, resolve aliases and lowercase only the command token."""
    value = str(text or '').strip()
    if value in ALIASES:
        return ALIASES[value]
    if not value:
        return ''
    parts = value.split()
    token = parts[0].lower()
    if token in ALIASES:
        token = ALIASES[token]
    if len(parts) == 1:
        return token
    return token + ' ' + ' '.join(parts[1:])


def _result(raw, name, args, valid, error=None):
    return {
        'raw': raw,
        'name': name if valid else None,
        'args': list(args),
        'valid': bool(valid),
        'error': error,
    }


def parse_command(text):
    """Parse and validate a command string.

    Returns ``{'raw', 'name', 'args', 'valid', 'error'}``.  Invalid inputs have
    ``name=None`` so callers can safely ignore them.
    """
    raw = text
    normalized = normalize_command(text)
    if not normalized:
        return _result(raw, None, [], False, 'Empty command')

    parts = normalized.split()
    name, args = parts[0], parts[1:]

    if name not in _COMMAND_NAMES:
        return _result(raw, None, args, False, f'Unknown command: {name}')

    if name == '/mode' and (len(args) != 1 or args[0].lower() not in _MODES):
        return _result(raw, name, args, False, 'Mode must be temp, power or both')
    if name == '/mode':
        args = [args[0].lower()]

    if name == '/cron' and (len(args) != 1 or args[0].lower() not in _CRON_ACTIONS):
        return _result(raw, name, args, False, 'Cron action must be start, stop or status')
    if name == '/cron':
        args = [args[0].lower()]

    if name == '/test':
        if len(args) != 1 or not args[0].isdigit() or int(args[0]) <= 0:
            return _result(raw, name, args, False, 'Test duration must be a positive integer')

    if name == '/config' and args:
        parsed = parse_config_set(args)
        if parsed is None:
            return _result(raw, name, args, False, 'Use: /config set <key> <value>')

    return _result(raw, name, args, True)


def filter_commands(query):
    """Case-insensitive substring match on command name and description."""
    needle = str(query or '').strip().lower()
    if not needle:
        return [dict(c) for c in COMMANDS]
    return [dict(c) for c in COMMANDS
            if needle in c['name'].lower() or needle in c['description'].lower()]


def autocomplete(query):
    """Return command names starting with ``query`` (leading '/' added)."""
    prefix = str(query or '').strip()
    if not prefix.startswith('/'):
        prefix = '/' + prefix
    prefix = prefix.lower()
    return [c['name'] for c in COMMANDS if c['name'].lower().startswith(prefix)]


def parse_config_set(args):
    """Return ``{'key', 'value'}`` for a valid ``set`` invocation, else None."""
    if not args or len(args) != 3:
        return None
    if str(args[0]).strip().lower() != 'set':
        return None
    key = str(args[1]).strip()
    value = str(args[2]).strip()
    if not key or not value:
        return None
    return {'key': key, 'value': value}


# ---------------------------------------------------------------------------
# Command execution (see EXPECTED_API.md §9)
# ---------------------------------------------------------------------------

def _exec_result(ok, status, effects=None, **extra):
    out = {
        'ok': bool(ok),
        'status': str(status or ''),
        'effects': list(effects or []),
    }
    out.update(extra)
    return out


def _scripts_dir(context):
    return str(context.get('scripts') or os.path.dirname(os.path.abspath(__file__)))


def _data_dir(context):
    return str(context.get('dir') or os.getcwd())


def _as_text(stdout):
    if stdout is None:
        return ''
    if isinstance(stdout, bytes):
        return stdout.decode('utf-8', 'replace')
    return str(stdout)


def _default_spawner(argv, **kwargs):
    """Non-blocking fallback launcher (never waits for the child)."""
    return subprocess.Popen(
        argv, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        start_new_session=True)


def _default_crontab_runner(argv, input=None, **kwargs):
    return subprocess.run(argv, input=input, capture_output=True,
                          text=True, timeout=10)


def _collector_line(scripts, data_dir):
    return ('* * * * * ' + os.path.join(scripts, 'collect.sh') +
            ' --mode both ' + os.path.join(data_dir, 'temp_log.csv'))


def _is_collector_line(line):
    return 'collect.sh' in str(line)


def _read_crontab(runner):
    proc = runner(['crontab', '-l'], input=None)
    return _as_text(getattr(proc, 'stdout', ''))


def _write_crontab(runner, text):
    return runner(['crontab', '-'], input=text)


def _default_report_fn(data_dir):
    """Fallback report builder bound to the injected data directory."""
    def build(**kwargs):
        import analyze
        log = os.path.join(data_dir, 'temp_log.csv')
        power = os.path.join(data_dir, 'power_log.csv')
        if not os.path.exists(power):
            power = None
        try:
            import power_source
            resolution = power_source.resolve(power_log=power)
            if resolution['source'] != power_source.SOURCE_LOG:
                kwargs.setdefault('power_records', resolution['records'])
                kwargs.setdefault('power_source', resolution['label'])
                kwargs.setdefault('power_hint', resolution['hint'])
                return analyze.build_report(log, **kwargs)
        except ImportError:  # pragma: no cover - standalone module use
            pass
        return analyze.build_report(log, power_log_file=power, **kwargs)
    return build


def execute_command(cmd, context):
    """Carry out a parsed command (or raw command string) with injected effects.

    ``context`` supplies every side-effecting collaborator; see the module-level
    docstring / EXPECTED_API.md §9 for the exact keys.  The return value always
    has ``ok``, ``status`` and ``effects`` plus command-specific keys.
    """
    context = dict(context or {})
    command = cmd if isinstance(cmd, dict) else parse_command(cmd)
    if not command or not command.get('valid'):
        error = command.get('error') if isinstance(command, dict) else str(command)
        return _exec_result(False, error or 'Invalid command', [])

    name, args = command.get('name'), list(command.get('args') or [])
    state = context.get('state')
    if state is None:
        state = {}
        context['state'] = state

    if name == '/test':
        minutes = int(args[0])
        spawner = context.get('process_spawner') or _default_spawner
        argv = [os.path.join(_scripts_dir(context), 'run-test.sh'), str(minutes)]
        mode = (context.get('config') or {}).get('mode')
        if mode:
            argv += ['--mode', str(mode)]
        spawner(argv)
        return _exec_result(True, f'Test {minutes} min started',
                       ['test_started'], minutes=minutes)

    if name == '/reset':
        confirm = context.get('confirm')
        if confirm is None:
            confirm = lambda prompt: False  # never destructive by default
        if not confirm('Delete all collected data and start fresh? [y/N] '):
            return _exec_result(True, 'Reset cancelled', ['reset_cancelled'])

        data_dir = _data_dir(context)
        removed = []
        for filename in ('temp_log.csv', 'power_log.csv', 'spikes.csv',
                         'collector.pid', 'spike_state'):
            path = os.path.join(data_dir, filename)
            if os.path.exists(path):
                try:
                    os.remove(path)
                    removed.append(filename)
                except OSError:
                    pass
        return _exec_result(True, 'Collected data cleared', ['logs_cleared'],
                       removed=removed)

    if name in ('/report', '/range'):
        report_fn = context.get('report_fn') or _default_report_fn(
            _data_dir(context))
        if name == '/report':
            report = report_fn()
            return _exec_result(True, 'Full report shown', ['report_shown'],
                           report=report, scrollable=False)
        spec = args[0] if args else ''
        report = report_fn(range_spec=spec)
        return _exec_result(True, f'Report for {spec}', ['range_report_shown'],
                       report=report, scrollable=True, range_spec=spec)

    if name == '/cron':
        runner = context.get('crontab_runner') or _default_crontab_runner
        action = args[0] if args else 'status'
        text = _read_crontab(runner)
        lines = text.splitlines()

        if action == 'status':
            running = any(_is_collector_line(line) for line in lines)
            status = ('Cron: RUNNING (background collector installed)'
                      if running else
                      'Cron: STOPPED (no background collector)')
            return _exec_result(True, status, ['cron_status'],
                           cron_running=running)

        if action == 'start':
            if not any(_is_collector_line(line) for line in lines):
                lines.append(_collector_line(_scripts_dir(context),
                                             _data_dir(context)))
                _write_crontab(runner, '\n'.join(lines) + '\n')
            return _exec_result(True, 'Cron collector started', ['cron_started'])

        # stop
        kept = [line for line in lines if not _is_collector_line(line)]
        _write_crontab(runner, '\n'.join(kept) + '\n')
        return _exec_result(True, 'Cron collector stopped', ['cron_stopped'])

    if name in ('/mode', '/config') and (
            name == '/mode' or (args and args[0].lower() == 'set')):
        import pistats_config as cfgmod
        config = context.get('config')
        if not isinstance(config, dict):
            config = cfgmod.load_config()
            context['config'] = config
        path = context.get('config_path') or cfgmod.config_path()
        try:
            if name == '/mode':
                updated = cfgmod.set_config_value(config, 'mode', args[0])
                status = f'Mode = {updated["mode"]}'
                effect = 'mode_changed'
            else:
                setting = parse_config_set(args)
                if setting is None:
                    return _exec_result(False, 'Use: /config set <key> <value>', [])
                updated = cfgmod.set_config_value(
                    config, setting['key'], setting['value'])
                status = f'Set {setting["key"]} = {updated[setting["key"]]}'
                effect = 'config_changed'
        except ValueError as exc:
            return _exec_result(False, str(exc), [])
        config.update(updated)
        cfgmod.save_config(updated, path=path)
        return _exec_result(True, status, [effect], config=dict(updated))

    if name == '/pause':
        state['paused'] = True
        return _exec_result(True, 'Rendering paused', ['paused'])
    if name == '/resume':
        state['paused'] = False
        return _exec_result(True, 'Rendering resumed', ['resumed'])
    if name == '/quit':
        state['quit'] = True
        return _exec_result(True, 'Exiting PiStats...', ['quit'])
    if name == '/decision':
        return _exec_result(True, 'Verdict shown', ['decision_shown'])

    # /help and /config with no arguments: informational no-ops.
    if name == '/help':
        return _exec_result(True, 'Commands: ' + ', '.join(
            c['name'] for c in COMMANDS), ['help_shown'])
    return _exec_result(True, 'Config tab', ['config_shown'])
