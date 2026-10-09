import unittest
import os
import tempfile
import datetime
import analyze


class Test7DaySimulation(unittest.TestCase):
    def test_7day_simulation_run(self):
        # Generate synthetic 7-day CSV data (10,080 rows)
        # Mix of idle (55C), agent tasks (68C), and LLM spikes (82C)
        start_time = datetime.datetime(2026, 10, 1, 0, 0, 0, tzinfo=datetime.timezone.utc)
        
        csv_lines = [
            "timestamp,soc_temp_c,pmic_temp_c,load_1m,arm_freq_mhz,throttled_hex,top_proc,top_cpu_pct"
        ]

        for i in range(10080):
            current_time = start_time + datetime.timedelta(minutes=i)
            ts_str = current_time.strftime("%Y-%m-%dT%H:%M:%SZ")

            # Simulate daily cycle: night idle, day agent work, occasional LLM
            hour = current_time.hour
            if 1 <= hour <= 6:
                temp = 52.0 + (i % 3) * 0.5
                load = 0.12
                freq = 1500
                throttled = "0x0"
                proc = "systemd"
                cpu = 1.0
            elif 14 <= hour <= 16 and (i % 60 < 15):
                # LLM experiment burst
                temp = 81.5 + (i % 5) * 0.4
                load = 3.50
                freq = 2400
                throttled = "0x80000" # soft temp limit hit
                proc = "ollama"
                cpu = 95.0
            else:
                # normal agent work
                temp = 63.0 + (i % 10) * 0.6
                load = 0.85
                freq = 2400
                throttled = "0x0"
                proc = "python3"
                cpu = 24.5

            csv_lines.append(f"{ts_str},{temp:.1f},58.5,{load:.2f},{freq},{throttled},{proc},{cpu:.1f}")

        csv_content = "\n".join(csv_lines)

        # Parse records using analyze module
        records = analyze.parse_csv_string(csv_content)
        self.assertEqual(len(records), 10080)

        soc_temps = [r['soc_temp'] for r in records]
        stats = analyze.calculate_stats(soc_temps)
        buckets = analyze.bucket_temperatures(soc_temps)
        throttle_summary = summarize = analyze.summarize_throttling(records)
        verdict = analyze.determine_verdict(stats, throttle_summary, buckets)

        # Assertions on simulation outcomes
        self.assertEqual(stats['count'], 10080)
        self.assertGreater(stats['avg'], 50.0)
        self.assertLess(stats['avg'], 75.0)
        self.assertGreater(stats['std_dev'], 0.0)
        self.assertGreater(throttle_summary['past_soft_temp_count'], 0)
        
        # Because of LLM soft temperature limit and p95 approaching 80C, verdict should recommend passive cooler
        self.assertEqual(verdict['decision'], analyze.DECISION_PASSIVE_COOLER)


if __name__ == '__main__':
    unittest.main()
