"""Pure command-palette parsing for the PiScope TUI.

No TUI / textual dependency: all helpers are plain functions over strings and
dicts so they can be unit-tested (and reused by the curses frontend) directly.
"""

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
    {'name': '/quit', 'description': 'Exit PiScope (cron is left untouched)'},
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
