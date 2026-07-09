"""`bin/lessons` entry point — invoked via the POSIX shell wrapper.

Dispatches to `cli.main(...)`. Kept thin per implplan §M.impl.2 file tree.
"""

from __future__ import annotations

import sys

from .cli import main


if __name__ == "__main__":
    sys.exit(main())
