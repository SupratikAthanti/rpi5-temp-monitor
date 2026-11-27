"""Live 1 Hz dashboard: sampling, graphs, session energy/cost and CSV log.

All tests are headless: curses rendering uses a fake screen, hardware readers
are patched, and the session log / config paths are redirected to temp dirs.
"""

import csv
import os
import sys
import tempfile
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


class TestGraphRendering(unittest.TestCase):
    def test_multi_line_plot_has_labels_and_varying_rows(self):
        lines = dash.render_graph(
            SERIES, width=60, height=6, title='TEMPERATURE HISTORY',
            unit='°C', current=50.5, now=1006)
        self.assertEqual(len(lines), 8)  # header + 6 plot rows + axis
        self.assertIn('TEMPERATURE HISTORY', lines[0])
        self.assertIn('now 50.5°C', lines[0])
        self.assertIn('last 5m', lines[0])
        self.assertGreaterEqual(sum('█' in line for line in lines[1:-1]), 3)
        self.assertIn('min 50.0°C', lines[-1])
        self.assertIn('max 62.0°C', lines[-1])
        self.assertIn('→', lines[-1])  # time span
        for line in lines:
            self.assertLessEqual(len(line), 60)

    def test_constant_series_does_not_divide_by_zero(self):
        flat = [(1000 + i, 55.0) for i in range(10)]
        lines = dash.render_graph(flat, width=40, height=5, title='FLAT',
                                  unit='°C', now=1009)
        plotted = [line for line in lines[1:-1] if '█' in line]
        self.assertEqual(len(plotted), 1)
        self.assertIn('min 55.0°C', lines[-1])
        self.assertIn('max 55.0°C', lines[-1])

    def test_empty_and_single_sample_histories(self):
        empty = dash.render_graph([], width=40, height=4, title='TEMP')
        self.assertTrue(any('no samples yet' in line for line in empty))
        self.assertEqual(len(empty), 6)
        one = dash.render_graph([(1000, 42.0)], width=40, height=4,
                                title='TEMP', unit='°C')
        self.assertTrue(any('█' in line for line in one[1:-1]))

    def test_non_finite_and_negative_values_are_defensive(self):
        samples = [(1000, float('nan')), (1001, float('inf')),
                   (1002, -4.0), None, (1003, 'bad'), (1004, 10.0)]
        lines = dash.render_graph(samples, width=40, height=4, title='POWER',
                                  unit='W', nonnegative=True)
        self.assertTrue(any('█' in line for line in lines[1:-1]))
        self.assertIn('min 0.0W', lines[-1])
        self.assertFalse(any('nan' in line.lower() for line in lines))

    def test_narrow_width_still_fits(self):
        lines = dash.render_graph(SERIES, width=14, height=2, title='TEMP')
        for line in lines:
            self.assertLessEqual(len(line), 14)


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
        temp_plots = sum('█' in lines[y] for y in range(
            y_temp_graph + 1, y_power))
        power_plots = sum('█' in lines[y] for y in range(
            y_power_graph + 1, y_energy))
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
