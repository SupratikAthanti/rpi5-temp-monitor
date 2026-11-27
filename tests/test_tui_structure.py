"""TUI structure and navigation tests — pure logic, NO textual required.

The implementation must keep these pure helpers in ``pistats_tui`` importable even
when ``textual`` is absent (guard the textual import). Nothing here starts a TUI.
"""

import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import pistats_tui as tui  # noqa: E402


def make_rows(n, sections=()):
    rows = []
    for i in range(n):
        kind = 'section' if i in sections else 'data'
        rows.append({'id': f'r{i}', 'label': f'Row {i}', 'text': f'Row {i}', 'kind': kind})
    return rows


def blob(rows):
    return ' '.join(str(v) for r in rows for v in r.values()).lower()


class TestTabStructure(unittest.TestCase):
    def test_exactly_five_tabs_in_order(self):
        self.assertEqual(tui.TABS,
                         ['Overview', 'Temperature', 'Performance', 'Battery', 'Config'])

    def test_config_is_last_tab(self):
        self.assertEqual(tui.TABS[-1], 'Config')

    def test_default_render_interval_is_one_second(self):
        self.assertEqual(tui.DEFAULT_RENDER_INTERVAL_S, 1.0)

    def test_key_bindings_cover_navigation_and_triggers(self):
        kb = tui.KEY_BINDINGS
        self.assertEqual(kb.get('?'), 'commands')
        self.assertEqual(kb.get('/'), 'commands')
        for key in ('left', 'right', 'up', 'down', 'enter', 'esc'):
            self.assertIn(key, kb)


class TestTabContent(unittest.TestCase):
    def test_temperature_has_stats_and_all_distributions(self):
        rows = tui.build_tab_rows('Temperature')
        self.assertGreater(len(rows), 10)
        text = blob(rows)
        for token in ('min', 'average', 'median', '95', 'max', 'pmic',
                      'load', 'mhz', 'temperature distribution',
                      'frequency distribution'):
            with self.subTest(token=token):
                self.assertIn(token, text)
        # All five temperature bands and all six frequency bands.
        for token in ('under 60', '60', '70', '80', 'over 85',
                      '2400', '1500', '1000', '750', '600', 'other'):
            with self.subTest(token=token):
                self.assertIn(token, text)
        kinds = {r['kind'] for r in rows}
        self.assertTrue({'section', 'data'}.issubset(kinds))

    def test_performance_has_required_sections(self):
        text = blob(tui.build_tab_rows('Performance'))
        for token in ('performance impact', 'throttl', 'top processes',
                      'spike', 'verdict'):
            with self.subTest(token=token):
                self.assertIn(token, text)

    def test_battery_has_live_power_rails_energy_cost_runtime(self):
        text = blob(tui.build_tab_rows('Battery'))
        for token in ('live power', 'wall', 'rail breakdown', 'energy',
                      'cost', 'runtime', 'battery'):
            with self.subTest(token=token):
                self.assertIn(token, text)

    def test_overview_has_no_verdict_or_trend(self):
        ctx = {'verdict': 'Active Cooler Strongly Recommended',
               'temp_spark': '▁▂▃▇█', 'watt_spark': '▂▃▆█▇'}
        rows = tui.build_tab_rows('Overview', ctx)
        ids = {row['id'] for row in rows}
        self.assertNotIn('overview.verdict', ids)
        self.assertNotIn('overview.sparkline', ids)
        text = blob(rows)
        for token in ('verdict', 'final verdict', 'trend'):
            with self.subTest(token=token):
                self.assertNotIn(token, text)
        # The verdict still belongs to the Performance tab.
        self.assertIn('verdict', blob(tui.build_tab_rows('Performance', ctx)))

    def test_overview_keeps_graphs_energy_status_and_span(self):
        ids = {row['id'] for row in tui.build_tab_rows('Overview')}
        for identifier in ('overview.temp_graph', 'overview.power_graph',
                           'overview.energy', 'overview.status',
                           'overview.cost', 'overview.span'):
            with self.subTest(identifier=identifier):
                self.assertIn(identifier, ids)

    def test_config_has_one_row_per_live_key(self):
        rows = tui.build_tab_rows('Config')
        text = blob(rows)
        import pistats_config
        for key in pistats_config.LIVE_KEYS:
            with self.subTest(key=key):
                self.assertIn(key, text)

    def test_rows_have_required_keys(self):
        for tab in tui.TABS:
            for row in tui.build_tab_rows(tab):
                self.assertTrue({'id', 'label', 'text', 'kind'}.issubset(row.keys()),
                                f'{tab}: {row}')
                self.assertIn(row['kind'], ('section', 'data'))


