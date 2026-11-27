"""Resolve the best available power data source for PiStats' UI paths.

**No cron job is required for live readings.**  While ``python3 pistats.py`` is
open it samples ``vcgencmd pmic_read_adc`` once per second into an in-memory
recorder (and its own session CSV).  The historical ``power_log.csv`` is only
needed for always-on background collection via cron.

This module is what keeps the *other* UI paths (the Battery / Overview rows,
``/report`` and ``python3 pistats.py view``) from showing misleading zeros when
that historical log is missing, empty, all-zero or stale: they fall back to the
live session samples.  When there is no readable ADC at all, the result is an
honest ``'unavailable'`` state with a hint — never invented hardware numbers.

Pure/stdlib, no curses and no side effects on import.
"""

import datetime
import math
import os
import time

import analyze
import pistats_dashboard as dashboard
import power_read

# A historical power log older than this is not a useful picture of *now*.
POWER_LOG_MAX_AGE_S = 300.0
_TINY_W = 1e-9

SOURCE_LOG = 'log'
SOURCE_LIVE = 'live'
SOURCE_UNAVAILABLE = 'unavailable'

SOURCE_LABELS = {
    SOURCE_LOG: 'log',
    SOURCE_LIVE: 'live (no cron)',
    SOURCE_UNAVAILABLE: 'unavailable',
}

HINT_LIVE = ('Live 1 Hz ADC samples; readings accumulate only while the CLI '
             'is open and stop when it closes (no cron needed).')
HINT_UNAVAILABLE = ('No power source available: power_log.csv has no recent '
                    'readings and vcgencmd pmic_read_adc could not be read here. '
                    'Live power needs a Raspberry Pi 5 PMIC.')


def _finite(value):
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) else None


def _iso(timestamp):
    try:
        moment = datetime.datetime.fromtimestamp(
            float(timestamp), datetime.timezone.utc)
        return moment.strftime('%Y-%m-%dT%H:%M:%SZ')
    except (ValueError, TypeError, OverflowError, OSError):
        return ''


def _epoch_of(value):
    """Epoch seconds from an epoch number or an ISO-8601 timestamp string."""
    epoch = _finite(value)
    if epoch is not None:
        return epoch
    if value in (None, ''):
        return None
    try:
        moment = datetime.datetime.fromisoformat(
            str(value).strip().replace('Z', '+00:00'))
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=datetime.timezone.utc)
        return float(moment.timestamp())
    except (ValueError, TypeError, OverflowError):
        return None


def log_status(records, now=None, max_age_s=POWER_LOG_MAX_AGE_S,
               check_freshness=True):
    """Return ``(healthy, reason)`` for parsed ``power_log.csv`` records.

    ``reason`` is one of ``'ok'``, ``'empty'``, ``'no-timestamps'``, ``'stale'``
    or ``'all-zero'``.  Freshness is skipped for an explicitly supplied
    historical log (``--power-log``): such a file is deliberately a saved
    record, so an old but non-zero log keeps its existing report behaviour.
    """
    items = list(records or [])
    if not items:
        return False, 'empty'
    epochs = []
    for record in items:
        if not isinstance(record, dict):
            continue
        raw = record.get('epoch')
        if raw in (None, ''):
            raw = record.get('timestamp')
        epoch = _epoch_of(raw)
        if epoch is not None:
            epochs.append(epoch)
    if not epochs:
        return False, 'no-timestamps'
    if check_freshness:
        reference = _finite(now)
        if reference is None:
            reference = time.time()
        window = _finite(max_age_s)
        if window is None or window < 0:
            window = POWER_LOG_MAX_AGE_S
        if reference - max(epochs) > window:
            return False, 'stale'
    if not any(abs(_finite(r.get('wall_w')) or 0.0) > _TINY_W
               for r in items if isinstance(r, dict)):
        return False, 'all-zero'
    return True, 'ok'


