# qa vs qna vs eli5 — the terminology rule

Three similarly-shaped names, three disjoint jobs. Confusing them costs
real work (a "qa" that answers questions finds no problems; a "qna"
that reviews finds no answers; an "eli5" that does either has broken
its one promise). The rule, in full:

| Term | Expansion | Job | Finds |
|---|---|---|---|
| **qa** | quality-assurance (adversarial) | adversarial review of an existing artifact against a deterministic rubric | **problems** — gaps, ambiguities, unverified claims |
| **qna** | question-and-answer | investigation of an operator-supplied question | **answers** — evidence-backed, with confidence |
| **eli5** | "explain like I'm five" | **translation** of existing material into plainspeak | **nothing new** — it re-expresses |

The eli5 clause carries the load: eli5 MUST NOT add findings, drop
caveats, or change substance. If simplifying would distort, it keeps
the caveat and glosses it instead ("this only holds when the cache is
warm — meaning right after the system has already looked something up
once"). A claim it cannot verify is carried as "not independently
checked", never asserted, never omitted.

Quick disambiguation when routing operator intent:

- "review this / poke holes in this / is this solid?" → **qa**
- "find out X / why does Y happen / what's the state of Z?" → **qna**
- "what does this mean / explain this to me / break this down" → **eli5**

## History

- The qa-vs-qna distinction was established with the qna subagent
  (roster v2, 2026-05-23) in a feedback note referenced as
  `feedback_qa_vs_qna_terminology.md`; that file was never added to the
  tracked tree. This document (2026-07-18, roster v3, the eli5 lens)
  supersedes it, covers all three terms, and is the tracked target for
  the citations that previously pointed at the untracked note
  (`agents/qna.md`, `commands/qna.md`, `agents/_roster.json`).

## Surfaces

- **qa** — `commands/qa.md` · `agents/qa.md` · `bin/qa` (`bin/_qa/`)
- **qna** — `commands/qna.md` · `agents/qna.md` (no CLI substrate)
- **eli5** — `commands/eli5.md` · `agents/eli5.md` · `bin/eli5`
  (`bin/_eli5/`)
