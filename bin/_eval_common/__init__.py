"""Shared substrate for §J evaluation + trace layer.

Per splock implplan §J.impl.2 — consumed by §A (chain driver
halt-hook), §F (Sonnet review emission seam), §H (morning-review label
attach), and the five §J CLIs.

This is a sole-writer module set:
- `score_writer.append_emission` / `append_label` — sole writers of
  `_scores.jsonl`.
- `failure_capture.capture` — sole writer of `_failures/<id>.json`.
- `regression_case.promote` / `retire` — sole writer of
  `_regression_cases/<id>.json`.
- `failure_gc.purge_unpromoted` — operator-runnable retention purge.
"""
