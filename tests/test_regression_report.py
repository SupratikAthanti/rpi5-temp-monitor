"""Regression tests for the 2026-11-21 QA audit of analyze.py.

Bugs forced: 3 (``/range`` must actually filter time-series records),
6 (report title must say PiStats).  All fixtures are written to temp dirs; no
repo data, home config or hardware is touched.  Required interfaces are in
``EXPECTED_API.md`` section 14.
"""

import datetime as dt
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import analyze  # noqa: E402

HEADER = ('timestamp,soc_temp_c,pmic_temp_c,load_1m,arm_freq_mhz,'
          'throttled_hex,top_proc,top_cpu_pct')
CONFIG = {'rate_per_kwh': 0.13, 'battery_wh': 45.0,
          'energy_windows_min': [60, 120]}


def epoch_of(text):
    return dt.datetime.fromisoformat(text.replace('Z', '+00:00')).timestamp()


def daily_timestamps(first_day=1, last_day=11):
    return [f'2026-11-{day:02d}T00:00:00Z'
            for day in range(first_day, last_day + 1)]


def hourly_timestamps(count=25):
    base = dt.datetime(2026, 11, 1, tzinfo=dt.timezone.utc)
    return [(base + dt.timedelta(hours=i)).strftime('%Y-%m-%dT%H:%M:%SZ')
            for i in range(count)]


def write_temp_log(path, timestamps):
    lines = [HEADER]
    for ts in timestamps:
        lines.append(f'{ts},60.0,N/A,0.5,2400,0x0,ps,1.0')
    with open(path, 'w') as f:
        f.write('\n'.join(lines) + '\n')


def write_power_log(path, timestamps, wall_values):
    with open(path, 'w') as f:
        f.write('timestamp,pmic_w,wall_w\n')
        for ts, wall in zip(timestamps, wall_values):
            f.write(f'{ts},{wall},{wall}\n')


class TestRangeFilteringHelpers(unittest.TestCase):
    """BUG 3: a real, deterministic time-filtering interface is required."""

    def test_relative_7d_from_latest_record(self):
        stamps = daily_timestamps()
        # Temperature records have NO 'epoch' key: the helper must parse the
        # ISO-8601 timestamps itself.
        records = [{'timestamp': ts, 'soc_temp': 60.0} for ts in stamps]
        now = epoch_of(stamps[-1])  # 2026-11-11T00:00:00Z

        kept = analyze.filter_records_by_range(records, '7d', now=now)
        # 7d window is inclusive of the boundary: Nov 04 .. Nov 11 = 8 days.
        self.assertEqual(len(kept), 8)
        self.assertEqual(kept[0]['timestamp'], '2026-11-04T00:00:00Z')
        self.assertEqual(kept[-1]['timestamp'], '2026-11-11T00:00:00Z')
        self.assertNotIn('2026-11-03T00:00:00Z',
                         [r['timestamp'] for r in kept])

    def test_relative_2h(self):
        stamps = hourly_timestamps(25)
        records = [{'timestamp': ts, 'epoch': epoch_of(ts), 'wall_w': 1.0}
                   for ts in stamps]
        kept = analyze.filter_records_by_range(
            records, '2h', now=epoch_of(stamps[-1]))
        self.assertEqual(len(kept), 3)
        self.assertEqual([r['timestamp'] for r in kept], [
            '2026-11-01T22:00:00Z',
            '2026-11-01T23:00:00Z',
            '2026-11-02T00:00:00Z',
        ])

    def test_explicit_date_range(self):
        stamps = daily_timestamps()
        records = [{'timestamp': ts, 'epoch': epoch_of(ts)} for ts in stamps]
        kept = analyze.filter_records_by_range(
            records, '2026-11-08..2026-11-10', now=epoch_of(stamps[-1]))
        self.assertEqual([r['timestamp'] for r in kept], [
            '2026-11-08T00:00:00Z',
            '2026-11-09T00:00:00Z',
            '2026-11-10T00:00:00Z',
        ])

    def test_none_spec_keeps_everything(self):
        stamps = daily_timestamps()
        records = [{'timestamp': ts} for ts in stamps]
        self.assertEqual(len(analyze.filter_records_by_range(records, None)),
                         len(records))


class TestBuildReportRangeIsExecuted(unittest.TestCase):
    """BUG 3: build_report must apply range_spec, not just label it."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.stamps = daily_timestamps()
        self.log = os.path.join(self.tmp.name, 'temp_log.csv')
        write_temp_log(self.log, self.stamps)

    def tearDown(self):
        self.tmp.cleanup()

    def test_7d_limits_temperature_rows_and_span(self):
        report = analyze.build_report(self.log, config=CONFIG,
                                      range_spec='7d')
        self.assertIn('Range             : 7d', report)
        self.assertIn('Total Samples     : 8', report)
        self.assertIn(
            '2026-11-04T00:00:00Z to 2026-11-11T00:00:00Z', report)
        self.assertNotIn('2026-11-01T00:00:00Z', report,
                         'out-of-window rows leaked into the report')

    def test_unfiltered_report_keeps_all_rows(self):
        report = analyze.build_report(self.log, config=CONFIG)
        self.assertIn('Total Samples     : 11', report)
        self.assertIn(
            '2026-11-01T00:00:00Z to 2026-11-11T00:00:00Z', report)

    def test_2h_limits_rows_to_the_last_two_hours(self):
        stamps = hourly_timestamps(25)
        log = os.path.join(self.tmp.name, 'hourly.csv')
        write_temp_log(log, stamps)
        report = analyze.build_report(log, config=CONFIG, range_spec='2h')
        self.assertIn('Total Samples     : 3', report)
        self.assertIn(
            '2026-11-01T22:00:00Z to 2026-11-02T00:00:00Z', report)

    def test_power_log_is_filtered_too(self):
        power = os.path.join(self.tmp.name, 'power_log.csv')
        # One huge outlier 10 days ago: the unfiltered full-log energy would
        # be (100*86400 + 9*1*86400)/3600 = 2616.00 Wh.  With '7d' only the
        # last 8 rows remain -> 7 intervals * 86400 / 3600 = 168.00 Wh.
        write_power_log(power, self.stamps, [100.0] + [1.0] * 10)

        full = analyze.build_report(self.log, power_log_file=power,
                                    config=CONFIG)
        self.assertRegex(full, r'Energy \(full log\)\s*:\s*2616\.00 Wh')

        ranged = analyze.build_report(self.log, power_log_file=power,
                                      config=CONFIG, range_spec='7d')
        self.assertRegex(ranged, r'Energy \(full log\)\s*:\s*168\.00 Wh')
        self.assertNotIn('2616.00 Wh', ranged,
                         'power records were not filtered by /range')


class TestReportTitle(unittest.TestCase):
    """BUG 6: the report must identify itself as PiStats."""

    def test_title_says_pistats_and_keeps_legacy_heading(self):
        with tempfile.TemporaryDirectory() as scratch:
            log = os.path.join(scratch, 'temp_log.csv')
            write_temp_log(log, daily_timestamps(1, 2))
            title = next(line for line in
                         analyze.build_report(log, config=CONFIG).splitlines()
                         if 'REPORT' in line)
            self.assertIn('PiStats', title)
            self.assertIn('TEMPERATURE & COOLER DECISION REPORT', title)


if __name__ == '__main__':
    unittest.main()
