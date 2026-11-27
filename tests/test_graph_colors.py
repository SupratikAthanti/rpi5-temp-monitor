"""Graph throttle colours: thresholds, chunked rendering and curses fallback.

The graph renderer is pure, so every level assertion here is headless.  Curses
is replaced by a small fake that records colour initialisation and per-chunk
attributes; no terminal (and no hardware) is involved.
"""

import os
import sys
import types
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import pistats_config as cfgmod  # noqa: E402
import pistats_dashboard as dash  # noqa: E402
import pistats_tui as tui  # noqa: E402


TEMP_VALUES = [60.0] * 8 + [70.0] * 3 + [82.0] * 4 + [87.0] * 4
POWER_VALUES = [5.0] * 8 + [10.0] * 3 + [16.0] * 4 + [26.0] * 4


def series(values, base=1000):
    return [(base + index, value) for index, value in enumerate(values)]


def has_braille(text):
    return any(0x2800 <= ord(char) <= 0x28FF for char in text)


class TestThresholds(unittest.TestCase):
    def test_named_temperature_thresholds_are_the_pi5_throttle_points(self):
        self.assertEqual(dash.TEMP_WARN_C, 80.0)
        self.assertEqual(dash.TEMP_CRITICAL_C, 85.0)
        self.assertLess(dash.TEMP_WARN_C, dash.TEMP_CRITICAL_C)

    def test_temperature_threshold_classification_is_inclusive(self):
        warn, critical = dash.TEMP_WARN_C, dash.TEMP_CRITICAL_C
        classify = lambda value: dash.value_level(value, warn, critical)
        self.assertEqual(classify(warn - 0.1), 'normal')
        self.assertEqual(classify(55.0), 'normal')
        self.assertEqual(classify(warn), 'warn')
        self.assertEqual(classify(82.0), 'warn')
        self.assertEqual(classify(critical - 0.01), 'warn')
        self.assertEqual(classify(critical), 'critical')
        self.assertEqual(classify(critical + 2.0), 'critical')
        self.assertEqual(classify(float('nan')), 'normal')
        self.assertEqual(classify(None), 'normal')

    def test_power_defaults_are_configurable_and_validated(self):
        self.assertEqual(cfgmod.DEFAULT_CONFIG['power_warn_w'], 15.0)
        self.assertEqual(cfgmod.DEFAULT_CONFIG['power_critical_w'], 25.0)
        cfg = cfgmod.validate_config({
            'power_warn_w': 12.0, 'power_critical_w': 22.0})
        self.assertEqual(cfg['power_warn_w'], 12.0)
        self.assertEqual(cfg['power_critical_w'], 22.0)
        # Inverted / invalid values cannot leave the thresholds backwards.
        inverted = cfgmod.validate_config({
            'power_warn_w': 30.0, 'power_critical_w': 20.0})
        self.assertEqual(inverted['power_critical_w'],
                         inverted['power_warn_w'])
        self.assertEqual(cfgmod.validate_config(
            {'power_warn_w': 'bad'})['power_warn_w'], 15.0)
        self.assertEqual(cfgmod.set_config_value(
            {}, 'power_warn_w', '13.5')['power_warn_w'], 13.5)

    def test_power_threshold_classification_is_inclusive(self):
        warn = cfgmod.DEFAULT_CONFIG['power_warn_w']
        critical = cfgmod.DEFAULT_CONFIG['power_critical_w']
        classify = lambda value: dash.value_level(value, warn, critical)
        self.assertEqual(classify(5.0), 'normal')
        self.assertEqual(classify(warn - 0.1), 'normal')
        self.assertEqual(classify(warn), 'warn')
        self.assertEqual(classify(24.9), 'warn')
        self.assertEqual(classify(critical), 'critical')
        self.assertEqual(classify(26.0), 'critical')
        # Disabling a threshold keeps everything cool.
        self.assertEqual(dash.value_level(99.0), 'normal')
        self.assertEqual(dash.value_level(99.0, None, None), 'normal')
        self.assertEqual(dash.value_level(99.0, warn, None), 'warn')


