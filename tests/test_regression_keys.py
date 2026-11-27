"""Regression tests for §14.5 literal control-character key resolution.

The string branch of ``pistats_tui.resolve_key_action`` currently does
``key.strip().lower()`` *before* testing the token.  That turns the literal
control characters ``'\\t'``, ``'\\n'`` and ``'\\r'`` into the empty string, so
the branches that try to match those literals are dead code and the helper
returns ``None``.  Real curses sends integers, but §14.5 of ``EXPECTED_API.md``
requires the literal string forms to resolve as well.

These tests are pure: a ``SimpleNamespace`` stands in for curses, so nothing
imports curses and no real files are touched.  They deliberately pass the raw
control characters (``chr(9)``/``chr(10)``/``chr(13)``) rather than their
stripped or symbolic forms, so the strip bug is genuinely caught.
"""

import os
import sys
import types
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import pistats_tui as tui  # noqa: E402


# Minimal curses stand-in: enough key codes to drive KEY_BINDINGS without
# importing curses (works on any platform).
FAKE_CURSES = types.SimpleNamespace(
    KEY_UP=259, KEY_DOWN=258, KEY_LEFT=260, KEY_RIGHT=261,
    KEY_HOME=262, KEY_END=263, KEY_PPAGE=265, KEY_NPAGE=266,
    KEY_BTAB=353,
)


class TestLiteralControlCharKeys(unittest.TestCase):
    """§14.5: literal '\t' / '\n' / '\r' must resolve like their key codes."""

    def test_literal_tab_is_next_tab(self):
        # Guard: this really is the raw control character, not 'tab' or a
        # stripped form, so a future edit cannot weaken the assertion.
        self.assertEqual('\t', chr(9))
        self.assertEqual(tui.resolve_key_action('\t', FAKE_CURSES), 'next_tab')

    def test_literal_newline_matches_integer_10(self):
        self.assertEqual('\n', chr(10))
        integer_action = tui.resolve_key_action(10, FAKE_CURSES)
        # Integer 10 is declared as KEY_BINDINGS['enter'] ('activate'); pin the
        # declared value so the equality below cannot pass vacuously as None.
        self.assertEqual(integer_action, tui.KEY_BINDINGS['enter'])
        self.assertEqual(tui.resolve_key_action('\n', FAKE_CURSES),
                         integer_action)

    def test_literal_carriage_return_matches_integer_13(self):
        self.assertEqual('\r', chr(13))
        integer_action = tui.resolve_key_action(13, FAKE_CURSES)
        self.assertEqual(integer_action, tui.KEY_BINDINGS['enter'])
        self.assertEqual(tui.resolve_key_action('\r', FAKE_CURSES),
                         integer_action)


class TestTabAndShiftTabForms(unittest.TestCase):
    """§14.5: all declared TAB / Shift+Tab forms resolve correctly."""

    def test_integer_9_is_next_tab(self):
        self.assertEqual(tui.resolve_key_action(9, FAKE_CURSES), 'next_tab')

    def test_symbolic_tab_lowercase_is_next_tab(self):
        self.assertEqual(tui.resolve_key_action('tab', FAKE_CURSES),
                         'next_tab')

    def test_symbolic_tab_uppercase_variant_is_next_tab(self):
        # The lowercasing in the string path must still map 'TAB' -> next_tab.
        self.assertEqual(tui.resolve_key_action('TAB', FAKE_CURSES),
                         'next_tab')

    def test_key_btab_code_is_prev_tab(self):
        self.assertEqual(tui.resolve_key_action(353, FAKE_CURSES), 'prev_tab')
        self.assertEqual(
            tui.resolve_key_action(FAKE_CURSES.KEY_BTAB, FAKE_CURSES),
            'prev_tab')

    def test_symbolic_shift_tab_is_prev_tab(self):
        self.assertEqual(tui.resolve_key_action('shift_tab', FAKE_CURSES),
                         'prev_tab')

    def test_tab_and_shift_tab_are_distinct(self):
        self.assertNotEqual(tui.resolve_key_action(9, FAKE_CURSES),
                            tui.resolve_key_action(353, FAKE_CURSES))


if __name__ == '__main__':
    unittest.main()
