"""`bin/route_issue` substrate — deferred-work routing (implplan §L.impl).

The five routing types codify plan §L.5:

  fix-now      — log-only; no artifact (zero blast radius)
  outstanding  — append to docs/outstanding_issues.md (lazy-dump-capped)
  marker       — delegate to bin/marker create (scheduled-markers substrate)
  tier-promote — mkdir + skeleton _recon.md + mutate origin to status: promoted
  escalate     — write structured handoff to morning-review queue

Triggers (§L.impl.4) fire FIRST, before the four-way rubric (§L.impl.5).
"""
