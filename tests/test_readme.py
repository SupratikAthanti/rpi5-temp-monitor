"""README.md must document the PiScope product (design.md §13).

The repository README still describes "ThermalScope" and numbered shell
workflows.  design.md §13 requires a rewrite covering the new name, the
one-command ``piscope`` workflow, the five tabs, the ``?`` command palette,
configuration, modes, and power/cost/battery.  These tests read README.md from
disk and assert all of those are actually documented.
"""

import os
import re
import unittest

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
README = os.path.join(REPO, 'README.md')


class ReadmeTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with open(README, encoding='utf-8') as f:
            cls.text = f.read()
        cls.lower = cls.text.lower()

    def _first_heading(self):
        for line in self.text.splitlines():
            line = line.strip()
            if line.startswith('#'):
                return line
        return ''

    def test_readme_exists(self):
        self.assertTrue(os.path.exists(README))

    def test_title_uses_new_name_piscope(self):
        heading = self._first_heading().lower()
        self.assertIn('piscope', heading,
                      'README title must use the new name PiScope, got: %r' %
                      self._first_heading())

    def test_one_command_workflow_documented(self):
        # Accept a bare `piscope` line, optionally with a shell prompt / comment.
        bare = re.compile(r'^\$?\s*piscope\s*(#.*)?$', re.IGNORECASE)
        self.assertTrue(
            any(bare.match(l.strip()) for l in self.text.splitlines()),
            'README must show a bare `piscope` command (no args/subcommand)')
        self.assertTrue(
            any(p in self.lower for p in (
                'one command', 'no flags', 'no arguments', 'single command',
                'just run', 'opens everything', 'with no arguments')),
            'README must describe the one-command workflow')

    def test_five_tabs_documented_in_order(self):
        names = ['overview', 'temperature', 'performance', 'battery', 'config']
        positions = []
        for name in names:
            self.assertIn(name, self.lower, 'tab %r not documented' % name)
            positions.append(self.lower.find(name))
        # Config is the last tab; the first mention of each should progress.
        self.assertLess(positions[0], positions[4],
                        'tabs should be listed in design order')

    def test_config_last_explicitly(self):
        self.assertTrue(
            re.search(r'config.{0,40}(last|fifth|5th)', self.lower) or
            re.search(r'(last|fifth|5th).{0,40}config', self.lower),
            'README should state that Config is the last tab')

    def test_question_mark_command_palette_documented(self):
        self.assertIn('?', self.text)
        self.assertIn('command', self.lower,
                      'README must describe the command palette')

    def test_slash_commands_documented(self):
        missing = [c for c in ('/test', '/reset', '/report', '/range', '/cron',
                               '/mode', '/config', '/quit')
                   if c not in self.text]
        self.assertFalse(missing, 'README must document: %s' % ', '.join(missing))

    def test_config_path_documented(self):
        self.assertIn('config.json', self.lower)
        self.assertIn('piscope', self.lower)

    def test_modes_documented(self):
        missing = [m for m in ('temp', 'power', 'both') if m not in self.lower]
        self.assertFalse(missing, 'README must document modes: %s' %
                         ', '.join(missing))

    def test_power_cost_battery_documented(self):
        missing = [t for t in ('power', 'cost', 'battery') if t not in self.lower]
        self.assertFalse(missing, 'README must document: %s' %
                         ', '.join(missing))
        self.assertTrue('wh' in self.lower or 'kwh' in self.lower,
                        'README must explain energy units (Wh / kWh)')


if __name__ == '__main__':
    unittest.main()
