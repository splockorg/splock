---
name: qa
description: Adversarial quality-assurance review of a slug artifact (recon by default; --subject selects recon/qna/research/plan) against a deterministically-constructed rubric. Use when the user says "qa X", "review X critically", "find the holes in X", "stress-test the plan". This is adversarial review (quality-assurance), distinct from qna (question-and-answer).
---

# qa

Adversarial review of a slug artifact against a constructed rubric. Produces
`docs/plans/<slug>/<slug>_qa.md`.

Operator entry: `/qa <slug> [free-text]`. `--subject` selects which artifact to
review (recon by default; also qna/research/plan).

Spawns the `qa` subagent (`agents/qa.md`). Note: `qa` is adversarial review;
`qna` is question-and-answer. Do not conflate the two.