def records_from_samples(samples, wall_factor=1.0, now=None,
                         max_age_s=POWER_LOG_MAX_AGE_S):
    """Convert live ``Sample`` objects into power-log-shaped record dicts.

    Only samples within ``max_age_s`` of ``now`` are kept: an old session CSV
    is a saved history, not a live power source.
    """
    factor = _finite(wall_factor)
    if factor is None or factor <= 0:
        factor = 1.0
    reference = _finite(now)
    if reference is None:
        reference = time.time()
    window = _finite(max_age_s)
    if window is None or window < 0:
        window = POWER_LOG_MAX_AGE_S

    records = []
    for sample in samples or []:
        try:
            if isinstance(sample, dict):
                epoch = _epoch_of(sample.get('epoch', sample.get('timestamp')))
                watts = _finite(sample.get(
                    'wall_w', sample.get('watts')))
                pmic = _finite(sample.get('pmic_w'))
            else:
                epoch = _finite(getattr(sample, 'timestamp', None))
                if epoch is None:
                    epoch = _finite(sample[0])
                watts = _finite(getattr(sample, 'watts', None))
                if watts is None:
                    watts = _finite(sample[-1])
                pmic = None
        except (TypeError, ValueError, IndexError, KeyError):
            continue
        if epoch is None or watts is None or reference - epoch > window:
            continue
        if pmic is None:
            pmic = watts / factor
        records.append({
            'timestamp': _iso(epoch),
            'epoch': epoch,
            'pmic_w': pmic,
            'wall_w': watts,
        })
    return records


def record_from_live(live, now=None):
    """Return one power-log record from a live ADC snapshot (or ``None``)."""
    live = live or {}
    wall = _finite(live.get('wall_w'))
    if wall is None or wall <= _TINY_W:
        return None
    epoch = _finite(now)
    if epoch is None:
        epoch = time.time()
    pmic = _finite(live.get('pmic_w'))
    if pmic is None:
        pmic = wall
    return {
        'timestamp': _iso(epoch),
        'epoch': epoch,
        'pmic_w': pmic,
        'wall_w': wall,
    }


def _session_samples(recorder=None, session_samples=None, session_path=None):
    if session_samples is not None:
        return list(session_samples)
    if recorder is not None:
        return list(getattr(recorder, 'samples', []) or [])
    try:
        return dashboard.read_session_samples(session_path)
    except Exception:
        return []


def resolve(power_log=None, session_samples=None, recorder=None,
            session_path=None, explicit=False, live=None, env=None,
            now=None, max_age_s=POWER_LOG_MAX_AGE_S, wall_factor=1.0,
            read_live=True):
    """Return the best available power data.

    Result keys: ``records``, ``source`` (``'log'`` / ``'live'`` /
    ``'unavailable'``), ``label`` (e.g. ``'live (no cron)'``), ``hint``,
    ``live`` (the ADC snapshot or ``None``) and ``reason`` (why the log was
    rejected, or ``'ok'``).
    """
    records = analyze.parse_power_log(power_log) if power_log else []
    healthy, reason = log_status(
        records, now=now, max_age_s=max_age_s,
        check_freshness=not explicit)
    if healthy:
        return {
            'records': records, 'source': SOURCE_LOG,
            'label': SOURCE_LABELS[SOURCE_LOG], 'hint': '',
            'live': live, 'reason': 'ok',
        }

    samples = _session_samples(recorder, session_samples, session_path)
    live_records = records_from_samples(
        samples, wall_factor=wall_factor, now=now, max_age_s=max_age_s)
    if live is None and read_live:
        try:
            live = power_read.read_power(env=env)
        except Exception:
            live = None

    if not live_records:
        single = record_from_live(live, now=now)
        if single is not None:
            live_records = [single]

    if live_records:
        return {
            'records': live_records, 'source': SOURCE_LIVE,
            'label': SOURCE_LABELS[SOURCE_LIVE], 'hint': HINT_LIVE,
            'live': live, 'reason': reason,
        }
    return {
        'records': [], 'source': SOURCE_UNAVAILABLE,
        'label': SOURCE_LABELS[SOURCE_UNAVAILABLE],
        'hint': HINT_UNAVAILABLE, 'live': live, 'reason': reason,
    }
