"""Live-vs-log power fallback: no cron job is needed for live CLI readings.

All tests are headless and hardware-free: the ADC is simulated with
``FAKE_PMIC_ADC`` and every log path is redirected into a temp directory.
"""

import argparse
import contextlib
import csv
import io
import os
import sys
import tempfile
import time
import unittest
from unittest import mock

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import analyze  # noqa: E402
import pistats_cli  # noqa: E402
import pistats_config as cfgmod  # noqa: E402
import pistats_dashboard as dash  # noqa: E402
import pistats_tui as tui  # noqa: E402
import power_read  # noqa: E402
import power_source  # noqa: E402


REAL_ADC = """\
   3V7_WL_SW_A current(0)=0.10247270A
   3V3_SYS_A current(1)=0.05757987A
   1V8_SYS_A current(2)=0.19421010A
  VDD_CORE_A current(7)=2.66114000A
 3V7_WL_SW_V volt(8)=3.65894600V
   3V3_SYS_V volt(9)=3.32302500V
   1V8_SYS_V volt(10)=1.80610300V
  VDD_CORE_V volt(15)=0.82708100V
     EXT5V_V volt(24)=5.14292000V
      BATT_V volt(25)=0.00000000V
"""

TEMP_CSV = """timestamp,soc_temp_c,pmic_temp_c,load_1m,arm_freq_mhz,throttled_hex,top_proc,top_cpu_pct
2026-11-21T10:00:00Z,60.0,50.0,0.50,2400,0x0,opencode,50.0
2026-11-21T10:01:00Z,70.0,55.0,1.00,1500,0x0,opencode,60.0
2026-11-21T10:02:00Z,80.0,60.0,2.00,1500,0x4,opencode,70.0
2026-11-21T10:03:00Z,90.0,65.0,3.00,2400,0x4,ps,80.0
"""

GOOD_POWER_CSV = """timestamp,pmic_w,wall_w
2026-11-21T10:00:00Z,2.0,2.4
2026-11-21T10:01:00Z,4.0,4.8
2026-11-21T10:02:00Z,6.0,7.2
2026-11-21T10:03:00Z,8.0,9.6
"""

_ENV_KEYS = ('PISTATS_CONFIG_PATH', 'PISTATS_SESSION_LOG',
             'PISTATS_POWER_LOG', 'FAKE_PMIC_ADC', 'WALL_FACTOR',
             'PISTATS_CRON_STATUS')


class FallbackBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.old_env = {key: os.environ.get(key) for key in _ENV_KEYS}
        os.environ['PISTATS_CONFIG_PATH'] = os.path.join(
            self.tmp.name, 'config.json')
        os.environ['PISTATS_SESSION_LOG'] = os.path.join(
            self.tmp.name, 'pistats_session.csv')
        os.environ['PISTATS_POWER_LOG'] = os.path.join(
            self.tmp.name, 'power_log.csv')
        os.environ['FAKE_PMIC_ADC'] = REAL_ADC
        os.environ['WALL_FACTOR'] = '1.0'
        self.temp_log = os.path.join(self.tmp.name, 'temp_log.csv')
        self.power_log = os.environ['PISTATS_POWER_LOG']
        self.session_path = os.environ['PISTATS_SESSION_LOG']
        with open(self.temp_log, 'w') as handle:
            handle.write(TEMP_CSV)

    def tearDown(self):
        for key, value in self.old_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        self.tmp.cleanup()

    def write_power_log(self, text):
        with open(self.power_log, 'w') as handle:
            handle.write(text)

    def write_stale_zero_log(self, epoch=None):
        epoch = time.time() if epoch is None else epoch
        stamp = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(epoch - 600))
        self.write_power_log(
            'timestamp,pmic_w,wall_w\n'
            f'{stamp},0.000000,0.000000\n'
            f'{stamp},0.000000,0.000000\n')

    def make_recorder(self, base=None, watts=6.0, count=5):
        base = time.time() if base is None else base
        recorder = dash.SessionRecorder(self.session_path)
        for index in range(count):
            recorder.add(base - (count - 1 - index) * 60.0,
                         50.0 + index, watts)
        return recorder, base


