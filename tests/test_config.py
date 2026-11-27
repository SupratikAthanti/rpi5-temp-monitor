"""Configuration tests for PiStats.

Covers defaults, JSON load/save at a temp path override (never real home),
validation/clamping, the live-apply key set, and typed `/config set` value parsing.
"""

import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import pistats_config as cfgmod  # noqa: E402


class TestConfigDefaults(unittest.TestCase):
    def test_defaults_have_expected_values(self):
        d = cfgmod.DEFAULT_CONFIG
        self.assertEqual(d['render_interval_s'], 1.0)
        self.assertEqual(d['sample_interval_s'], 1.0)
        self.assertEqual(d['log_cadence_s'], 60)
        self.assertEqual(d['mode'], 'both')
        self.assertEqual(d['rate_per_kwh'], 0.13)
        self.assertEqual(d['battery_wh'], 45.0)
        self.assertEqual(d['wall_factor'], 1.0)
        self.assertEqual(d['energy_windows_min'], [60, 120])

    def test_live_keys_cover_all_design_keys(self):
        required = {
            'render_interval_s', 'sample_interval_s', 'log_cadence_s', 'mode',
            'rate_per_kwh', 'battery_wh', 'wall_factor', 'energy_windows_min',
        }
        self.assertTrue(required.issubset(set(cfgmod.LIVE_KEYS)),
                        f'LIVE_KEYS missing {required - set(cfgmod.LIVE_KEYS)}')


class TestConfigPath(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.tmp.name, 'pistats', 'config.json')
        self._old = os.environ.get(cfgmod.CONFIG_PATH_ENV)
        os.environ[cfgmod.CONFIG_PATH_ENV] = self.path

    def tearDown(self):
        if self._old is None:
            os.environ.pop(cfgmod.CONFIG_PATH_ENV, None)
        else:
            os.environ[cfgmod.CONFIG_PATH_ENV] = self._old
        self.tmp.cleanup()

    def test_env_override_is_honoured(self):
        self.assertEqual(cfgmod.config_path(), self.path)

    def test_load_missing_file_returns_defaults(self):
        self.assertFalse(os.path.exists(self.path))
        self.assertEqual(cfgmod.load_config(), dict(cfgmod.DEFAULT_CONFIG))

    def test_save_then_load_round_trip(self):
        cfg = dict(cfgmod.DEFAULT_CONFIG)
        cfg['render_interval_s'] = 2.0
        cfg['mode'] = 'power'
        cfgmod.save_config(cfg)
        self.assertTrue(os.path.exists(self.path))
        loaded = cfgmod.load_config()
        self.assertEqual(loaded['render_interval_s'], 2.0)
        self.assertEqual(loaded['mode'], 'power')
        # unchanged defaults survive
        self.assertEqual(loaded['rate_per_kwh'], 0.13)

    def test_save_uses_path_override_argument(self):
        other = os.path.join(self.tmp.name, 'other', 'cfg.json')
        cfgmod.save_config({'mode': 'temp'}, path=other)
        self.assertEqual(cfgmod.load_config(path=other)['mode'], 'temp')

    def test_corrupt_file_falls_back_to_defaults(self):
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        with open(self.path, 'w') as f:
            f.write('{ not valid json ')
        self.assertEqual(cfgmod.load_config(), dict(cfgmod.DEFAULT_CONFIG))

    def test_partial_file_is_merged_with_defaults(self):
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        with open(self.path, 'w') as f:
            json.dump({'battery_wh': 60.0}, f)
        loaded = cfgmod.load_config()
        self.assertEqual(loaded['battery_wh'], 60.0)
        self.assertEqual(loaded['render_interval_s'], 1.0)

    def test_no_real_home_write(self):
        # Regression guard (audit item 10): the old version of this test merely
        # re-checked config_path(), so a save_config() that ignored the env
        # override and wrote to ~/.config/pistats/config.json went unnoticed.
        # Point HOME at a temp dir FIRST so a regression can never touch the
        # real home, then assert that nothing was written there.
        old_home = os.environ.get('HOME')
        with tempfile.TemporaryDirectory() as fake_home:
            os.environ['HOME'] = fake_home
            try:
                legacy = os.path.join(fake_home, cfgmod.CONFIG_REL_PATH)
                self.assertFalse(os.path.exists(legacy))
                cfgmod.save_config(dict(cfgmod.DEFAULT_CONFIG))
                self.assertEqual(cfgmod.config_path(), self.path)
                self.assertTrue(os.path.exists(self.path),
                                'config was not written to PISTATS_CONFIG_PATH')
                self.assertFalse(
                    os.path.exists(legacy),
                    'save_config wrote to HOME instead of PISTATS_CONFIG_PATH')
                self.assertFalse(os.path.exists(
                    os.path.join(fake_home, '.config')),
                    'save_config created a ~/.config tree despite the override')
            finally:
                if old_home is None:
                    os.environ.pop('HOME', None)
                else:
                    os.environ['HOME'] = old_home


