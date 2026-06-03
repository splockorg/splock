"""Module entry point for `python -m bin._chain_overnight`.

Per CCOR.1 T-7 (driver integration): thin shim that delegates to
`main.main()`. The CLI dispatch logic (argument parsing, `--from-resume`
startup-time inject consume, `--release-lock` pause-sentinel cleanup)
all lives in `main.py` so the existing `bin/chain-overnight` bash
launcher (`exec python -m bin._chain_overnight.main "$@"`) and this
module-style entry produce identical behavior.

The two entry shapes:

    bin/chain-overnight <args>            # bash launcher → main.main()
    python -m bin._chain_overnight <args> # this file    → main.main()

Both paths share the `--from-resume` and `--release-lock` semantics
documented in `main.py` (per R-from-resume-symmetry / R-release-lock-pause
in `docs/plans/_closed/ccor_1/design_resolutions.md`).
"""

from __future__ import annotations

import sys

from . import main as _main


if __name__ == "__main__":
    sys.exit(_main.main())
