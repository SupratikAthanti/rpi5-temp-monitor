"""Regression tests for the 2026-11-21 QA audit of the PiScope TUI.

Every test here targets a genuine bug that the original 198 tests missed.  The
helpers exercised are all *pure* (no curses, no hardware, no real home dir), so
they can be driven headlessly.  Each bug's required interface is documented in
``EXPECTED_API.md`` section 14.

Bugs forced: 1 (palette Enter), 2 (page-scroll visibility), 5 (Tab/Shift+Tab),
7 (config edit pre-fill), 8 (palette selection rendering), 9 (Esc transition).
"""

import os
import sys
import tempfile
import types
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import piscope_commands as cmds  # noqa: E402
import piscope_tui as tui  # noqa: E402


def make_rows(count=20, sections=(0, 5, 10, 15)):
    return [
        {'id': f'row.{i}', 'label': f'row {i}', 'text': f'value {i}',
         'kind': 'section' if i in sections else 'data'}
        for i in range(count)
    ]


# A stand-in for the curses module: enough key codes to prove the mapping is
# driven by KEY_BINDINGS, without importing curses (works on any platform).
FAKE_CURSES = types.SimpleNamespace(
    KEY_UP=259, KEY_DOWN=258, KEY_LEFT=260, KEY_RIGHT=261,
    KEY_HOME=262, KEY_END=263, KEY_PPAGE=265, KEY_NPAGE=266,
    KEY_BTAB=353,
)


class TestPaletteEnterRunsHighlightedCommand(unittest.TestCase):
    """BUG 1: typing a filter then Enter must run the highlighted command."""

    def setUp(self):
        # 're' is a *filter fragment*, not a command.  It highlights /reset.
        self.filtered = cmds.filter_commands('re')
        self.assertNotIn('re', {c['name'] for c in self.filtered})
        self.assertEqual(self.filtered[0]['name'], '/reset')

    def test_typed_filter_enter_executes_highlighted_reset(self):
        text = tui.resolve_palette_enter('re', self.filtered, 0)
        self.assertEqual(text, '/reset',
                         'Enter with a filter buffer must run the selected row')

        # Prove the effect, not just the string: the resolved command must
        # really clear logs when executed through the injected context.
        with tempfile.TemporaryDirectory() as scratch:
            log = os.path.join(scratch, 'temp_log.csv')
            with open(log, 'w') as f:
                f.write('timestamp,soc_temp_c\n')
            context = {'dir': scratch, 'confirm': lambda prompt: True}
            result = cmds.execute_command(text, context)
            self.assertIn('logs_cleared', result.get('effects', []), result)
            self.assertFalse(os.path.exists(log),
                             '/reset removed the wrong file (or nothing)')

    def test_enter_uses_current_selection_index(self):
        # The selected index is 2 -> '/range', not the first match '/reset'.
        self.assertEqual(tui.resolve_palette_enter('re', self.filtered, 2),
                         '/range')

    def test_valid_complete_command_in_buffer_is_still_honoured(self):
        # Design §12: "? then type config set ..., Enter" must type a full
        # command, not pick a filtered row.  The helper must keep that path.
        self.assertEqual(tui.resolve_palette_enter('/test 60', self.filtered, 0),
                         '/test 60')
        self.assertEqual(
            tui.resolve_palette_enter('/config set battery_wh 60',
                                      self.filtered, 0),
            '/config set battery_wh 60')

    def test_selection_index_is_clamped_to_filtered_bounds(self):
        self.assertEqual(tui.resolve_palette_enter('re', self.filtered, 999),
                         self.filtered[-1]['name'])
        self.assertEqual(tui.resolve_palette_enter('re', self.filtered, -3),
                         self.filtered[0]['name'])
        # Nothing typed and nothing to pick -> help, never an IndexError.
        self.assertEqual(tui.resolve_palette_enter('', [], 5), '?')


