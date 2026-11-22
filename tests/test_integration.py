"""End-to-end data-in / expected-out integration test.

Hand-crafted CSV fixtures with known values are written to a temp dir and fed to the
analyzer's report builder. Concrete expected numbers are asserted, proving that
input data actually flows through to correct output (not just that functions exist).
"""

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import analyze  # noqa: E402

TEMP_CSV = """timestamp,soc_temp_c,pmic_temp_c,load_1m,arm_freq_mhz,throttled_hex,top_proc,top_cpu_pct
2026-11-21T10:00:00Z,60.0,50.0,0.50,2400,0x0,opencode,50.0
2026-11-21T10:01:00Z,70.0,55.0,1.00,1500,0x0,opencode,60.0
2026-11-21T10:02:00Z,80.0,60.0,2.00,1500,0x4,opencode,70.0
2026-11-21T10:03:00Z,90.0,65.0,3.00,2400,0x4,ps,80.0
"""

POWER_CSV = """timestamp,pmic_w,wall_w
2026-11-21T10:00:00Z,2.0,2.4
2026-11-21T10:01:00Z,4.0,4.8
2026-11-21T10:02:00Z,6.0,7.2
2026-11-21T10:03:00Z,8.0,9.6
"""

SPIKES_CSV = """timestamp,soc_temp_c,baseline_temp_c,delta_c,load_1m,culprit_app,top_processes_detail
2026-11-21T10:03:00Z,90.0,70.0,20.0,3.0,opencode,opencode(80%)
"""

CONFIG = {'rate_per_kwh': 0.15, 'battery_wh': 45.0, 'energy_windows_min': [60, 120]}


class TestSyntheticReport(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.log = os.path.join(self.tmp.name, 'temp_log.csv')
        self.power = os.path.join(self.tmp.name, 'power_log.csv')
        with open(self.log, 'w') as f:
            f.write(TEMP_CSV)
        with open(self.power, 'w') as f:
            f.write(POWER_CSV)
        with open(os.path.join(self.tmp.name, 'spikes.csv'), 'w') as f:
            f.write(SPIKES_CSV)
        self.report = analyze.build_report(self.log, power_log_file=self.power,
                                           config=CONFIG)

    def tearDown(self):
        self.tmp.cleanup()

    def test_span_and_sample_count(self):
        self.assertIn('Total Samples     : 4', self.report)
        self.assertIn('2026-11-21T10:00:00Z to 2026-11-21T10:03:00Z', self.report)

    def test_soc_temp_stats(self):
        self.assertIn('Min             : 60.0°C', self.report)
        self.assertIn('Average         : 75.0°C', self.report)
        self.assertIn('Median          : 75.0°C', self.report)
        self.assertIn('95th Percentile : 90.0°C', self.report)
        self.assertIn('Max             : 90.0°C', self.report)
        self.assertIn('11.2°C std dev', self.report)

    def test_pmic_load_freq_stats(self):
        self.assertIn('PMIC TEMP (Avg)   : 57.5°C (Max: 65.0°C)', self.report)
        self.assertIn('CPU LOAD (1m Avg) : 1.62 (Max: 3.00)', self.report)
        self.assertIn('ARM FREQUENCY (Avg): 1950 MHz (Max: 2400 MHz)', self.report)

    def test_distributions(self):
        # 60,70,80,90 -> one sample in each of four bands.
        self.assertIn('60°C - 70°C (Normal)', self.report)
        self.assertIn('(25.00%)', self.report)
        # 2400 MHz x2, 1500 MHz x2
        self.assertIn('2400 MHz (Max)', self.report)
        self.assertIn('1500 MHz (Throttled)', self.report)

    def test_performance_impact_numbers(self):
        self.assertIn('18.8% slower', self.report)
        self.assertIn('81.2% of maximum', self.report)
        self.assertIn('~14 minutes', self.report)

    def test_top_processes_and_spikes(self):
        self.assertIn('opencode', self.report)
        self.assertIn('3 samples', self.report)
        self.assertIn('SPIKE ATTRIBUTION (5°C+ jumps, 1 events)', self.report)
        self.assertIn('frequent', self.report)

    def test_verdict_and_reasons(self):
        self.assertIn('Active Cooler Strongly Recommended', self.report)
        self.assertIn('Maximum temperature reached 90.0°C', self.report)
        self.assertIn('Recorded active throttling events (2 samples)', self.report)
        self.assertIn('Spent time above 85°C.', self.report)

    def test_energy_cost_and_battery_numbers(self):
        # Left-Riemann: (2.4 + 4.8 + 7.2) watts * 60 s / 3600 = 0.24 Wh.
        self.assertIn('0.24 Wh', self.report)
        self.assertIn('$0.00004', self.report)
        self.assertIn('7.5 h', self.report)   # 45 Wh / 6.0 W avg
        self.assertIn('4.7 h', self.report)   # 45 Wh / 9.6 W p95 (conservative)

    def test_range_kwarg_accepted(self):
        report = analyze.build_report(self.log, power_log_file=self.power,
                                      config=CONFIG, range_spec='7d')
        self.assertIsInstance(report, str)
        self.assertIn('Total Samples     : 4', report)


if __name__ == '__main__':
    unittest.main()
