"""Live 1 Hz dashboard: sampling, graphs, session energy/cost and CSV log.

All tests are headless: curses rendering uses a fake screen, hardware readers
are patched, and the session log / config paths are redirected to temp dirs.
"""

import csv
import datetime
import os
import re
import sys
import tempfile
import time
import unittest
from unittest import mock

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import analyze  # noqa: E402
import pistats_config as cfgmod  # noqa: E402
import pistats_dashboard as dash  # noqa: E402
import pistats_tui as tui  # noqa: E402


SERIES = [(1000 + i, 50.0 + value) for i, value in enumerate(
    [0.0, 1.5, 5.0, 12.0, 9.0, 3.0, 0.5])]


class FakeStdscr:
    """Minimal stdscr that records every write for inspection."""

    def __init__(self, height, width, keys=()):
        self.height = height
        self.width = width
        self.keys = list(keys)
        self.calls = []

    def getmaxyx(self):
        return self.height, self.width

    def timeout(self, ms):
        pass

    def erase(self):
        self.calls = []

    def refresh(self):
        pass

    def addnstr(self, y, x, text, n, attr=0):
        self.calls.append((y, x, str(text)[:max(0, n)], str(text)))

    def getch(self):
        return self.keys.pop(0) if self.keys else -1


def frame_lines(screen):
    lines = {}
    for y, _x, drawn, _natural in screen.calls:
        lines[y] = lines.get(y, '') + drawn
    return lines


def has_braille(text):
    return any(0x2800 <= ord(char) <= 0x28FF for char in text)


def braille_rows(lines):
    """Plot rows that actually contain braille dots (header/ticks excluded)."""
    return [line for line in lines[1:-2] if has_braille(line)]


class TestGraphRendering(unittest.TestCase):
    def test_multi_line_plot_has_labels_and_varying_rows(self):
        lines = dash.render_graph(
            SERIES, width=60, height=6, title='TEMPERATURE HISTORY',
            unit='°C', current=50.5, now=1006)
        self.assertEqual(len(lines), 9)  # header + 6 plot rows + ticks + axis
        self.assertIn('TEMPERATURE HISTORY', lines[0])
        self.assertIn('now 50.5°C', lines[0])
        self.assertIn('last 30s', lines[0])  # adaptive floor, not last 5m
        self.assertGreaterEqual(len(braille_rows(lines)), 3)
        self.assertIn('min 50.0°C', lines[-1])
        self.assertIn('max 62.0°C', lines[-1])
        self.assertIn('→', lines[-1])  # time span
        self.assertGreaterEqual(len(re.findall(r'\d\d:\d\d:\d\d', lines[-2])), 2)
        for line in lines:
            self.assertLessEqual(len(line), 60)

    def test_constant_series_does_not_divide_by_zero(self):
        flat = [(1000 + i, 55.0) for i in range(10)]
        lines = dash.render_graph(flat, width=40, height=5, title='FLAT',
                                  unit='°C', now=1009)
        self.assertEqual(len(braille_rows(lines)), 1)
        self.assertIn('min 55.0°C', lines[-1])
        self.assertIn('max 55.0°C', lines[-1])

    def test_empty_and_single_sample_histories(self):
        empty = dash.render_graph([], width=40, height=4, title='TEMP')
        self.assertTrue(any('waiting for samples' in line for line in empty))
        self.assertEqual(len(empty), 7)
        one = dash.render_graph([(1000, 42.0)], width=40, height=4,
                                title='TEMP', unit='°C')
        self.assertTrue(braille_rows(one))

    def test_non_finite_and_negative_values_are_defensive(self):
        samples = [(1000, float('nan')), (1001, float('inf')),
                   (1002, -4.0), None, (1003, 'bad'), (1004, 10.0)]
        lines = dash.render_graph(samples, width=40, height=4, title='POWER',
                                  unit='W', nonnegative=True)
        self.assertTrue(braille_rows(lines))
        self.assertIn('min 0.0W', lines[-1])
        self.assertFalse(any('nan' in line.lower() for line in lines))

    def test_narrow_width_still_fits(self):
        lines = dash.render_graph(SERIES, width=14, height=2, title='TEMP')
        for line in lines:
            self.assertLessEqual(len(line), 14)

    def test_braille_lines_are_thin_and_high_resolution(self):
        lines = dash.render_graph(SERIES, width=40, height=4, title='TEMP',
                                  unit='°C', now=1006)
        plotted = braille_rows(lines)
        self.assertGreaterEqual(len(plotted), 2)
        self.assertFalse(any('█' in line or '░' in line for line in lines))
        # 4 character rows -> up to 16 subpixel rows; each cell is one glyph.
        dots = [ord(char) - 0x2800 for line in plotted for char in line
                if 0x2800 <= ord(char) <= 0x28FF]
        self.assertTrue(dots)
        self.assertLessEqual(sum(bin(dot).count('1') for dot in dots) / len(dots),
                             3.0)
        self.assertGreater(len(plotted), 1)


