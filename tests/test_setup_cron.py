"""setup-cron.sh (design.md §13) installs/removes a cron entry SAFELY.

The script must be the counterpart to stop.sh and must manage only PiScope's
collector line.  To guarantee we never touch the real crontab, every test puts a
fake ``crontab`` executable first on PATH and also points the common env
overrides (CRON_BIN / CRONTAB / CRON_CMD) at it.  The fake keeps its "crontab"
in a temp file and logs every invocation so we can assert exact effects.
"""

import os
import stat
import subprocess
import tempfile
import unittest

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
SCRIPT = os.path.join(REPO, 'setup-cron.sh')

FAKE_CRONTAB = r'''#!/usr/bin/env bash
set -u
printf '%s\n' "$*" >> "$CRON_FAKE_LOG"
case "${1:-}" in
  -l)
    if [[ -f "$CRON_FAKE_STATE" ]]; then cat "$CRON_FAKE_STATE"; fi
    exit 0
    ;;
  -)
    cat > "$CRON_FAKE_STATE"
    exit 0
    ;;
esac
exit 1
'''


class SetupCronTestBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = self.tmp.name
        self.bin = os.path.join(self.dir, 'bin')
        os.makedirs(self.bin, exist_ok=True)
        self.fake = os.path.join(self.bin, 'crontab')
        with open(self.fake, 'w') as f:
            f.write(FAKE_CRONTAB)
        os.chmod(self.fake, os.stat(self.fake).st_mode | stat.S_IXUSR)
        self.state = os.path.join(self.dir, 'crontab.state')
        self.log = os.path.join(self.dir, 'crontab.log')
        self.env = dict(os.environ)
        self.env.update({
            'PATH': self.bin + os.pathsep + self.env.get('PATH', ''),
            'CRON_BIN': self.fake,
            'CRONTAB': self.fake,
            'CRON_CMD': self.fake,
            'CRON_FAKE_STATE': self.state,
            'CRON_FAKE_LOG': self.log,
        })

    def tearDown(self):
        self.tmp.cleanup()

    def seed(self, text):
        with open(self.state, 'w') as f:
            f.write(text)

    def crontab_text(self):
        if not os.path.exists(self.state):
            return ''
        with open(self.state) as f:
            return f.read()

    def run_script(self, *args):
        return subprocess.run(
            [SCRIPT, *args], cwd=self.dir, env=self.env,
            input='y\n', stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, timeout=15)

    def invocations(self):
        if not os.path.exists(self.log):
            return ''
        with open(self.log) as f:
            return f.read()

    @staticmethod
    def collector_lines(text):
        return [l for l in text.splitlines() if 'collect.sh' in l]


class TestSetupCronScript(SetupCronTestBase):
    def test_script_exists_and_is_executable(self):
        self.assertTrue(os.path.exists(SCRIPT),
                        'setup-cron.sh is missing at the repo root')
        self.assertTrue(os.access(SCRIPT, os.X_OK),
                        'setup-cron.sh must be executable')

    def test_start_installs_a_collector_entry(self):
        res = self.run_script('start')
        self.assertEqual(res.returncode, 0, res.stderr)
        lines = self.collector_lines(self.crontab_text())
        self.assertTrue(lines, 'start must install a collect.sh cron entry')
        self.assertTrue(any('--mode' in l or 'both' in l for l in lines),
                        'cron entry must use --mode both: %r' % lines)
        self.assertIn('-l', self.invocations())
        self.assertIn('-', self.invocations())

    def test_start_is_idempotent(self):
        self.run_script('start')
        self.run_script('start')
        lines = self.collector_lines(self.crontab_text())
        self.assertEqual(len(lines), 1,
                         'running start twice must not duplicate the entry')

    def test_start_preserves_unrelated_crontab_lines(self):
        unrelated = '0 3 * * * /usr/bin/backup --nightly'
        self.seed(unrelated + '\n')
        res = self.run_script('start')
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertIn(unrelated, self.crontab_text(),
                      'existing unrelated cron entries must survive')

    def test_stop_removes_only_the_collector_entry(self):
        unrelated = '0 3 * * * /usr/bin/backup --nightly'
        self.seed(unrelated + '\n* * * * * /opt/pi/collect.sh --mode both /opt/pi/temp_log.csv\n')
        res = self.run_script('stop')
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertFalse(self.collector_lines(self.crontab_text()),
                         'stop must remove the collector entry')
        self.assertIn(unrelated, self.crontab_text(),
                      'stop must preserve unrelated cron entries')

    def test_status_does_not_modify_crontab(self):
        original = '0 3 * * * /usr/bin/backup\n'
        self.seed(original)
        res = self.run_script('status')
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertEqual(self.crontab_text(), original,
                         'status must not change the crontab')
        self.assertNotIn('\n-\n', self.invocations(),
                         'status must not write a new crontab')

    def test_all_crontab_calls_go_through_the_injected_command(self):
        res = self.run_script('start')
        self.assertEqual(res.returncode, 0, res.stderr)
        calls = self.invocations()
        self.assertTrue(calls.strip(),
                        'the fake crontab must have been used; the script '
                        'must not bypass the injected crontab command')
        self.assertNotIn('/usr/bin/crontab', calls)


if __name__ == '__main__':
    unittest.main()