class TestSegmentedGraph(unittest.TestCase):
    def test_segments_join_to_the_exact_plain_graph_text(self):
        cases = [
            (series(TEMP_VALUES), dict(width=60, height=5, title='TEMPERATURE',
                                       unit='°C', current=87.0, now=1018,
                                       warn_at=80, critical_at=85)),
            (series(POWER_VALUES), dict(width=48, height=6, title='POWER',
                                        unit='W', current=26.0, now=1018,
                                        nonnegative=True, warn_at=15,
                                        critical_at=25)),
            ([], dict(width=20, height=3, title='EMPTY', warn_at=80,
                      critical_at=85)),
            (series([50.0, 51.0]), dict(width=14, height=2, title='NARROW',
                                        warn_at=80, critical_at=85)),
        ]
        for samples, kwargs in cases:
            with self.subTest(title=kwargs['title']):
                plain = dash.render_graph(samples, **kwargs)
                segments = dash.render_graph_segments(samples, **kwargs)
                joined = [''.join(chunk for chunk, _level in line)
                          for line in segments]
                self.assertEqual(joined, plain)
                self.assertEqual(len(segments), len(plain))
                for line in segments:
                    self.assertTrue(line)
                    self.assertTrue(all(chunk for chunk, _level in line))
                    self.assertTrue(all(level in dash.GRAPH_LEVELS
                                        for _chunk, level in line))
                    self.assertLessEqual(len(''.join(c for c, _ in line)),
                                         kwargs['width'])

    def test_plain_graph_text_is_byte_for_byte_unchanged(self):
        # Narrow width keeps the time-dependent tick/footer labels away, so
        # these literals are the pre-colour renderer's exact output.
        samples = series([50.0, 52.0, 51.0])
        expected = [
            'TEMP  now…', '│       ⣰', '┤       ⠼', '', 'min 50.0°…',
        ]
        plain = dash.render_graph(samples, width=10, height=2, title='TEMP',
                                  unit='°C', now=1002, warn_at=80,
                                  critical_at=85)
        self.assertEqual(plain, expected)
        segments = dash.render_graph_segments(
            samples, width=10, height=2, title='TEMP', unit='°C', now=1002,
            warn_at=80, critical_at=85)
        self.assertEqual([''.join(chunk for chunk, _level in line)
                          for line in segments], expected)

    def test_plot_cells_use_the_peak_value_not_the_average(self):
        samples = series(TEMP_VALUES)
        segments = dash.render_graph_segments(
            samples, width=60, height=5, unit='°C', current=87.0, now=1018,
            warn_at=80, critical_at=85)
        plot = segments[1:-2]

        def count(level):
            return sum(len(chunk) for line in plot
                       for chunk, found in line
                       if found == level and has_braille(chunk))

        counts = {level: count(level) for level in dash.GRAPH_LEVELS}
        self.assertGreater(counts['normal'], 0, 'cool dots stay default')
        self.assertGreater(counts['warn'], 0, '82 °C dots are orange')
        self.assertGreater(counts['critical'], 0, '87 °C dots are red')
        # A single-cell band with a spike must be coloured by its high edge.
        spike = [dash.Band(1000 + i, 60.0, 60.0, 60.0) for i in range(12)]
        spike[6] = dash.Band(1006, 60.0, 88.0, 61.0)
        spiked = dash.render_graph_segments(
            spike, width=40, height=4, unit='°C', now=1011,
            warn_at=80, critical_at=85)
        self.assertTrue(any(level == 'critical' and has_braille(chunk)
                            for line in spiked[1:-2]
                            for chunk, level in line))

    def test_labels_are_coloured_by_the_value_they_show(self):
        segments = dash.render_graph_segments(
            series([60.0, 87.0]), width=50, height=4, unit='°C',
            now=1001, warn_at=80, critical_at=85)
        self.assertIn(('87.0°C', 'critical'), segments[0])
        self.assertTrue(any('max 87.0°C' in chunk and level == 'critical'
                            for chunk, level in segments[-1]))
        self.assertTrue(any('min 60.0°C' in chunk and level == 'normal'
                            for chunk, level in segments[-1]))
        ticks = [level for _chunk, level in segments[-2]]
        self.assertEqual(ticks, ['normal'])


