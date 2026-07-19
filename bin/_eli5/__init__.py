"""`bin/eli5` — the plainspeak briefing lens (`/eli5`).

The third leg of the qa/qna/eli5 terminology triangle
(docs/feedback_eli5_terminology.md):

- **qa**   — adversarial review of an artifact. Finds problems.
- **qna**  — investigation of an operator question. Finds answers.
- **eli5** — TRANSLATION of existing material into plainspeak. Finds
  *nothing new*: it must not add findings, drop caveats, or change
  substance. It re-expresses. If simplifying would distort, it keeps
  the caveat and glosses it instead.

eli5 is a **lens, not a stage**: it never writes lifecycle/status state
of any kind (no `_state.json`, no orchestrator/plan substrate, no
markers; in deployments that layer a fleet tracker on top of splock,
no `_fleet*` files or hub zones either).

Modules:

- `format`    — `build_format(mode)`: the deterministically-constructed
                output format (per the arXiv-cited rubric-determinism
                doctrine used by `bin/_qa/rubric.py`). The agent never
                authors its own format; drivers obtain the bytes via
                `bin/eli5 --print-format <mode>`.
- `subject`   — eli5's OWN five-member subject enum (recon/qna/research/
                plan/qa — deliberately NOT `bin._qa.subject.ALL_SUBJECTS`,
                which has four members and no `qa`), the slug-bound
                stage-precedence resolver, and the 8KB paragraph-boundary
                subject truncation used before the wrap envelope.
- `promptfile`— the paste-able decision-sheet mechanic: decision-item
                counting, `_eli5_prompt_<N>.txt` numbering, sheet body.
- `invoke`    — SDK-direct single call on the subscription transport
                (`bin._sdk_bridge.SubscriptionClient`), mirroring
                `bin/_qa/invoke.py`.
- `main`      — argparse CLI behind the POSIX wrapper `bin/eli5`.
                Deliberately subject-file-only in v1: slug binding,
                append/reopen artifact modes, and the auto-offer are
                in-Claude surface features (`commands/eli5.md`).
"""
