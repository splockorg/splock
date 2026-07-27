# Build brief — the `/splock` super-skill (privileged human-only plumbing-admin)

> **Audience:** an agent working **inside this repo** (`splock`). This is a
> self-contained implementation brief — you do not need any other repo's context.
> **Status:** design is settled; this brief is the consolidated, build-ready spec.
> The one historical dependency (the framework extraction that produced this repo —
> the `splock-*` hook rename + the `${CLAUDE_PLUGIN_DATA}` state backend + `bin/_env_paths`)
> is **already in place**, so this build is **unblocked**.
> **Enforcement model (settled — do NOT re-litigate):** "**Ship A / document B**".

---

## 0. TL;DR — what you are building

A **12th skill**, `/splock`, that lets a **human operator** safely edit `splock`'s own
deterministic *plumbing* — the files the framework deliberately seals against agents:
`hooks/**`, `hooks/sealed_paths.txt`, `settings.json` (if/when present), `.claude-plugin/**`,
`schemas/**`. Editing these by hand is exactly what the seal forbids, so `/splock` provides a
**sanctioned, hook-gated, audit-logged, human-confirmed** write path instead.

The whole thing is **enforcement-in-the-hook, never enforcement-in-prose**. The skill body
is just a thin entry; every guarantee is carried by a hook + a deterministic helper + a TTY
confirm + (for adopters) OS-level hardening. There are **three independent defense tiers**;
the design is built so that **even total hook failure still cannot** let a headless agent or a
subagent reach the privileged write.

---

## 1. The three-tier defense doctrine (the load-bearing frame — build to this ordering)

Build and document the defenses in this exact priority order. The ordering *is* the design.

### Tier 1 — Platform floor (free, no code): the **headline** headless defense
`/splock` is a **skill**, and user-invoked skills are **only available in interactive mode**;
in `claude -p` / headless, skills cannot be invoked at all (the platform tells the model to
"describe the task instead"). Source (cite verbatim in your docs):
`https://code.claude.com/docs/en/headless`. **Therefore a skill-gated `/splock` is structurally
unreachable from headless before any hook fires.** This is the single strongest fact in the design.

### Tier 2 — The hook (the shipped code): defense-in-depth + out-of-box convenience
A `UserPromptSubmit` **arming** hook + a `PreToolUse` **gate** sub-hook. The arming hook writes a
one-shot per-session marker only on a **turn-1, interactive** prompt; the gate denies the privileged
helper unless that marker exists and consumes it on use. This is *not* the headless discriminator
(Tier 1 is) — it is the in-session convenience gate.

### Tier 3 — Root-own + password-`sudo` (the **hard boundary**): "document B"
A **documentation** deliverable for adopters: root-own the plumbing read-only to the agent uid, and
provide a password-`sudo` (PAM) write helper. This is the deployment-grade boundary that **does not
depend on the hook at all**.

### The graceful-degradation property (must be stated in DESIGN/doctrine)
If the hook breaks entirely (lost `transcript_path`, marker race, etc.), `/splock` **still** cannot
run headless (Tier 1) and **still** cannot write root-owned files without the PAM password (Tier 3).
The hook is convenience; the platform floor + OS hardening are the real boundaries. Do **not** let a
weakness in one tier (e.g. the `/dev/tty` caveat below) read as a hole in the whole model.

---

## 2. Dependency status — UNBLOCKED

The design was gated on the framework extraction being complete. That is done:
- the lifecycle hooks are named `splock-*` (e.g. `hooks/splock-session-start.sh`,
  `hooks/splock-session-end.sh`, `hooks/splock-user-prompt-submit.sh`);
- the state backend resolves data paths via `bin/_env_paths` (`plugin_root()` /
  `plugin_data_dir()`) against `${CLAUDE_PLUGIN_DATA}` / `${CLAUDE_PLUGIN_ROOT}`;
- the repo is committed, the test suite is green, and the trace-grep gate is clean.

**Author everything `splock-*` / `SPLOCK_*` from the start. Resolve every path via `bin/_env_paths`
— NEVER hardcode `parents[2]` or a repo-relative path** (it breaks in installed-plugin mode).

