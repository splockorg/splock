"""Morning-review queue UX substrate (implplan §H.impl).

Per-plan queue files live at
`docs/plans/<slug>/morning-review/<YYYY-MM-DD>.md` and are produced by
the chain-driver halt-handoff path (§F.impl.7). This package owns the
operator-triage CLI surface, the entry parser/renderer, the rolling
`_index.md` regenerator, and the archive-move discipline.

Entry-point: `bin/morning-review` (POSIX shell wrapper) → `main.py`.
"""
