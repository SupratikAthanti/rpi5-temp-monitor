"""Sampling sub-view and dual sampling-interval tests for PiScope (stdlib only).

The Config tab exposes one SAMPLING row which opens a small sub-view with two
options.  ``power_sample_interval_s`` drives the Battery context cache while
the legacy ``sample_interval_s`` drives Temperature / Performance; Overview
merges both.
"""

import os
import sys
import tempfile
import unittest
from unittest import mock

try:  # curses is stdlib, but may be absent on exotic builds
    import curses
except ImportError:  # pragma: no cover
    curses = None

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import piscope_config as cfgmod  # noqa: E402
import piscope_tui as tui  # noqa: E402


class TestSamplingRows(unittest.TestCase):
    def test_rows_carry_both_keys_and_current_values(self):
        config = {'power_sample_interval_s': 2.5, 'sample_interval_s': 5.0}
        rows = tui.build_sampling_rows(config)
        self.assertEqual(len(rows), 2)
        self.assertEqual([r['label'] for r in rows],
                         list(tui.SAMPLING_LABELS))
        text = ' '.join(str(r['text']) for r in rows)
        for token in ('power_sample_interval_s', '2.5',
                      'sample_interval_s', '5.0'):
            with self.subTest(token=token):
                self.assertIn(token, text)
        for row in rows:
            self.assertIn('[ ', row['text'])
            self.assertIn(' ] s', row['text'])

    def test_rows_default_to_one_second(self):
        rows = tui.build_sampling_rows({})
        self.assertEqual(len(rows), 2)
        for row in rows:
            self.assertIn('1.0', row['text'])


class TestSamplingState(unittest.TestCase):
    def setUp(self):
        self.rows = tui.build_sampling_rows({
            'power_sample_interval_s': 2.5,
            'sample_interval_s': 5.0,
        })

    def test_start_sampling_edit_seeds_right_buffer_without_mutation(self):
        state = {'submenu': 'sampling', 'sub_index': 1, 'buffer': 'old'}
        before = dict(state)
        out = tui.start_sampling_edit(state, self.rows)
        self.assertTrue(out['sub_editing'])
        self.assertEqual(out['sub_index'], 1)
        self.assertEqual(out['buffer'], '5.0')
        self.assertEqual(out['cursor'], len('5.0'))
        self.assertEqual(state, before, 'start_sampling_edit must not mutate input')

        first = tui.start_sampling_edit({'sub_index': 0}, self.rows)
        self.assertEqual(first['buffer'], '2.5')

    def test_start_sampling_edit_clamps_index(self):
        out = tui.start_sampling_edit({'sub_index': 99}, self.rows)
        self.assertEqual(out['sub_index'], 1)
        self.assertEqual(out['buffer'], '5.0')

    def test_close_sampling_clears_submenu_state(self):
        state = {'submenu': 'sampling', 'sub_index': 1,
                 'sub_editing': True, 'buffer': '9', 'tab_index': 4}
        before = dict(state)
        out = tui.close_sampling(state)
        self.assertIsNone(out['submenu'])
        self.assertEqual(out['sub_index'], 0)
        self.assertFalse(out['sub_editing'])
        self.assertEqual(out['buffer'], '')
        self.assertEqual(out['tab_index'], 4, 'unrelated state is preserved')
        self.assertEqual(state, before, 'close_sampling must not mutate input')


class TestSamplingNavigation(unittest.TestCase):
    def test_config_sampling_row_resolves_to_submenu(self):
        rows = tui.build_tab_rows('Config')
        sampling = [r for r in rows if r['id'] == 'config.sampling']
        self.assertEqual(len(sampling), 1)
        self.assertEqual(sampling[0]['label'], 'SAMPLING')
        self.assertEqual(tui.resolve_enter_action(sampling[0], 'Config'),
                         'submenu')
        # Ordinary Config rows still open the inline editor.
        other = next(r for r in rows if r['id'] == 'config.mode')
        self.assertEqual(tui.resolve_enter_action(other, 'Config'), 'edit')

    def test_config_has_exactly_one_sampling_row_and_no_flat_rows(self):
        rows = tui.build_tab_rows('Config')
        ids = [r['id'] for r in rows]
        self.assertEqual(ids.count('config.sampling'), 1)
        for key in tui.SAMPLING_KEYS:
            with self.subTest(key=key):
                self.assertNotIn('config.' + key, ids)
        self.assertEqual(
            len([r for r in rows if r['label'] == 'SAMPLING']), 1)

    def test_interval_seconds_coerces_values(self):
        self.assertEqual(tui._interval_seconds('2.5'), 2.5)
        self.assertEqual(tui._interval_seconds(5), 5.0)
        self.assertEqual(tui._interval_seconds('bad', 4.0), 4.0)
        self.assertEqual(tui._interval_seconds(None, 3.0), 3.0)
        self.assertEqual(tui._interval_seconds('0'), 0.1)


