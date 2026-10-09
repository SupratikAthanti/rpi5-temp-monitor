import unittest
import math
import sys
import os

# Add parent dir to path so analyze module can be imported
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import analyze


class TestAnalyzeMetrics(unittest.TestCase):
    def test_calculate_stats_empty(self):
        stats = analyze.calculate_stats([])
        self.assertIsNone(stats)

    def test_calculate_stats_basic(self):
        vals = [50.0, 60.0, 70.0, 80.0, 90.0]
        stats = analyze.calculate_stats(vals)
        self.assertEqual(stats['count'], 5)
        self.assertEqual(stats['min'], 50.0)
        self.assertEqual(stats['max'], 90.0)
        self.assertAlmostEqual(stats['avg'], 70.0, places=2)
        self.assertAlmostEqual(stats['median'], 70.0, places=2)
        # 95th percentile of 5 items
        self.assertGreaterEqual(stats['p95'], 85.0)

    def test_calculate_stats_single(self):
        stats = analyze.calculate_stats([65.2])
        self.assertEqual(stats['count'], 1)
        self.assertEqual(stats['min'], 65.2)
        self.assertEqual(stats['max'], 65.2)
        self.assertEqual(stats['avg'], 65.2)
        self.assertEqual(stats['p95'], 65.2)


class TestThrottleDecoder(unittest.TestCase):
    def test_zero_throttle(self):
        res = analyze.decode_throttled_bits("0x0")
        self.assertFalse(res['active_throttled'])
        self.assertFalse(res['past_throttled'])
        self.assertFalse(res['active_soft_temp_limit'])
        self.assertFalse(res['past_soft_temp_limit'])
        self.assertEqual(len(res['flags']), 0)

    def test_past_soft_temp_limit(self):
        # 0x80000 = bit 19: Soft temperature limit has occurred
        res = analyze.decode_throttled_bits("0x80000")
        self.assertFalse(res['active_throttled'])
        self.assertTrue(res['past_soft_temp_limit'])
        self.assertIn("Past: Soft temperature limit (80°C) occurred", res['flags'])

    def test_active_and_past_throttle(self):
        # bit 2 (0x4: currently throttled) + bit 18 (0x40000: throttled has occurred)
        res = analyze.decode_throttled_bits("0x40004")
        self.assertTrue(res['active_throttled'])
        self.assertTrue(res['past_throttled'])
        self.assertIn("Active: Currently throttled", res['flags'])


class TestBucketing(unittest.TestCase):
    def test_temperature_buckets(self):
        temps = [45.0, 55.0, 62.0, 75.0, 81.0, 86.0]
        buckets = analyze.bucket_temperatures(temps)
        self.assertEqual(buckets['under_60']['count'], 2)
        self.assertEqual(buckets['60_to_70']['count'], 1)
        self.assertEqual(buckets['70_to_80']['count'], 1)
        self.assertEqual(buckets['80_to_85']['count'], 1)
        self.assertEqual(buckets['over_85']['count'], 1)
        self.assertAlmostEqual(buckets['under_60']['pct'], 2 / 6 * 100, places=1)

    def test_frequency_buckets(self):
        freqs = [2400, 2400, 1500, 1000, 750, 600, 0]
        buckets = analyze.bucket_frequencies(freqs)
        self.assertEqual(buckets['2400_mhz']['count'], 2)
        self.assertEqual(buckets['1500_mhz']['count'], 1)
        self.assertEqual(buckets['1000_mhz']['count'], 1)
        self.assertEqual(buckets['750_mhz']['count'], 1)
        self.assertEqual(buckets['600_mhz']['count'], 1)
        self.assertEqual(buckets['other']['count'], 1)


class TestPerformanceLoss(unittest.TestCase):
    def test_performance_loss_full_speed(self):
        freqs = [2400, 2400, 2400]
        loss = analyze.calculate_performance_loss(freqs)
        self.assertAlmostEqual(loss['performance_loss_pct'], 0.0, places=1)
        self.assertAlmostEqual(loss['effective_speed_factor'], 1.0, places=2)

    def test_performance_loss_half_speed(self):
        freqs = [1200, 1200, 1200]
        loss = analyze.calculate_performance_loss(freqs)
        self.assertAlmostEqual(loss['performance_loss_pct'], 50.0, places=1)
        self.assertAlmostEqual(loss['effective_speed_factor'], 0.5, places=2)

    def test_performance_loss_mixed(self):
        freqs = [2400, 1500, 1000]
        loss = analyze.calculate_performance_loss(freqs)
        avg = (2400 + 1500 + 1000) / 3
        expected_loss = (1 - avg / 2400) * 100
        self.assertAlmostEqual(loss['performance_loss_pct'], expected_loss, places=1)

    def test_performance_loss_empty(self):
        loss = analyze.calculate_performance_loss([])
        self.assertIsNone(loss)

    def test_performance_loss_zeros(self):
        loss = analyze.calculate_performance_loss([0, 0, 0])
        self.assertIsNone(loss)