class TestAdaptiveGraphAxes(unittest.TestCase):
    def test_window_text_formats_seconds_minutes_hours_and_days(self):
        self.assertEqual(dash.format_window_s(47), '47s')
        self.assertEqual(dash.format_window_s(720), '12m')
        self.assertEqual(dash.format_window_s(3 * 3600), '3h')
        self.assertEqual(dash.format_window_s(6 * 86400), '6d')

    def test_window_grows_from_startup_to_weeks(self):
        self.assertEqual(dash.adaptive_window_s(6), 30.0)
        self.assertEqual(dash.adaptive_window_s(90), 120.0)
        self.assertEqual(dash.adaptive_window_s(3 * 3600), 3 * 3600.0)
        self.assertEqual(dash.adaptive_window_s(5 * 86400), 7 * 86400.0)
        self.assertEqual(dash.adaptive_window_s(90 * 86400), 30 * 86400.0)

    def test_header_shows_actual_adaptive_window(self):
        base = 1_700_000_000
        short = [(base + i, 50.0) for i in range(6)]
        self.assertIn('last 30s', dash.render_graph(short, width=60,
                      height=4, unit='°C', now=base + 5)[0])
        hours = [(base + i * 900, 50.0 + i % 3) for i in range(20)]
        self.assertIn('last 6h', dash.render_graph(hours, width=60,
                      height=4, unit='°C', now=hours[-1][0])[0])
        days = [(base + i * 86400, 50.0 + i % 3) for i in range(6)]
        self.assertIn('last 7d', dash.render_graph(days, width=60,
                      height=4, unit='°C', now=days[-1][0])[0])

    def test_cross_day_ticks_carry_dates(self):
        # 23:00 -> 01:00 local time the next day (fixed zone-free construction
        # via a known epoch around UTC midnight).
        moment = datetime.datetime(2026, 11, 27, 23, 0, 0)
        base = moment.timestamp()
        samples = [(base + i * 600, 50.0 + i % 2) for i in range(25)]
        lines = dash.render_graph(samples, width=100, height=4, unit='°C',
                                  now=samples[-1][0])
        self.assertRegex(lines[-2], r'\d{2}-\d{2}\s+\d{2}:\d{2}')
        self.assertRegex(lines[-1], r'\d{2}-\d{2} \d{2}:\d{2}→\d{2}-\d{2} \d{2}:\d{2}')

    def test_week_long_ticks_use_dates(self):
        base = 1_700_000_000
        samples = [(base + i * 86400, 50.0 + i % 2) for i in range(14)]
        lines = dash.render_graph(samples, width=80, height=4, unit='°C',
                                  now=samples[-1][0])
        self.assertRegex(lines[-2], r'\d{4}-\d{2}-\d{2}')

    def test_min_max_envelope_keeps_spikes_in_long_series(self):
        base = 1_700_000_000
        # Flat samples at whole seconds; a short-lived spike between two of
        # them would be lost by point interpolation but survives as a band.
        samples = [dash.Band(base + i, 50.0, 50.0, 50.0) for i in range(120)]
        samples.insert(60, dash.Band(base + 60.4, 50.0, 70.0, 60.0))
        lines = dash.render_graph(samples, width=60, height=4, unit='°C',
                                  window_s=120)
        dots = [ord(char) - 0x2800 for line in braille_rows(lines)
                for char in line if 0x2800 <= ord(char) <= 0x28FF]
        # Top subpixel row (dot bits 0x01 / 0x11) is only reachable from 70 °C.
        self.assertTrue(any(dot & 0x11 for dot in dots))
        self.assertIn('max 70.0°C', lines[-1])
        self.assertIn('min 50.0°C', lines[-1])

    def test_startup_y_range_floors_prevent_magnification(self):
        temp = dash.series_y_bounds(
            [(1000, 50.0), (1001, 50.02), (1002, 50.04)], unit='°C')
        self.assertGreaterEqual(temp[1] - temp[0], 2.0 - 1e-9)
        power = dash.series_y_bounds(
            [(1000, 6.0), (1001, 6.01)], unit='W', nonnegative=True)
        self.assertGreaterEqual(power[1] - power[0], 1.0 - 1e-9)
        self.assertGreaterEqual(power[0], 0.0)
        # Real variation still autoscales beyond the floor.
        wide = dash.series_y_bounds([(0, 10.0), (1, 90.0)], unit='°C')
        self.assertAlmostEqual(wide[0], 10.0)
        self.assertAlmostEqual(wide[1], 90.0)


