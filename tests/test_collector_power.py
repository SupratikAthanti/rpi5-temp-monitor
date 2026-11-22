"""Collector power-log / mode / lock tests.

All paths and external readings are overridden via env vars so nothing touches real
hardware or real home directories. Uses only stdlib.
"""

import csv
import os
import shutil
import subprocess
import tempfile
import unittest

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
COLLECT = os.path.join(REPO, 'collect.sh')
RESET = os.path.join(REPO, 'reset.sh')

PMIC_TEXT = (
    "3V3_SYS A 0.5\n3V3_SYS V 3.3\n"
    "VDD_CORE A 1.0\nVDD_CORE V 0.85\n"
    "EXT5V_V V 5.0\nBATT_V V 4.1\n"
)
EXPECTED_PMIC_W = 0.5 * 3.3 + 1.0 * 0.85  # 2.5 W


class CollectorTestBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.temp_log = os.path.join(self.tmp.name, 'temp_log.csv')
        self.power_log = os.path.join(self.tmp.name, 'power_log.csv')
        self.pid_file = os.path.join(self.tmp.name, 'collector.pid')
        self.env = dict(os.environ)
        self.env.update({
            'POWER_LOG': self.power_log,
            'COLLECTOR_PID_FILE': self.pid_file,
            'FAKE_PMIC_ADC': PMIC_TEXT,
            'WALL_FACTOR': '1.0',
            'FAKE_SOC_TEMP': '60.0',
            'SPIKE_STATE_FILE': os.path.join(self.tmp.name, 'spike_state'),
            'SPIKE_LOG': os.path.join(self.tmp.name, 'spikes.csv'),
        })

    def tearDown(self):
        self.tmp.cleanup()

    def run_collector(self, *args, env=None):
        # cwd=temp dir: until --mode is implemented, the legacy collector treats
        # '--mode' as a log path; keep any stray file out of the repo root.
        return subprocess.run([COLLECT, *args, self.temp_log],
                              cwd=self.tmp.name,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              text=True, env=env or self.env, timeout=20)


class TestPowerLogWriting(CollectorTestBase):
    def test_power_log_header_and_row(self):
        res = self.run_collector('--mode', 'both')
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertTrue(os.path.exists(self.power_log), 'power_log.csv not created')
        with open(self.power_log, newline='') as f:
            rows = list(csv.reader(f))
        self.assertEqual(rows[0], ['timestamp', 'pmic_w', 'wall_w'])
        self.assertEqual(len(rows), 2)
        self.assertAlmostEqual(float(rows[1][1]), EXPECTED_PMIC_W, places=3)
        self.assertAlmostEqual(float(rows[1][2]), EXPECTED_PMIC_W, places=3)

    def test_wall_factor_applied_to_log(self):
        env = dict(self.env)
        env['WALL_FACTOR'] = '1.2'
        res = self.run_collector('--mode', 'power', env=env)
        self.assertEqual(res.returncode, 0, res.stderr)
        with open(self.power_log, newline='') as f:
            rows = list(csv.reader(f))
        self.assertAlmostEqual(float(rows[1][1]), EXPECTED_PMIC_W, places=3)
        self.assertAlmostEqual(float(rows[1][2]), EXPECTED_PMIC_W * 1.2, places=3)

    def test_power_log_separate_from_temp_log(self):
        res = self.run_collector('--mode', 'both')
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertTrue(os.path.exists(self.temp_log))
        self.assertTrue(os.path.exists(self.power_log))
        with open(self.temp_log) as f:
            self.assertTrue(f.readline().startswith('timestamp,soc_temp_c'))
        with open(self.power_log) as f:
            self.assertTrue(f.readline().startswith('timestamp,pmic_w'))

    def test_appending_does_not_repeat_header(self):
        self.run_collector('--mode', 'both')
        self.run_collector('--mode', 'both')
        with open(self.power_log, newline='') as f:
            rows = list(csv.reader(f))
        self.assertEqual(len(rows), 3)  # header + 2 rows
        self.assertEqual(rows[0][0], 'timestamp')


class TestModeGating(CollectorTestBase):
    def test_mode_temp_writes_only_temp_log(self):
        res = self.run_collector('--mode', 'temp')
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertTrue(os.path.exists(self.temp_log))
        self.assertFalse(os.path.exists(self.power_log))

    def test_mode_power_writes_only_power_log(self):
        res = self.run_collector('--mode', 'power')
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertTrue(os.path.exists(self.power_log))
        self.assertFalse(os.path.exists(self.temp_log))

    def test_default_mode_is_both(self):
        res = self.run_collector()
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertTrue(os.path.exists(self.temp_log))
        self.assertTrue(os.path.exists(self.power_log))


class TestLockAwareSkip(CollectorTestBase):
    def test_skip_if_locked_exits_zero_without_writing(self):
        holder = subprocess.Popen(['sleep', '30'])
        try:
            with open(self.pid_file, 'w') as f:
                f.write(f'{holder.pid}\n')
            res = self.run_collector('--mode', 'both', '--skip-if-locked')
            self.assertEqual(res.returncode, 0, res.stderr)
            self.assertFalse(os.path.exists(self.temp_log),
                             'skip-if-locked must not write temp_log.csv')
            self.assertFalse(os.path.exists(self.power_log),
                             'skip-if-locked must not write power_log.csv')
        finally:
            holder.terminate()
            holder.wait(timeout=5)


class TestResetClearsPowerLog(unittest.TestCase):
    def test_reset_removes_power_log_alongside_other_logs(self):
        # Run a COPY of reset.sh so its SCRIPT_DIR is the temp dir (never repo root).
        with tempfile.TemporaryDirectory() as td:
            shutil.copy(RESET, os.path.join(td, 'reset.sh'))
            for name in ('temp_log.csv', 'power_log.csv', 'spikes.csv'):
                with open(os.path.join(td, name), 'w') as f:
                    f.write('timestamp\n')
            res = subprocess.run([os.path.join(td, 'reset.sh')], cwd=td,
                                 stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                 text=True, timeout=20)
            self.assertEqual(res.returncode, 0, res.stderr)
            for name in ('temp_log.csv', 'power_log.csv', 'spikes.csv'):
                self.assertFalse(os.path.exists(os.path.join(td, name)),
                                 f'{name} should be removed by reset.sh')


if __name__ == '__main__':
    unittest.main()
