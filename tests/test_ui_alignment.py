"""UI right-edge / alignment regression coverage for PiScope.

Two kinds of invariants live here:

1. Runtime invariants driven through ``piscope_tui._render`` with a fake
   ``stdscr`` that records every ``addnstr`` call into a character grid.
   These assert that no drawn line can overrun the terminal width and that
   the structural lines (header, tab bar, command bar, Config caret) stay
   within bounds.  They PASS on the current implementation.

2. Static invariants over the ``design.md`` mockups.  Every bordered ASCII
   box (the frames in sections 3 and 5-9) must have a single, consistent
   right-border column: the top border defines the width and every other
   line in the box must reach exactly that column.  These FAIL on the
   current ``design.md`` and precisely enumerate the ragged right edges the
   audit found.  They are intentionally not weakened to make them pass.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import piscope_tui as tui  # noqa: E402

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
DESIGN = os.path.join(ROOT, 'design.md')

SIZES = [(100, 30), (80, 24), (60, 20)]


class FakeStdscr:
    """Minimal stdscr: records ``addnstr`` writes for a fixed terminal size."""

    def __init__(self, height, width):
        self.height = height
        self.width = width
        self.calls = []  # (y, x, drawn_text, natural_text)

    def getmaxyx(self):
        return self.height, self.width

    def erase(self):
        pass

    def addnstr(self, y, x, text, n, attr=0):
        natural = str(text)
        drawn = natural[:max(0, n)]
        self.calls.append((y, x, drawn, natural))

    def addstr(self, y, x, text, attr=0):
        natural = str(text)
        self.calls.append((y, x, natural, natural))

    def refresh(self):
        pass


def render_frame(tab_index, width, height, editing=False, palette=None,
                 status=''):
    rows = tui.build_tab_rows(tui.TABS[tab_index])
    state = {
        'tab_index': tab_index, 'index': 0, 'scroll': 0, 'buffer': '1 s',
        'palette': bool(palette), 'editing': editing, 'cursor': 3,
        'detail': None, 'paused': False, 'cmd_index': 0,
    }
    scr = FakeStdscr(height, width)
    tui._render(scr, state, rows, {}, filtered=palette, status=status)
    return scr


def frame_lines(scr):
    """Return {y: drawn_line} for a rendered frame."""
    lines = {}
    for (y, x, drawn, natural) in scr.calls:
        lines[y] = lines.get(y, '') + drawn
    return lines


def render_sampling_frame(width, height, editing=False):
    """Render the Config tab with the Sampling sub-view open."""
    rows = tui.build_tab_rows('Config')
    state = {
        'tab_index': tui.TABS.index('Config'), 'index': 0, 'scroll': 0,
        'buffer': '2.5', 'cursor': 3, 'palette': False, 'editing': False,
        'detail': None, 'paused': False, 'cmd_index': 0,
        'submenu': 'sampling', 'sub_index': 0, 'sub_editing': editing,
    }
    cfg = {'power_sample_interval_s': 2.5, 'sample_interval_s': 5.0}
    scr = FakeStdscr(height, width)
    tui._render(scr, state, rows, cfg)
    return scr


class TestSamplingSubmenuRightEdge(unittest.TestCase):
    """The sub-view panel obeys the same width / single-write contract."""

    def _assert_contract(self, scr, width):
        for (y, x, drawn, natural) in scr.calls:
            self.assertLessEqual(
                len(drawn), width - 1,
                f'row {y}: drew {len(drawn)} cols (> {width - 1})')
            self.assertLessEqual(x + len(drawn), width)
        by_y = {}
        for (y, x, drawn, natural) in scr.calls:
            by_y.setdefault(y, []).append((x, drawn))
        for y, writes in by_y.items():
            next_x = -1
            for x, drawn in sorted(writes):
                self.assertGreaterEqual(
                    x, next_x, f'overlapping writes on row {y} at x={x}')
                next_x = x + len(drawn)

    def test_sampling_panel_fits(self):
        for width, height in SIZES:
            for editing in (False, True):
                with self.subTest(size=(width, height), editing=editing):
                    scr = render_sampling_frame(width, height, editing)
                    self._assert_contract(scr, width)
                    lines = frame_lines(scr)
                    for y in (0, 1, height - 1):
                        self.assertIn(y, lines)
                        self.assertLessEqual(len(lines[y]), width - 1)

    def test_sampling_panel_shows_both_options_and_caret(self):
        scr = render_sampling_frame(80, 24, editing=True)
        lines = frame_lines(scr)
        self.assertIn('SAMPLING', lines[4])
        self.assertIn('>', lines[5])
        self.assertIn('power_sample_interval_s', lines[5])
        self.assertIn('[ 2.5_ ]', lines[5])
        self.assertIn('sample_interval_s', lines[6])
        self.assertNotIn('power_sample_interval_s', lines[6])


class TestRuntimeRightEdge(unittest.TestCase):
    """The curses frontend truncates with addnstr; nothing may wrap/overrun."""

    def test_no_drawn_line_exceeds_terminal_width(self):
        for tab in range(len(tui.TABS)):
            for width, height in SIZES:
                with self.subTest(tab=tui.TABS[tab], size=(width, height)):
                    scr = render_frame(tab, width, height)
                    for (_y, _x, drawn, _natural) in scr.calls:
                        self.assertLessEqual(
                            len(drawn), width - 1,
                            f'{tui.TABS[tab]} @ {width}x{height}: drew '
                            f'{len(drawn)} cols (> {width - 1}) past right edge')

    def test_header_tabbar_and_command_bar_fit(self):
        for tab in range(len(tui.TABS)):
            for width, height in SIZES:
                with self.subTest(tab=tui.TABS[tab], size=(width, height)):
                    lines = frame_lines(render_frame(tab, width, height))
                    for y in (0, 1, height - 1):
                        self.assertIn(y, lines)
                        self.assertLessEqual(len(lines[y]), width - 1)

    def test_tab_bar_has_all_tabs_and_no_collision(self):
        for width, height in SIZES:
            with self.subTest(size=(width, height)):
                bar = frame_lines(render_frame(0, width, height))[1]
                for name in tui.TABS:
                    self.assertIn(name, bar)
                # The curses frontend marks the active tab with a single
                # leading '◀' (design.md mockups show a trailing '▶' too,
                # but that is a design/code discrepancy, not an alignment
                # defect).  Assert the structural separator count instead.
                self.assertEqual(bar.count('◀'), 1)
                self.assertEqual(bar.count('│'), 4)
                self.assertLessEqual(len(bar), width - 1)

    def test_config_edit_caret_fits_within_row(self):
        for width, height in SIZES:
            with self.subTest(size=(width, height)):
                scr = render_frame(tui.TABS.index('Config'), width, height,
                                   editing=True)
                bar = frame_lines(scr)[height - 1]
                self.assertIn('edit [', bar)
                self.assertIn('_', bar)  # inline caret
                self.assertLessEqual(len(bar), width - 1)


def _fenced_blocks(text):
    """Yield (start_line, [content lines]) for each fenced code block."""
    blocks, cur, start, inside = [], [], 0, False
    for num, line in enumerate(text.split('\n'), 1):
        if line.strip().startswith('```'):
            if inside and cur:
                blocks.append((start, cur))
            cur, inside = [], not inside
            start = num + 1
        elif inside:
            cur.append((num, line))
    if cur:
        blocks.append((start, cur))
    return blocks


class TestDesignMockupRightEdge(unittest.TestCase):
    """Every bordered mockup box must share one right-edge column.

    These assertions intentionally FAIL against the current design.md: the
    top/bottom borders of the section 5-9 frames are 82 columns wide while
    most content lines stop at 80 or 81.  The failures enumerate each ragged
    line so the boxes can be corrected.
    """

    def test_bordered_boxes_have_uniform_width(self):
        with open(DESIGN, encoding='utf-8') as fh:
            blocks = _fenced_blocks(fh.read())
        failures = []
        for start, blines in blocks:
            if not blines or not blines[0][1].lstrip().startswith(('╔', '┌')):
                continue  # skip open snippets (§10) and edit fragments (§9)
            expected = len(blines[0][1])
            for num, line in blines:
                if len(line) != expected:
                    failures.append(
                        f'design.md L{num} width={len(line)} '
                        f'expected={expected}: {line!r}')
        self.assertEqual(failures, [],
                         'Ragged right edges in design.md mockups:\n  '
                         + '\n  '.join(failures))

    def test_tab_bar_lines_share_the_box_right_edge(self):
        with open(DESIGN, encoding='utf-8') as fh:
            blocks = _fenced_blocks(fh.read())
        failures = []
        for start, blines in blocks:
            if not blines or not blines[0][1].lstrip().startswith(('╔', '┌')):
                continue
            expected = len(blines[0][1])
            for num, line in blines:
                if ('Overview' in line and 'Temperature' in line
                        and 'Config' in line):
                    if len(line) != expected:
                        failures.append(
                            f'design.md L{num} tab bar width={len(line)} '
                            f'expected={expected}: {line!r}')
        self.assertEqual(failures, [],
                         'Misaligned tab bars in design.md:\n  '
                         + '\n  '.join(failures))


if __name__ == '__main__':
    unittest.main(verbosity=2)