class TestPowerLogHealth(FallbackBase):
    def test_fresh_nonzero_log_is_healthy(self):
        now = time.time()
        records = [{'timestamp': '', 'epoch': now - 30.0,
                    'pmic_w': 4.0, 'wall_w': 5.0}]
        healthy, reason = power_source.log_status(records, now=now)
        self.assertTrue(healthy, reason)
        self.assertEqual(reason, 'ok')

    def test_all_zero_log_is_unhealthy(self):
        now = time.time()
        records = [{'epoch': now - 30.0, 'wall_w': 0.0},
                   {'epoch': now - 10.0, 'wall_w': 0.0}]
        healthy, reason = power_source.log_status(records, now=now)
        self.assertFalse(healthy)
        self.assertEqual(reason, 'all-zero')

    def test_stale_log_is_unhealthy_but_explicit_history_is_kept(self):
        now = time.time()
        records = [{'epoch': now - 900.0, 'wall_w': 5.0}]
        healthy, reason = power_source.log_status(records, now=now)
        self.assertFalse(healthy)
        self.assertEqual(reason, 'stale')
        healthy, reason = power_source.log_status(
            records, now=now, check_freshness=False)
        self.assertTrue(healthy)
        self.assertEqual(reason, 'ok')


class TestResolveFallback(FallbackBase):
    def test_stale_log_falls_back_to_live_session_samples(self):
        self.write_stale_zero_log()
        recorder, base = self.make_recorder()
        result = power_source.resolve(
            power_log=self.power_log, recorder=recorder,
            live={'rails': {'VDD_CORE': 2.0}, 'pmic_w': 5.0,
                  'wall_w': 5.0, 'wall_factor': 1.0},
            now=base, read_live=False)
        self.assertEqual(result['source'], 'live')
        self.assertEqual(result['label'], 'live (no cron)')
        self.assertEqual(len(result['records']), 5)
        self.assertGreater(analyze.energy_wh(result['records']), 0.0)
        self.assertIn('no cron', result['hint'].lower())

    def test_empty_log_falls_back_to_live_adc(self):
        recorder, base = self.make_recorder(count=0)
        result = power_source.resolve(
            power_log=None, session_samples=[], env=dict(os.environ),
            now=base)
        self.assertEqual(result['source'], 'live')
        self.assertEqual(len(result['records']), 1)
        self.assertGreater(result['records'][0]['wall_w'], 0.0)

    def test_no_adc_and_no_samples_is_honestly_unavailable(self):
        recorder, base = self.make_recorder(count=0)
        env = dict(os.environ)
        env['FAKE_PMIC_ADC'] = ''
        result = power_source.resolve(
            power_log=None, recorder=recorder, env=env, now=base)
        self.assertEqual(result['source'], 'unavailable')
        self.assertEqual(result['label'], 'unavailable')
        self.assertEqual(result['records'], [])
        self.assertIn('No power source', result['hint'])

    def test_healthy_log_is_kept_unchanged(self):
        self.write_power_log(GOOD_POWER_CSV)
        result = power_source.resolve(
            power_log=self.power_log, session_samples=[], explicit=True,
            now=time.time(), read_live=False)
        self.assertEqual(result['source'], 'log')
        self.assertEqual(len(result['records']), 4)
        self.assertAlmostEqual(
            analyze.energy_wh(result['records']), 0.24, places=9)


class TestBuildContextFallback(FallbackBase):
    def test_battery_and_overview_rows_show_live_source_and_energy(self):
        self.write_stale_zero_log()
        recorder, base = self.make_recorder()
        snapshot = {'rails': {'VDD_CORE': 2.0}, 'pmic_w': 2.0,
                    'wall_w': 2.0, 'wall_factor': 1.0}
        with mock.patch.object(tui, '_first_log', return_value=self.temp_log):
            ctx = tui.build_context(
                {'wall_factor': 1.0, 'rate_per_kwh': 0.13},
                recorder=recorder, live=snapshot)
        self.assertEqual(ctx['power_source_label'], 'live (no cron)')
        self.assertEqual(ctx['wall_w'], '2.00')
        self.assertGreater(float(ctx['energy_1h']), 0.0)
        self.assertGreater(float(ctx['energy_full']), 0.0)
        self.assertNotEqual(ctx['cost'], '$0.00000')

        overview = {row['id']: row for row in tui.build_tab_rows('Overview', ctx)}
        self.assertIn('Power: live (no cron)', overview['overview.status']['text'])
        self.assertTrue(overview['overview.power_graph']['samples'])
        battery = {row['id']: row for row in tui.build_tab_rows('Battery', ctx)}
        self.assertIn('Power data: live (no cron)', battery['bat.source']['text'])

    def test_unavailable_adc_does_not_show_zero_watts(self):
        self.write_stale_zero_log()
        recorder, _base = self.make_recorder(count=0)
        with mock.patch.object(tui, '_first_log', return_value=self.temp_log):
            ctx = tui.build_context(
                {}, recorder=recorder, live={'rails': {}, 'pmic_w': 0.0,
                                             'wall_w': 0.0, 'wall_factor': 1.0})
        self.assertEqual(ctx['pmic_w'], '—')
        self.assertEqual(ctx['wall_w'], '—')
        self.assertEqual(ctx['power_source_label'], 'unavailable')
        self.assertTrue(ctx['power_source_hint'])


