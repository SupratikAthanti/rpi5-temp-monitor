"""Power-domain unit tests for the PiStats spec.

Covers:
  * vcgencmd pmic_read_adc parsing (rail A*V, exclusions, malformed input, duplicates)
  * PMIC -> wall conversion (configurable factor + linear calibration)
  * energy (Wh), cost ($), and battery runtime estimation

All tests are pure/stdlib and must run in milliseconds.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import power_read  # noqa: E402  (expected to be created by the implementation subagent)
import analyze  # noqa: E402


ADC_SAMPLE = """
3V3_SYS A  0.50000
3V3_SYS V  3.3000
VDD_CORE A  1.00000
VDD_CORE V  0.8500
EXT5V_V V  5.0000
BATT_V  V  4.1000
"""


class TestPmicAdcParsing(unittest.TestCase):
    """Expected interface: power_read.parse_pmic_adc(text) -> {rail: watts}."""

    def test_sum_av_per_rail(self):
        rails = power_read.parse_pmic_adc(ADC_SAMPLE)
        self.assertAlmostEqual(rails['3V3_SYS'], 0.5 * 3.3, places=6)
        self.assertAlmostEqual(rails['VDD_CORE'], 1.0 * 0.85, places=6)

    def test_voltage_only_rails_excluded(self):
        rails = power_read.parse_pmic_adc(ADC_SAMPLE)
        self.assertNotIn('EXT5V_V', rails)
        self.assertNotIn('BATT_V', rails)

    def test_explicit_exclusion_even_with_current_reading(self):
        text = ADC_SAMPLE + "EXT5V_V A 2.0\n"
        rails = power_read.parse_pmic_adc(text)
        self.assertNotIn('EXT5V_V', rails)

    def test_total_is_sum_of_rails(self):
        expected = 0.5 * 3.3 + 1.0 * 0.85
        self.assertAlmostEqual(power_read.sum_pmic_watts(ADC_SAMPLE), expected, places=6)
        self.assertAlmostEqual(
            power_read.sum_pmic_watts(power_read.parse_pmic_adc(ADC_SAMPLE)),
            expected, places=6)

    def test_whitespace_and_tabs_tolerated(self):
        text = "\tVDD_CORE\tA\t0.40  \n   VDD_CORE   V   0.90   \n"
        rails = power_read.parse_pmic_adc(text)
        self.assertAlmostEqual(rails['VDD_CORE'], 0.36, places=6)

    def test_malformed_lines_skipped(self):
        text = (
            "3V3_SYS A 0.5\n"
            "3V3_SYS V 3.3\n"
            "GARBAGE\n"                 # no fields
            "BAD_A A not-a-number\n"    # non-numeric value
            "BAD_B X 1.0\n"             # unknown unit
            "ONLY_V V 1.8\n"            # voltage only -> excluded
        )
        rails = power_read.parse_pmic_adc(text)
        self.assertEqual(set(rails), {'3V3_SYS'})
        self.assertAlmostEqual(rails['3V3_SYS'], 1.65, places=6)

    def test_exact_duplicate_lines_collapsed(self):
        text = (
            "VDD_CORE A 1.0\n"
            "VDD_CORE A 1.0\n"   # identical duplicate -> counts once
            "VDD_CORE V 0.8\n"
            "VDD_CORE V 0.8\n"   # identical duplicate -> counts once
        )
        rails = power_read.parse_pmic_adc(text)
        self.assertAlmostEqual(rails['VDD_CORE'], 0.8, places=6)

    def test_dual_reported_currents_are_summed(self):
        # Two distinct current readings (dual-phase rail) sum before multiplying.
        text = (
            "VDD_CORE A 0.25\n"
            "VDD_CORE A 0.75\n"
            "VDD_CORE V 0.8\n"
        )
        rails = power_read.parse_pmic_adc(text)
        self.assertAlmostEqual(rails['VDD_CORE'], 0.8, places=6)

    def test_empty_and_garbage_text_returns_empty_dict(self):
        self.assertEqual(power_read.parse_pmic_adc(""), {})
        self.assertEqual(power_read.parse_pmic_adc("   \n \n"), {})
        self.assertEqual(power_read.parse_pmic_adc("junk\nmore junk\n"), {})

    def test_multiple_voltage_readings_use_max(self):
        text = (
            "RAIL_X A 0.5\n"
            "RAIL_X V 1.0\n"
            "RAIL_X V 1.2\n"
        )
        rails = power_read.parse_pmic_adc(text)
        self.assertAlmostEqual(rails['RAIL_X'], 0.6, places=6)


class TestWallFactorMath(unittest.TestCase):
    """Expected interface: analyze.pmic_to_wall / analyze.calibrated_wall_w."""

    def test_default_factor_is_identity(self):
        self.assertAlmostEqual(analyze.pmic_to_wall(5.42), 5.42, places=6)

    def test_configurable_factor(self):
        self.assertAlmostEqual(analyze.pmic_to_wall(5.0, factor=1.2), 6.0, places=6)
        self.assertAlmostEqual(analyze.pmic_to_wall(5.0, factor=0.5), 2.5, places=6)

    def test_linear_calibration_form(self):
        # wall = slope * pmic + intercept
        self.assertAlmostEqual(
            analyze.calibrated_wall_w(5.0, slope=1.1, intercept=0.5), 6.0, places=6)
        self.assertAlmostEqual(
            analyze.calibrated_wall_w(0.0, slope=1.1, intercept=0.5), 0.5, places=6)

    def test_factor_and_calibration_agree_when_intercept_zero(self):
        for w in (0.0, 1.25, 7.7):
            self.assertAlmostEqual(
                analyze.pmic_to_wall(w, factor=1.15),
                analyze.calibrated_wall_w(w, slope=1.15, intercept=0.0),
                places=9)


class TestEnergyCostBattery(unittest.TestCase):
    """Expected interface: analyze.energy_wh / cost_dollars / battery_runtime_hours /
    analyze.battery_estimate."""

    def test_energy_wh_left_riemann(self):
        # 3 intervals of 60 s at 2.4, 4.8, 7.2 W -> (14.4*60)/3600 = 0.24 Wh
        samples = [(0, 2.4), (60, 4.8), (120, 7.2), (180, 9.6)]
        self.assertAlmostEqual(analyze.energy_wh(samples), 0.24, places=9)

    def test_energy_wh_accepts_dict_records(self):
        records = [
            {'epoch': 0, 'wall_w': 2.4},
            {'epoch': 60, 'wall_w': 4.8},
            {'epoch': 120, 'wall_w': 7.2},
            {'epoch': 180, 'wall_w': 9.6},
        ]
        self.assertAlmostEqual(analyze.energy_wh(records), 0.24, places=9)

    def test_energy_wh_ignores_last_sample(self):
        # Two 1-hour intervals at 10 W = 20 Wh; the final reading has no
        # trailing interval, so its 999 W must NOT contribute.
        samples = [(0, 10.0), (3600, 10.0), (7200, 999.0)]
        self.assertAlmostEqual(analyze.energy_wh(samples), 20.0, places=9)

    def test_energy_wh_window_filters_to_recent_samples(self):
        # 2 h of 1 W samples; only the last 60 min should count.
        samples = [(i * 600, 1.0) for i in range(13)]  # 0 .. 7200 s
        self.assertAlmostEqual(analyze.energy_wh(samples), 2.0, places=9)
        self.assertAlmostEqual(analyze.energy_wh(samples, window_minutes=60), 1.0, places=9)

    def test_energy_wh_short_input(self):
        self.assertEqual(analyze.energy_wh([]), 0.0)
        self.assertEqual(analyze.energy_wh([(0, 5.0)]), 0.0)

    def test_cost_from_kwh_rate(self):
        self.assertAlmostEqual(analyze.cost_dollars(1000.0, 0.13), 0.13, places=9)
        self.assertAlmostEqual(analyze.cost_dollars(0.24, 0.13), 0.0000312, places=9)

    def test_battery_runtime(self):
        self.assertAlmostEqual(analyze.battery_runtime_hours(45.0, 5.0), 9.0, places=6)
        self.assertAlmostEqual(analyze.battery_runtime_hours(45.0, 6.0), 7.5, places=6)
        self.assertEqual(analyze.battery_runtime_hours(45.0, 0.0), 0.0)
        self.assertEqual(analyze.battery_runtime_hours(45.0, -1.0), 0.0)

    def test_battery_estimate_conservative_uses_p95(self):
        est = analyze.battery_estimate(45.0, avg_power_w=6.0, p95_power_w=9.6)
        self.assertAlmostEqual(est['avg_hours'], 7.5, places=6)
        self.assertAlmostEqual(est['p95_hours'], 4.6875, places=6)
        self.assertAlmostEqual(est['conservative_hours'], est['p95_hours'], places=9)
        self.assertLess(est['conservative_hours'], est['avg_hours'])
        self.assertEqual(est['capacity_wh'], 45.0)


if __name__ == '__main__':
    unittest.main()
