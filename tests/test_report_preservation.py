"""Regression tests: new PiStats code must not remove/break the old report API.

The existing tests already cover most pure functions; this module checks that every
old exported symbol still exists and that the generated report still carries every
field from the README's "Example Report Output".
"""

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import analyze  # noqa: E402

CSV = """timestamp,soc_temp_c,pmic_temp_c,load_1m,arm_freq_mhz,throttled_hex,top_proc,top_cpu_pct
2026-11-21T10:00:00Z,60.0,50.0,0.50,2400,0x0,opencode,50.0
2026-11-21T10:01:00Z,70.0,55.0,1.00,1500,0x0,opencode,60.0
2026-11-21T10:02:00Z,80.0,60.0,2.00,1500,0x4,opencode,70.0
2026-11-21T10:03:00Z,90.0,65.0,3.00,2400,0x4,ps,80.0
"""

SPIKES = """timestamp,soc_temp_c,baseline_temp_c,delta_c,load_1m,culprit_app,top_processes_detail
2026-11-21T10:03:00Z,90.0,70.0,20.0,3.0,opencode,opencode(80%)
"""


class TestOldExportedFunctions(unittest.TestCase):
    def test_all_legacy_symbols_present(self):
        names = [
            'parse_csv_string', 'parse_csv_file', 'calculate_stats',
            'bucket_temperatures', 'bucket_frequencies',
            'calculate_performance_loss', 'decode_throttled_bits',
            'summarize_throttling', 'detect_culprit_app', 'parse_spikes_string',
            'parse_spikes_file', 'group_spikes_by_app', 'frequency_weighted_note',
            'determine_verdict', 'main',
        ]
        for name in names:
            with self.subTest(name=name):
                self.assertTrue(callable(getattr(analyze, name, None)),
                                f'analyze.{name} is missing')

    def test_legacy_functions_still_compute(self):
        records = analyze.parse_csv_string(CSV)
        stats = analyze.calculate_stats([r['soc_temp'] for r in records])
        self.assertEqual(stats['count'], 4)
        self.assertEqual(analyze.bucket_temperatures([60, 70, 80, 90])['over_85']['count'], 1)
        self.assertEqual(analyze.bucket_frequencies([2400, 1500])['2400_mhz']['count'], 1)
        self.assertIsNotNone(analyze.calculate_performance_loss([2400, 1500]))
        self.assertTrue(analyze.decode_throttled_bits('0x4')['active_throttled'])
        self.assertEqual(analyze.summarize_throttling(records)['active_throttle_count'], 2)
        self.assertEqual(analyze.detect_culprit_app('ollama|90|10|ollama serve'), 'ollama')
        spikes = analyze.parse_spikes_string(SPIKES)
        self.assertEqual(analyze.group_spikes_by_app(spikes)['opencode']['count'], 1)
        self.assertIn('frequent', analyze.frequency_weighted_note(spikes, 4))
        verdict = analyze.determine_verdict(stats, analyze.summarize_throttling(records),
                                            analyze.bucket_temperatures([60, 70, 80, 90]))
        self.assertEqual(verdict['decision'], analyze.DECISION_ACTIVE_COOLER)


class TestReportFieldsPreserved(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.log = os.path.join(self.tmp.name, 'temp_log.csv')
        with open(self.log, 'w') as f:
            f.write(CSV)
        with open(os.path.join(self.tmp.name, 'spikes.csv'), 'w') as f:
            f.write(SPIKES)
        self.report = analyze.build_report(self.log)

    def tearDown(self):
        self.tmp.cleanup()

    def test_report_is_a_string(self):
        self.assertIsInstance(self.report, str)
        self.assertGreater(len(self.report), 500)

    def test_header_fields(self):
        for token in ('TEMPERATURE & COOLER DECISION REPORT', 'Log File',
                      'Total Samples', 'Time Span'):
            with self.subTest(token=token):
                self.assertIn(token, self.report)

    def test_temperature_stats_fields(self):
        for token in ('SoC TEMPERATURE STATS', 'Min', 'Average', 'Median',
                      '95th Percentile', 'Max', 'std dev', 'PMIC TEMP',
                      'CPU LOAD', 'ARM FREQUENCY'):
            with self.subTest(token=token):
                self.assertIn(token, self.report)

    def test_distribution_fields(self):
        for token in ('TEMPERATURE DISTRIBUTION', 'ARM FREQUENCY DISTRIBUTION',
                      'Under 60', 'Over 85', '2400 MHz', '1500 MHz', 'Other/Unknown'):
            with self.subTest(token=token):
                self.assertIn(token, self.report)

    def test_performance_fields(self):
        for token in ('PERFORMANCE IMPACT ANALYSIS', 'Performance Loss',
                      'Effective Speed', 'Time Impact'):
            with self.subTest(token=token):
                self.assertIn(token, self.report)

    def test_process_spike_verdict_fields(self):
        for token in ('TOP PROCESSES', 'SPIKE ATTRIBUTION', 'FINAL VERDICT',
                      'Reasons', 'Active Cooler Strongly Recommended'):
            with self.subTest(token=token):
                self.assertIn(token, self.report)


if __name__ == '__main__':
    unittest.main()