class TestPowerSamplingConfig(unittest.TestCase):
    """Config persistence stays inside a temp path override."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.tmp.name, 'piscope', 'config.json')
        self._old = os.environ.get(cfgmod.CONFIG_PATH_ENV)
        os.environ[cfgmod.CONFIG_PATH_ENV] = self.path

    def tearDown(self):
        if self._old is None:
            os.environ.pop(cfgmod.CONFIG_PATH_ENV, None)
        else:
            os.environ[cfgmod.CONFIG_PATH_ENV] = self._old
        self.tmp.cleanup()

    def test_defaults_and_live_key(self):
        self.assertEqual(cfgmod.DEFAULT_CONFIG['power_sample_interval_s'], 1.0)
        self.assertIn('power_sample_interval_s', cfgmod.LIVE_KEYS)
        self.assertEqual(cfgmod.load_config()['power_sample_interval_s'], 1.0)

    def test_set_config_value_parses_and_saves_through_override(self):
        updated = cfgmod.set_config_value(cfgmod.load_config(),
                                          'power_sample_interval_s', '2.5')
        self.assertEqual(updated['power_sample_interval_s'], 2.5)
        cfgmod.save_config(updated)
        self.assertTrue(os.path.exists(self.path))
        reloaded = cfgmod.load_config()
        self.assertEqual(reloaded['power_sample_interval_s'], 2.5)
        # The Temperature interval is independent.
        self.assertEqual(reloaded['sample_interval_s'], 1.0)

    def test_validate_clamps_both_intervals_independently(self):
        cfg = cfgmod.validate_config({
            'power_sample_interval_s': 9999.0,
            'sample_interval_s': -4.0,
        })
        self.assertEqual(cfg['power_sample_interval_s'], 60.0)
        self.assertEqual(cfg['sample_interval_s'], 0.1)
        clamped = cfgmod.set_config_value(cfgmod.DEFAULT_CONFIG,
                                          'power_sample_interval_s', '0')
        self.assertGreaterEqual(clamped['power_sample_interval_s'], 0.1)


@unittest.skipIf(curses is None, 'curses is not available')
class TestLoopBehavior(unittest.TestCase):
    """Drive _tui_loop with a fake screen to check key routing / exit."""

    class Screen:
        def __init__(self, keys):
            self.keys = list(keys)

        def timeout(self, ms):
            pass

        def getmaxyx(self):
            return 30, 100

        def erase(self):
            pass

        def refresh(self):
            pass

        def addnstr(self, *args, **kwargs):
            pass

        def getch(self):
            return self.keys.pop(0) if self.keys else -1

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.tmp.name, 'piscope', 'config.json')
        self._old = os.environ.get(cfgmod.CONFIG_PATH_ENV)
        os.environ[cfgmod.CONFIG_PATH_ENV] = self.path

    def tearDown(self):
        if self._old is None:
            os.environ.pop(cfgmod.CONFIG_PATH_ENV, None)
        else:
            os.environ[cfgmod.CONFIG_PATH_ENV] = self._old
        self.tmp.cleanup()

    def _drive(self, keys):
        """Run the loop to completion; return a snapshot of every frame."""
        seen = []
        clock = [0.0]

        def monotonic():
            clock[0] += 0.1
            if clock[0] > 100:
                raise AssertionError('_tui_loop did not exit')
            return clock[0]

        def render(scr, state, rows, cfg, filtered=None, status=''):
            seen.append({
                'tab_index': state.get('tab_index'),
                'submenu': state.get('submenu'),
                'sub_index': state.get('sub_index'),
                'sub_editing': state.get('sub_editing'),
                'buffer': state.get('buffer'),
                'status': status,
                'power': cfg.get('power_sample_interval_s'),
            })

        cfg = cfgmod.load_config()
        cfg['log_cadence_s'] = 99999  # no collector process during the test
        with mock.patch.object(tui, '_render', render), \
                mock.patch.object(tui, '_init_colors'), \
                mock.patch.object(tui, '_persist_sample'), \
                mock.patch.object(tui, 'build_context', lambda *a, **k: {}), \
                mock.patch.object(tui.time, 'monotonic', monotonic):
            tui._tui_loop(self.Screen(keys), cfg)
        return seen

    def test_esc_at_main_screen_quits(self):
        seen = self._drive([curses.KEY_RIGHT, 27, 27])
        # One frame per key press; the final Esc ends the loop with no re-render.
        self.assertEqual(len(seen), 2)
        self.assertEqual(seen[0]['tab_index'], 0)
        self.assertEqual(seen[1]['tab_index'], 1)

    def test_sampling_subview_consumes_keys_and_esc_returns(self):
        keys = ([curses.KEY_RIGHT] * 4 + [curses.KEY_DOWN, 10,
                                          curses.KEY_DOWN, 27, 27, 27])
        seen = self._drive(keys)
        self.assertEqual(seen[4]['tab_index'], 4)  # Config reached
        self.assertIsNone(seen[5]['submenu'])  # frame precedes the Enter key
        self.assertEqual(seen[6]['submenu'], 'sampling')
        self.assertEqual(seen[6]['sub_index'], 0)
        self.assertEqual(seen[7]['submenu'], 'sampling')
        self.assertEqual(seen[7]['sub_index'], 1)  # Down moved inside sub-view
        self.assertIsNone(seen[8]['submenu'])  # Esc closed the sub-view
        # The tab never changed while the sub-view consumed keys.
        for frame in seen[5:8]:
            self.assertEqual(frame['tab_index'], 4)

    def test_saving_power_interval_from_subview_is_immediate(self):
        keys = ([curses.KEY_RIGHT] * 4 + [curses.KEY_DOWN, 10, 10,
                8, 8, 8, ord('3'), 10, 27, 27])
        seen = self._drive(keys)
        self.assertEqual(cfgmod.load_config()['power_sample_interval_s'], 3.0)
        self.assertTrue(any(f['status'].startswith(
            'Saved power_sample_interval_s') for f in seen))
        self.assertTrue(any(f['power'] == 3.0 for f in seen),
                        'rendering must use the reassigned config dict')


if __name__ == '__main__':
    unittest.main(verbosity=2)
