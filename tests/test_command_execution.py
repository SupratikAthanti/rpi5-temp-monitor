"""TUI-internal commands must EXECUTE, not just print help text.

design.md §4/§10/§13 requires ``/test``, ``/reset``, ``/report``, ``/range`` and
``/cron start|stop|status`` to do real work from inside the dashboard.  The old
implementation only returned status strings such as "run ./reset.sh from the
shell".  These tests drive a headless command executor with injected context so
every side effect (spawned process, crontab edit, deleted log, report text,
config write) is observable without touching the real machine.

See EXPECTED_API.md, section "9. Command execution" for the exact interface.
"""

import os
import sys
import tempfile
import unittest
from types import SimpleNamespace

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import pistats_config as cfgmod  # noqa: E402
import pistats_commands as cmds  # noqa: E402


def get_execute_command():
    """Canonical location is pistats_commands; a TUI re-export is also accepted."""
    fn = getattr(cmds, 'execute_command', None)
    if fn is None:
        import pistats_tui as tui
        fn = getattr(tui, 'execute_command', None)
    return fn


class FakeCrontab:
    """Drop-in for ``crontab_runner(argv, input=None)`` backed by an in-memory file."""

    def __init__(self, initial=''):
        self.content = initial
        self.calls = []  # list of {'argv': [...], 'input': str|None}

    def __call__(self, argv, input=None, **kwargs):
        argv = list(argv)
        self.calls.append({'argv': argv, 'input': input})
        action = argv[1] if len(argv) > 1 else ''
        if action == '-l':
            return SimpleNamespace(returncode=0, stdout=self.content, stderr='')
        if action == '-':
            self.content = input if input is not None else ''
            return SimpleNamespace(returncode=0, stdout='', stderr='')
        return SimpleNamespace(returncode=1, stdout='', stderr='unsupported')

    @property
    def writes(self):
        return [c for c in self.calls if len(c['argv']) > 1 and c['argv'][1] == '-']


class FakeSpawner:
    """Records ``process_spawner(argv, **kwargs)`` calls; never runs anything."""

    def __init__(self):
        self.calls = []

    def __call__(self, argv, **kwargs):
        self.calls.append({'argv': list(argv), 'kwargs': dict(kwargs)})
        return SimpleNamespace(returncode=0, stdout='', stderr='')


class FakeReport:
    """Stands in for analyze.build_report so we can tell which path was taken."""

    def __init__(self, text='MARKER-FULL-REPORT-9f3a'):
        self.text = text
        self.calls = []

    def __call__(self, **kwargs):
        self.calls.append(dict(kwargs))
        return self.text


class ExecutionTestBase(unittest.TestCase):
    def setUp(self):
        self.execute = get_execute_command()
        self.assertIsNotNone(
            self.execute,
            'execute_command(cmd, context) must be importable from '
            'pistats_commands (or re-exported from pistats_tui)')
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = self.tmp.name
        self.config_path = os.path.join(self.dir, 'cfg', 'config.json')
        self._old = os.environ.get(cfgmod.CONFIG_PATH_ENV)
        os.environ[cfgmod.CONFIG_PATH_ENV] = self.config_path
        self.base = {
            'dir': self.dir,
            'config': dict(cfgmod.DEFAULT_CONFIG),
            'config_path': self.config_path,
            'scripts': os.path.abspath(os.path.join(os.path.dirname(__file__), '..')),
            'state': {},
            'confirm': lambda prompt: True,
            'process_spawner': FakeSpawner(),
            'crontab_runner': FakeCrontab(),
            'report_fn': FakeReport(),
        }

    def tearDown(self):
        if self._old is None:
            os.environ.pop(cfgmod.CONFIG_PATH_ENV, None)
        else:
            os.environ[cfgmod.CONFIG_PATH_ENV] = self._old
        self.tmp.cleanup()

    def ctx(self, **overrides):
        out = dict(self.base)
        out.update(overrides)
        return out


class TestTimedTestCommand(ExecutionTestBase):
    def test_test_spawns_run_test_script_with_duration(self):
        spawner = FakeSpawner()
        result = self.execute('/test 60', self.ctx(process_spawner=spawner))
        self.assertTrue(result.get('ok'), result)
        self.assertEqual(len(spawner.calls), 1,
                         '/test must actually start the timed test, not print help')
        argv = spawner.calls[0]['argv']
        self.assertTrue(any(str(a).endswith('run-test.sh') for a in argv), argv)
        self.assertIn('60', argv, argv)
        self.assertIn('test_started', result.get('effects', []), result)

    def test_test_result_reports_the_duration(self):
        result = self.execute('/test 5', self.ctx())
        text = str(result.get('status', '')).lower()
        self.assertIn('5', text, result)


class TestResetCommand(ExecutionTestBase):
    def _make_logs(self):
        paths = {
            'temp_log': os.path.join(self.dir, 'temp_log.csv'),
            'power_log': os.path.join(self.dir, 'power_log.csv'),
            'spikes': os.path.join(self.dir, 'spikes.csv'),
        }
        for p in paths.values():
            with open(p, 'w') as f:
                f.write('timestamp\n')
        return paths

    def test_reset_confirms_then_clears_all_logs(self):
        paths = self._make_logs()
        result = self.execute('/reset', self.ctx())
        self.assertTrue(result.get('ok'), result)
        for name, path in paths.items():
            self.assertFalse(os.path.exists(path),
                             f'/reset must delete {name}')
        self.assertIn('logs_cleared', result.get('effects', []), result)

    def test_reset_cancelled_leaves_files_untouched(self):
        paths = self._make_logs()
        result = self.execute('/reset', self.ctx(confirm=lambda prompt: False))
        for name, path in paths.items():
            self.assertTrue(os.path.exists(path),
                            f'cancelled /reset must not delete {name}')
        self.assertIn('reset_cancelled', result.get('effects', []), result)