class TestCompactedHistory(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.old = os.environ.get(dash.SESSION_LOG_ENV)
        self.path = os.path.join(self.tmp.name, 'pistats_session.csv')
        os.environ[dash.SESSION_LOG_ENV] = self.path

    def tearDown(self):
        if self.old is None:
            os.environ.pop(dash.SESSION_LOG_ENV, None)
        else:
            os.environ[dash.SESSION_LOG_ENV] = self.old
        self.tmp.cleanup()

    def test_five_days_compact_into_bins_with_exact_energy(self):
        recorder = dash.SessionRecorder(bucket_s=60.0, recent_window_s=30.0)
        base = time.time() - 5 * 86400
        raw = []
        for index in range(120):
            stamp = base + index * 3600.0
            watts = 5.0 + index % 5
            recorder.add(stamp, 50.0 + index % 7, watts)
            raw.append((stamp, watts))
        self.assertLessEqual(len(recorder.samples), 2)
        self.assertGreater(len(recorder.buckets), 100)
        self.assertEqual(recorder.sample_count, 120)
        self.assertAlmostEqual(recorder.energy_wh(),
                               dash.session_energy_wh(raw), places=9)
        ctx = recorder.context(now=base + 119 * 3600.0)
        self.assertIn('4d', ctx['session_elapsed'])
        self.assertGreaterEqual(
            ctx['session_temp_bands'][-1].timestamp -
            ctx['session_temp_bands'][0].timestamp, 4 * 86400)
        header = dash.render_graph(
            ctx['session_temp_bands'], width=80, height=4,
            unit='°C', now=ctx['session_now'])[0]
        self.assertIn('last 7d', header)

    def test_csv_is_read_once_at_startup_not_per_frame(self):
        recorder = dash.SessionRecorder()
        for index in range(3):
            recorder.add(1_700_000_000 + index, 50.0 + index, 6.0)
        with mock.patch.object(dash, 'read_session_samples',
                               wraps=dash.read_session_samples) as reader:
            recorder.load_history()
            for _ in range(5):
                recorder.context(now=1_700_000_004)
        reader.assert_not_called()
        # A fresh recorder does load the persisted history exactly once.
        with mock.patch.object(dash, 'read_session_samples',
                               wraps=dash.read_session_samples) as reader:
            second = dash.SessionRecorder()
        reader.assert_called_once()
        self.assertEqual(second.sample_count, 3)


class TestSessionMath(unittest.TestCase):
    def test_trapezoidal_integration_and_cost_at_13_cents(self):
        samples = [(1000, 61.0, 100.0), (1001, 62.0, 100.0),
                   (1002, 63.0, 200.0), (1003, 64.0, 200.0)]
        # (100*1 + 150*1 + 200*1) watt-seconds / 3600 = 0.125 Wh
        self.assertAlmostEqual(dash.session_energy_wh(samples), 0.125, places=9)
        self.assertAlmostEqual(
            dash.session_cost_dollars(dash.session_energy_wh(samples), 0.13),
            0.00001625, places=12)
        self.assertAlmostEqual(dash.session_cost_dollars(1000.0, 0.13), 0.13)

    def test_trailing_interval_uses_last_power_draw(self):
        samples = [(1000, 50.0, 100.0), (1001, 50.0, 100.0)]
        self.assertAlmostEqual(
            dash.session_energy_wh(samples, until=1003), 100.0 * 3 / 3600,
            places=9)

    def test_empty_single_and_bad_samples(self):
        self.assertEqual(dash.session_energy_wh([]), 0.0)
        self.assertEqual(dash.session_energy_wh([(0, 50.0, 10.0)]), 0.0)
        samples = [(0, 50.0, float('nan')), (1, 50.0, -8.0),
                   (2, 50.0, 8.0), (3, 50.0, 8.0)]
        # Negative reading clamped to 0: trapezoids (0+0)/2+(0+8)/2+(8+8)/2.
        self.assertAlmostEqual(dash.session_energy_wh(samples), 12.0 / 3600)
        self.assertEqual(dash.session_cost_dollars(float('nan'), 0.13), 0.0)

    def test_default_rate_is_thirteen_cents_and_override_wins(self):
        self.assertEqual(cfgmod.DEFAULT_CONFIG['rate_per_kwh'], 0.13)
        self.assertAlmostEqual(analyze.cost_dollars(1000.0), 0.13)
        with tempfile.TemporaryDirectory() as tmp:
            old = os.environ.get(cfgmod.CONFIG_PATH_ENV)
            os.environ[cfgmod.CONFIG_PATH_ENV] = os.path.join(tmp, 'config.json')
            try:
                self.assertEqual(cfgmod.load_config()['rate_per_kwh'], 0.13)
                override = cfgmod.set_config_value(
                    cfgmod.load_config(), 'rate_per_kwh', '0.22')
                cfgmod.save_config(override)
                self.assertEqual(cfgmod.load_config()['rate_per_kwh'], 0.22)
            finally:
                if old is None:
                    os.environ.pop(cfgmod.CONFIG_PATH_ENV, None)
                else:
                    os.environ[cfgmod.CONFIG_PATH_ENV] = old


class TestSessionLog(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.tmp.name, 'session', 'pistats_session.csv')
        self._old = os.environ.get(dash.SESSION_LOG_ENV)
        os.environ[dash.SESSION_LOG_ENV] = self.path

    def tearDown(self):
        if self._old is None:
            os.environ.pop(dash.SESSION_LOG_ENV, None)
        else:
            os.environ[dash.SESSION_LOG_ENV] = self._old
        self.tmp.cleanup()

    def test_env_override_and_one_row_per_sample(self):
        self.assertEqual(dash.session_log_path('/ignored'), self.path)
        recorder = dash.SessionRecorder('/also-ignored.csv')
        self.assertEqual(recorder.path, self.path)
        for i in range(3):
            recorder.add(1000.0 + i, 50.0 + i, 6.0 + i)
        with open(self.path, newline='') as handle:
            rows = list(csv.reader(handle))
        self.assertEqual(rows[0], ['timestamp', 'temp_c', 'wall_w'])
        self.assertEqual(len(rows), 4)
        self.assertAlmostEqual(float(rows[-1][1]), 52.0)
        self.assertAlmostEqual(float(rows[-1][2]), 8.0)
        back = dash.read_session_samples(self.path)
        self.assertEqual(len(back), 3)
        # The shared historical log is not touched by the session recorder.
        self.assertFalse(os.path.exists(
            os.path.join(self.tmp.name, 'temp_log.csv')))

    def test_header_is_written_only_once(self):
        dash.append_session_sample(self.path, dash.Sample(1000.0, 50.0, 5.0))
        dash.append_session_sample(self.path, dash.Sample(1001.0, 51.0, 6.0))
        with open(self.path) as handle:
            text = handle.read()
        self.assertEqual(text.count('timestamp,temp_c,wall_w'), 1)
        self.assertEqual(len(text.strip().splitlines()), 3)


@unittest.skipIf(tui._curses is None, 'curses is not available')
class TestLoopSamplesAtOneHertz(unittest.TestCase):
    """Drive the real loop with a fake screen and a fake monotonic clock."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self._old_cfg = os.environ.get(cfgmod.CONFIG_PATH_ENV)
        self._old_session = os.environ.get(dash.SESSION_LOG_ENV)
        os.environ[cfgmod.CONFIG_PATH_ENV] = os.path.join(
            self.tmp.name, 'config.json')
        os.environ[dash.SESSION_LOG_ENV] = os.path.join(
            self.tmp.name, 'pistats_session.csv')

    def tearDown(self):
        for env, old in ((cfgmod.CONFIG_PATH_ENV, self._old_cfg),
                         (dash.SESSION_LOG_ENV, self._old_session)):
            if old is None:
                os.environ.pop(env, None)
            else:
                os.environ[env] = old
        self.tmp.cleanup()

    def test_every_second_records_temp_and_power(self):
        frames = []
        clock = [0.0]
        temps = iter([50.0 + i for i in range(5)])
        watts = iter([6.0 + i for i in range(5)])

        def monotonic():
            clock[0] += 1.0
            return clock[0]

        def render(screen, state, rows, cfg, filtered=None, status=''):
            frames.append(rows)

        cfg = dict(cfgmod.DEFAULT_CONFIG)
        cfg['log_cadence_s'] = 99999  # no collector subprocess during the test
        with mock.patch.object(tui, '_render', render), \
                mock.patch.object(tui, '_init_colors'), \
                mock.patch.object(tui, '_persist_sample'), \
                mock.patch.object(tui, 'build_context', lambda *a, **k: {}), \
                mock.patch.object(
                    tui, 'read_soc_temp', side_effect=lambda: next(temps)), \
                mock.patch.object(tui.power_read, 'read_power', side_effect=(
                    lambda: {'pmic_w': 5.0, 'wall_w': next(watts)})), \
                mock.patch.object(tui.time, 'monotonic', monotonic):
            # Five frames: four no-op keys, then 'q' to exit.
            tui._tui_loop(FakeStdscr(30, 100, [0, 0, 0, 0, ord('q')]), cfg)

        self.assertEqual(len(frames), 5)
        for index, rows in enumerate(frames, start=1):
            by_id = {row['id']: row for row in rows}
            self.assertEqual(len(by_id['overview.temp_graph']['samples']), index)
            self.assertEqual(len(by_id['overview.power_graph']['samples']), index)
            self.assertAlmostEqual(
                by_id['overview.power_graph']['samples'][-1][1], 5.0 + index)
        samples = dash.read_session_samples(os.environ[dash.SESSION_LOG_ENV])
        self.assertEqual(len(samples), 5)
        self.assertAlmostEqual(samples[0].temp, 50.0)
        self.assertAlmostEqual(samples[-1].temp, 54.0)
        self.assertAlmostEqual(samples[-1].watts, 10.0)


@unittest.skipIf(tui._curses is None, 'curses is not available')
class TestDashboardFrame(unittest.TestCase):
    def _context(self):
        stamps = [1000 + i for i in range(20)]
        return {
            'session_temp_samples': [
                (t, 50.0 + (i % 7)) for i, t in enumerate(stamps)],
            'session_power_samples': [(t, 6.0 + (i % 5) * 0.5)
                                      for i, t in enumerate(stamps)],
            'session_temp_now': '55.0', 'session_power_now': '7.50',
            'session_energy_text': '0.042 Wh',
            'session_cost': '$0.000005', 'session_rate': 0.13,
            'session_elapsed': '00:00:20', 'session_samples': 20,
            'session_now': stamps[-1],
        }

    def _render(self, height, width):
        ctx = self._context()
        rows = tui.build_tab_rows('Overview', ctx)
        state = {'tab_index': 0, 'index': 0, 'scroll': 0, 'buffer': '',
                 'palette': False, 'editing': False, 'detail': None,
                 'paused': False, 'cmd_index': 0,
                 'session_now': ctx['session_now']}
        screen = FakeStdscr(height, width)
        tui._render(screen, state, rows, {})
        return frame_lines(screen), screen

    def _find(self, lines, needle):
        found = [y for y, text in lines.items() if needle in text]
        self.assertTrue(found, f'{needle!r} not drawn')
        return min(found)

    def test_order_temp_graph_power_graph_then_energy(self):
        lines, screen = self._render(30, 100)
        y_temp = self._find(lines, 'SoC 55.0 °C')
        y_temp_graph = self._find(lines, 'TEMPERATURE HISTORY')
        y_power = self._find(lines, 'Wall 7.50 W')
        y_power_graph = self._find(lines, 'POWER HISTORY')
        y_energy = self._find(lines, '0.042 Wh')
        self.assertLess(y_temp, y_temp_graph)
        self.assertLess(y_temp_graph, y_power)
        self.assertLess(y_power, y_power_graph)
        self.assertLess(y_power_graph, y_energy)
        temp_plots = sum(has_braille(lines[y])
                         for y in range(y_temp_graph + 1, y_power))
        power_plots = sum(has_braille(lines[y])
                          for y in range(y_power_graph + 1, y_energy))
        self.assertGreaterEqual(temp_plots, 3)
        self.assertGreaterEqual(power_plots, 3)
        self.assertTrue(any('min 50.0°C' in lines[y]
                            for y in range(y_temp_graph + 1, y_power)))
        self.assertTrue(any('max' in lines[y]
                            for y in range(y_power_graph + 1, y_energy)))
        self.assertIn('cost $0.000005', lines[y_energy])
        self.assertIn('@ $0.13/kWh', lines[y_energy])
        self.assertIn('00:00:20', lines[y_energy])
        for _y, x, drawn, _natural in screen.calls:
            self.assertLessEqual(x + len(drawn), 100)

    def test_small_terminal_degrades_without_overrun(self):
        for height, width in ((20, 60), (14, 50)):
            with self.subTest(size=(height, width)):
                lines, screen = self._render(height, width)
                self.assertLess(self._find(lines, 'TEMPERATURE HISTORY'),
                                self._find(lines, '0.042 Wh'))
                self.assertIn('POWER HISTORY', ' '.join(lines.values()))
                for _y, x, drawn, _natural in screen.calls:
                    self.assertLessEqual(len(drawn), width - 1)
                    self.assertLessEqual(x + len(drawn), width)


if __name__ == '__main__':
    unittest.main(verbosity=2)
