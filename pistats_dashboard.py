"""Pure helpers for the live PiStats dashboard.

This module has **no** curses / hardware dependency: it holds the in-memory
sample history (full-resolution recent samples plus time-bucketed aggregates
for older data), the per-second session CSV log, the time-weighted energy
integral and the conversion of a time series into a character grid.  The TUI
uses these helpers; the tests exercise them without a terminal.

A sample is ``(timestamp, temp, watts)`` where ``timestamp`` is an epoch
seconds float.  Temperatures are degrees Celsius, power is wall watts.

Graphs are drawn with Unicode braille cells (U+2800..U+28FF).  Every character
cell contains a 2 x 4 grid of subpixels, so a plot has twice the horizontal and
four times the vertical resolution of a character grid while remaining a
single codepoint per cell (the existing clipping / length logic still works).
Each plotted column is a one-subpixel-wide stroke covering that column's
min/max envelope, so short spikes survive downsampling.
"""

import csv
import datetime
import math
import os
import time
from collections import deque
from typing import NamedTuple

SESSION_LOG_ENV = 'PISTATS_SESSION_LOG'
SESSION_LOG_FILENAME = 'pistats_session.csv'
MAX_SESSION_SAMPLES = 86400          # hard cap on full-resolution recent samples
RECENT_WINDOW_S = 3600.0             # keep 1 Hz detail for the last hour
BUCKET_S = 60.0                      # one-minute min/max/avg bins for older data
MAX_HISTORY_S = 30 * 86400           # keep ~30 days of compacted history
MIN_WINDOW_S = 30.0                  # never magnify the first few seconds
WINDOW_STEPS_S = (
    30.0, 60.0, 120.0, 300.0, 600.0, 900.0, 1800.0,
    3600.0, 3 * 3600.0, 6 * 3600.0, 12 * 3600.0,
    86400.0, 2 * 86400.0, 3 * 86400.0, 7 * 86400.0,
    14 * 86400.0, 30 * 86400.0,
)
GRAPH_WINDOW_S = 300.0               # legacy fallback: trailing energy hold cap
DEFAULT_RATE = 0.13                  # dollars per kWh (13.0 cents)
MIN_Y_RANGE = {'°C': 2.0, 'W': 1.0}  # startup anti-magnification floors
BRAILLE_BASE = 0x2800


class Sample(NamedTuple):
    """One per-second observation: epoch timestamp, °C and wall watts."""

    timestamp: float
    temp: float
    watts: float


class Band(NamedTuple):
    """One plottable time slice: time, low, high and representative value.

    Full-resolution samples have ``low == high == value``.  Compact bins carry
    their min/max in ``low``/``high`` and their average in ``value`` so the
    renderer can draw a thin envelope instead of hiding spikes.
    """

    timestamp: float
    low: float
    high: float
    value: float


class Bucket(NamedTuple):
    """A time-aligned aggregate of samples older than ``RECENT_WINDOW_S``."""

    key: int                 # integer bucket index (timestamp // BUCKET_S)
    start: float             # first sample timestamp in the bucket
    end: float               # last sample timestamp in the bucket
    count: int
    temp_min: float
    temp_max: float
    temp_sum: float
    temp_count: int
    watts_min: float
    watts_max: float
    watts_sum: float
    watts_count: int
    watts_first: float
    watts_last: float
    watt_seconds: float      # trapezoid integral inside the bucket


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
    samples.sort(key=lambda sample: sample.timestamp)
    return samples


def adaptive_window_s(span_s):
    """Pick a display window that grows with the recorded session span.

    Short sessions get a 30 s floor so a handful of startup samples are not
    magnified; longer sessions step through minutes, hours and finally days /
    weeks, capped at ``MAX_HISTORY_S``.
    """
    span = _finite(span_s)
    if span is None or span <= 0:
        return MIN_WINDOW_S
    for step in WINDOW_STEPS_S:
        if span <= step:
            return step
    return float(WINDOW_STEPS_S[-1])


