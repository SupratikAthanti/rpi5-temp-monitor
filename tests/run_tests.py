#!/usr/bin/env python3
"""Parallel stdlib-unittest runner for the PiScope test suite.

Design constraints (from the project brief):
  * never more than 2 concurrent test processes (RPi 5, 4 GB),
  * hard global timeout of 5 minutes -- anything still running is killed,
  * ordering dependencies honoured via a simple manifest of sequential groups,
  * stdlib only (no pytest),
  * temp dirs / env overrides mean parallel workers never clash over data files,
  * clear summary with pass/fail/error/skip counts and TOTAL wall-clock time.

Usage:
    python3 tests/run_tests.py

Manifest (`tests/SEQUENTIAL_GROUPS.json`) is a JSON list of lists of module names.
Every group is scheduled into ONE worker and its modules run in the listed order.
All other modules are scheduled as individual units. Units are handed to the two
workers round-robin; if two modules depend on each other, put them in the same group.
"""

import io
import json
import multiprocessing
import os
import sys
import time
import traceback
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, '..'))
MANIFEST = os.path.join(HERE, 'SEQUENTIAL_GROUPS.json')
MAX_WORKERS = 2
GLOBAL_TIMEOUT_S = 300  # 5 minutes hard cap


# ---------------------------------------------------------------------------
# Planning
# ---------------------------------------------------------------------------
def discover_module_names():
    names = []
    for entry in sorted(os.listdir(HERE)):
        if entry.startswith('test_') and entry.endswith('.py'):
            names.append(entry[:-3])
    return names


def load_manifest(available):
    groups = []
    if os.path.exists(MANIFEST):
        try:
            with open(MANIFEST) as f:
                raw = json.load(f)
            for group in raw:
                valid = [m for m in group if m in available]
                if valid:
                    groups.append(valid)
        except (ValueError, TypeError):
            pass
    return groups


def build_units():
    available = set(discover_module_names())
    units = []  # each unit is a list of module names run sequentially in one worker
    grouped = set()
    for group in load_manifest(available):
        units.append(group)
        grouped.update(group)
    for name in sorted(available - grouped):
        units.append([name])
    # Round-robin assignment => deterministic, at most MAX_WORKERS workers.
    buckets = [[] for _ in range(MAX_WORKERS)]
    for i, unit in enumerate(units):
        buckets[i % MAX_WORKERS].append(unit)
    return buckets


# ---------------------------------------------------------------------------
# Worker (runs in a child process)
# ---------------------------------------------------------------------------
def _worker(units):
    for p in (REPO, HERE):
        if p not in sys.path:
            sys.path.insert(0, p)

    results = []
    loader = unittest.defaultTestLoader
    for unit in units:
        for name in unit:
            stream = io.StringIO()
            try:
                suite = loader.loadTestsFromName(name)
                result = unittest.TextTestRunner(
                    stream=stream, verbosity=1).run(suite)
                results.append({
                    'module': name,
                    'run': result.testsRun,
                    'failures': len(result.failures),
                    'errors': len(result.errors),
                    'skipped': len(result.skipped),
                    'output': stream.getvalue(),
                })
            except Exception:
                results.append({
                    'module': name,
                    'run': 1,
                    'failures': 0,
                    'errors': 1,
                    'skipped': 0,
                    'output': f'IMPORT/LOAD ERROR for {name}:\n{traceback.format_exc()}',
                })
    return results


# ---------------------------------------------------------------------------
# Parent
# ---------------------------------------------------------------------------
def _run_parallel(buckets):
    """Run up to two workers; return (results, timed_out)."""
    ctx = multiprocessing.get_context('fork')
    active = [b for b in buckets if b]
    if not active:
        return [], False
    pool = ctx.Pool(processes=min(MAX_WORKERS, len(active)))
    async_results = [pool.apply_async(_worker, (bucket,)) for bucket in active]
    deadline = time.monotonic() + GLOBAL_TIMEOUT_S
    results = []
    timed_out = False
    try:
        for ar in async_results:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                timed_out = True
                break
            try:
                results.extend(ar.get(timeout=remaining))
            except multiprocessing.TimeoutError:
                timed_out = True
                break
            except Exception as exc:  # worker crashed -- report as an error
                results.append({
                    'module': '<WORKER CRASH>', 'run': 1, 'failures': 0,
                    'errors': 1, 'skipped': 0,
                    'output': f'Worker crashed: {exc!r}\n{traceback.format_exc()}',
                })
        pool.close()
    except Exception:
        timed_out = True
    finally:
        if timed_out:
            pool.terminate()
        pool.join()
    if timed_out:
        results.append({
            'module': '<TIMEOUT>', 'run': 1, 'failures': 0, 'errors': 1,
            'skipped': 0,
            'output': f'GLOBAL TIMEOUT: suite exceeded {GLOBAL_TIMEOUT_S}s hard cap.',
        })
    return results, timed_out


def main():
    started = time.monotonic()
    buckets = build_units()
    results, timed_out = _run_parallel(buckets)

    print('=' * 64)
    print(' PiScope test suite (parallel, max 2 workers)')
    print('=' * 64)
    for r in results:
        status = 'OK' if (r['failures'] == 0 and r['errors'] == 0) else 'FAIL'
        print(f"  [{status:4}] {r['module']:<28} "
              f"run={r['run']:<3} fail={r['failures']:<2} "
              f"error={r['errors']:<2} skip={r['skipped']}")
        if status == 'FAIL':
            print('  ' + '-' * 60)
            for line in r['output'].rstrip().splitlines():
                print('  ' + line)

    total = sum(r['run'] for r in results)
    failures = sum(r['failures'] for r in results)
    errors = sum(r['errors'] for r in results)
    skipped = sum(r['skipped'] for r in results)
    passed = total - failures - errors - skipped
    elapsed = time.monotonic() - started

    print('-' * 64)
    print(' SUMMARY')
    print(f'   Total tests : {total}')
    print(f'   Passed      : {passed}')
    print(f'   Failed      : {failures}')
    print(f'   Errors      : {errors}')
    print(f'   Skipped     : {skipped}')
    print(f'   Timed out   : {"YES" if timed_out else "no"}')
    print(f'   TOTAL wall-clock time: {elapsed:.2f}s')
    print('=' * 64)

    return 1 if (failures or errors or timed_out) else 0


if __name__ == '__main__':
    sys.exit(main())
