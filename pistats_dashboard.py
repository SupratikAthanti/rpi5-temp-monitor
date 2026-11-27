"""Pure helpers for the live PiStats dashboard.

This module has **no** curses / hardware dependency: it holds the in-memory
sample ring buffer, the per-second session CSV log, the time-weighted energy
integral and the conversion of a time series into a character grid.  The TUI
uses these helpers; the tests exercise them without a terminal.

A sample is ``(timestamp, temp, watts)`` where ``timestamp`` is an epoch
seconds float.  Temperatures are degrees Celsius, power is wall watts.
"""

import bisect
import csv
import datetime
import math
import os
import time
from collections import deque
from typing import NamedTuple

SESSION_LOG_ENV = 'PISTATS_SESSION_LOG'
SESSION_LOG_FILENAME = 'pistats_session.csv'
MAX_SESSION_SAMPLES = 86400          # 24 h at 1 Hz
GRAPH_WINDOW_S = 300.0               # last five minutes
DEFAULT_RATE = 0.13                  # dollars per kWh (13.0 cents)


class Sample(NamedTuple):
    """One per-second observation: epoch timestamp, °C and wall watts."""

    timestamp: float
    temp: float
    watts: float


def _finite(value):
    """Return a float, or ``None`` for missing / non-finite values."""
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) else None


def _iso(timestamp):
    """UTC ISO-8601 second-resolution timestamp for CSV output."""
    moment = datetime.datetime.fromtimestamp(
        float(timestamp), datetime.timezone.utc)
    return moment.strftime('%Y-%m-%dT%H:%M:%SZ')


def session_log_path(log_dir=None):
    """Return the session CSV path.

    ``PISTATS_SESSION_LOG`` wins over ``log_dir``; the default is
    ``pistats_session.csv`` in ``log_dir`` (or the current directory).  The
    separate file keeps the shared ``temp_log.csv`` / ``power_log.csv``
    collector formats untouched and never writes duplicate rows to them.
    """
    override = os.environ.get(SESSION_LOG_ENV)
    if override:
        return override
    return os.path.join(log_dir or os.getcwd(), SESSION_LOG_FILENAME)


def append_session_sample(path, sample):
    """Append one ``Sample`` to the session CSV, creating its header once."""
    target = path or session_log_path()
    parent = os.path.dirname(os.path.abspath(target))
    if parent:
        os.makedirs(parent, exist_ok=True)
    needs_header = not os.path.exists(target) or os.path.getsize(target) == 0

    def cell(value):
        return '' if value is None else f'{float(value):.6f}'

    with open(target, 'a', newline='') as handle:
        writer = csv.writer(handle, lineterminator='\n')
        if needs_header:
            writer.writerow(['timestamp', 'temp_c', 'wall_w'])
        writer.writerow([
            _iso(sample.timestamp), cell(sample.temp), cell(sample.watts)])


def read_session_samples(path):
    """Read a session CSV back into ``Sample`` objects (empty on error)."""
    target = path or session_log_path()
    samples = []
    try:
        with open(target, 'r', newline='') as handle:
            for row in csv.DictReader(handle):
                stamp = (row.get('timestamp') or '').strip()
                if not stamp:
                    continue
                try:
                    epoch = datetime.datetime.fromisoformat(
                        stamp.replace('Z', '+00:00')).timestamp()
                except ValueError:
                    continue
                samples.append(Sample(
                    epoch, _finite(row.get('temp_c')), _finite(row.get('wall_w'))))
    except (OSError, ValueError, TypeError):
        return []
    return samples


class SessionRecorder:
    """Ring buffer of live samples plus its per-second CSV persistence."""

    def __init__(self, path=None, maxlen=MAX_SESSION_SAMPLES):
        self.path = (os.environ.get(SESSION_LOG_ENV) or path
                     or session_log_path())
        self.samples = deque(maxlen=max(1, int(maxlen)))

    def add(self, timestamp, temp=None, watts=None):
        """Record one sample (in memory and on disk); return the ``Sample``."""
        stamp = _finite(timestamp)
        if stamp is None:
            stamp = time.time()
        sample = Sample(stamp, _finite(temp), _finite(watts))
        self.samples.append(sample)
        append_session_sample(self.path, sample)
        return sample

    def temp_series(self):
        return [(s.timestamp, s.temp) for s in self.samples
                if s.temp is not None]

    def power_series(self):
        return [(s.timestamp, s.watts) for s in self.samples
                if s.watts is not None]

    def energy_wh(self, until=None):
        return session_energy_wh(self.power_series(), until=until)

    def context(self, rate=DEFAULT_RATE, elapsed=0, now=None):
        """Dashboard context: graph series, energy, cost and elapsed time."""
        wh = self.energy_wh(until=now)
        rate_value = _finite(rate)
        if rate_value is None or rate_value < 0:
            rate_value = DEFAULT_RATE
        cost = session_cost_dollars(wh, rate_value)
        return {
            'session_temp_samples': self.temp_series(),
            'session_power_samples': self.power_series(),
            'session_energy_wh': wh,
            'session_energy_text': f'{wh:.3f} Wh',
            'session_cost': f'${cost:.6f}',
            'session_cost_value': cost,
            'session_elapsed': _elapsed_text(elapsed),
            'session_rate': rate_value,
            'session_samples': len(self.samples),
            'session_now': now if _finite(now) is not None else (
                self.samples[-1].timestamp if self.samples else time.time()),
        }


def _elapsed_text(seconds):
    try:
        total = max(0, int(float(seconds)))
    except (TypeError, ValueError, OverflowError):
        total = 0
    return f'{total // 3600:02d}:{(total % 3600) // 60:02d}:{total % 60:02d}'