def format_window_s(window_s):
    """Compact window text for a graph header: ``47s``, ``12m``, ``3h``, ``6d``."""
    seconds = _finite(window_s)
    if seconds is None or seconds <= 0:
        return f'{int(MIN_WINDOW_S)}s'
    total = int(round(seconds))
    if total < 60:
        return f'{total}s'
    if total < 3600:
        minutes = int(round(total / 60.0))
        return f'{minutes}m' if minutes < 60 else '1h'
    if total < 86400:
        hours = int(round(total / 3600.0))
        return f'{hours}h' if hours < 24 else '1d'
    return f'{int(round(total / 86400.0))}d'


def format_tick(stamp, span, start=None):
    """Format one x-axis tick, adding dates whenever the window crosses days."""
    moment = datetime.datetime.fromtimestamp(
        float(stamp)).astimezone()
    span = _finite(span) or 0.0
    if span < 120.0:
        return moment.strftime('%H:%M:%S')
    crosses_day = False
    if start is not None:
        first = datetime.datetime.fromtimestamp(float(start)).astimezone()
        crosses_day = first.date() != moment.date() or (
            datetime.datetime.fromtimestamp(
                float(start) + span).astimezone().date() != first.date())
    if span < 2 * 86400.0:
        return moment.strftime('%m-%d %H:%M') if crosses_day else moment.strftime('%H:%M')
    if span < 7 * 86400.0:
        return moment.strftime('%m-%d %H:%M')
    return moment.strftime('%Y-%m-%d')


