#!/usr/bin/env python3
"""PiScope launcher — start the app with plain Python, from any directory.

Usage:
    python3 start.py                      open the full-screen dashboard
    python3 start.py view [LOG] [OPTIONS] print the text report

The launcher resolves its own location with realpath(), so its companion
modules (piscope_cli, piscope_tui, analyze, ...) are importable no matter what
the current working directory is.  The executable ``piscope`` file is just a
thin shim around this module.
"""

import os
import sys


def repo_dir():
    """Absolute directory containing this launcher (symlinks resolved)."""
    return os.path.dirname(os.path.realpath(__file__))


def main(argv=None):
    """Dispatch into the PiScope CLI and return its exit status."""
    sys.path.insert(0, repo_dir())

    from piscope_cli import main as cli_main  # noqa: WPS433  (lazy by design)

    return cli_main(argv)


if __name__ == '__main__':
    sys.exit(main())