def _series_points(samples, nonnegative=False):
    """Coerce a series into finite ``(epoch, value)`` pairs."""
    points = []
    for item in samples or []:
        try:
            if isinstance(item, dict):
                stamp = _finite(item.get('timestamp'))
                value = _finite(item.get(
                    'value', item.get('watts', item.get('wall_w'))))
            else:
                stamp = _finite(item[0])
                # 2-tuples are (timestamp, value); 3-tuples are the full
                # (timestamp, temp, watts) samples stored by the recorder.
                value = _finite(item[-1])
        except (TypeError, ValueError, IndexError, KeyError):
            continue
        if stamp is None or value is None:
            continue
        if nonnegative:
            value = max(0.0, value)
        points.append((stamp, value))
    points.sort(key=lambda pair: pair[0])
    return points


def session_energy_wh(samples, until=None):
    """Time-weighted energy in Wh from ``(timestamp, watts)`` samples.

    Adjacent samples are integrated with the trapezoidal rule; negative power
    readings are clamped to zero and non-finite entries are skipped.  When
    ``until`` is a later epoch, the last reading is held for the trailing
    interval (capped at five minutes) so the very start of a session still
    accumulates energy.  Empty / single-sample histories yield ``0.0``.
    """
    points = _series_points(samples, nonnegative=True)
    if not points:
        return 0.0
    watt_seconds = 0.0
    for (t0, w0), (t1, w1) in zip(points, points[1:]):
        if t1 >= t0:
            watt_seconds += 0.5 * (w0 + w1) * (t1 - t0)
    end = _finite(until)
    if end is not None:
        last_t, last_w = points[-1]
        trailing = min(max(0.0, end - last_t), GRAPH_WINDOW_S)
        watt_seconds += last_w * trailing
    return watt_seconds / 3600.0


def session_cost_dollars(energy_wh_value, rate_per_kwh=DEFAULT_RATE):
    """Cost in dollars for ``energy_wh_value`` Wh at ``rate_per_kwh`` $/kWh."""
    wh = _finite(energy_wh_value)
    rate = _finite(rate_per_kwh)
    if wh is None or rate is None or wh < 0 or rate < 0:
        return 0.0
    return wh / 1000.0 * rate


def _fit(text, width):
    text = str(text)
    if width <= 0:
        return ''
    if len(text) <= width:
        return text
    if width == 1:
        return '…'
    return text[:width - 1] + '…'


def _interpolate(times, values, at):
    """Linear interpolation (nearest value outside the sample range)."""
    idx = bisect.bisect_right(times, at)
    if idx <= 0:
        return values[0]
    if idx >= len(values):
        return values[-1]
    t_left, t_right = times[idx - 1], times[idx]
    if t_right <= t_left:
        return values[idx - 1]
    fraction = (at - t_left) / (t_right - t_left)
    return values[idx - 1] + fraction * (values[idx] - values[idx - 1])


def render_graph(samples, width=48, height=6, *, title='', unit='',
                 current=None, window_s=GRAPH_WINDOW_S, now=None,
                 nonnegative=False):
    """Render a time series as labelled, multi-line characters.

    Returns a list of strings: a header (title, current value, time window),
    ``height`` plot rows with a y axis, and a footer with min / max and the
    plotted time span.  Pure: no curses, no I/O, no exceptions on bad input.
    """
    plot_w = max(4, int(width) - 2)
    plot_h = max(1, int(height))
    points = _series_points(samples, nonnegative=nonnegative)
    window = _finite(window_s)
    if window is None or window <= 0:
        window = GRAPH_WINDOW_S

    end = _finite(now)
    if points:
        latest, last_value = points[-1]
        end = latest if end is None else max(end, latest)
        if end - latest > window:
            points = [(t, v) for t, v in points if t >= end - window]
            if not points:
                points = [(latest, last_value)]
        start = points[0][0]
        span = max(1e-6, end - start)
    else:
        end = end if end is not None else time.time()
        start = end - window
        span = window

    current_value = _finite(current)
    if current_value is None and points:
        current_value = points[-1][1]
    shown = ('—' if current_value is None
             else f'{current_value:.1f}{unit}')
    header = _fit(
        f'{title}  now {shown}  last {int(window / 60)}m', width)

    grid = [[' '] * plot_w for _ in range(plot_h)]
    if not points:
        grid[-1] = ['·'] * plot_w
        axis = _fit(f'min —  max —  no samples yet', width)
    else:
        times = [t for t, _ in points]
        values = [v for _, v in points]
        low, high = min(values), max(values)
        if high <= low:
            pad = max(0.5, abs(low) * 0.01)
            low, high = low - pad, high + pad
        for x in range(plot_w):
            position = start if plot_w == 1 else start + span * x / (plot_w - 1)
            value = _interpolate(times, values, position)
            row = int(round((high - value) / (high - low) * (plot_h - 1)))
            row = max(0, min(plot_h - 1, row))
            grid[row][x] = '█'
            for below in range(row + 1, plot_h):
                grid[below][x] = '░'
        real_low, real_high = min(values), max(values)
        clock = lambda stamp: time.strftime('%H:%M:%S', time.localtime(stamp))
        axis = _fit(
            f'min {real_low:.1f}{unit}  max {real_high:.1f}{unit}  '
            f'{clock(start)}→{clock(end)}', width)

    lines = [header]
    for index, row in enumerate(grid):
        y_label = '┤' if index == plot_h // 2 else '│'
        lines.append(_fit(y_label + ''.join(row).rstrip(), width))
    lines.append(axis)
    return lines