def fake_curses(can_change=True, colors=16, fail_init=False):
    """Record curses colour setup; optionally refuse custom colours."""
    calls = []

    def init_color(color, red, green, blue):
        calls.append(('color', color, red, green, blue))
        if fail_init:
            raise RuntimeError('terminal refused init_color')

    mod = types.SimpleNamespace(
        COLOR_WHITE=3, COLOR_GREEN=4, COLOR_YELLOW=5, COLOR_RED=6,
        A_BOLD=1 << 20, A_REVERSE=1 << 21, COLORS=colors,
        start_color=lambda: calls.append('start'),
        use_default_colors=lambda: calls.append('defaults'),
        can_change_color=lambda: can_change,
        init_color=init_color,
        init_pair=lambda pair, fg, bg: calls.append(('pair', pair, fg, bg)),
        color_pair=lambda pair: 1 << (8 + pair),
    )
    return mod, calls


class RecordingScreen:
    """stdscr stand-in recording each write with its attribute."""

    def __init__(self, height=30, width=100):
        self.height, self.width = height, width
        self.calls = []

    def getmaxyx(self):
        return self.height, self.width

    def erase(self):
        self.calls = []

    def refresh(self):
        pass

    def addnstr(self, y, x, text, n, attr=0):
        self.calls.append((y, x, str(text)[:max(0, n)], attr))


class TestGraphColourTheme(unittest.TestCase):
    def setUp(self):
        self.saved_curses = tui._curses
        self.saved_attrs = dict(tui._COLOR_ATTRS)

    def tearDown(self):
        tui._curses = self.saved_curses
        tui._COLOR_ATTRS.clear()
        tui._COLOR_ATTRS.update(self.saved_attrs)

    def _pairs(self, calls):
        return {name: pair for pair, (name, _color)
                in enumerate(tui._THEME_COLORS, start=1)}

    def test_custom_orange_is_initialized_for_graph_warn(self):
        fake, calls = fake_curses(can_change=True)
        tui._curses = fake
        tui._COLOR_ATTRS.clear()
        tui._init_colors()

        self.assertIn(('color', tui.CUSTOM_ORANGE_COLOR,
                       *tui.CUSTOM_ORANGE_RGB), calls)
        registrations = {call[1]: (call[2], call[3]) for call in calls
                         if isinstance(call, tuple) and call[0] == 'pair'}
        roles = self._pairs(calls)
        self.assertEqual(registrations[roles['graph_warn']][0],
                         tui.CUSTOM_ORANGE_COLOR)
        self.assertEqual(tui._style('graph_warn'),
                         fake.color_pair(roles['graph_warn']))
        # Existing semantic roles are untouched.
        self.assertEqual(registrations[roles['warn']][0], fake.COLOR_YELLOW)
        self.assertEqual(registrations[roles['critical']][0], fake.COLOR_RED)
        self.assertEqual(registrations[roles['selected']][0], fake.COLOR_GREEN)

    def test_yellow_fallback_without_custom_colour_support(self):
        for kwargs in ({'can_change': False},
                       {'can_change': True, 'colors': 8},
                       {'can_change': True, 'fail_init': True}):
            with self.subTest(kwargs=kwargs):
                fake, calls = fake_curses(**kwargs)
                tui._curses = fake
                tui._COLOR_ATTRS.clear()
                tui._init_colors()
                colour_calls = [tag for tag, *_rest in calls
                                if tag == 'color']
                self.assertEqual(bool(colour_calls),
                                 bool(kwargs.get('fail_init')))
                registrations = {call[1]: call[2] for call in calls
                                 if isinstance(call, tuple)
                                 and call[0] == 'pair'}
                roles = self._pairs(calls)
                self.assertEqual(registrations[roles['graph_warn']],
                                 fake.COLOR_YELLOW)

    def test_no_curses_keeps_graph_colours_plain(self):
        tui._curses = None
        tui._COLOR_ATTRS.clear()
        tui._init_colors()
        self.assertEqual(tui._graph_style('warn'), 0)
        self.assertEqual(tui._graph_style('critical'), 0)
        self.assertEqual(tui._graph_style('normal'), 0)


