"""J.16 — `bin/route_issue` is deterministic + emits exactly one side-effect.

Per inventory + userguide §8 ("The decision is the value — routing it
consistently is the substrate's job") + plan §L.impl.

The structural claim under test:
  Given identical inputs, route_issue MUST pick the same handler/bucket
  every time AND emit exactly ONE downstream side-effect per invocation.
  If an LLM seam slipped into the dispatch path, this test would flake
  (different bucket across runs) or amplify (multiple side-effects per
  run). The "consistency is the substrate's job" promise depends on
  this discipline.

Two assertions:
  (a) Run `bin._route_issue.cli.parse_args(...)` then route through
      `rubric.route_after_triggers(...)` ten times with identical args
      — same `handler` + same `category` each time, no exceptions.
  (b) Invoke a real handler (`fix_now.run` — log-only, smallest surface
      area) ten times with identical args; assert exactly 10 forensic
      rows appended to `_orchestrator_log.jsonl` (one per invocation,
      no amplification).
"""

from __future__ import annotations

import json
import pytest
from pathlib import Path


pytestmark = pytest.mark.acceptance


def test_rubric_route_after_triggers_is_deterministic():
    """J.16a: ten identical --type fix-now decisions all map to the same handler."""
    from bin._route_issue import rubric

    decisions = []
    for _ in range(10):
        d = rubric.route_after_triggers("fix-now")
        decisions.append((d.refused, d.handler, d.category))

    distinct = set(decisions)
    assert len(distinct) == 1, (
        f"route_after_triggers('fix-now') non-deterministic across 10 calls: "
        f"saw {distinct}"
    )
    refused, handler, category = next(iter(distinct))
    assert not refused, f"fix-now should not be refused; got {decisions[0]}"
    assert handler == "fix_now", f"expected handler='fix_now'; got {handler!r}"


def test_fix_now_handler_emits_exactly_one_row_in_isolated_plan_dir(
    tmp_slug_dir, monkeypatch,
):
    """J.16c: with resolve_plan_dir redirected to tmp_slug_dir, 10 invocations
    produce exactly 10 rows in the local _orchestrator_log.jsonl.
    """
    from bin._route_issue import fix_now, log_emit

    # Redirect resolve_plan_dir so the handler writes under tmp_slug_dir
    # rather than the real docs/plans/splock/ surface.
    monkeypatch.setattr(
        log_emit, "resolve_plan_dir",
        lambda plan_dir, plan_slug: tmp_slug_dir,
    )

    for i in range(10):
        rc = fix_now.run(
            description=f"acceptance/j16c deterministic invocation #{i}",
            context="J16c:test",
            dry_run=False,
            json_output=False,
            repo_root=None,
            plan_slug=None,
        )
        assert rc == 0, f"fix_now.run iteration {i} returned non-zero: {rc}"

    log = tmp_slug_dir / "_orchestrator_log.jsonl"
    assert log.exists(), (
        "no _orchestrator_log.jsonl emitted by fix_now.run × 10 — handler "
        "side-effect suppressed (the §C writer may be unavailable; see "
        "fix_now.py warning path)."
    )
    rows = [
        json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert len(rows) == 10, (
        f"expected exactly 10 rows (one per fix_now invocation); got {len(rows)}\n"
        "Either the handler amplified (>10) or suppressed (<10) the side-effect "
        "— route_issue's determinism contract is violated."
    )

    # All 10 rows should have the same shape (transition/from + to + event_type),
    # differing only in reason (which embeds the per-call description).
    distinct_transitions = {
        (r.get("transition", {}).get("from"), r.get("transition", {}).get("to"))
        for r in rows
    }
    assert len(distinct_transitions) == 1, (
        f"transition shapes drifted across 10 identical invocations: {distinct_transitions}"
    )
    distinct_event_types = {r.get("event_type") for r in rows}
    assert distinct_event_types == {"fix_now_logged"}, (
        f"event_type drifted: {distinct_event_types}"
    )


def test_route_dispatch_table_is_static_no_llm_seam(repo_root):
    """J.16d: rubric.route_after_triggers source has no LLM/random call.

    Source-walk guard: the dispatch must be a static lookup. If a future
    refactor introduces a `random.choice(...)`, `requests.post(...)`, an
    `anthropic.Anthropic()` invocation, or anything that would make the
    same input produce a different output, this test catches it.
    """
    src = (repo_root / "bin" / "_route_issue" / "rubric.py").read_text(
        encoding="utf-8"
    )
    forbidden_substrings = (
        "anthropic.",
        "messages.create",
        "openai.",
        "random.choice",
        "secrets.choice",
        "requests.post",
        "requests.get",
        "httpx.",
        "urllib.request",
    )
    leaks = [s for s in forbidden_substrings if s in src]
    assert not leaks, (
        f"bin/_route_issue/rubric.py imports/calls a non-deterministic source: "
        f"{leaks}. Route dispatch MUST be a static table — operator-facing "
        "consistency is the substrate's promise. Move any LLM call upstream "
        "(before --type is decided), not into the dispatch layer."
    )