---

## 3. Integration surface — the EXACT files in THIS repo you wire into

Confirm the precise line numbers yourself (they drift); the **contracts** below are what matter.

| Seam | File (this repo) | What it gives you |
|---|---|---|
| Event wiring | `hooks/hooks.json` | Already wires `PreToolUse → bin/security-dispatch.sh`, `UserPromptSubmit → hooks/splock-user-prompt-submit.sh` (one hook today), `SessionStart → hooks/splock-session-start.sh`, `SessionEnd → hooks/splock-session-end.sh`. You ADD to these. |
| PreToolUse Bash router | `bin/_hooks/security_dispatch.py` (via `bin/security-dispatch.sh`) | First-deny-wins sequential sub-hook dispatch; routes Bash by command shape (install→package-safety, ddl→safe-ddl). **You add a new Bash-shape route** for the splock-admin sentinel → your new gate sub-hook, ordered before the generic allow. **Missing sub-hook scripts are skipped** — so the substrate ships safely even before your gate lands. |
| Deny-envelope contract | `bin/_hooks/sealed_paths_hook.py` | The dispatcher detects a deny by parsing the **last stdout line** for `hookSpecificOutput.permissionDecision == "deny"`. Your gate MUST emit exactly: `{"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"deny","permissionDecisionReason":"…"}}` to stdout, exit 0. |
| **The Python-IO-bypass seam** | `bin/_hooks/plan_render_on_edit.py` + `bin/_hooks/sealed_paths_hook.py` + `bin/plan` | PreToolUse hooks fire on the **Edit/Write/Read/Bash tools**, NOT on file I/O performed *inside a Python subprocess*. `bin/plan --amend` is the canonical precedent: a privileged, audit-logged, re-validating mutation of a sealed doc, reachable only via a CLI (Python I/O) that bypasses the Edit/Write seal — while raw Edit/Write stays denied with a message **routing** the agent to the CLI. **Model your privileged helper on `bin/plan --amend`.** |
| Path resolution | `bin/_env_paths/__init__.py` → `plugin_root()`, `plugin_data_dir(create=True)` | `plugin_data_dir()` = `${CLAUDE_PLUGIN_DATA}` (survives plugin update) → the marker lives here. `plugin_root()` = the editable plumbing's root. |
| Turn-1 sentinel home | `hooks/splock-session-start.sh` | Extend it to write a per-`session_id` "armed-once" sentinel **bound to `source=="startup"`** (a fresh start — NOT `resume`/`clear`/`compact`). |
| Marker sweep home | `hooks/splock-session-end.sh` | Extend it to sweep the arming marker for the ending `session_id`. |
| Existing UserPromptSubmit hook | `hooks/splock-user-prompt-submit.sh` | Today it's the single `UserPromptSubmit` hook (already extracts `session_id`). Add your **arming gate as a second `UserPromptSubmit` hook** in `hooks.json` (or fold into this one — second hook is cleaner/isolated). |
| Skill + command format | `skills/<name>/SKILL.md` + `commands/<name>.md` | Mirror these exactly (YAML frontmatter `name:` + `description:`; command adds `argument-hint:` + `$ARGUMENTS`). `/splock` is `skills/splock/SKILL.md` + `commands/splock.md`. |
| Sealed inventory | `hooks/sealed_paths.txt` + `bin/_hooks/sealed_paths.py` + `hooks/chain-sealed-state-delete-block.sh` | The list of sealed paths your helper is the *sanctioned exception* to. Note the **delete-block** hook catches `rm`/truncate/`>file` Bash shapes too. |

**How every shell hook gets context:** it reads the full event JSON from stdin
(`HOOK_INPUT="$(cat || true)"`), exports `PYTHONPATH`, and pipes to a `python -m bin._hooks.*`
entry that parses `tool_name` / `tool_input` / `file_path` / `session_id` / `agent_id` /
`agent_type` / `permission_mode`. `session_id` extraction is already demonstrated in the existing
`UserPromptSubmit` hook — reuse that idiom.

