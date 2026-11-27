#!/usr/bin/env python3
"""PiStats entry point — run it with ``python3 pistats.py``.

All launch logic lives in start.py.  The documented startup is
``python3 pistats.py`` (no arguments opens the TUI; ``view`` prints a report).
The shebang also allows running the file directly as ``./pistats.py``; neither
form needs a PATH setup because this script resolves its own directory.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))

from start import main  # noqa: E402

if __name__ == '__main__':
    sys.exit(main())
