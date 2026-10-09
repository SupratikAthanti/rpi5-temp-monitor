import unittest
import subprocess
import os
import tempfile
import csv
import sys
import time

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
import analyze


def run_collector(log_file, state_file, env_extra=None):
    script = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'collect.sh'))
    env = dict(os.environ)
    env['SPIKE_STATE_FILE'] = state_file
    # Default spike log next to main log unless overridden
    if env_extra:
        env.update(env_extra)
    res = subprocess.run([script, log_file], stdout=subprocess.PIPE,
                         stderr=subprocess.PIPE, text=True, env=env)
    return res


def spike_log_for(log_file, env_extra=None):
    if env_extra and 'SPIKE_LOG' in env_extra:
        return env_extra['SPIKE_LOG']
    return os.path.join(os.path.dirname(log_file), 'spikes.csv')


class TestSpikeTrigger(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.log = os.path.join(self.tmp.name, 'temp_log.csv')
        self.state = os.path.join(self.tmp.name, 'state')
        self.spikes = os.path.join(self.tmp.name, 'spikes.csv')
        self.base_env = {
            'SPIKE_LOG': self.spikes,
            'SPIKE_DELTA': '5.0',
            'SPIKE_MIN_TEMP': '60.0',
            'SPIKE_COOLDOWN_SEC': '600',
        }

    def tearDown(self):
        self.tmp.cleanup()

    def test_no_spike_on_small_drift(self):
        # Seed baseline at 60.0, last spike long ago
        with open(self.state, 'w') as f:
            f.write('60.0 0\n')
        env = dict(self.base_env)
        env['FAKE_SOC_TEMP'] = '62.0'
        res = run_collector(self.log, self.state, env)
        self.assertEqual(res.returncode, 0, res.stderr)
        # Small 2C drift must NOT create a spike log entry
        if os.path.exists(self.spikes):
            with open(self.spikes) as fh:
                rows = list(csv.reader(fh))
            # header only or no file content beyond header
            self.assertLessEqual(len(rows), 1)

    def test_spike_triggers_on_5deg_jump(self):
        with open(self.state, 'w') as f:
            f.write('60.0 0\n')
        env = dict(self.base_env)
        env['FAKE_SOC_TEMP'] = '65.5'
        env['FAKE_TOP_DETAIL'] = 'ollama|95.0|12.0|ollama serve'
        res = run_collector(self.log, self.state, env)
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertTrue(os.path.exists(self.spikes), 'spikes.csv should be created on 5.5C jump')
        with open(self.spikes) as fh:
            rows = list(csv.reader(fh))
        self.assertGreaterEqual(len(rows), 2)  # header + 1 spike
        self.assertEqual(rows[0][0], 'timestamp')
        # delta column should reflect ~5.5C jump
        delta = float(rows[1][3])
        self.assertGreaterEqual(delta, 5.0)

    def test_spike_cooldown_prevents_spam(self):
        with open(self.state, 'w') as f:
            f.write('60.0 0\n')
        env = dict(self.base_env)
        env['FAKE_SOC_TEMP'] = '66.0'
        run_collector(self.log, self.state, env)
        # Immediate second run while still hot must NOT append again
        run_collector(self.log, self.state, env)
        with open(self.spikes) as fh:
            rows = list(csv.reader(fh))
        self.assertEqual(len(rows), 2, f'cooldown should limit to 1 spike row, got {len(rows)-1}')

    def test_baseline_updates_on_normal_temp(self):
        with open(self.state, 'w') as f:
            f.write('60.0 0\n')
        env = dict(self.base_env)
        env['FAKE_SOC_TEMP'] = '61.0'
        run_collector(self.log, self.state, env)
        with open(self.state) as fh:
            baseline = float(fh.read().strip().split()[0])
        # EMA should drift slightly toward 61 but stay near 60
        self.assertGreater(baseline, 60.0)
        self.assertLess(baseline, 61.0)


class TestSpikeAttribution(unittest.TestCase):
    def test_detect_culprit_known_apps(self):
        for detail, expected in [
            ('ollama|90.0|10.0|ollama serve', 'ollama'),
            ('python3|40.0|5.0|/usr/bin/python3 hermes-agent --run', 'hermes'),
            ('node|30.0|4.0|opencode serve', 'opencode'),
            ('tailscaled|5.0|1.0|tailscaled --state', 'tailscaled'),
        ]:
            self.assertEqual(analyze.detect_culprit_app(detail), expected)

    def test_group_spikes_by_app(self):
        csv_text = (
            'timestamp,soc_temp_c,baseline_temp_c,delta_c,load_1m,culprit_app,top_processes_detail\n'
            '2026-10-09T10:00:00Z,70.5,60.0,10.5,3.5,ollama,ollama(90%)\n'
            '2026-10-09T12:00:00Z,71.0,60.5,10.5,3.2,ollama,ollama(88%)\n'
            '2026-10-09T14:00:00Z,68.0,61.0,7.0,1.5,opencode,opencode(40%)\n'
        )
        spikes = analyze.parse_spikes_string(csv_text)
        self.assertEqual(len(spikes), 3)
        grouped = analyze.group_spikes_by_app(spikes)
        self.assertEqual(grouped['ollama']['count'], 2)
        self.assertEqual(grouped['opencode']['count'], 1)
        self.assertAlmostEqual(grouped['ollama']['max_temp'], 71.0)

    def test_frequency_note_rare_vs_empty(self):
        self.assertIn('No temperature spikes', analyze.frequency_weighted_note([], 10080))
        rare_spikes = [
            {'culprit_app': 'ollama', 'soc_temp': 82.0, 'delta': 10.0},
            {'culprit_app': 'ollama', 'soc_temp': 83.0, 'delta': 11.0},
        ]
        note = analyze.frequency_weighted_note(rare_spikes, 10080)
        self.assertIn('rare', note.lower())


if __name__ == '__main__':
    unittest.main()
