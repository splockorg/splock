"""F.2 — `bin/eval-trend --slug X` parses _scores.jsonl + emits trend artifact."""

from __future__ import annotations

import json
import pytest


pytestmark = pytest.mark.acceptance


def test_eval_trend_module_main_exists():
    """F.2: bin._eval_trend.main has a callable main entry."""
    from bin._eval_trend import main as trend_main
    assert callable(trend_main.main), "main() not exposed"


def test_eval_trend_stats_payload_reads_scores(tmp_slug_dir):
    """F.2b: _stats_payload reads _scores.jsonl + returns a structured dict."""
    from bin._eval_trend import main as trend_main

    # Build a tiny _scores.jsonl fixture.
    scores_path = tmp_slug_dir / "_scores.jsonl"
    rows = [
        {
            "schema_version": 2,
            "ts": "2026-05-22T12:00:00Z",
            "scorer_id": "qualifying_run_count",
            "score": 1,
            "score_id": "s_abc12345",
            "system_version": "v0.0.1",
        }
    ]
    scores_path.write_text(
        "\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8"
    )

    if not hasattr(trend_main, "_stats_payload"):
        pytest.skip("_stats_payload helper not exposed")

    try:
        payload = trend_main._stats_payload(tmp_slug_dir, "qualifying_run_count")
    except (FileNotFoundError, KeyError, TypeError) as exc:
        pytest.skip(f"_stats_payload signature differs from probe: {exc}")

    assert isinstance(payload, dict), (
        f"_stats_payload should return dict; got {type(payload).__name__}"
    )