class TestPageScrollKeepsSelectionVisible(unittest.TestCase):
    """BUG 2: section anchoring must never push the highlighted row off-screen."""

    def setUp(self):
        self.rows = make_rows(20, sections=(0, 5, 10, 15))

    def _page_move(self, index, scroll, height, direction):
        """Mirror the TUI's PgUp/PgDn path: move, then anchor to a section."""
        new_index, new_scroll = tui.move_selection(
            self.rows, index, scroll, height, direction)
        if direction in ('page_up', 'page_down'):
            new_scroll = tui.scroll_to_section(
                self.rows, new_index, new_scroll, height)
        return new_index, new_scroll

    def test_exact_audit_pagedown_repro(self):
        # 20 rows, sections at 0/5/10/15, viewport 4.  PgDn from 0 lands on
        # row 4; scroll 1 shows rows 1-4.  The old scroll_to_section forced 0
        # (rows 0-3) and row 4 vanished.
        index, scroll = self._page_move(0, 0, 4, 'page_down')
        self.assertEqual(index, 4)
        self.assertEqual(scroll, 1, 'PgDn anchored away the highlighted row')
        self.assertTrue(scroll <= index < scroll + 4)

    def test_exact_pageup_repro(self):
        # From row 18 (view 16-19), PgUp lands on row 14.  The preceding
        # section is row 10, but anchoring to 10 would hide row 14; the
        # minimum legal scroll is 11.
        index, scroll = self._page_move(18, 16, 4, 'page_up')
        self.assertEqual(index, 14)
        self.assertEqual(scroll, 11)

    def test_scroll_to_section_never_hides_selected_row(self):
        self.assertEqual(tui.scroll_to_section(self.rows, 4, 1, 4), 1)
        self.assertEqual(tui.scroll_to_section(self.rows, 14, 14, 4), 11)

    def test_invariant_holds_after_every_direction(self):
        starting_points = [(0, 0), (4, 1), (9, 6), (14, 11), (19, 16)]
        for direction in ('up', 'down', 'home', 'end', 'page_up', 'page_down'):
            for index, scroll in starting_points:
                with self.subTest(direction=direction, index=index,
                                  scroll=scroll):
                    new_index, new_scroll = self._page_move(
                        index, scroll, 4, direction)
                    self.assertGreaterEqual(new_index, 0)
                    self.assertLess(new_index, len(self.rows))
                    self.assertGreaterEqual(new_scroll, 0)
                    self.assertLessEqual(new_scroll,
                                         len(self.rows) - 4)
                    self.assertTrue(
                        new_scroll <= new_index < new_scroll + 4,
                        f'{direction}: row {new_index} hidden by scroll '
                        f'{new_scroll} (viewport 4)')


class TestKeyMapping(unittest.TestCase):
    """BUG 5: Tab and Shift+Tab must map to different, declared actions."""

    def test_tab_is_next_tab(self):
        self.assertEqual(tui.KEY_BINDINGS['tab'], 'next_tab')
        self.assertEqual(tui.resolve_key_action(9, FAKE_CURSES), 'next_tab')
        self.assertEqual(tui.resolve_key_action('tab', FAKE_CURSES), 'next_tab')

    def test_shift_tab_is_previous_tab(self):
        self.assertEqual(tui.KEY_BINDINGS['shift_tab'], 'prev_tab')
        self.assertEqual(
            tui.resolve_key_action(FAKE_CURSES.KEY_BTAB, FAKE_CURSES),
            'prev_tab')
        self.assertEqual(tui.resolve_key_action('shift_tab', FAKE_CURSES),
                         'prev_tab')

    def test_tab_and_shift_tab_are_not_conflated(self):
        self.assertNotEqual(tui.resolve_key_action(9, FAKE_CURSES),
                            tui.resolve_key_action(FAKE_CURSES.KEY_BTAB,
                                                   FAKE_CURSES))

    def test_arrows_still_map_to_their_declared_actions(self):
        self.assertEqual(tui.resolve_key_action(FAKE_CURSES.KEY_LEFT,
                                                FAKE_CURSES), 'prev_tab')
        self.assertEqual(tui.resolve_key_action(FAKE_CURSES.KEY_RIGHT,
                                                FAKE_CURSES), 'next_tab')
        self.assertEqual(tui.resolve_key_action(FAKE_CURSES.KEY_UP,
                                                FAKE_CURSES), 'move_up')
        self.assertEqual(tui.resolve_key_action(FAKE_CURSES.KEY_DOWN,
                                                FAKE_CURSES), 'move_down')


class TestPaletteView(unittest.TestCase):
    """BUG 8: the selected command must be marked and always drawn."""

    COMMANDS = cmds.COMMANDS

    def test_selected_entry_is_marked_and_others_are_not(self):
        view = tui.build_palette_view(self.COMMANDS, 0, limit=4)
        self.assertEqual(view['index'], 0)
        self.assertEqual(view['start'], 0)
        self.assertEqual(len(view['lines']), 4)
        self.assertTrue(view['lines'][0].startswith('>'), view)
        self.assertIn('/help', view['lines'][0])
        for line in view['lines'][1:]:
            self.assertTrue(line.startswith(' '), view)
        self.assertEqual(sum(1 for ln in view['lines']
                             if ln.startswith('>')), 1)

    def test_selection_window_follows_index(self):
        # /cron is command 6; with a 4-row window the smallest shift that
        # still shows it is start=3 (rows 3..6).
        view = tui.build_palette_view(self.COMMANDS, 6, limit=4)
        self.assertEqual(view['index'], 6)
        self.assertEqual(view['start'], 3)
        marked = [ln for ln in view['lines'] if ln.startswith('>')]
        self.assertEqual(len(marked), 1)
        self.assertIn('/cron', marked[0])

    def test_out_of_range_index_is_clamped(self):
        high = tui.build_palette_view(self.COMMANDS, 999, limit=4)
        self.assertEqual(high['index'], len(self.COMMANDS) - 1)
        self.assertIn('/decision',
                      [ln for ln in high['lines'] if ln.startswith('>')][0])

        low = tui.build_palette_view(self.COMMANDS, -5, limit=4)
        self.assertEqual(low['index'], 0)
        self.assertIn('/help',
                      [ln for ln in low['lines'] if ln.startswith('>')][0])

    def test_small_and_empty_filters(self):
        two = tui.build_palette_view(self.COMMANDS, 1, limit=2)
        self.assertEqual(two['start'], 0)
        self.assertEqual(len(two['lines']), 2)
        self.assertIn('/config',
                      [ln for ln in two['lines'] if ln.startswith('>')][0])

        empty = tui.build_palette_view([], 3, limit=4)
        self.assertEqual(empty['lines'], [])
        self.assertEqual(empty['index'], 0)
        self.assertEqual(empty['start'], 0)