class TestViewFallback(FallbackBase):
    def _view(self, power_log):
        args = argparse.Namespace(log=self.temp_log, power_log=power_log,
                                  range_spec=None)
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            code = pistats_cli.run_view(args)
        return code, output.getvalue()

    def test_view_uses_live_power_when_explicit_log_is_all_zero(self):
        self.write_stale_zero_log()
        code, report = self._view(self.power_log)
        self.assertEqual(code, 0)
        self.assertIn('Source                 : live (no cron)', report)
        self.assertRegex(
            report,
            r'Live PMIC sum\s+:\s+(?P<watts>\d+\.\d+) W')

    def test_view_keeps_explicit_historical_log(self):
        self.write_power_log(GOOD_POWER_CSV)
        code, report = self._view(self.power_log)
        self.assertEqual(code, 0)
        self.assertIn('Source                 : log', report)
        self.assertIn('0.24 Wh', report)

    def test_view_reports_unavailable_without_inventing_numbers(self):
        self.write_power_log('timestamp,pmic_w,wall_w\n')
        env = dict(os.environ)
        env['FAKE_PMIC_ADC'] = ''
        with mock.patch.dict(os.environ, env, clear=False):
            code, report = self._view(self.power_log)
        self.assertEqual(code, 0)
        self.assertIn('Source                 : unavailable', report)
        self.assertNotIn('Live PMIC sum', report)


class _FakeScreen:
    """Minimal stdscr: enough for ``_render`` and never blocks on input."""

    def __init__(self, keys=()):
        self.keys = list(keys)

    def getmaxyx(self):
        return 40, 120

    def timeout(self, _ms):
        pass

    def erase(self):
        pass

    def refresh(self):
        pass

    def addnstr(self, *_args, **_kwargs):
        pass

    def getch(self):
        return self.keys.pop(0) if self.keys else -1


@unittest.skipIf(tui._curses is None, 'curses is not available')
class TestLiveLoopWithoutCron(FallbackBase):
    def test_live_loop_shows_positive_power_and_accumulating_energy(self):
        self.write_stale_zero_log()
        frames = []
        monotonic = [0.0]
        wall = [1000.0]

        def fake_monotonic():
            monotonic[0] += 1.0
            return monotonic[0]

        def fake_time():
            wall[0] += 1.0
            return wall[0]

        def render(_screen, _state, rows, _cfg, filtered=None, status=''):
            frames.append(rows)

        cfg = dict(cfgmod.DEFAULT_CONFIG)
        cfg['log_cadence_s'] = 99999  # no collector subprocess during the test
        with mock.patch.object(tui, '_render', render), \
                mock.patch.object(tui, '_init_colors'), \
                mock.patch.object(tui, '_persist_sample'), \
                mock.patch.object(tui, 'read_soc_temp', return_value=50.0), \
                mock.patch.object(tui, '_first_log',
                                  return_value=self.temp_log), \
                mock.patch.object(tui.time, 'monotonic', fake_monotonic), \
                mock.patch.object(tui.time, 'time', fake_time):
            tui._tui_loop(_FakeScreen([0, 0, ord('q')]), cfg)

        self.assertEqual(len(frames), 3)
        last = {row['id']: row for row in frames[-1]}
        samples = last['overview.power_graph']['samples']
        self.assertEqual(len(samples), 3)
        self.assertTrue(all(
            (tui._sample_parts(sample)[1] or 0.0) > 0.0 for sample in samples))
        self.assertIn('Power: live (no cron)', last['overview.status']['text'])
        import re
        match = re.search(r'(\d+\.\d{3}) Wh',
                          last['overview.energy']['text'])
        self.assertIsNotNone(match)
        self.assertGreater(float(match.group(1)), 0.0)

        with open(self.session_path, newline='') as handle:
            rows = list(csv.DictReader(handle))
        self.assertEqual(len(rows), 3)
        self.assertTrue(all(float(row['wall_w']) > 0.0 for row in rows))


if __name__ == '__main__':
    unittest.main(verbosity=2)
