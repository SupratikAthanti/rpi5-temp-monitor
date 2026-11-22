"""Rich ``Enter`` detail overlays: provenance and spike process snapshots.

design.md §6: "Enter on a highlighted row shows provenance — which sample
produced the value and the raw source line."
design.md §7: "Enter on a spike opens the captured top-process snapshot (CPU %,
memory %, arguments) from spikes.csv."

The current TUI simply stores ``row['text']`` as its detail overlay.  These tests
require a pure ``build_detail(row, tab, ctx)`` helper (piscope_tui) that produces
a structured overlay with the original sample and raw CSV line, plus parsed
per-process snapshot entries for spike rows.  Context is injected, so no real
log files are read.

See EXPECTED_API.md, section "10. Detail overlays".
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import piscope_tui as tui  # noqa: E402


RECORDS = [
    {'timestamp': '2026-11-21T10:00:00Z', 'soc_temp': 60.0, 'pmic_temp': 50.0,
     'load_1m': 0.5, 'arm_freq': 2400, 'throttled': '0x0',
     'top_proc': 'alpha', 'top_cpu': 1.0},
    {'timestamp': '2026-11-21T10:01:00Z', 'soc_temp': 90.0, 'pmic_temp': 55.0,
     'load_1m': 2.0, 'arm_freq': 1500, 'throttled': '0x4',
     'top_proc': 'beta', 'top_cpu': 2.0},
]
RAW_LINES = [
    '2026-11-21T10:00:00Z,60.0,50.0,0.50,2400,0x0,alpha,1.0',
    '2026-11-21T10:01:00Z,90.0,55.0,2.00,1500,0x4,beta,2.0',
]
SPIKES = [
    {'timestamp': '2026-11-21T10:01:00Z', 'soc_temp': 90.0, 'baseline': 70.0,
     'delta': 20.0, 'load': 2.0, 'culprit_app': 'opencode',
     'detail': 'opencode(80.0%|mem:12.3%|/usr/bin/opencode serve);'
               'ps(1.0%|mem:0.1%|ps -eo aux)'},
]
CTX = {'records': RECORDS, 'raw_lines': RAW_LINES, 'spikes': SPIKES}


class DetailOverlayTestBase(unittest.TestCase):
    def setUp(self):
        self.build_detail = getattr(tui, 'build_detail', None)
        self.assertIsNotNone(self.build_detail,
                             'piscope_tui.build_detail(row, tab, ctx) is missing')

    @staticmethod
    def row(rows, row_id):
        for r in rows:
            if r['id'] == row_id:
                return r
        raise AssertionError('row %r not found in %r' % (
            row_id, [r['id'] for r in rows]))


class TestProvenanceOverlay(DetailOverlayTestBase):
    def test_max_row_shows_the_sample_that_produced_the_value(self):
        row = self.row(tui.build_tab_rows('Temperature', CTX), 'temp.max')
        detail = self.build_detail(row, 'Temperature', CTX)
        prov = detail['provenance']
        idx = prov['sample_index']
        self.assertEqual(RECORDS[idx]['soc_temp'], 90.0)
        self.assertEqual(prov['timestamp'], RECORDS[idx]['timestamp'])
        self.assertEqual(prov['raw_line'].strip(), RAW_LINES[idx].strip())

    def test_min_row_shows_the_sample_that_produced_the_value(self):
        row = self.row(tui.build_tab_rows('Temperature', CTX), 'temp.min')
        detail = self.build_detail(row, 'Temperature', CTX)
        prov = detail['provenance']
        idx = prov['sample_index']
        self.assertEqual(RECORDS[idx]['soc_temp'], 60.0)
        self.assertEqual(prov['timestamp'], RECORDS[idx]['timestamp'])
        self.assertEqual(prov['raw_line'].strip(), RAW_LINES[idx].strip())

    def test_aggregate_row_still_has_provenance(self):
        row = self.row(tui.build_tab_rows('Temperature', CTX), 'temp.avg')
        detail = self.build_detail(row, 'Temperature', CTX)
        prov = detail['provenance']
        self.assertLess(prov['sample_index'], len(RECORDS))
        self.assertIn(prov['raw_line'].strip(), [l.strip() for l in RAW_LINES])

    def test_detail_has_a_title(self):
        row = self.row(tui.build_tab_rows('Temperature', CTX), 'temp.max')
        detail = self.build_detail(row, 'Temperature', CTX)
        self.assertTrue(detail.get('title'), 'overlay needs a title')

    def test_no_context_does_not_crash(self):
        row = self.row(tui.build_tab_rows('Temperature', CTX), 'temp.max')
        detail = self.build_detail(row, 'Temperature', None)
        self.assertIsInstance(detail, dict)
        self.assertIn('provenance', detail)

    def test_enter_action_is_detail_for_a_provenance_row(self):
        row = self.row(tui.build_tab_rows('Temperature', CTX), 'temp.max')
        self.assertEqual(tui.resolve_enter_action(row, 'Temperature'), 'detail')


class TestSpikeSnapshotOverlay(DetailOverlayTestBase):
    def setUp(self):
        super().setUp()
        rows = tui.build_tab_rows('Performance', CTX)
        self.spike_row = next(
            (r for r in rows if r['id'].startswith('perf.spike')),
            None)
        self.assertIsNotNone(self.spike_row,
                             'Performance tab must expose a spike data row '
                             'with id starting "perf.spike"')

    def test_spike_detail_includes_top_process_snapshot(self):
        detail = self.build_detail(self.spike_row, 'Performance', CTX)
        snapshot = detail['spike_snapshot']
        self.assertIsInstance(snapshot, list)
        self.assertGreaterEqual(len(snapshot), 2,
                                'the captured top-process snapshot must be '
                                'parsed from spikes.csv')

    def test_snapshot_has_comm_cpu_mem_and_args(self):
        detail = self.build_detail(self.spike_row, 'Performance', CTX)
        proc = detail['spike_snapshot'][0]
        self.assertEqual(proc['comm'], 'opencode')
        cpu = float(str(proc['cpu_pct']).rstrip('%'))
        mem = float(str(proc['mem_pct']).rstrip('%'))
        self.assertAlmostEqual(cpu, 80.0, places=2)
        self.assertAlmostEqual(mem, 12.3, places=2)
        self.assertIn('/usr/bin/opencode serve', proc['args'])

    def test_second_process_is_parsed_too(self):
        detail = self.build_detail(self.spike_row, 'Performance', CTX)
        proc = detail['spike_snapshot'][1]
        self.assertEqual(proc['comm'], 'ps')
        self.assertIn('ps -eo aux', proc['args'])

    def test_spike_detail_has_a_title(self):
        detail = self.build_detail(self.spike_row, 'Performance', CTX)
        self.assertTrue(detail.get('title'))


if __name__ == '__main__':
    unittest.main()
