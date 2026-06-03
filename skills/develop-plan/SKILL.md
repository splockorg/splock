---
name: develop-plan
description: Autonomously execute a multi-phase plan document by spawning implementer + evaluator agents per phase with no human gates between phases. Use when the user says "develop-plan <path>", "run the whole plan autonomously", or wants a markdown plan driven to completion phase-by-phase without per-phase stops. Long-running and autonomous; prefer the gated /code loop when human checkpoints are wanted.
---

# develop-plan

Autonomous multi-phase plan executor. Drives a plan markdown document to
completion, spawning an implementer and an evaluator agent per phase with no
human gate between phases.

Operator entry: `/develop-plan <plan-path-relative-to-repo-root>`.

The repo root is derived at runtime (templated `{repo_root}`), not baked. This
is the most autonomous entry point; use the gated `/code` loop when per-phase
human review is desired.
