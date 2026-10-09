import unittest
import subprocess
import os
import tempfile
import csv


class TestCollectorScript(unittest.TestCase):
    def setUp(self):
        self.script_path = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'collect.sh'))
        self.temp_dir = tempfile.TemporaryDirectory()
        self.log_file = os.path.join(self.temp_dir.name, 'test_temp_log.csv')

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_collector_creates_header_and_first_row(self):
        # Run collector pointing to our temp log file
        cmd = [self.script_path, self.log_file]
        res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        self.assertEqual(res.returncode, 0, f"Script failed: {res.stderr}")

        self.assertTrue(os.path.exists(self.log_file))

        with open(self.log_file, 'r', newline='') as f:
            reader = list(csv.reader(f))

        self.assertGreaterEqual(len(reader), 2)
        expected_header = [
            'timestamp',
            'soc_temp_c',
            'pmic_temp_c',
            'load_1m',
            'arm_freq_mhz',
            'throttled_hex',
            'top_proc',
            'top_cpu_pct'
        ]
        self.assertEqual(reader[0], expected_header)

        # Check data row
        row = reader[1]
        self.assertEqual(len(row), 8)
        # Validate float temperature
        soc_temp = float(row[1])
        self.assertGreater(soc_temp, 0.0)
        self.assertLess(soc_temp, 120.0)

        # Validate load
        load = float(row[3])
        self.assertGreaterEqual(load, 0.0)

        # Validate throttled format (starts with 0x)
        self.assertTrue(row[5].startswith('0x'))

    def test_collector_appends_without_repeating_header(self):
        cmd = [self.script_path, self.log_file]
        subprocess.run(cmd, check=True)
        subprocess.run(cmd, check=True)

        with open(self.log_file, 'r', newline='') as f:
            reader = list(csv.reader(f))

        # 1 header + 2 data rows
        self.assertEqual(len(reader), 3)
        self.assertEqual(reader[0][0], 'timestamp')
        self.assertNotEqual(reader[1][0], 'timestamp')
        self.assertNotEqual(reader[2][0], 'timestamp')

    def test_collector_dry_run_stdout(self):
        # When passed --dry-run or stdout mode, output prints to stdout without writing
        cmd = [self.script_path, '--dry-run']
        res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        self.assertEqual(res.returncode, 0)
        out = res.stdout.strip()
        parts = out.split(',')
        self.assertEqual(len(parts), 8, f"Expected 8 comma-separated fields, got: {out}")


if __name__ == '__main__':
    unittest.main()
