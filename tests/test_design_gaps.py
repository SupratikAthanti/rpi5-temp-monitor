"""Additional design.md coverage found during the gap audit.

Most of design.md is already covered by the existing suite.  These two required
behaviours were not: (a) the configured wall factor actually reaching
``power_read.read_power`` via the config path override, and (b) ``piscope view
--range`` routing the range spec into ``analyze.build_report``.  They are quick,
fully isolated tests that lock in those contracts.
"""

import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import piscope_config as cfgmod  # noqa: E402
import power_read  # noqa: E402

ADC = (
    '3V3_SYS A 0.5\n3V3_SYS V 3.3\n'
    'VDD_CORE A 1.0\nVDD_CORE V 0.85\n'
)
PMIC_W = 0.5 * 3.3 + 1.0 * 0.85  # 2.5 W

LOG = ('timestamp,soc_temp_c,pmic_temp_c,load_1m,arm_freq_mhz,'
       'throttled_hex,top_proc,top_cpu_pct\n'
       '2026-11-21T10:00:00Z,60.0,50.0,0.50,2400,0x0,opencode,50.0\n')


class TestConfiguredWallFactor(unittest.TestCase):
    def test_read_power_uses_config_wall_factor(self):
        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, 'cfg.json')
            cfg = dict(cfgmod.DEFAULT_CONFIG)
            cfg['wall_factor'] = 1.5
            cfgmod.save_config(cfg, path=path)
            old = os.environ.get(cfgmod.CONFIG_PATH_ENV)
            os.environ[cfgmod.CONFIG_PATH_ENV] = path
            try:
                env = {'FAKE_PMIC_ADC': ADC, cfgmod.CONFIG_PATH_ENV: path}
                snap = power_read.read_power(env=env)
            finally:
                if old is None:
                    os.environ.pop(cfgmod.CONFIG_PATH_ENV, None)
                else:
                    os.environ[cfgmod.CONFIG_PATH_ENV] = old
        self.assertAlmostEqual(snap['pmic_w'], PMIC_W, places=6)
        self.assertEqual(snap['wall_factor'], 1.5)
        self.assertAlmostEqual(snap['wall_w'], PMIC_W * 1.5, places=6)


class TestViewRangeRouting(unittest.TestCase):
    def test_view_forwards_range_spec_to_build_report(self):
        import piscope_cli as cli
        with tempfile.TemporaryDirectory() as td:
            log = os.path.join(td, 'temp_log.csv')
            with open(log, 'w') as f:
                f.write(LOG)
            with mock.patch.object(cli.analyze, 'build_report',
                                   return_value='report text') as build:
                rc = cli.main(['view', log, '--range', '7d'])
        self.assertEqual(rc, 0)
        build.assert_called_once()
        kwargs = build.call_args.kwargs
        self.assertEqual(str(kwargs.get('range_spec')), '7d')


if __name__ == '__main__':
    unittest.main()
