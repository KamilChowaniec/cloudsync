"""Zipapp entry point.

`python -m zipapp ... -m "pkg.module:func"` runs func() but ignores its
return value, so exit codes are lost. This wrapper converts the CLI's
return value into a real process exit code (doctor's FAIL must fail —
install.sh runs under `set -e`).
"""

import sys

from .cli import main


def run() -> None:
    sys.exit(main())