class TestOverviewGraphColours(unittest.TestCase):
    def setUp(self):
        self.saved_curses = tui._curses
        self.saved_attrs = dict(tui._COLOR_ATTRS)

    def tearDown(self):
        tui._curses = self.saved_curses
        tui._COLOR_ATTRS.clear()
        tui._COLOR_ATTRS.update(self.saved_attrs)

    def test_overview_draws_warm_hot_and_cool_plot_chunks(self):
        fake, _calls = fake_curses(can_change=True)
        tui._curses = fake
        tui._COLOR_ATTRS.clear()
        tui._init_colors()
        normal = tui._style('value')
        warn = tui._style('graph_warn')
        critical = tui._style('graph_critical')
        self.assertNotEqual(normal, warn)
        self.assertNotEqual(warn, critical)

        temp = series(TEMP_VALUES)
        power = series(POWER_VALUES)
        ctx = {
            'session_temp_samples': temp,
            'session_power_samples': power,
            'session_now': temp[-1][0],
            'session_temp_now': '87.0',
            'session_power_now': '26.00',
        }
        state = {
            'tab_index': 0, 'index': 0, 'scroll': 0, 'buffer': '',
            'palette': False, 'editing': False, 'detail': None,
            'cmd_index': 0, 'session_now': temp[-1][0],
        }
        screen = RecordingScreen(height=30, width=100)
        tui._render(screen, state, tui.build_tab_rows('Overview', ctx),
                    dict(cfgmod.DEFAULT_CONFIG))
        plots = [(y, x, text, attr) for y, x, text, attr in screen.calls
                 if has_braille(text)]
        self.assertTrue(plots, 'no braille plot chunks were drawn')
        for attr, label in ((normal, 'cool'), (warn, 'warm'),
                            (critical, 'hot')):
            self.assertTrue(any(drawn_attr == attr
                                for _y, _x, _text, drawn_attr in plots),
                            f'no {label} graph chunks reached the screen')
        # Every write stays inside the frame, with no column overruns.
        for y, x, text, _attr in screen.calls:
            self.assertLessEqual(len(text), screen.width - 1)
            self.assertLessEqual(x + len(text), screen.width)
            self.assertLess(y, screen.height)

    def test_power_thresholds_come_from_the_config(self):
        fake, _calls = fake_curses(can_change=False)
        tui._curses = fake
        tui._COLOR_ATTRS.clear()
        tui._init_colors()
        warn = tui._style('graph_warn')
        critical = tui._style('graph_critical')
        power = series(POWER_VALUES)
        ctx = {'session_power_samples': power, 'session_now': power[-1][0]}
        state = {
            'tab_index': 0, 'index': 0, 'scroll': 0, 'buffer': '',
            'palette': False, 'editing': False, 'detail': None,
            'cmd_index': 0, 'session_now': power[-1][0],
        }

        def plot_attrs(cfg):
            screen = RecordingScreen(height=30, width=100)
            tui._render(screen, state, tui.build_tab_rows('Overview', ctx),
                        cfg)
            return [attr for _y, _x, text, attr in screen.calls
                    if has_braille(text)]

        # Defaults make the 26 W tail critical (25 W hard threshold).
        self.assertIn(critical, plot_attrs(dict(cfgmod.DEFAULT_CONFIG)))
        # Raising the configured critical edge to 30 W keeps the same samples
        # merely warm, proving the TUI reads the live config values.
        cfg = dict(cfgmod.DEFAULT_CONFIG)
        cfg['power_warn_w'] = 20.0
        cfg['power_critical_w'] = 30.0
        customized = plot_attrs(cfg)
        self.assertIn(warn, customized)
        self.assertNotIn(critical, customized)


if __name__ == '__main__':
    unittest.main()
