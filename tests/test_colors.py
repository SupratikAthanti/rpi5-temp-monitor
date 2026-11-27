"""Tests for the curses colour theme and its monochrome fallback."""

import os
import sys
import types
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import pistats_tui as tui  # noqa: E402


def fake_curses():
    """A curses stand-in that records colour initialisation calls."""
    calls = []
    mod = types.SimpleNamespace(
        COLOR_BLUE=1, COLOR_CYAN=2, COLOR_WHITE=3, COLOR_GREEN=4,
        COLOR_YELLOW=5, COLOR_RED=6,
        A_BOLD=1 << 20, A_REVERSE=1 << 21,
        start_color=lambda: calls.append('start'),
        use_default_colors=lambda: calls.append('defaults'),
        init_pair=lambda pair, fg, bg: calls.append(('pair', pair, fg, bg)),
        color_pair=lambda pair: 1 << (8 + pair),
    )
    return mod, calls


class AttrScreen:
    """Minimal stdscr that records which attributes each row was drawn with."""

    def __init__(self, height=24, width=80):
        self.height, self.width = height, width
        self.attrs = {}

    def getmaxyx(self):
        return self.height, self.width

    def erase(self):
        pass

    def refresh(self):
        pass

    def addnstr(self, y, x, text, n, attr=0):
        self.attrs.setdefault(y, []).append(attr)


class TestColorTheme(unittest.TestCase):
    def setUp(self):
        self.saved_curses = tui._curses
        self.saved_attrs = dict(tui._COLOR_ATTRS)

    def tearDown(self):
        tui._curses = self.saved_curses
        tui._COLOR_ATTRS.clear()
        tui._COLOR_ATTRS.update(self.saved_attrs)

    def test_init_registers_every_theme_colour(self):
        fake, calls = fake_curses()
        tui._curses = fake
        tui._COLOR_ATTRS.clear()
        tui._init_colors()
        self.assertIn('start', calls)
        pairs = [c for c in calls if isinstance(c, tuple)]
        self.assertEqual(len(pairs), len(tui._THEME_COLORS))
        self.assertNotEqual(tui._style('critical'), 0)
        self.assertTrue(tui._style('header', bold=True) & fake.A_BOLD)

    def test_no_curses_keeps_plain_attributes(self):
        tui._curses = None
        tui._COLOR_ATTRS.clear()
        tui._init_colors()
        self.assertEqual(tui._style('critical', bold=True), 0)

    def test_render_uses_colours_when_available(self):
        fake, _calls = fake_curses()
        tui._curses = fake
        tui._COLOR_ATTRS.clear()
        tui._init_colors()

        screen = AttrScreen()
        rows = tui.build_tab_rows(tui.TABS[0])
        state = {
            'tab_index': 0, 'index': 0, 'scroll': 0, 'buffer': '',
            'palette': False, 'editing': False, 'detail': None,
            'cmd_index': 0,
        }
        tui._render(screen, state, rows, {})
        self.assertTrue(any(attr for attrs in screen.attrs.values()
                            for attr in attrs),
                        'no colour attributes reached the renderer')

    def test_value_style_marks_verdicts_and_hot_rows(self):
        self.assertEqual(tui._value_style({
            'label': 'Final verdict', 'text': 'Active Cooler Strongly Recommended'}),
            'critical')
        self.assertEqual(tui._value_style({
            'label': 'Recommendation', 'text': 'Passive Heatsink Sufficient'}),
            'warn')
        self.assertEqual(tui._value_style({
            'label': 'SoC temp p95', 'text': '84.2C'}), 'warn')
        self.assertEqual(tui._value_style({
            'label': 'PMIC temp', 'text': '61.0C'}), 'value')


if __name__ == '__main__':
    unittest.main()