class TestReportCommands(ExecutionTestBase):
    def test_report_returns_full_report_text(self):
        report = FakeReport('UNIQUE-REPORT-BODY-12345')
        result = self.execute('/report', self.ctx(report_fn=report))
        self.assertEqual(len(report.calls), 1,
                         '/report must invoke the report builder')
        self.assertIn('UNIQUE-REPORT-BODY-12345', str(result.get('report', '')))
        self.assertIn('report_shown', result.get('effects', []), result)

    def test_range_passes_the_spec_and_is_scrollable(self):
        report = FakeReport('SEVEN-DAY-REPORT-xyz')
        result = self.execute('/range 7d', self.ctx(report_fn=report))
        self.assertEqual(len(report.calls), 1)
        spec = report.calls[0].get('range_spec')
        self.assertEqual(str(spec), '7d',
                         '/range 7d must ask the report builder for 7d')
        self.assertIn('SEVEN-DAY-REPORT-xyz', str(result.get('report', '')))
        self.assertTrue(result.get('scrollable'),
                        '/range must produce a scrollable report')
        self.assertIn('range_report_shown', result.get('effects', []), result)


class TestCronCommands(ExecutionTestBase):
    def test_cron_status_reports_running_when_entry_present(self):
        line = '* * * * * /opt/pi/collect.sh --mode both /opt/pi/temp_log.csv\n'
        fake = FakeCrontab(initial='0 3 * * * /usr/bin/backup\n' + line)
        result = self.execute('/cron status', self.ctx(crontab_runner=fake))
        self.assertTrue(result.get('ok'), result)
        self.assertIn('running', str(result.get('status', '')).lower(), result)
        self.assertEqual(len(fake.writes), 0,
                         'status must not modify the crontab')

    def test_cron_status_reports_stopped_when_no_entry(self):
        fake = FakeCrontab(initial='0 3 * * * /usr/bin/backup\n')
        result = self.execute('/cron status', self.ctx(crontab_runner=fake))
        self.assertTrue(result.get('ok'), result)
        self.assertIn('stopped', str(result.get('status', '')).lower(), result)

    def test_cron_start_installs_a_collector_entry(self):
        fake = FakeCrontab()
        result = self.execute('/cron start', self.ctx(crontab_runner=fake))
        self.assertTrue(result.get('ok'), result)
        collector = [l for l in fake.content.splitlines() if 'collect.sh' in l]
        self.assertTrue(collector, 'cron start must install a collect.sh entry')
        self.assertTrue(any('both' in l for l in collector),
                        'cron entry must use --mode both')
        self.assertTrue(fake.writes, 'cron start must write the new crontab')
        self.assertIn('cron_started', result.get('effects', []), result)

    def test_cron_start_is_idempotent(self):
        fake = FakeCrontab()
        self.execute('/cron start', self.ctx(crontab_runner=fake))
        self.execute('/cron start', self.ctx(crontab_runner=fake))
        collector = [l for l in fake.content.splitlines() if 'collect.sh' in l]
        self.assertEqual(len(collector), 1,
                         'cron start must not create duplicate entries')

    def test_cron_stop_removes_collector_but_keeps_other_entries(self):
        unrelated = '0 3 * * * /usr/bin/backup'
        collector = '* * * * * /opt/pi/collect.sh --mode both /opt/pi/temp_log.csv'
        fake = FakeCrontab(initial=unrelated + '\n' + collector + '\n')
        result = self.execute('/cron stop', self.ctx(crontab_runner=fake))
        self.assertTrue(result.get('ok'), result)
        self.assertNotIn('collect.sh', fake.content)
        self.assertIn(unrelated, fake.content,
                      'cron stop must preserve unrelated crontab lines')
        self.assertTrue(fake.writes, 'cron stop must write the new crontab')
        self.assertIn('cron_stopped', result.get('effects', []), result)


class TestOtherCommandEffects(ExecutionTestBase):
    def test_mode_command_persists_new_mode(self):
        result = self.execute('/mode power', self.ctx())
        self.assertTrue(result.get('ok'), result)
        self.assertIn('mode_changed', result.get('effects', []), result)
        self.assertTrue(os.path.exists(self.config_path))
        loaded = cfgmod.load_config(path=self.config_path)
        self.assertEqual(loaded['mode'], 'power')

    def test_config_set_persists_value(self):
        result = self.execute('/config set battery_wh 60', self.ctx())
        self.assertTrue(result.get('ok'), result)
        loaded = cfgmod.load_config(path=self.config_path)
        self.assertEqual(loaded['battery_wh'], 60.0)

    def test_pause_resume_and_quit_update_state(self):
        state = {}
        self.execute('/pause', self.ctx(state=state))
        self.assertTrue(state.get('paused'))
        self.execute('/resume', self.ctx(state=state))
        self.assertFalse(state.get('paused'))
        self.execute('/quit', self.ctx(state=state))
        self.assertTrue(state.get('quit'))


if __name__ == '__main__':
    unittest.main()
