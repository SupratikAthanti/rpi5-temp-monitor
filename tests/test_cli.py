"""CLI entry-point tests for `piscope`.

Covers existence/executability, --help, `view` text report, and the no-argument
TUI entry path (mocked so no real TUI is started).
"""

import io
import os
import sys
import subprocess
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest import mock

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
SCRIPT = os.path.join(REPO, 'piscope')

TEMP_CSV = """timestamp,soc_temp_c,pmic_temp_c,load_1m,arm_freq_mhz,throttled_hex,top_proc,top_cpu_pct
2026-11-21T10:00:00Z,60.0,50.0,0.50,2400,0x0,opencode,50.0
2026-11-21T10:01:00Z,70.0,55.0,1.00,1500,0x0,opencode,60.0
"""


class TestEntryScript(unittest.TestCase):
    def test_entry_point_exists(self):
        self.assertTrue(os.path.exists(SCRIPT),
                        'piscope entry script is missing at repo root')

    def test_entry_point_is_executable(self):
        self.assertTrue(os.path.exists(SCRIPT))
        self.assertTrue(os.access(SCRIPT, os.X_OK), 'piscope must be executable')

    def test_help_exits_zero_and_prints_usage(self):
        self.assertTrue(os.path.exists(SCRIPT))
        res = subprocess.run([SCRIPT, '--help'], stdout=subprocess.PIPE,
                             stderr=subprocess.PIPE, text=True, timeout=20)
        self.assertEqual(res.returncode, 0, res.stderr)
        out = (res.stdout + res.stderr).lower()
        self.assertIn('usage', out)
        self.assertIn('piscope', out)
        self.assertIn('view', out)

    def test_view_mode_prints_text_report(self):
        self.assertTrue(os.path.exists(SCRIPT))
        with tempfile.TemporaryDirectory() as td:
            log = os.path.join(td, 'temp_log.csv')
            with open(log, 'w') as f:
                f.write(TEMP_CSV)
            res = subprocess.run([SCRIPT, 'view', log], stdout=subprocess.PIPE,
                                 stderr=subprocess.PIPE, text=True, timeout=20)
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertIn('SoC TEMPERATURE STATS', res.stdout)
        self.assertIn('Average', res.stdout)
        self.assertIn('FINAL VERDICT', res.stdout)


class TestCliModule(unittest.TestCase):
    def setUp(self):
        import piscope_cli  # noqa: F401  (import is the smoke test)
        self.cli = sys.modules['piscope_cli']

    def test_build_parser_returns_parser(self):
        parser = self.cli.build_parser()
        self.assertTrue(hasattr(parser, 'parse_args'))

    def test_no_args_launches_tui(self):
        with mock.patch.object(self.cli, 'launch_tui', return_value=0) as launch:
            rc = self.cli.main([])
        launch.assert_called_once()
        self.assertEqual(rc, 0)

    def test_view_subcommand_prints_report(self):
        with tempfile.TemporaryDirectory() as td:
            log = os.path.join(td, 'temp_log.csv')
            with open(log, 'w') as f:
                f.write(TEMP_CSV)
            out = io.StringIO()
            with redirect_stdout(out):
                rc = self.cli.main(['view', log])
        self.assertEqual(rc, 0)
        self.assertIn('SoC TEMPERATURE STATS', out.getvalue())

    def test_view_missing_file_nonzero(self):
        with tempfile.TemporaryDirectory() as td:
            out = io.StringIO()
            with redirect_stdout(out):
                rc = self.cli.main(['view', os.path.join(td, 'nope.csv')])
        self.assertNotEqual(rc, 0)

    def test_help_via_main_exits_zero(self):
        with self.assertRaises(SystemExit) as ctx:
            self.cli.main(['--help'])
        self.assertEqual(ctx.exception.code, 0)


if __name__ == '__main__':
    unittest.main()
