"""Derived span emitter for `bin/render_spans`.

Per splock implplan §J.impl.3. v2.7 ships derived spans —
`bin/render_spans` reads `_orchestrator_log.jsonl` + (optional)
`hook_log.jsonl` + `_chain_sessions.json` and emits OpenInference-shape
spans to `_spans.jsonl`.

Native per-emitter span writes ship later via marker NSE (see
`bin/_eval_common/span_writer.py`).
"""
