---
name: research
description: Enumerate external sources (web, docs, prior art) for a slug and produce <slug>_research.md. Use when the user says "research X", "find prior art for X", "what do external sources say about X", or when a plan needs evidence from outside the repository (libraries, standards, papers, vendor docs).
---

# research

External-source enumeration for a slug. Produces
`docs/plans/<slug>/<slug>_research.md`.

Operator entry: `/research <slug> [free-text-tail]`. The tail may request a
re-run mode or a directive.

Spawns the `research` subagent (`agents/research.md`), which uses web fetch /
search to gather and cite external sources (by name + URL, in its own words).