class TestAutoScrollSelection(unittest.TestCase):
    """move_selection(rows, index, scroll, viewport_height, direction)."""

    def setUp(self):
        self.rows = make_rows(10)

    def test_down_moves_one_row_without_scroll(self):
        self.assertEqual(tui.move_selection(self.rows, 0, 0, 3, 'down'), (1, 0))
        self.assertEqual(tui.move_selection(self.rows, 1, 0, 3, 'down'), (2, 0))

    def test_down_scrolls_by_minimum_amount(self):
        # Moving to index 3 with viewport 3 => scroll must become exactly 1.
        self.assertEqual(tui.move_selection(self.rows, 2, 0, 3, 'down'), (3, 1))
        self.assertEqual(tui.move_selection(self.rows, 3, 1, 3, 'down'), (4, 2))

    def test_up_scrolls_by_minimum_amount(self):
        self.assertEqual(tui.move_selection(self.rows, 3, 1, 3, 'up'), (2, 1))
        self.assertEqual(tui.move_selection(self.rows, 1, 1, 3, 'up'), (0, 0))

    def test_selected_row_always_visible(self):
        index, scroll = 0, 0
        for _ in range(20):
            index, scroll = tui.move_selection(self.rows, index, scroll, 3, 'down')
            self.assertLessEqual(scroll, index)
            self.assertLess(index, scroll + 3)

    def test_clamping_at_both_ends(self):
        self.assertEqual(tui.move_selection(self.rows, 0, 0, 3, 'up'), (0, 0))
        self.assertEqual(tui.move_selection(self.rows, 9, 7, 3, 'down'), (9, 7))

    def test_home_and_end(self):
        self.assertEqual(tui.move_selection(self.rows, 5, 4, 3, 'home'), (0, 0))
        self.assertEqual(tui.move_selection(self.rows, 0, 0, 3, 'end'), (9, 7))

    def test_page_up_and_page_down(self):
        self.assertEqual(tui.move_selection(self.rows, 0, 0, 3, 'page_down'), (3, 1))
        self.assertEqual(tui.move_selection(self.rows, 9, 7, 3, 'page_up'), (6, 6))

    def test_section_title_scrolls_its_section_into_view(self):
        rows = make_rows(12, sections=(0, 4, 8))
        self.assertEqual(tui.scroll_to_section(rows, 4, 0, 3), 4)
        # A data row inside the section at index 4 anchors to that section.
        self.assertEqual(tui.scroll_to_section(rows, 6, 0, 3), 4)

    def test_enter_resolves_to_detail_or_edit(self):
        data_row = make_rows(1)[0]
        section_row = make_rows(1, sections=(0,))[0]
        self.assertEqual(tui.resolve_enter_action(data_row, 'Temperature'), 'detail')
        self.assertEqual(tui.resolve_enter_action(data_row, 'Config'), 'edit')
        self.assertIsNone(tui.resolve_enter_action(section_row, 'Config'))

    def test_esc_cancels_an_edit(self):
        state = {'editing': True, 'buffer': '2_', 'tab': 'Config', 'index': 3}
        out = tui.cancel_edit(state)
        self.assertFalse(out['editing'])
        self.assertEqual(out['buffer'], '')
        self.assertEqual(out['tab'], 'Config')


class TestLogSpan(unittest.TestCase):
    """The Overview log span is friendly text, not raw ISO timestamps."""

    def test_same_day_span_is_compact_with_duration(self):
        self.assertEqual(
            tui._format_log_span('2026-11-22T01:45:07Z',
                                 '2026-11-22T02:46:40Z'),
            'Nov 22 01:45 → 02:46 (1h 01m)')

    def test_cross_day_span_shows_both_dates(self):
        self.assertEqual(
            tui._format_log_span('2026-11-22T23:45:07Z',
                                 '2026-11-23T00:46:40Z'),
            'Nov 22 23:45 → Nov 23 00:46 (1h 01m)')

    def test_cross_year_span_shows_years(self):
        self.assertEqual(
            tui._format_log_span('2025-12-31T23:30:00Z',
                                 '2026-01-01T00:15:00Z'),
            'Dec 31 2025 23:30 → Jan 01 2026 00:15 (45m)')

    def test_short_and_multi_day_durations(self):
        self.assertEqual(
            tui._format_log_span('2026-11-22T01:45:00Z',
                                 '2026-11-22T01:45:30Z'),
            'Nov 22 01:45 → 01:45 (30s)')
        self.assertEqual(
            tui._format_log_span('2026-11-21T10:00:00Z',
                                 '2026-11-25T13:05:00Z'),
            'Nov 21 10:00 → Nov 25 13:05 (4d 03h 05m)')

    def test_missing_or_malformed_timestamps_fall_back(self):
        for start, end in ((None, '2026-11-22T01:45:07Z'),
                           ('', ''), ('not-a-date', '2026-11-22T01:45:07Z')):
            with self.subTest(start=start, end=end):
                self.assertEqual(tui._format_log_span(start, end), '—')

    def test_build_context_and_overview_row_use_friendly_span(self):
        log_text = (
            'timestamp,soc_temp_c,pmic_temp_c,load_1m,arm_freq_mhz,'
            'throttled_hex,top_proc,top_cpu_pct\n'
            '2026-11-22T23:45:07Z,60.0,50.0,0.50,2400,0x0,opencode,50.0\n'
            '2026-11-23T00:46:40Z,61.0,51.0,0.50,2400,0x0,opencode,50.0\n'
        )
        with tempfile.TemporaryDirectory() as td:
            log = os.path.join(td, 'temp_log.csv')
            with open(log, 'w') as f:
                f.write(log_text)
            env = {'PISTATS_POWER_LOG': os.path.join(td, 'missing_power.csv')}
            with mock.patch.object(tui, '_first_log', return_value=log), \
                    mock.patch.object(tui, '_config', None), \
                    mock.patch.dict(os.environ, env):
                ctx = tui.build_context(
                    {}, live={'rails': {}, 'pmic_w': None, 'wall_w': None})
        expected = 'Nov 22 23:45 → Nov 23 00:46 (1h 01m)'
        self.assertEqual(ctx['span'], expected)
        self.assertNotIn('T23:45:07Z', ctx['span'])
        rows = {row['id']: row for row in tui.build_tab_rows('Overview', ctx)}
        self.assertIn(expected, rows['overview.span']['text'])


if __name__ == '__main__':
    unittest.main()
