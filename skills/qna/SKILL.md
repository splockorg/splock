---
name: qna
description: Answer an operator question about a slug with supporting evidence — the subagent investigates and returns an evidenced answer. Use when the user asks a direct question about an initiative ("how does X work?", "why did Y happen?", "qna X: <question>") rather than requesting adversarial review. This is question-and-answer (qna), distinct from qa (adversarial review).
---

# qna

Operator-question -> evidenced-answer for a slug. Produces
`docs/plans/<slug>/<slug>_qna.md`.

Operator entry: `/qna <slug> [<question>] [-- <directive>]`.

Spawns the `qna` subagent (`agents/qna.md`), which investigates and answers with
supporting evidence. A `/plan` run can later ingest the qna artifact as input.
Note: `qna` is question-and-answer; `qa` is adversarial review.