class SessionRecorder:
    """Recent 1 Hz samples, compacted history and per-second CSV persistence.

    Recent samples stay at full resolution for ``recent_window_s``; anything
    older is folded into one-minute min/max/avg buckets.  The session CSV is
    read **once** at construction so a long-running dashboard does not re-read
    the log on every frame, while ``power_source`` keeps using ``.samples``
    (recent readings only) for live-power decisions.
    """

    def __init__(self, path=None, maxlen=MAX_SESSION_SAMPLES,
                 bucket_s=BUCKET_S, recent_window_s=RECENT_WINDOW_S,
                 history_limit_s=MAX_HISTORY_S, load=True):
        self.path = (os.environ.get(SESSION_LOG_ENV) or path
                     or session_log_path())
        self.maxlen = max(2, int(maxlen))
        self.bucket_s = max(1.0, _finite(bucket_s) or BUCKET_S)
        self.recent_window_s = max(1.0, _finite(recent_window_s)
                                   or RECENT_WINDOW_S)
        self.history_limit_s = max(self.bucket_s,
                                   _finite(history_limit_s) or MAX_HISTORY_S)
        self.max_buckets = max(2, int(self.history_limit_s / self.bucket_s) + 2)
        self.samples = deque(maxlen=self.maxlen)
        self.buckets = deque(maxlen=self.max_buckets)
        self._loaded = False
        if load:
            self.load_history()

    # -- ingestion / compaction --------------------------------------------

    def load_history(self):
        """Read the session CSV once into the compacted in-memory history."""
        if self._loaded:
            return 0
        self._loaded = True
        rows = read_session_samples(self.path)
        for sample in rows:
            self._ingest(sample, persist=False)
        return len(rows)

    def add(self, timestamp, temp=None, watts=None):
        """Record one sample (in memory and on disk); return the ``Sample``."""
        stamp = _finite(timestamp)
        if stamp is None:
            stamp = time.time()
        sample = Sample(stamp, _finite(temp), _finite(watts))
        self._ingest(sample, persist=True)
        return sample

    def _ingest(self, sample, persist=True):
        if persist:
            append_session_sample(self.path, sample)
        self.samples.append(sample)
        while len(self.samples) > 1 and (
                self.samples[-1].timestamp - self.samples[0].timestamp
                > self.recent_window_s or len(self.samples) > self.maxlen):
            self._absorb(self.samples.popleft())
        # Prune by recorded span (not wall clock), so saved logs work too.
        if self.buckets:
            newest = max(self.buckets[-1].end,
                         self.samples[-1].timestamp if self.samples else
                         self.buckets[-1].end)
            while self.buckets and (
                    newest - self.buckets[0].end > self.history_limit_s or
                    len(self.buckets) > self.max_buckets):
                self.buckets.popleft()

    def _absorb(self, sample):
        """Fold an aged-out sample into its minute bucket."""
        stamp = sample.timestamp
        temp = _finite(sample.temp)
        watts = _finite(sample.watts)
        key = int(stamp // self.bucket_s)
        if self.buckets and self.buckets[-1].key == key:
            old = self.buckets[-1]
            step = max(0.0, stamp - old.end)
            added_ws = (0.5 * (old.watts_last + watts) * step
                        if watts is not None and old.watts_count else 0.0)
            bucket = Bucket(
                key=old.key, start=min(old.start, stamp),
                end=max(old.end, stamp), count=old.count + 1,
                temp_min=old.temp_min if temp is None or (
                    old.temp_min is not None and old.temp_min < temp) else temp,
                temp_max=old.temp_max if temp is None or (
                    old.temp_max is not None and old.temp_max > temp) else temp,
                temp_sum=old.temp_sum + (temp if temp is not None else 0.0),
                temp_count=old.temp_count + (1 if temp is not None else 0),
                watts_min=old.watts_min if watts is None or (
                    old.watts_min is not None and old.watts_min < watts)
                else watts,
                watts_max=old.watts_max if watts is None or (
                    old.watts_max is not None and old.watts_max > watts)
                else watts,
                watts_sum=old.watts_sum + (watts if watts is not None else 0.0),
                watts_count=old.watts_count + (1 if watts is not None else 0),
                watts_first=(watts if old.watts_first is None else
                             old.watts_first),
                watts_last=watts if watts is not None else old.watts_last,
                watt_seconds=old.watt_seconds + added_ws)
            self.buckets[-1] = bucket
        else:
            self.buckets.append(Bucket(
                key=key, start=stamp, end=stamp, count=1,
                temp_min=temp, temp_max=temp,
                temp_sum=temp if temp is not None else 0.0,
                temp_count=1 if temp is not None else 0,
                watts_min=watts, watts_max=watts,
                watts_sum=watts if watts is not None else 0.0,
                watts_count=1 if watts is not None else 0,
                watts_first=watts, watts_last=watts,
                watt_seconds=0.0))

    # -- graph series -------------------------------------------------------

    @staticmethod
    def _bucket_band(bucket, field):
        if field == 'temp':
            if not bucket.temp_count:
                return None
            low, high = bucket.temp_min, bucket.temp_max
            value = bucket.temp_sum / bucket.temp_count
        else:
            if not bucket.watts_count:
                return None
            low, high = bucket.watts_min, bucket.watts_max
            value = bucket.watts_sum / bucket.watts_count
        if low is None or high is None:
            return None
        middle = bucket.start + (bucket.end - bucket.start) / 2.0
        return Band(middle, low, high, value)

    def temp_bands(self):
        """Temperature plot slices: compact bins first, then recent samples."""
        bands = [self._bucket_band(bucket, 'temp') for bucket in self.buckets]
        bands.extend(Band(sample.timestamp, sample.temp, sample.temp, sample.temp)
                     for sample in self.samples if sample.temp is not None)
        return [band for band in bands if band is not None]

    def power_bands(self):
        """Wall-power plot slices: compact bins first, then recent samples."""
        bands = [self._bucket_band(bucket, 'watts') for bucket in self.buckets]
        bands.extend(Band(sample.timestamp, sample.watts, sample.watts,
                          sample.watts)
                     for sample in self.samples if sample.watts is not None)
        return [band for band in bands if band is not None]

    def temp_series(self):
        return [(band.timestamp, band.value) for band in self.temp_bands()]

    def power_series(self):
        return [(band.timestamp, band.value) for band in self.power_bands()]

    @property
    def sample_count(self):
        return sum(bucket.count for bucket in self.buckets) + len(self.samples)

    def first_timestamp(self):
        candidates = [bucket.start for bucket in self.buckets]
        candidates.extend(sample.timestamp for sample in self.samples)
        return min(candidates) if candidates else None

    # -- energy / context ----------------------------------------------------

    def energy_wh(self, until=None):
        """Duration-weighted energy across compacted and recent samples.

        Bucket-internal trapezoids, cross-bucket trapezoids and the recent
        deque are chained together, which exactly reproduces integrating the
        original 1 Hz series.  The trailing hold is capped like
        ``session_energy_wh`` so a quiet session does not accrue energy forever.
        """
        watt_seconds = sum(bucket.watt_seconds for bucket in self.buckets)
        compacted = list(self.buckets)
        for older, newer in zip(compacted, compacted[1:]):
            if older.watts_last is not None and newer.watts_first is not None:
                gap = max(0.0, newer.start - older.end)
                watt_seconds += 0.5 * (older.watts_last +
                                       newer.watts_first) * gap
        chain = []
        if self.buckets and self.buckets[-1].watts_last is not None:
            chain.append((self.buckets[-1].end, self.buckets[-1].watts_last))
        chain.extend((sample.timestamp, sample.watts)
                     for sample in self.samples if sample.watts is not None)
        for (t0, w0), (t1, w1) in zip(chain, chain[1:]):
            if t1 >= t0:
                watt_seconds += 0.5 * (w0 + w1) * (t1 - t0)
        end = _finite(until)
        if end is not None and chain:
            last_t, last_w = chain[-1]
            trailing = min(max(0.0, end - last_t), GRAPH_WINDOW_S)
            watt_seconds += last_w * trailing
        return max(0.0, watt_seconds) / 3600.0

    def context(self, rate=DEFAULT_RATE, elapsed=0, now=None):
        """Dashboard context: graph series, energy, cost and elapsed time."""
        wall_now = _finite(now)
        if wall_now is None:
            wall_now = (self.samples[-1].timestamp if self.samples
                        else time.time())
        wh = self.energy_wh(until=wall_now)
        rate_value = _finite(rate)
        if rate_value is None or rate_value < 0:
            rate_value = DEFAULT_RATE
        cost = session_cost_dollars(wh, rate_value)
        first = self.first_timestamp()
        if first is not None:
            elapsed = max(_finite(elapsed) or 0.0, max(0.0, wall_now - first))
        return {
            'session_temp_samples': self.temp_series(),
            'session_power_samples': self.power_series(),
            'session_temp_bands': self.temp_bands(),
            'session_power_bands': self.power_bands(),
            'session_energy_wh': wh,
            'session_energy_text': f'{wh:.3f} Wh',
            'session_cost': f'${cost:.6f}',
            'session_cost_value': cost,
            'session_elapsed': _elapsed_text(elapsed),
            'session_rate': rate_value,
            'session_samples': self.sample_count,
            'session_now': wall_now,
        }


def _elapsed_text(seconds):
    try:
        total = max(0, int(float(seconds)))
    except (TypeError, ValueError, OverflowError):
        total = 0
    days, remainder = divmod(total, 86400)
    hours, remainder = divmod(remainder, 3600)
    minutes, secs = divmod(remainder, 60)
    clock = f'{hours:02d}:{minutes:02d}:{secs:02d}'
    return f'{days}d {clock}' if days else clock


def _series_points(samples, nonnegative=False):
    """Coerce a series into finite ``(epoch, value)`` pairs."""
    points = []
    for item in samples or []:
        try:
            if isinstance(item, (Band, Sample)) or hasattr(item, 'timestamp'):
                stamp = _finite(item.timestamp)
                if isinstance(item, Band):
                    value = _finite(item.value)
                elif isinstance(item, Sample):
                    value = _finite(item.watts)
                else:
                    value = _finite(getattr(item, 'value', None))
            elif isinstance(item, dict):
                stamp = _finite(item.get('timestamp'))
                value = _finite(item.get(
                    'value', item.get('watts', item.get('wall_w'))))
            else:
                stamp = _finite(item[0])
                # 2-tuples are (timestamp, value); 3-tuples are the full
                # (timestamp, temp, watts) samples stored by the recorder.
                value = _finite(item[-1])
        except (TypeError, ValueError, IndexError, KeyError, AttributeError):
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


def _graph_bands(samples, nonnegative=False):
    """Coerce samples / bands into sorted ``(time, low, high, value)`` tuples."""
    result = []
    for item in samples or []:
        try:
            if isinstance(item, Band):
                stamp = _finite(item.timestamp)
                low, high = _finite(item.low), _finite(item.high)
                value = _finite(item.value)
            elif isinstance(item, (Sample, dict)) or hasattr(
                    item, 'timestamp'):
                if isinstance(item, dict):
                    stamp = _finite(item.get('timestamp'))
                    raw = item.get('value', item.get(
                        'watts', item.get('wall_w')))
                else:
                    stamp = _finite(item.timestamp)
                    raw = (item.watts if isinstance(item, Sample)
                           else getattr(item, 'value', None))
                low = high = value = _finite(raw)
            else:
                stamp = _finite(item[0])
                if len(item) >= 4:
                    low, high, value = (_finite(item[1]), _finite(item[2]),
                                        _finite(item[3]))
                else:
                    low = high = value = _finite(item[-1])
        except (TypeError, ValueError, IndexError, KeyError, AttributeError):
            continue
        if stamp is None or value is None:
            continue
        if low is None:
            low = value
        if high is None:
            high = value
        if low > high:
            low, high = high, low
        if nonnegative:
            low, value, high = max(0.0, low), max(0.0, value), max(0.0, high)
        result.append((stamp, low, high, value))
    result.sort(key=lambda band: band[0])
    return result


def series_y_bounds(samples, unit='', nonnegative=False):
    """Y-axis bounds with a unit-aware floor for tiny early variations."""
    bands = _graph_bands(samples, nonnegative=nonnegative)
    if not bands:
        return (0.0, 1.0)
    low = min(band[1] for band in bands)
    high = max(band[2] for band in bands)
    floor = float(MIN_Y_RANGE.get(unit, 1.0))
    if high - low < floor:
        middle = (low + high) / 2.0
        low, high = middle - floor / 2.0, middle + floor / 2.0
        if nonnegative and low < 0.0:
            low, high = 0.0, max(floor, high)
    return (low, high)


def _tick_row(start, end, width):
    """Evenly spaced time labels, with dates when the span crosses days."""
    if width <= 10:
        return ''
    span = max(1e-6, end - start)
    count = max(2, min(5, (width - 2) // 14))
    cells = [' '] * width  # column 0 is reserved for the y-axis glyph
    previous = 0
    for index in range(count):
        stamp = start + span * index / (count - 1)
        label = format_tick(stamp, span, start)
        room = width - len(label) - 1
        target = 1 + int(index * room / (count - 1)) if room > 0 else 1
        position = min(max(previous + 1, target), width - 1)
        label = label[:width - position]
        for offset, char in enumerate(label):
            cells[position + offset] = char
        previous = position + len(label) - 1
    return ''.join(cells)


def _braille_dot(column, row):
    """Braille bit for a subpixel (two dot columns, four dot rows per cell)."""
    return 1 << (row + 4 * column)


def _draw_braille_line(bits, columns, rows, x0, y0, x1, y1):
    """Bresenham stroke between two subpixels (one subpixel thick)."""
    dx = abs(x1 - x0)
    dy = -abs(y1 - y0)
    sx = 1 if x0 < x1 else -1
    sy = 1 if y0 < y1 else -1
    error = dx + dy
    while True:
        if 0 <= x0 < columns and 0 <= y0 < rows:
            bits[y0][x0] |= _braille_dot(x0 % 2, y0 % 4)
        if x0 == x1 and y0 == y1:
            break
        doubled = 2 * error
        if doubled >= dy:
            error += dy
            x0 += sx
        if doubled <= dx:
            error += dx
            y0 += sy


def braille_sparkline(values, width=12):
    """One-row braille trend (thin dots) for very small terminals."""
    columns = max(2, int(width) * 2)
    numbers = []
    for value in values or []:
        number = _finite(value)
        if number is not None:
            numbers.append(number)
    if not numbers:
        return ' ' * max(0, int(width))
    low, high = min(numbers), max(numbers)
    if high <= low:
        low, high = low - 1.0, high + 1.0
    bits = [[0] * columns for _ in range(4)]

    def point(index, value):
        column = min(columns - 1, max(0,
                     int(index * (columns - 1) / max(1, len(numbers) - 1))))
        row = min(3, max(0, int((high - value) / (high - low) * 4)))
        return column, row

    mapped = [point(index, value) for index, value in enumerate(numbers)]
    for (c0, r0), (c1, r1) in zip(mapped, mapped[1:]):
        _draw_braille_line(bits, columns, 4, c0, r0, c1, r1)
    if len(mapped) == 1:
        column, row = mapped[0]
        bits[row][column] |= _braille_dot(column % 2, row % 4)
    characters = []
    for x in range(0, columns, 2):
        mask = 0
        for dot_col in (0, 1):
            for dot_row in range(4):
                if bits[dot_row][x + dot_col]:
                    mask |= 1 << (dot_row + 4 * dot_col)
        characters.append(chr(BRAILLE_BASE + mask) if mask else ' ')
    return ''.join(characters)[:max(0, int(width))]


def render_graph(samples, width=48, height=6, *, title='', unit='',
                 current=None, window_s=None, now=None, nonnegative=False):
    """Render a time series as labelled, thin braille lines.

    Returns a list of strings: a header (title, current value, adaptive time
    window), ``height`` plot rows (2 x 4 braille subpixels per cell), an x-axis
    row with several tick labels and a footer with min / max and the time span.
    Pure: no curses, no I/O, no exceptions on bad input.
    """
    plot_w = max(4, int(width) - 2)
    plot_h = max(1, int(height))
    bands = _graph_bands(samples, nonnegative=nonnegative)

    explicit = _finite(window_s)
    if bands and (latest := bands[-1][0]) >= bands[0][0]:
        span = latest - bands[0][0]
    else:
        span = 0.0
    window = explicit if explicit is not None and explicit > 0 else (
        adaptive_window_s(span))

    end = _finite(now)
    if bands:
        latest = bands[-1][0]
        if end is None:
            end = latest
        elif end - latest > window:
            # A saved / paused history is plotted up to its last sample
            # instead of leaving the whole graph empty.
            end = latest
        else:
            end = max(end, latest)
    else:
        end = end if end is not None else time.time()
    start = end - window
    visible = [band for band in bands if band[0] >= start - 1e-9]
    if bands and not visible:
        visible = bands[-1:]

    current_value = _finite(current)
    if current_value is None and visible:
        current_value = visible[-1][3]
    shown = ('—' if current_value is None
             else f'{current_value:.1f}{unit}')
    header = _fit(f'{title}  now {shown}  last {format_window_s(window)}',
                  width)

    columns, rows = plot_w * 2, plot_h * 4
    bits = [[0] * columns for _ in range(rows)]
    if not visible:
        grid = [[' '] * plot_w for _ in range(plot_h)]
        notice = 'waiting for samples'
        offset = max(0, (plot_w - len(notice)) // 2)
        for index, char in enumerate(notice[:plot_w - offset]):
            grid[0][offset + index] = char
        axis = 'min —  max —  waiting for samples'
    else:
        low, high = series_y_bounds(visible, unit=unit,
                                    nonnegative=nonnegative)
        real_low = min(band[1] for band in visible)
        real_high = max(band[2] for band in visible)

        def to_column(stamp):
            return max(0, min(columns - 1,
                              int((stamp - start) / window * columns)))

        def to_row(value):
            return max(0, min(rows - 1,
                              int((high - value) / (high - low) * rows)))

        envelope = {}
        representatives = {}
        for stamp, band_low, band_high, value in visible:
            column = to_column(stamp)
            top, bottom = to_row(band_high), to_row(band_low)
            if column in envelope:
                top = min(top, envelope[column][0])
                bottom = max(bottom, envelope[column][1])
            envelope[column] = (top, bottom)
            representatives[column] = to_row(value)
        for column, (top, bottom) in envelope.items():
            for row in range(top, bottom + 1):
                bits[row][column] |= _braille_dot(column % 2, row % 4)
        ordered = sorted(representatives)
        for left, right in zip(ordered, ordered[1:]):
            _draw_braille_line(bits, columns, rows, left,
                               representatives[left], right,
                               representatives[right])

        clock = lambda stamp: format_tick(stamp, window, start)
        axis = (f'min {real_low:.1f}{unit}  max {real_high:.1f}{unit}  '
                f'{clock(start)}→{clock(end)}')
        grid = []
        for char_row in range(plot_h):
            line = []
            for char_col in range(plot_w):
                mask = 0
                for dot_col in (0, 1):
                    for dot_row in range(4):
                        if bits[char_row * 4 + dot_row][char_col * 2 + dot_col]:
                            mask |= 1 << (dot_row + 4 * dot_col)
                line.append(chr(BRAILLE_BASE + mask) if mask else ' ')
            grid.append(line)

    lines = [header]
    for index, row in enumerate(grid):
        y_label = '┤' if index == plot_h // 2 else '│'
        lines.append(_fit(y_label + ''.join(row), width))
    lines.append(_fit(_tick_row(start, end, width), width))
    lines.append(_fit(axis, width))
    return lines
