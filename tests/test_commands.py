"""Pure command-palette parser tests (no TUI / textual required).

Covers the `?` / `/` triggers, every slash command from design.md §10, argument
parsing/normalization, filtering, autocomplete and `/config set` parsing.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import pistats_commands as cmds  # noqa: E402


class TestCommandCatalog(unittest.TestCase):
    def test_all_required_commands_present(self):
        names = {c['name'] for c in cmds.COMMANDS}
        required = {'/help', '/config', '/reset', '/report', '/range', '/test',
                    '/cron', '/mode', '/pause', '/resume', '/quit', '/decision'}
        self.assertTrue(required.issubset(names),
                        f'missing commands: {required - names}')

    def test_commands_have_descriptions(self):
        for c in cmds.COMMANDS:
            self.assertTrue(c.get('description'), f'{c["name"]} lacks description')


class TestTriggerAndNormalization(unittest.TestCase):
    def test_question_mark_is_help_alias(self):
        self.assertEqual(cmds.normalize_command('?'), '/help')

    def test_bare_slash_is_help_alias(self):
        self.assertEqual(cmds.normalize_command('/'), '/help')

    def test_whitespace_trimmed(self):
        self.assertEqual(cmds.normalize_command('   /reset   '), '/reset')

    def test_help_command_parses(self):
        res = cmds.parse_command('?')
        self.assertTrue(res['valid'])
        self.assertEqual(res['name'], '/help')


class TestSlashCommandParsing(unittest.TestCase):
    def assert_parses(self, text, name, args):
        res = cmds.parse_command(text)
        self.assertTrue(res['valid'], f'{text!r} should be valid: {res}')
        self.assertEqual(res['name'], name)
        self.assertEqual(res['args'], args)

    def test_simple_commands(self):
        for text in ('/reset', '/report', '/pause', '/resume', '/quit', '/decision', '/config'):
            with self.subTest(text=text):
                self.assert_parses(text, text, [])

    def test_range_with_duration(self):
        self.assert_parses('/range 7d', '/range', ['7d'])
        self.assert_parses('/range 2h', '/range', ['2h'])
        self.assert_parses('/range 2026-11-20..21', '/range', ['2026-11-20..21'])

    def test_test_with_minutes(self):
        self.assert_parses('/test 60', '/test', ['60'])

    def test_cron_subcommands(self):
        for sub in ('start', 'stop', 'status'):
            with self.subTest(sub=sub):
                self.assert_parses(f'/cron {sub}', '/cron', [sub])

    def test_mode_subcommands(self):
        for mode in ('temp', 'power', 'both'):
            with self.subTest(mode=mode):
                self.assert_parses(f'/mode {mode}', '/mode', [mode])

    def test_config_set_arguments(self):
        self.assert_parses('/config set battery_wh 60', '/config',
                           ['set', 'battery_wh', '60'])

    def test_extra_whitespace_normalized(self):
        self.assert_parses('  /range    7d  ', '/range', ['7d'])

    def test_invalid_inputs(self):
        for text in ('', '   ', '/bogus', '/mode turbo', '/cron restart', '/test abc', '/test 0'):
            with self.subTest(text=text):
                res = cmds.parse_command(text)
                self.assertFalse(res['valid'], f'{text!r} should be invalid: {res}')
                self.assertIsNone(res.get('name'))


class TestConfigSetParsing(unittest.TestCase):
    def test_valid_set(self):
        self.assertEqual(
            cmds.parse_config_set(['set', 'battery_wh', '60']),
            {'key': 'battery_wh', 'value': '60'})

    def test_set_with_spaced_value(self):
        self.assertEqual(
            cmds.parse_config_set(['set', 'energy_windows_min', '30,90']),
            {'key': 'energy_windows_min', 'value': '30,90'})

    def test_non_set_returns_none(self):
        self.assertIsNone(cmds.parse_config_set(['battery_wh', '60']))
        self.assertIsNone(cmds.parse_config_set([]))
        self.assertIsNone(cmds.parse_config_set(['set', 'only_key']))


class TestFilteringAndAutocomplete(unittest.TestCase):
    def test_filter_matches_name(self):
        results = cmds.filter_commands('reset')
        self.assertIn('/reset', {c['name'] for c in results})
        self.assertNotIn('/quit', {c['name'] for c in results})

    def test_filter_matches_design_example(self):
        # design.md shows "?re_" listing /reset, /report (and /range).
        names = {c['name'] for c in cmds.filter_commands('re')}
        self.assertIn('/reset', names)
        self.assertIn('/report', names)

    def test_filter_is_case_insensitive(self):
        self.assertEqual({c['name'] for c in cmds.filter_commands('RESET')},
                         {c['name'] for c in cmds.filter_commands('reset')})

    def test_filter_empty_returns_all(self):
        self.assertEqual(len(cmds.filter_commands('')), len(cmds.COMMANDS))

    def test_autocomplete_prefix(self):
        self.assertEqual(cmds.autocomplete('/ra'), ['/range'])
        self.assertIn('/report', cmds.autocomplete('/rep'))

    def test_autocomplete_adds_leading_slash(self):
        self.assertEqual(cmds.autocomplete('ran'), ['/range'])

    def test_autocomplete_no_match(self):
        self.assertEqual(cmds.autocomplete('/zzz'), [])


if __name__ == '__main__':
    unittest.main()
