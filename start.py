#!/usr/bin/env python3
"""PiStats launcher — used by ``python3 pistats.py``, from any directory.

Usage:
    python3 pistats.py                      open the full-screen dashboard
    python3 pistats.py view [LOG] [OPTIONS] print the text report

The launcher resolves its own location with realpath(), so its companion
modules (pistats_cli, pistats_tui, analyze, ...) are importable no matter what
the current working directory is.  The ``pistats.py`` file is the Python entry
point that delegates here; running it via ``python3 pistats.py`` needs no PATH
setup and no executable bit.
"""

import os
import sys


def repo_dir():
    """Absolute directory containing this launcher (symlinks resolved)."""
    return os.path.dirname(os.path.realpath(__file__))


def main(argv=None):
    """Dispatch into the PiStats CLI and return its exit status."""
    sys.path.insert(0, repo_dir())

    from pistats_cli import main as cli_main  # noqa: WPS433  (lazy by design)

    return cli_main(argv)


if __name__ == '__main__':
    sys.exit(main())