---

## 4. Build deliverables — components, locations, acceptance criteria

Build all nine. Each lists **where** it lands and the **acceptance bar** it must clear.

### D1 — The `/splock` skill + command entry
- **Files:** `skills/splock/SKILL.md`, `commands/splock.md`.
- **Behavior:** thin entry that explains the human-only privileged-plumbing flow and invokes the
  privileged helper (D2). **No enforcement logic in the prose** — the skill body documents intent;
  the hook (D5) + helper (D2) enforce.
- **Accept:** `claude plugin validate . --strict` passes with the new skill/command present; frontmatter
  matches the existing skills' shape; the command parses an operator directive (`$ARGUMENTS`).

### D2 — The privileged write helper (modeled on `bin/plan --amend`)
- **File:** a new CLI, e.g. `bin/splock-admin` (POSIX wrapper) → `python -m bin._splock_admin.main`
  (mirror how `bin/plan` wraps its Python entry).
- **Behavior:** performs the sealed write via **Python/process I/O** (structurally bypassing the
  Edit/Write/Read PreToolUse seam). It must:
  1. **Audit-log** the directive (what file, what change, when, which `session_id`).
  2. **Scope-bound** the change to the named plumbing surface only: `hooks/**`, `hooks/sealed_paths.txt`,
     `settings.json`, `.claude-plugin/**`, `schemas/**`. Reject writes outside this set.
  3. **Re-validate per file type BEFORE committing the write** (the validation matrix):
     - shell hook scripts → `bash -n <file>` (syntax check);
     - `settings.json` → `python -m json.tool` parse **AND** re-run the settings content guard
       (`scan_settings_content`) — **CRITICAL: preserve the CVE-2025-59536 guard
       (`enableAllProjectMcpServers`); never strip it; re-run the scanner on your OWN output before
       writing** (risk G.7);
     - `.claude-plugin/plugin.json` (+ `marketplace.json`) → `jsonschema` validation;
     - `schemas/*.json` → JSON parse + (if a meta-schema is available) `jsonschema` self-validate.
     Note: hooks/schemas are NOT schema-validated the way `plan.json` is — so this helper owns its own
     post-write validation matrix. If any check fails, **abort the write** (fail-closed, leave the file
     untouched).
  4. Read a **`/dev/tty` y/N confirm** at apply-time (D7) and **fail closed** if `/dev/tty` is unreadable.
- **Accept:** raw `Edit`/`Write` on any path in the scoped set stays **denied** (the deny message routes
  here); the helper round-trips a benign edit to each file type and re-validation catches a deliberately
  malformed edit (golden negative tests); the CVE guard survives a `settings.json` edit.

### D3 — `SessionStart` turn-1 sentinel
- **File:** extend `hooks/splock-session-start.sh` (+ its Python entry if it has one).
- **Behavior:** on `source=="startup"` ONLY, write a per-`session_id` "armed-once" sentinel under
  `plugin_data_dir()`. Prefer this sentinel over counting transcript records (the transcript JSONL schema
  is undocumented/version-dependent — risk G.5). Do NOT write the sentinel on `resume`/`clear`/`compact`.
- **Accept:** a `startup` session writes exactly one sentinel; a `resume` of the same `session_id` does NOT.

### D4 — `UserPromptSubmit` arming hook
- **Files:** `hooks/splock-user-prompt-submit-gate.sh` (+ `python -m bin._hooks.splock_arm` or similar),
  wired as a **second** `UserPromptSubmit` entry in `hooks/hooks.json`.
- **Behavior:** arm `/splock` for the current `session_id` **only when ALL of**:
  (a) **turn-1** — the D3 sentinel exists and is unconsumed;
  (b) **interactive** — `agent_id` AND `agent_type` are both **absent** (subagent ⇒ refuse), AND
      `permission_mode != "bypassPermissions"`;
  (c) on success, write the one-shot arming marker (D6) and consume the turn-1 sentinel.
- **Accept:** arms on a turn-1 interactive prompt; refuses on a later prompt, inside a subagent, and in a
  `bypassPermissions` session.