class FakeStdscr:
    """Minimal curses stand-in used to inspect what _render actually draws."""

    def __init__(self, height=20, width=80):
        self.height = height
        self.width = width
        self.drawn = []

    def getmaxyx(self):
        return self.height, self.width

    def erase(self):
        self.drawn = []

    def addnstr(self, y, x, text, n, attr=0):
        self.drawn.append((y, str(text)))

    def refresh(self):
        pass


class TestPaletteRenderUsesView(unittest.TestCase):
    """BUG 8 (wiring): the drawn palette must show the highlighted entry."""

    def test_render_marks_the_selected_command(self):
        commands = cmds.COMMANDS
        state = {'tab_index': 0, 'index': 0, 'scroll': 0,
                 'buffer': '', 'editing': False, 'detail': None,
                 'paused': False, 'cmd_index': 6}
        screen = FakeStdscr(height=20, width=96)
        tui._render(screen, state, tui.build_tab_rows('Overview'),
                    {'render_interval_s': 1.0, 'mode': 'both'}, commands, '')

        # Data rows use '> <label>' too; command lines start with '> /'.
        marked = [text for _, text in screen.drawn if text.startswith('> /')]
        self.assertEqual(len(marked), 1,
                         'exactly one command line must be highlighted')
        self.assertIn('/cron', marked[0],
                      'the highlighted command (cmd_index=6) must be drawn')


class TestConfigEditPrefill(unittest.TestCase):
    """BUG 7: Enter on a Config row must seed the editor with its value."""

    def setUp(self):
        self.rows = tui.build_tab_rows('Config', {
            'render_interval_s': 2.0,
            'mode': 'power',
        })
        self.assertIn('config.render_interval_s',
                      [r['id'] for r in self.rows])

    def _state_for(self, row_id):
        index = next(i for i, r in enumerate(self.rows) if r['id'] == row_id)
        return {'tab_index': 4, 'index': index, 'buffer': '',
                'palette': False, 'detail': None}

    def test_edit_seeds_current_value_and_cursor(self):
        state = self._state_for('config.render_interval_s')
        out = tui.start_config_edit(state, self.rows)
        self.assertTrue(out['editing'])
        self.assertEqual(out['buffer'], '2.0',
                         'editor must start from the displayed current value')
        self.assertEqual(out['cursor'], len(out['buffer']),
                         'cursor must be exposed at the end of the seeded text')
        self.assertEqual(out['tab_index'], 4)
        self.assertEqual(out['index'], state['index'])

    def test_string_value_seeded_and_input_untouched(self):
        state = self._state_for('config.mode')
        before = dict(state)
        out = tui.start_config_edit(state, self.rows)
        self.assertEqual(out['buffer'], 'power')
        self.assertEqual(out['cursor'], 5)
        self.assertEqual(state, before, 'start_config_edit must not mutate input')


class TestEscapeTransition(unittest.TestCase):
    """BUG 9: Esc must really focus the command bar, not just print a message."""

    def test_escape_focuses_command_bar(self):
        state = {'palette': False, 'command_focus': False, 'buffer': 'old',
                 'cmd_index': 3, 'tab_index': 2, 'index': 7, 'detail': None}
        out = tui.handle_escape(state)
        self.assertTrue(out['palette'], 'Esc must open/focus the palette')
        self.assertTrue(out.get('command_focus'),
                        'Esc must set a real focus state, not a status string')
        self.assertEqual(out['buffer'], '')
        self.assertEqual(out['cmd_index'], 0)
        self.assertEqual(out['tab_index'], 2)
        self.assertEqual(out['index'], 7)

    def test_escape_does_not_mutate_input(self):
        state = {'palette': False, 'buffer': 'old'}
        before = dict(state)
        tui.handle_escape(state)
        self.assertEqual(state, before)


if __name__ == '__main__':
    unittest.main()
