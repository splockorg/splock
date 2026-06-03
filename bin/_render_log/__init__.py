"""bin/_render_log — Render-flow modules for `bin/render_log`.

Per implplan §C.impl.10. The CLI entry is `main.py`; per-line MD
formatting is `md_emit.py`; the 120-char truncation rule is
`truncation.py`.

The MD form (`_orchestrator_log.md`) is DERIVED, never source of truth.
Per implplan §C.impl.4 the JSONL is the canonical artifact.
"""
