"""PiStats configuration: defaults, JSON persistence and validation.

All paths are overridable with ``PISTATS_CONFIG_PATH`` so tests (and the TUI)
never touch the real home directory.  Only stdlib is used.
"""

import json
import os

CONFIG_REL_PATH = os.path.join('.config', 'pistats', 'config.json')
CONFIG_PATH_ENV = 'PISTATS_CONFIG_PATH'

DEFAULT_CONFIG = {
    'render_interval_s': 1.0,
    'sample_interval_s': 1.0,
    'power_sample_interval_s': 1.0,
    'log_cadence_s': 60,
    'mode': 'both',
    'rate_per_kwh': 0.15,
    'battery_wh': 45.0,
    'wall_factor': 1.0,
    'wall_slope': 1.0,
    'wall_intercept': 0.0,
    'energy_windows_min': [60, 120],
}

# The eight settings that can be changed live from the Config tab / /config set.
LIVE_KEYS = {
    'render_interval_s', 'sample_interval_s', 'power_sample_interval_s',
    'log_cadence_s', 'mode', 'rate_per_kwh', 'battery_wh', 'wall_factor',
    'energy_windows_min',
}


def config_path():
    """Return the active config file path (env override wins)."""
    return os.environ.get(CONFIG_PATH_ENV) or os.path.join(
        os.path.expanduser('~'), CONFIG_REL_PATH)


def _as_float(value, default):
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def _clamp(value, lo, hi):
    return max(lo, min(hi, value))


def validate_config(cfg):
    """Return a complete config dict with all values coerced and clamped."""
    out = dict(DEFAULT_CONFIG)
    if not isinstance(cfg, dict):
        return out

    for key in ('render_interval_s', 'sample_interval_s',
                'power_sample_interval_s'):
        if key in cfg:
            out[key] = _clamp(
                _as_float(cfg[key], DEFAULT_CONFIG[key]), 0.1, 60.0)

    if 'log_cadence_s' in cfg:
        out['log_cadence_s'] = int(_clamp(
            _as_float(cfg['log_cadence_s'], DEFAULT_CONFIG['log_cadence_s']),
            1, 86400))

    if 'mode' in cfg:
        mode = str(cfg['mode']).strip().lower()
        out['mode'] = mode if mode in ('temp', 'power', 'both') else 'both'

    if 'rate_per_kwh' in cfg:
        out['rate_per_kwh'] = max(0.0, _as_float(
            cfg['rate_per_kwh'], DEFAULT_CONFIG['rate_per_kwh']))
    if 'battery_wh' in cfg:
        out['battery_wh'] = max(0.0, _as_float(
            cfg['battery_wh'], DEFAULT_CONFIG['battery_wh']))

    if 'wall_factor' in cfg:
        wf = _as_float(cfg['wall_factor'], DEFAULT_CONFIG['wall_factor'])
        out['wall_factor'] = wf if wf > 0 else DEFAULT_CONFIG['wall_factor']

    if 'wall_slope' in cfg:
        out['wall_slope'] = _as_float(
            cfg['wall_slope'], DEFAULT_CONFIG['wall_slope'])
    if 'wall_intercept' in cfg:
        out['wall_intercept'] = _as_float(
            cfg['wall_intercept'], DEFAULT_CONFIG['wall_intercept'])

    if 'energy_windows_min' in cfg:
        raw = cfg['energy_windows_min']
        if isinstance(raw, str):
            raw = [p for p in raw.split(',') if p.strip()]
        windows = []
        if isinstance(raw, (list, tuple)):
            for item in raw:
                try:
                    number = float(item)
                except (TypeError, ValueError):
                    continue
                if number > 0:
                    windows.append(int(number) if number.is_integer() else number)
        out['energy_windows_min'] = (
            windows if windows else list(DEFAULT_CONFIG['energy_windows_min']))

    return out


def load_config(path=None):
    """Load config from ``path or config_path()``; missing/corrupt -> defaults."""
    cfg = dict(DEFAULT_CONFIG)
    target = path or config_path()
    try:
        with open(target, 'r') as f:
            data = json.load(f)
        if isinstance(data, dict):
            cfg.update(data)
    except (OSError, ValueError, TypeError):
        pass
    return validate_config(cfg)


def save_config(cfg, path=None):
    """Write indented JSON to ``path or config_path()`` (parent dirs created)."""
    target = path or config_path()
    parent = os.path.dirname(os.path.abspath(target))
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(target, 'w') as f:
        json.dump(cfg, f, indent=2, sort_keys=True)
        f.write('\n')


def set_config_value(cfg, key, value):
    """Return a copy of ``cfg`` with ``key`` set from a string ``value``.

    Parsing follows the type of the default value: float, int, mode string or
    a comma-separated list of numbers.  Unknown keys raise ``ValueError``.
    """
    if key not in DEFAULT_CONFIG:
        raise ValueError(f'Unknown config key: {key}')

    updated = dict(cfg or {})
    default = DEFAULT_CONFIG[key]

    if isinstance(default, list):
        pieces = [p.strip() for p in str(value).split(',') if p.strip()]
        try:
            parsed = [float(p) for p in pieces]
        except ValueError as exc:
            raise ValueError(f'Invalid list value for {key}: {value}') from exc
        updated[key] = parsed
    elif isinstance(default, bool):
        updated[key] = str(value).strip().lower() in ('1', 'true', 'yes', 'on')
    elif isinstance(default, int):
        try:
            updated[key] = int(str(value).strip())
        except ValueError as exc:
            raise ValueError(f'Invalid integer for {key}: {value}') from exc
    elif isinstance(default, float):
        try:
            updated[key] = float(str(value).strip())
        except ValueError as exc:
            raise ValueError(f'Invalid number for {key}: {value}') from exc
    else:
        updated[key] = str(value).strip()

    return validate_config(updated)
