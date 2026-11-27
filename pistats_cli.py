"""Command-line entry point for PiStats.

``python3 pistats.py`` with no arguments opens the dashboard;
``python3 pistats.py view`` prints the same text report as ``python3 analyze.py``.
``textual`` is never imported here so the module works with a stock Python 3.13
stdlib installation.
"""

import argparse
import os
import sys

import analyze


def build_parser():
    parser = argparse.ArgumentParser(
        prog='python3 pistats.py',
        description='PiStats — live CLI dashboard and temperature/power reports. '
                    'Run with no arguments to open the TUI.')
    sub = parser.add_subparsers(dest='command')

    view = sub.add_parser(
        'view', help='print the full text report for a temperature log')
    view.add_argument('log', nargs='?', default=None,
                      help='path to temp_log.csv (default: ./temp_log.csv)')
    view.add_argument('--power-log', dest='power_log', default=None,
                      help='optional power_log.csv to add energy/cost/battery data')
    view.add_argument('--range', dest='range_spec', default=None,
                      help='report range such as 2h, 7d or a date range')
    view.add_argument('--summary', action='store_true',
                      help='print only the summary section')
    return parser


def run_view(args):
    """Print ``analyze.build_report`` output for a ``view`` invocation."""
    log = args.log or os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                   'temp_log.csv')
    if not os.path.exists(log):
        print(f'pistats: log file not found: {log}', file=sys.stderr)
        return 1
    power_log = getattr(args, 'power_log', None)
    if power_log is None:
        adjacent = os.path.join(os.path.dirname(os.path.abspath(log)),
                                'power_log.csv')
        if os.path.exists(adjacent):
            power_log = adjacent
    config = None
    try:
        import pistats_config
        config = pistats_config.load_config()
    except Exception:
        config = None
    try:
        report = analyze.build_report(
            log, power_log_file=power_log, config=config,
            range_spec=getattr(args, 'range_spec', None))
    except OSError as exc:
        print(f'pistats: could not read log: {exc}', file=sys.stderr)
        return 1
    sys.stdout.write(report)
    return 0


def launch_tui(args):
    """Launch the full-screen dashboard (mocked by tests)."""
    from pistats_tui import run_tui
    return run_tui()


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    if getattr(args, 'command', None) == 'view':
        return run_view(args)
    return launch_tui(args)


if __name__ == '__main__':
    sys.exit(main())