### D5 — `PreToolUse` gate sub-hook
- **Files:** `hooks/splock-admin-gate.sh` → `python -m bin._hooks.splock_admin_gate`, **registered as a new
  Bash-shape route in `bin/_hooks/security_dispatch.py`** (recognize the splock-admin sentinel command shape;
  order before the generic allow).
- **Behavior:** when the privileged helper (D2) is invoked via Bash, **deny unless** the arming marker for this
  `session_id` exists; **consume (delete) the marker on success** (one-shot); **also refuse if `agent_id`/`agent_type`
  is present** (belt-and-suspenders with D4 — the gate must not rely on "subagents can't"); emit the **exact deny
  envelope** (§3) on refusal.
- **Accept:** helper invocation with no marker is denied with the exact envelope (golden test against the
  dispatcher's last-line parser); with a valid marker it's allowed and the marker is gone afterward; a subagent
  invocation is denied even if a marker somehow exists.

### D6 — Arming-marker lifecycle + race/TOCTOU/detached hardening
- **Path:** `plugin_data_dir() / "splock_arm" / <session_id>` (resolved via `bin/_env_paths`; **never** `parents[2]`).
  Must live under `plugin_data_dir()` (survives plugin update), NOT `plugin_root()` (ephemeral cache).
- **Lifecycle:** written at turn-1 interactive (D4) → consumed on first privileged use (D5) → swept at `SessionEnd`
  (extend `hooks/splock-session-end.sh`).
- **Hardening:**
  - **Stale/resume reuse (G.4):** bind creation to `source=="startup"` (via D3) + embed a timestamp/TTL so a stale
    marker from a crashed session cannot arm a later `resume` of the same `session_id`.
  - **TOCTOU (between arm and check):** name the window explicitly; mitigate via atomic create+consume and a
    nonce/timestamp in the marker that the gate validates. (If you cannot fully close it, document the residual as a
    tracked open question — do not silently assume it away.)
  - **Detached-subprocess bypass (G.3):** PreToolUse hooks do **not** fire on driver/detached shell invocations
    (see `hooks/chain-sealed-state-delete-block.sh`'s own rationale). Therefore the gate cannot be the *only*
    enforcement — the **helper itself must fail-closed on `/dev/tty`** (D7), an in-process check independent of the
    tool layer, so a detached invocation that skips PreToolUse still cannot proceed without a human terminal.
- **Accept:** marker survives a simulated plugin-update (lives under `plugin_data_dir()`); TTL rejects a stale marker;
  sweep removes it at SessionEnd; the detached path is blocked by the helper's own `/dev/tty` check.

### D7 — `/dev/tty` apply-time human-presence confirm
- **Where:** inside the D2 helper, at apply-time.
- **Behavior:** read a `y/N` from `/dev/tty`; **fail closed if `/dev/tty` is unreadable**. This is the in-band
  human-presence check and doubles as the detached-subprocess fail-closed (D6/G.3).
- **DO NOT conflate with the headless discriminator (G.1):** `/dev/tty` proves **terminal presence**, NOT
  **non-`-p`** (a human who pipes `echo x | claude -p` still has a controlling TTY). The non-`-p` guarantee comes
  from **Tier 1** (platform floor), not from `/dev/tty`. Write the code comments + docs so a future maintainer can't
  "optimize away" the `/dev/tty` confirm on the false belief that it was the headless defense.
- **Accept:** unreadable `/dev/tty` ⇒ helper aborts; a `n`/empty answer ⇒ no write; an explicit `y` ⇒ write proceeds.

### D8 — Subagent block (dual; neither is the hard boundary)
- **Belt:** D4 + D5 refuse whenever `agent_id`/`agent_type` is present.
- **Suspenders:** framework subagents set `disallowedTools: Skill` (or an allowlist `tools:` omitting `Skill`) so
  they cannot invoke `/splock` via the `Skill` tool by default. Apply this to the agent definitions under `agents/`.
- **Doctrine:** state explicitly that **neither is the hard boundary** (Tier 3 is). Both are specified because
  subagents *can* invoke skills by default. Supporting platform facts to cite
  (`https://code.claude.com/docs/en/sub-agents`): subagents run in isolated fresh contexts, **cannot spawn other
  subagents**, and the `Agent` tool is **unavailable to subagents** — so the threat surface is a single-level
  subagent invoking `Skill`, which both mechanisms close.
- **Accept:** a subagent with the default tool set cannot reach `/splock` (gate refuses); framework subagent defs
  omit `Skill`.

### D9 — "Document B" adopter hardening (the hard boundary — a DOC, not code)
- **File:** an adopter doc (e.g. `docs/HARDENING.md`), and link it from `ADOPTION.md`.
- **Content:**
  - **Lead with the WSL `NOPASSWD` fix (G.6):** if the adopter's WSL sudoers has `NOPASSWD`, the password-`sudo`
    boundary silently evaporates. "**Fix `NOPASSWD` first**" must be the opening line, or the hard boundary is void.
  - Root-own the plumbing read-only to the agent uid + a **password-`sudo` (PAM)** write helper as the deployment-grade
    hard boundary that does not depend on the hook.
  - Package the apply-time helper as a **generated, diff-previewed, `/dev/tty`-confirmed bash script** — NOT inline
    `cp`/paste (pasting from rendered markdown injects invisible characters).
  - Frame the arc: *today* = "agent stages a file, human runs it out-of-band" → *`/splock`* = "human-only skill arms a
    hook-gated helper that writes in-band, audit-logged, TTY-confirmed" → *document B* = deployment backstop for adopters
    who don't trust the hook alone.
- **Accept:** the doc leads with the NOPASSWD fix; specifies root-own-read-only + PAM helper; the apply-time helper is a
  diff-previewed TTY-confirmed script.

---

## 5. Acceptance test suite (build these — the design deferred them to you)

Add under `tests/` (mirror the existing test layout; keep the full suite green and the trace-grep clean):

1. **`bash -n`** on every new/edited gate shell script.
2. **Marker lifecycle unit tests** — write (turn-1 startup) → consume (first use) → sweep (SessionEnd); startup-binding
   rejects `resume`; TTL rejects a stale marker.
3. **Deny-envelope golden test** — assert the gate emits the exact `permissionDecision:"deny"` envelope and that the
   dispatcher's last-line parser reads it as a deny.
4. **Re-validation matrix tests** — a malformed hook script (`bash -n` fails), a `settings.json` that drops the
   CVE-2025-59536 guard, and an invalid `plugin.json` are each **rejected** by the helper before any write.
5. **Red-team (`/qa`) suite** — cross-session marker reuse (resume same `session_id`); TOCTOU between arm and check;
   detached-subprocess path (helper must fail-closed on `/dev/tty`); a subagent with the default `Skill` tool attempting
   `/splock` (gate refuses).
6. **Subagent-block test** — framework subagent defs omit `Skill`; the gate refuses on `agent_id`/`agent_type`.
7. **Platform-floor assertion** — a test/doc-check asserting `/splock` is a skill (interactive-only) and citing the
   headless-doc fact; this is the headline defense and should be explicitly recorded.
8. **Regression** — `claude plugin validate . --strict` stays green; the existing suite stays green; `bash tests/trace_grep.sh`
   stays at 0 host-identity traces.

---

## 6. Risk & open-question ledger (carry these — do not silently drop)

**Risks (each mapped to the deliverable that addresses it):**
- **G.1 `/dev/tty` ≠ non-`-p`** → D7 (human-presence only; Tier 1 owns non-`-p`).
- **G.2 subagents CAN invoke skills by default** → D8 (gate refusal + `disallowedTools: Skill`; neither is the hard boundary).
- **G.3 detached-subprocess bypasses PreToolUse** → D6 + D7 (helper-side `/dev/tty` fail-closed).
- **G.4 marker stale/resume reuse** → D6 (startup-binding + TTL).
- **G.5 transcript JSONL parsing fragility** → D3 (prefer the SessionStart sentinel over transcript-counting).
- **G.6 WSL `NOPASSWD` nullifies the PAM boundary** → D9 (lead with the NOPASSWD fix).
- **G.7 `settings.json` CVE-2025-59536 content guard** → D2 (preserve + re-run `scan_settings_content` on own output).
- **G.8 plugin-vs-in-tree path divergence** → resolve via `bin/_env_paths` everywhere; never `parents[2]`.

**Carried-open questions (verify during build; don't assume):**
- *(/research)* exact `transcript_path` JSONL record schema across Claude Code versions; whether `SessionStart source=="startup"`
  reliably fires exactly once before the first `UserPromptSubmit`; whether `permission_mode` can be `bypassPermissions` in an
  otherwise-interactive session; whether a `PreToolUse permissionDecision:"deny"` is terminal or can be overridden by
  `--allowedTools`/`acceptEdits`/`bypassPermissions` (docs imply terminal — confirm; only relevant if Tier 1 were ever bypassed).
- *(/qa)* re-confirm the `bin/plan --amend` audit-log + re-validate guarantees actually hold for arbitrary plumbing files
  (hooks/schemas aren't schema-validated like `plan.json` — D2's matrix is the answer; confirm it's sufficient).

---

## 7. Hard guardrails for this build

- **Public artifact hygiene:** introduce **no personal identity and no external-host identity** anywhere — keep
  `bash tests/trace_grep.sh` at 0. Use only `splock` / `SPLOCK_` / `splock-*` / `splockorg` conventions.
- **Enforcement in the hook, never in skill prose.** The SKILL.md is documentation; every guarantee is code.
- **Fail-closed everywhere** — unreadable `/dev/tty`, missing marker, failed re-validation, subagent context ⇒ refuse/abort.
- **Never weaken an existing seal** — `/splock` is an *additive* sanctioned exception via the Python-IO seam; the raw
  Edit/Write deny on sealed paths must stay intact, and the CVE-2025-59536 guard must never be stripped.
- **Paths via `bin/_env_paths` only** (`plugin_data_dir()` for the marker, `plugin_root()` for edited plumbing).
- **Keep the suite + `claude plugin validate . --strict` green** at every step.

---

## 8. Suggested build sequence

The design DAG (use it as your task order; later items depend on earlier):

1. **Doctrine** (D-doctrine) — write the three-tier defense ordering + graceful-degradation into `DESIGN.md` (or a
   `docs/SUPER_SKILL.md`). Everything else cites it.
2. **D6 marker lifecycle + `bin/_env_paths` resolution** — the substrate the hooks share.
3. **D3 SessionStart sentinel** → **D4 arming hook** → **D5 PreToolUse gate** (the hook chain; D5 last because it
   consumes D4's marker).
4. **D2 privileged helper** + **D7 `/dev/tty` confirm** (the helper embeds the confirm).
5. **D1 skill/command entry** (now that the helper exists to invoke).
6. **D8 subagent block** + **D9 document B**.
7. **§5 test suite** alongside each component; finish with the red-team suite + regression.

**Two ways to drive it** (operator's choice): (a) **dogfood** — this repo ships the full `/recon /plan /implplan /code`
framework, so you can run them on this brief (this slug dir, `docs/plans/splock_super_skill/`, is already created); or
(b) **build directly** against this brief. Either way, this brief is the source-of-truth spec.

---

## 9. External references (verified — cite these in the docs)

- Hooks reference — `https://code.claude.com/docs/en/hooks` (event payload fields: `session_id`, `transcript_path`,
  `permission_mode`, `agent_id`, `agent_type`; `PreToolUse permissionDecision` ∈ allow/deny/ask/defer; exit-code contract).
- Headless — `https://code.claude.com/docs/en/headless` (**skills are interactive-only; unavailable in `-p`** — Tier 1).
- Subagents — `https://code.claude.com/docs/en/sub-agents` (isolated context; cannot spawn subagents; `Agent` tool
  unavailable to subagents; `Skill` removable via `disallowedTools`).
- CVE-2025-59536 — the `settings.json` `enableAllProjectMcpServers` content guard your helper must preserve (D2/G.7).
