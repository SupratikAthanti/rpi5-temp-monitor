"""TUI structure and navigation tests — pure logic, NO textual required.

The implementation must keep these pure helpers in ``piscope_tui`` importable even
when ``textual`` is absent (guard the textual import). Nothing here starts a TUI.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import piscope_tui as tui  # noqa: E402


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

    def test_overview_has_compact_verdict(self):
        self.assertIn('verdict', blob(tui.build_tab_rows('Overview')))

    def test_config_has_one_row_per_live_key(self):
        rows = tui.build_tab_rows('Config')
        text = blob(rows)
        import piscope_config
        for key in piscope_config.LIVE_KEYS:
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


if __name__ == '__main__':
    unittest.main()
