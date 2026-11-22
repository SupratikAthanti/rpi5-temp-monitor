"""Regression test for the 2026-11-21 QA audit: persistence env-var mismatch.

``piscope_tui._persist_sample`` exports ``PISCOPE_POWER_LOG`` to the collector,
but ``collect.sh`` historically only read ``POWER_LOG``.  These tests run the
real collector script with every path overridden into a temp dir (and with a
private PID file), so no real hardware, home directory or repo data is touched.
See ``EXPECTED_API.md`` section 14, bug 4.
"""

import csv
import os
import subprocess
import tempfile
import unittest

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
COLLECT = os.path.join(REPO, 'collect.sh')

PMIC_TEXT = (
    '3V3_SYS A 0.5\n3V3_SYS V 3.3\n'
    'VDD_CORE A 1.0\nVDD_CORE V 0.85\n'
    'EXT5V_V V 5.0\nBATT_V V 4.1\n'
)
EXPECTED_PMIC_W = 0.5 * 3.3 + 1.0 * 0.85  # 2.5 W


class TestPowerLogEnvAlias(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.temp_log = os.path.join(self.tmp.name, 'temp_log.csv')
        self.env = dict(os.environ)
        # Explicitly remove both overrides: the test adds exactly one at a time.
        self.env.pop('POWER_LOG', None)
        self.env.pop('PISCOPE_POWER_LOG', None)
        self.env.update({
            'COLLECTOR_PID_FILE': os.path.join(self.tmp.name, 'collector.pid'),
            'FAKE_PMIC_ADC': PMIC_TEXT,
            'WALL_FACTOR': '1.0',
            'FAKE_SOC_TEMP': '60.0',
            'SPIKE_STATE_FILE': os.path.join(self.tmp.name, 'spike_state'),
            'SPIKE_LOG': os.path.join(self.tmp.name, 'spikes.csv'),
        })

    def tearDown(self):
        self.tmp.cleanup()

    def run_collector(self, env):
        return subprocess.run(
            [COLLECT, '--mode', 'power', self.temp_log],
            cwd=self.tmp.name, env=env, text=True,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=20)

    def test_piscope_power_log_env_is_honoured(self):
        target = os.path.join(self.tmp.name, 'piscope_power.csv')
        env = dict(self.env)
        env['PISCOPE_POWER_LOG'] = target

        result = self.run_collector(env)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(os.path.exists(target),
                        'collector ignored PISCOPE_POWER_LOG set by the TUI')
        with open(target, newline='') as f:
            rows = list(csv.reader(f))
        self.assertEqual(rows[0], ['timestamp', 'pmic_w', 'wall_w'])
        self.assertEqual(len(rows), 2)
        self.assertAlmostEqual(float(rows[1][1]), EXPECTED_PMIC_W, places=3)
        self.assertAlmostEqual(float(rows[1][2]), EXPECTED_PMIC_W, places=3)

        # The old default sibling must NOT get a stray power log.
        default = os.path.join(self.tmp.name, 'power_log.csv')
        self.assertFalse(os.path.exists(default))

    def test_legacy_power_log_env_still_works(self):
        # Backwards compatibility: existing cron users set POWER_LOG.
        legacy = os.path.join(self.tmp.name, 'legacy_power.csv')
        env = dict(self.env)
        env['POWER_LOG'] = legacy

        result = self.run_collector(env)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(os.path.exists(legacy),
                        'legacy POWER_LOG override was broken')
        with open(legacy, newline='') as f:
            self.assertEqual(len(list(csv.reader(f))), 2)


if __name__ == '__main__':
    unittest.main()
