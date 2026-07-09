# qa rubric — adversarial review of `<slug>_recon.md`

Read the recon end-to-end. For every load-bearing claim, ask: is the
evidence cited? does the evidence support the claim? what assumptions
are unstated? For every gap acknowledged in the recon, identify what
specific research would close the gap. Surface ambiguity in defer-line
drawing.

**Output discipline:**

1. Markdown. No JSON, no code-block-only output.
2. Cite the recon section/paragraph each question challenges, e.g.
   "(§4.2(b), line 244)". This is the recon's section + approximate
   line — not a verified-against-tree citation; the planner will
   re-verify if needed.
3. Questions are NOT answered here. Surface them for the planner /
   research subagents to address before Call 2 emission.
4. Organize findings into four blocks (A/B/C/D below). Use H2 for the
   block headings. Use bold inline tags (`**A.1.**`, `**B.3.**`) for
   individual findings.
5. Do not invent file paths or function names not in the recon. If a
   citation in the recon looks suspicious, flag it under Block B as
   "verify line citation against current tree" — this CLI substrate
   runs SDK-direct without tool access, so claim-against-tree
   verification is the planner's job at Call 1 re-verification time.

## Block A — Verified cross-references (housekeeping)

Cross-references the recon makes that ARE internally consistent with
itself (one section pointing to another, table entries matching prose,
etc.). Acknowledge these with one-line confirmations so the planner
knows the housekeeping passed. No remediation required.

For each item under Block A, include:
- The recon's claim (section, paragraph, what it says)
- Whether the claim is internally consistent with the rest of the recon
- "No remediation" line if confirmed

## Block B — Unverified / under-supported claims (load-bearing)

Claims that are load-bearing for the planner's design decisions but
where the recon does NOT cite sufficient evidence. These are the
highest-priority qa findings.

For each item under Block B, include:
- The recon's claim (with section + line citation)
- Why the claim matters (which planner decision rests on it)
- What evidence is missing (file/test/spec the recon should have cited)
- Suggested resolution (e.g., "planner Call 1 should grep X to confirm Y")

## Block C — Substrate-interaction risks the recon missed or under-explored

Failure modes or interaction risks involving existing substrate
(modules, schemas, locks, sentinels, exit codes, settings) that the
recon either did not consider or hand-waved. These are usually surfaced
by reasoning about the recon's design against what the planner will
need to write — gaps that block decisive plan emission.

For each item under Block C, include:
- The substrate component (lock file, sentinel JSON, exit code, schema)
- The interaction the recon missed (e.g., "what happens when X and Y
  both occur")
- The specific subsection or table the planner should add to address it

## Block D — Ambiguous or inconsistent defer-line drawing

Cases where the recon's §7 out-of-scope table conflicts with §4
in-scope text, or where the recon defers item X to "future marker N"
but a different section assumes X is in scope. Marker-prefix
allocations that double-book a slug. Inconsistencies in what is "ship
now" vs "next marker."

For each item under Block D, include:
- The two conflicting recon statements (with section + line citations)
- The marker-prefix or sub-feature the conflict involves
- Suggested resolution (re-allocate marker numbers, move section to
  in-scope, etc.)

---

**Closing note:** if the recon is high-quality and you find few or no
issues in a block, say so explicitly ("Block D — no inconsistencies
found"). An empty block is signal; do not pad with low-value findings.
