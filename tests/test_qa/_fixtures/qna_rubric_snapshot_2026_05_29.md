# qa rubric — adversarial review of `<slug>_qna.md`

Read the qna end-to-end. For each Question/Answer pair, ask: is every
load-bearing clause in the Answer backed by a numbered Evidence entry,
and does that entry actually support the clause? Is the stated
Confidence justified by the evidence density and source quality? Does
the Answer address the verbatim Question, or drift into an adjacent
question or unrequested solutioning? A qna may hold multiple
Question/Answer pairs — review each.

**Output discipline:**

1. Markdown. No JSON, no code-block-only output.
2. Cite the qna section/paragraph each question challenges, e.g.
   "(§4.2(b), line 244)". This is the qna's section + approximate
   line — not a verified-against-tree citation; the planner will
   re-verify if needed.
3. Questions are NOT answered here. Surface them for the planner /
   research subagents to address before Call 2 emission.
4. Organize findings into four blocks (A/B/C/D below). Use H2 for the
   block headings. Use bold inline tags (`**A.1.**`, `**B.3.**`) for
   individual findings.
5. Do not invent file paths or function names not in the qna. If a
   citation in the qna looks suspicious, flag it under Block B as
   "verify line citation against current tree" — this CLI substrate
   runs SDK-direct without tool access, so claim-against-tree
   verification is the planner's job at Call 1 re-verification time.

## Block A — Answer supported by evidence

For each Question/Answer pair, check that every load-bearing clause in
the Answer maps to a numbered Evidence entry, and that the entry
actually supports the clause (the qna contract requires numbered
evidence per claim). This is the highest-priority qna check.

For each item under Block A, include:
- The Answer clause (with the question number + line citation)
- The Evidence entry it should map to (or note that none exists)
- Whether the cited evidence actually supports the clause, or is a
  non-sequitur / overstatement

## Block B — Confidence calibration

Is the stated Confidence (high / medium / low) justified by the
evidence density and source quality? Flag over-confidence (high
confidence on a single uncorroborated source) and under-confidence
(low confidence on a well-evidenced answer). Do NOT re-score the
answer — surface the calibration mismatch for operator judgment.

For each item under Block B, include:
- The stated Confidence + the question it attaches to (line citation)
- The evidence density / source quality actually present
- Why the stated confidence is mis-calibrated (over or under)

## Block C — Question fidelity / scope

Does the Answer address the verbatim Question, or drift to an adjacent
question? Does it over-reach into solutioning the operator did not ask
for? Flag scope drift in either direction.

For each item under Block C, include:
- The verbatim Question (with line citation)
- Where the Answer drifts, narrows, or over-reaches
- The specific clause that is out-of-scope for the question asked

## Block D — Evidence-citation integrity + Tier-1-patch safety

Do `file:line` citations look plausible (flag for planner
re-verification, mirroring the do-not-invent-paths discipline)? Are
external citations dated; are command-output citations reproducible? If
the qna DESCRIBES a Tier-1 fix, is the described change actually
single-file / conversational-scope, not a disguised wide-blast-radius
change?

For each item under Block D, include:
- The citation or described patch (with line citation)
- Why it is suspect (undated, irreproducible, or wider than Tier-1)
- Suggested resolution (planner re-verify, or re-scope the patch)

---

**Closing note:** if the qna is high-quality and you find few or no
issues in a block, say so explicitly ("Block C — no calibration concerns"). An empty block is signal; do not pad with low-value findings.