class TestConfigValidation(unittest.TestCase):
    def test_mode_clamped_to_valid(self):
        self.assertEqual(cfgmod.validate_config({'mode': 'nonsense'})['mode'], 'both')
        self.assertEqual(cfgmod.validate_config({'mode': 'TEMP'})['mode'], 'temp')
        for mode in ('temp', 'power', 'both'):
            self.assertEqual(cfgmod.validate_config({'mode': mode})['mode'], mode)

    def test_intervals_clamped(self):
        cfg = cfgmod.validate_config({
            'render_interval_s': 0.0,
            'sample_interval_s': 1000.0,
            'log_cadence_s': -5,
        })
        self.assertGreaterEqual(cfg['render_interval_s'], 0.1)
        self.assertLessEqual(cfg['sample_interval_s'], 60)
        self.assertGreaterEqual(cfg['log_cadence_s'], 1)

    def test_numeric_floors(self):
        cfg = cfgmod.validate_config({
            'rate_per_kwh': -2.0, 'battery_wh': -10.0, 'wall_factor': 0.0,
        })
        self.assertGreaterEqual(cfg['rate_per_kwh'], 0)
        self.assertGreaterEqual(cfg['battery_wh'], 0)
        self.assertGreater(cfg['wall_factor'], 0)

    def test_bad_energy_windows_replaced(self):
        cfg = cfgmod.validate_config({'energy_windows_min': []})
        self.assertEqual(cfg['energy_windows_min'], [60, 120])
        cfg = cfgmod.validate_config({'energy_windows_min': [60, -1]})
        self.assertTrue(all(x > 0 for x in cfg['energy_windows_min']))

    def test_validate_returns_all_keys(self):
        cfg = cfgmod.validate_config({})
        for key in cfgmod.DEFAULT_CONFIG:
            self.assertIn(key, cfg)


class TestConfigSetValue(unittest.TestCase):
    def test_float_key_parsed(self):
        cfg = cfgmod.set_config_value(dict(cfgmod.DEFAULT_CONFIG),
                                      'render_interval_s', '2.5')
        self.assertEqual(cfg['render_interval_s'], 2.5)

    def test_int_key_parsed(self):
        cfg = cfgmod.set_config_value(dict(cfgmod.DEFAULT_CONFIG),
                                      'log_cadence_s', '30')
        self.assertEqual(cfg['log_cadence_s'], 30)

    def test_mode_key_normalized(self):
        cfg = cfgmod.set_config_value(dict(cfgmod.DEFAULT_CONFIG), 'mode', 'POWER')
        self.assertEqual(cfg['mode'], 'power')

    def test_energy_windows_csv_parsed(self):
        cfg = cfgmod.set_config_value(dict(cfgmod.DEFAULT_CONFIG),
                                      'energy_windows_min', '30,90')
        self.assertEqual(cfg['energy_windows_min'], [30, 90])

    def test_invalid_value_clamped(self):
        cfg = cfgmod.set_config_value(dict(cfgmod.DEFAULT_CONFIG),
                                      'render_interval_s', '99999')
        self.assertLessEqual(cfg['render_interval_s'], 60)

    def test_set_returns_copy_not_mutation(self):
        original = dict(cfgmod.DEFAULT_CONFIG)
        updated = cfgmod.set_config_value(original, 'battery_wh', '80')
        self.assertEqual(original['battery_wh'], 45.0)
        self.assertEqual(updated['battery_wh'], 80.0)

    def test_unknown_key_rejected(self):
        with self.assertRaises((ValueError, KeyError)):
            cfgmod.set_config_value(dict(cfgmod.DEFAULT_CONFIG), 'bogus_key', '1')


if __name__ == '__main__':
    unittest.main()
