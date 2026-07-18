## eli5 output format (deterministic — injected byte-exact; never restructure it)

Every item in the source material that survives focus-narrowing becomes ONE
five-part block, exactly this shape:

### <n>. <plain-language title — what this is about, not the jargon name>

**ELI5:** 2–5 sentences. Plain language. Every jargon term either dropped or
glossed inline ("the component catalog — the system's parts list"). Analogy
allowed, accuracy first.

**Example:** one concrete scenario showing the item biting or mattering.
Grounded in the actual system (real component names, real flows) — never a
generic hypothetical when a real one is available.

**Impact:** what breaks, stalls, or gets decided-by-default if this is
ignored; what it gates downstream. One short paragraph.

**TL;DR:** one sentence.

**Options:**            <- decision items only; omitted entirely in informative items
- **<n>-A (recommended, if any)** — <option> — <one-line consequence>
- **<n>-B** — <option> — <one-line consequence>
- ...

Format rules (output discipline — every rule binds):

1. Markdown. No JSON. No code-block-only output.
2. Stable decision IDs: items numbered 1, 2, 3…; sub-decisions 1a, 1b; options
   lettered A, B, C… — so a reply like `1a-C, 2-B` is unambiguous.
3. At most ONE recommendation per decision, marked `(recommended)`. If the
   source material already recommends, carry that recommendation and attribute
   it ("QA-recommended"); if you derive one yourself, attribute that too
   ("my recommendation"). Never two.
4. Faithfulness: every fact traces to the source material or the tree. No new
   findings, no dropped caveats. If a source claim can't be verified with the
   tools available, say "not independently checked" rather than asserting or
   omitting.
5. Informative items get NO Options block — not an empty one.
6. Decision-mode tail: after all items, a `### Decision sheet` section — one
   line telling the operator how to reply, plus the compact code list (e.g.
   "Reply like: `1a-C · 2-B · 3-A`"). Omitted when zero decision items.
7. Don't pad. An item too thin to need an Example may say "Example — not
   needed; the ELI5 is the whole story." Silence is worse than admission.
8. Source-ID traceability: when the source material carries its own item IDs,
   the plain-language title ends with the source ID in parens — e.g.
   `### 1. Nobody picked a backend (source: B.1)` — so renumbering never
   severs the traceback.

Mode: auto — classify each item yourself. An item is a DECISION item iff the
source material presents mutually exclusive resolutions, asks for operator
input, or is tagged as blocking/BLOCKER. Everything else is informative. A
single briefing may mix both kinds.
