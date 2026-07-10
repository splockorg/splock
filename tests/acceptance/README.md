# splock Acceptance Test Suite

Autonomous acceptance tests for the user-facing surface of the
splock substrate. Exercises behavior the existing 1314
unit/integration tests don't cover (cross-CLI seams, recovery
state-machines, negative-claim conformance, cross-surface consistency
audits).

**Source-of-truth inventory:** the inventory this suite was written against
lives in the origin repo's plan history and is not carried here; the block
map below (§A–§M) is the shipped reference.

## Run

```bash
# Run the full suite
pytest tests/acceptance/

# Run + emit a Markdown findings report
pytest tests/acceptance/ \
  --acceptance-report=acceptance_findings.md

# Run a single block (e.g., Block J cross-surface consistency)
pytest tests/acceptance/ -k acceptance_J_
```

## Constraints

Per inventory §1:

1. **Fully autonomous** — no operator gestures during the run.
2. **Test isolation** — every plan-slug fixture lives under `tmp_path`.
3. **No real LLM calls** — subagent invocations use recorded fixtures.
4. **Don't re-flag documented residuals** — see §10 report §4.2 + §7.4
   for the known-issues source-of-truth; Block K is the xfail-watcher
   surface for tracked follow-ups.
5. **Substrate self-modification guard is real** — tests cannot patch
   `.claude/{hooks,agents,commands}/` (by design, per `permissions.deny`).

## Block structure (87 tests across 11 blocks)

| Block | What it covers |
|---|---|
| A | Five-step workflow file-existence gates (3 tests) |
| B | Subagent contracts (5 tests) |
| C | Chain driver scenarios + exit codes (11 tests) |
| D | Operator CLIs end-to-end (15 tests) |
| E | Hook allow/deny pairs (17 tests) |
| F | Eval + trace pipeline (5 tests) |
| G | §P intent registry (5 tests) |
| H | Schemas + standing requirements (3 tests) |
| I | Negative-claim conformance (5 tests) |
| J | Cross-surface consistency audit (11 tests) |
| K | Follow-up regression watchers — xfail until landed (8 tests) |

## Implementation status

Pass 1 (this commit): conftest + 5 prove-the-harness tests
(A.1, B.1, C.1, D.1, E.1). Subsequent passes follow the inventory's
implementation order (§4).

## When a Block K xfail flips to xpass

That means a §10 §7.4 long-tail follow-up landed. The test owner
should:

1. Remove the `@pytest.mark.xfail(...)` decorator
2. Flip the assertion direction (the test was pinning broken behavior;
   now it should pin correct behavior)
3. Add a note in the test docstring referencing the landing commit

The `--acceptance-report` flag surfaces these flips in the Markdown
report's "Block K" section.