class TestVerdictDecisionMatrix(unittest.TestCase):
    def test_verdict_bare_board_fine(self):
        # Cool conditions: max 68C, avg 54C, 0 throttles
        stats = {'min': 45.0, 'avg': 54.0, 'p95': 62.0, 'max': 68.0, 'count': 100}
        throttle_summary = {
            'active_throttle_count': 0,
            'past_throttle_count': 0,
            'past_soft_temp_count': 0,
        }
        buckets = {
            'under_60': {'pct': 80.0},
            '60_to_70': {'pct': 20.0},
            '70_to_80': {'pct': 0.0},
            '80_to_85': {'pct': 0.0},
            'over_85': {'pct': 0.0},
        }
        verdict = analyze.determine_verdict(stats, throttle_summary, buckets)
        self.assertEqual(verdict['decision'], analyze.DECISION_NO_COOLER)

    def test_verdict_passive_cooler_sufficient(self):
        # Moderate conditions: warm baseline or brief bursts up to 81C during experiments (<1% time), no hard throttling (>85C)
        stats = {'min': 52.0, 'avg': 67.0, 'p95': 74.0, 'max': 81.5, 'count': 1000}
        throttle_summary = {
            'active_throttle_count': 0,
            'past_throttle_count': 0,
            'past_soft_temp_count': 5, # Hit soft temp briefly
        }
        buckets = {
            'under_60': {'pct': 10.0},
            '60_to_70': {'pct': 60.0},
            '70_to_80': {'pct': 29.5},
            '80_to_85': {'pct': 0.5}, # < 1% of time
            'over_85': {'pct': 0.0},
        }
        verdict = analyze.determine_verdict(stats, throttle_summary, buckets)
        self.assertEqual(verdict['decision'], analyze.DECISION_PASSIVE_COOLER)

    def test_verdict_active_cooler_required(self):
        # Heavy heat / sustained throttling: temps hit >= 85C or >=80C for >2% of time or frequent active throttles
        stats = {'min': 65.0, 'avg': 78.0, 'p95': 84.5, 'max': 86.2, 'count': 1000}
        throttle_summary = {
            'active_throttle_count': 12,
            'past_throttle_count': 45,
            'past_soft_temp_count': 120,
        }
        buckets = {
            'under_60': {'pct': 0.0},
            '60_to_70': {'pct': 5.0},
            '70_to_80': {'pct': 60.0},
            '80_to_85': {'pct': 25.0},
            'over_85': {'pct': 10.0},
        }
        verdict = analyze.determine_verdict(stats, throttle_summary, buckets)
        self.assertEqual(verdict['decision'], analyze.DECISION_ACTIVE_COOLER)


class TestCSVParser(unittest.TestCase):
    def test_parse_csv_records(self):
        csv_data = """timestamp,soc_temp_c,pmic_temp_c,load_1m,arm_freq_mhz,throttled_hex,top_proc,top_cpu_pct
2026-10-09T12:00:00Z,65.2,58.0,0.45,2400,0x0,python3,14.5
2026-10-09T12:01:00Z,78.5,67.1,2.10,2400,0x80000,ollama,95.0
2026-10-09T12:02:00Z,85.1,72.4,3.80,1500,0x80004,ollama,98.2
"""
        records = analyze.parse_csv_string(csv_data)
        self.assertEqual(len(records), 3)
        self.assertEqual(records[0]['soc_temp'], 65.2)
        self.assertEqual(records[0]['pmic_temp'], 58.0)
        self.assertEqual(records[1]['top_proc'], 'ollama')
        self.assertEqual(records[1]['throttled_hex'], '0x80000')
        self.assertEqual(records[2]['soc_temp'], 85.1)

    def test_parse_csv_header_only(self):
        csv_data = """timestamp,soc_temp_c,pmic_temp_c,load_1m,arm_freq_mhz,throttled_hex,top_proc,top_cpu_pct
"""
        records = analyze.parse_csv_string(csv_data)
        self.assertEqual(len(records), 0)

    def test_parse_csv_empty(self):
        records = analyze.parse_csv_string("")
        self.assertEqual(len(records), 0)

    def test_parse_csv_malformed_rows(self):
        csv_data = """timestamp,soc_temp_c,pmic_temp_c,load_1m,arm_freq_mhz,throttled_hex,top_proc,top_cpu_pct
2026-10-09T12:00:00Z,65.2,58.0,0.45,2400,0x0,python3,14.5
invalid,data,here
2026-10-09T12:02:00Z,85.1,72.4,3.80,1500,0x80004,ollama,98.2
"""
        records = analyze.parse_csv_string(csv_data)
        # Should skip malformed row and return 2 valid records
        self.assertEqual(len(records), 2)

    def test_parse_csv_only_header(self):
        csv_data = "timestamp,soc_temp_c,pmic_temp_c,load_1m,arm_freq_mhz,throttled_hex,top_proc,top_cpu_pct\n"
        records = analyze.parse_csv_string(csv_data)
        self.assertEqual(len(records), 0)

    def test_parse_csv_with_n_a_pmic(self):
        csv_data = """timestamp,soc_temp_c,pmic_temp_c,load_1m,arm_freq_mhz,throttled_hex,top_proc,top_cpu_pct
2026-10-09T12:00:00Z,65.2,N/A,0.45,2400,0x0,python3,14.5
"""
        records = analyze.parse_csv_string(csv_data)
        self.assertEqual(len(records), 1)
        self.assertIsNone(records[0]['pmic_temp'])


if __name__ == '__main__':
    unittest.main()
