# Contributing to splock

Thanks for your interest in improving splock. This document covers how to
propose changes and the one legal requirement we ask of every contribution.

---

## Developer Certificate of Origin (DCO)

splock uses the [Developer Certificate of Origin](https://developercertificate.org/)
(DCO) instead of a Contributor License Agreement. The DCO is a lightweight
attestation that you wrote the contribution, or otherwise have the right to
submit it under the project's Apache-2.0 license.

**Every commit must be signed off.** Add a `Signed-off-by` trailer to your
commit message:

```text
Signed-off-by: Your Name <your.email@example.com>
```

The easiest way is the `-s` flag:

```bash
git commit -s -m "your message"
```

By signing off you certify the statements in the DCO (reproduced below). The
name and email in the trailer must be real and must match the commit author.

### Developer Certificate of Origin 1.1

By making a contribution to this project, I certify that:

1. The contribution was created in whole or in part by me and I have the right
   to submit it under the open source license indicated in the file; or
2. The contribution is based upon previous work that, to the best of my
   knowledge, is covered under an appropriate open source license and I have
   the right under that license to submit that work with modifications,
   whether created in whole or in part by me, under the same open source
   license (unless I am permitted to submit under a different license), as
   indicated in the file; or
3. The contribution was provided directly to me by some other person who
   certified (1), (2) or (3) and I have not modified it.
4. I understand and agree that this project and the contribution are public and
   that a record of the contribution (including all personal information I
   submit with it, including my sign-off) is maintained indefinitely and may be
   redistributed consistent with this project or the open source license(s)
   involved.

---

## Making a change

1. **Open an issue first** for anything beyond a trivial fix, so the design can
   be discussed before code is written. splock is a plan-first project — that
   applies to changes to splock itself.
2. **Keep the enforcement spine deterministic.** New guardrails belong in a
   hook or a CLI exit code, never in agent/skill prose. A boundary that can
   only be enforced by asking a model nicely is not a boundary.
3. **Respect the host-trace gate.** Do not introduce any host-specific
   identity, path, organization, or domain token. The trace-grep gate
   (`tests/trace_grep.sh`) must stay clean.
4. **Add or update tests.** Changes to `bin/` tooling or hooks should come with
   tests under `tests/`. The suite must stay green.

---

## Running the tests

The project venv lives outside the repo at `~/.venvs/splock`. Create it once:

```bash
python3 -m venv ~/.venvs/splock
~/.venvs/splock/bin/pip install pytest jsonschema Pygments python-dotenv
```

Then activate and run:

```bash
source ~/.venvs/splock/bin/activate
# From the repo root:
python -m pytest tests/ -q

# The host-trace scrub gate:
bash tests/trace_grep.sh
```

Set `SPLOCK_VENV=~/.venvs/splock` in your shell profile so the bin/ wrappers
and hooks activate the right interpreter (see [ADOPTION.md](ADOPTION.md)).

---

## Reporting security issues

If you believe you have found a security problem — especially anything that
lets an agent bypass a deterministic guardrail — please report it privately via
the repository's security advisory channel rather than opening a public issue.
