---
slug: portability_scaffolding
artifact: recon
produced: 2026-07-15
mode: first-authorship (append default)
subject: docs/PORTABILITY_REVIEW.md (cross-artifact recon)
read_only: true
---

<operator-directive>
Review docs/PORTABILITY_REVIEW.md. We are moving splock to a hexagonal architecture to support OpenAI Codex CLI and Google Antigravity alongside Claude Code. Your task is to plan Phase 0: seam hardening. Do not implement the new host adapters. Instead, audit the codebase for Claude-specific coupling and propose the abstract Python interfaces for the new Host Adapter layer. Specifically, design the base classes/interfaces for the Hook Shim, the Transcript Provider, and the Model Transport.
</operator-directive>

# Recon — portability_scaffolding (Phase 0: seam hardening)

Subject artifact `docs/PORTABILITY_REVIEW.md` (committed c16cf12) proposes a
hexagonal split — one splock core, per-host adapters — with Phase 0 defined as
in-tree seam hardening: formalize a hook shim, a transcript provider, and a
model transport interface while Claude Code stays the only live host and CI
stays green. This recon audits the actual coupling surfaces and proposes the
Phase 0 interface designs. No host adapters (Codex/Antigravity) are designed
here beyond what the interfaces must leave room for.

## 1. Coupling audit — where Claude-specific knowledge lives today

### 1.A Hook wiring layer (HIGH coupling, but already half-normalized)

- `hooks/hooks.json:1-83` — registers 15 hook entries (15 unique trampoline
  scripts) across seven CC events
  (`PreToolUse`, `PostToolUse`, `SessionStart`, `Stop`, `UserPromptSubmit`,
  `SubagentStop`, `SessionEnd`) with CC matcher syntax (`"Edit|Write|Read|Bash|Task"`)
  and `${CLAUDE_PLUGIN_ROOT}` command interpolation.
- Every shell hook follows one pattern (e.g. `bin/security-dispatch.sh:45-49`,
  `hooks/intent-on-first-edit.sh:47-51`): activate `$SPLOCK_VENV`, `cat` stdin,
  pipe verbatim into a `python -m bin._hooks.*` / `bin._intent.*` module. The
  shell layer is a thin trampoline — **the CC payload/decision dialect is
  consumed inside the Python modules**, not the scripts.
- Decision dialect is CC's PreToolUse contract: exit 0 always, refusal as a
  JSON `permissionDecision: deny` object on stdout (deny is the only verdict
  the hook emitters produce today)
  (`bin/security-dispatch.sh:29-31`, `hooks/intent-on-first-edit.sh:22-23`).
  Notable: this is closer to Antigravity's exit-0 + JSON-verdict dialect than
  to CC's alternative exit-2 path — splock already avoids exit-code semantics,
  which shrinks the shim's job.
- `hooks/permissions.deny` mirrors sealed paths as CC settings-level deny
  rules — a CC-only backstop with no analog abstraction.

### 1.B Transcript / session scraping (HIGHEST-risk coupling)

`bin/_intent/hook_writer.py` hardwires Claude Code's on-disk session layout
and internal jsonl schema:

- Path convention: `~/.claude/projects/<munged-cwd>/<session_id>.jsonl`
  (`_claude_project_dir` / `_jsonl_path`, lines 58-66; munge = `-` + cwd with
  `/`→`-`).
- Byte-regexes against CC's private jsonl fields (lines 48-56):
  `<command-name>…</command-name>`, `"gitBranch"`, `"customTitle"`,
  `"type":"tool_use","id":"toolu_…"`, `"file_path"`, `"type":"user"` — plus
  structural knowledge of `message.content` blocks (lines 93-130), `isMeta`,
  TaskCreate/TaskUpdate/TodoWrite replay (lines 156-212).
- Live-status sidecar: `~/.claude/sessions/*.json` with `sessionId`/`status`/
  `updatedAt` fields (`_live_status_for`, lines 259-281).
- Subagent layout: `<project-dir>/<parent-sid>/subagents/agent-*.jsonl` +
  sibling `.meta.json` with `agentType`/`description`
  (`_find_recent_subagent_file`/`_upsert_subagent`, lines 379-458).
- The RDS schema itself leaks the host: `extraction.agent_sessions.claude_session_id`
  and `agent_subagents.parent_claude_session_id` columns (lines 289-316, 429-441).

`bin/_hooks/session_start_hook.py:154-195` reads the SessionStart envelope
(`session_id`, `source`) — mild coupling; the envelope fields are the shim's
to normalize.

### 1.C Model transport (MEDIUM coupling — three seams already exist)

- `bin/_sdk_bridge.py::SubscriptionClient` (lines 496-561) — Anthropic-`Message`-
  shaped `.messages.create/.stream` over `claude_agent_sdk`; injectable
  `query_fn`/`options_cls`; `_AdaptedMessage` return contract (lines 117-139);
  Claude-CLI schema-dialect sanitizer `strip_schema_meta_keys` (lines 199-230);
  subscription-auth env stripping `_force_subscription_auth` (lines 286-306) —
  billing/auth policy that is itself host-specific.
- `bin/_planner/two_call.py` — `AnthropicClient` Protocol (lines 245-262) +
  `_default_client()` factory (line 263); constrained emission via
  `output_config={"format":{"type":"json_schema",…}}` (lines 9-32);
  `DEFAULT_PLANNER_MODEL = "claude-opus-4-8"` (line 64); `_resolve_model_id`
  discovery fallback (lines 450-481). Same Protocol pattern in
  `bin/_qa/invoke.py:258-277` with `DEFAULT_QA_MODEL = "claude-opus-4-8"`
  (line 103).
- `bin/_retry_loop/sdk_spawners.py` — `ClaudeAgentSDKClient` Protocol
  (lines 369-430); reviewer/coder spawners build `ClaudeAgentOptions` directly
  (lines 1946-1982, 2617-2633); rubric-bound structured output via
  `resolve_schema(rubric_kind)` (line 1982); model pins `_DEFAULT_REVIEWER_MODEL
  = "sonnet"` (1636), `_DEFAULT_CODER_MODEL = "opus"` (2204); gate command
  `claude plugin validate` (line 545).
- The retry loop core is ALREADY transport-agnostic:
  `bin/_retry_loop/iteration_loop.py::run_iteration` takes injected
  `spawn_opus_fn` / `run_verify_fn` / `spawn_reviewer_fn` callables
  (lines 149-153), threaded through `run_test_step_loop` (lines 321-329); `bin/_chain_overnight/phase_spawn.py:385-441`
  performs the DI. **Phase 0 needs to formalize the callable signatures, not
  invert any control.**
- Verifier independence pin: `agents/verifier.md:5` frontmatter
  `model: claude-haiku-4-5-20251001`, deliberately non-overridable — a design
  invariant the transport interface must be able to enforce per family.

### 1.D Env contract & packaging (LOW coupling, already centralized)

- `bin/_env_paths/__init__.py:51-53` — the only place `CLAUDE_PLUGIN_ROOT` /
  `CLAUDE_PLUGIN_DATA` / `CLAUDE_PROJECT_DIR` names appear as constants;
  resolution algorithm is host-neutral. Phase 0 can widen this to a host-keyed
  lookup without touching callers.
- `commands/*.md`, `skills/*/SKILL.md`, `agents/*.md`, `.claude-plugin/` —
  packaging surface; PORTABILITY_REVIEW.md assigns per-host generation to a
  later phase. Out of Phase 0 scope except that `agents/_roster.json` stays the
  roster source-of-truth.

## 2. Proposed Host Adapter layer — abstract Python interfaces

Placement: new stdlib-only package `bin/_host/` (peer of `bin/_hooks/`),
following the repo's lazy-import + Protocol/dataclass idioms. Three port
interfaces + one shared vocabulary module. ABCs chosen over Protocols for the
ports themselves (adapters *opt in* and inherit fail-open helpers); frozen
dataclasses for the boundary values.

### 2.A Shared vocabulary — `bin/_host/events.py`

```python
"""Host-neutral hook vocabulary. NO host imports allowed in this module."""
from __future__ import annotations
import enum, pathlib
from dataclasses import dataclass, field
from typing import Any, Literal, Mapping


class HookEventKind(enum.Enum):
    PRE_TOOL_USE = "pre_tool_use"
    POST_TOOL_USE = "post_tool_use"
    SESSION_START = "session_start"
    SESSION_END = "session_end"
    USER_PROMPT_SUBMIT = "user_prompt_submit"   # emulated on hosts without it
    AGENT_STOP = "agent_stop"                   # CC "Stop"
    SUBAGENT_STOP = "subagent_stop"             # emulated on hosts without it


class ToolClass(enum.Enum):
    """Semantic tool classes the enforcement hooks reason about.

    Hosts map their native tool names onto these (CC: Edit|Write -> FILE_WRITE,
    Read -> FILE_READ, Bash -> SHELL, Task -> AGENT_SPAWN; Antigravity:
    write_to_file -> FILE_WRITE, run_command -> SHELL, ...).
    """
    FILE_WRITE = "file_write"
    FILE_READ = "file_read"
    SHELL = "shell"
    AGENT_SPAWN = "agent_spawn"
    OTHER = "other"


@dataclass(frozen=True)
class HookEvent:
    kind: HookEventKind
    session_id: str
    cwd: pathlib.Path
    tool_class: ToolClass | None = None      # None for lifecycle events
    tool_name: str = ""                      # host-native name, forensics only
    file_path: str | None = None             # normalized from tool args
    command: str | None = None               # normalized shell command line
    prompt: str | None = None                # USER_PROMPT_SUBMIT payload
    source: str = ""                         # SESSION_START source / END reason
    transcript_path: pathlib.Path | None = None
    raw: Mapping[str, Any] = field(default_factory=dict)  # verbatim host payload


@dataclass(frozen=True)
class HookDecision:
    verdict: Literal["allow", "deny", "warn"]   # "warn" reserved; current hooks emit deny only
    reason: str = ""
    context: str | None = None   # SESSION_START context injection, if any


@dataclass(frozen=True)
class HookOutcome:
    """What the trampoline script must do — fully host-rendered."""
    exit_code: int
    stdout: str = ""
    stderr: str = ""
```

### 2.B Port 1 — Hook Shim: `bin/_host/shim.py`

```python
class HostHookShim(abc.ABC):
    """Translates ONE host's hook dialect <-> the neutral vocabulary.

    Core hooks (sealed-paths, intent, suppression, ...) become pure
    functions HookEvent -> HookDecision and never see host JSON again.
    Fail-open discipline: parse errors yield an ALLOW outcome + hook-log
    row, matching today's exit-0 contract (security-dispatch.sh:29-31).
    """

    host: ClassVar[str]                       # "claude" | "codex" | "antigravity"

    @abc.abstractmethod
    def parse_event(self, stdin_payload: bytes,
                    env: Mapping[str, str]) -> HookEvent: ...

    @abc.abstractmethod
    def render_decision(self, decision: HookDecision,
                        event: HookEvent) -> HookOutcome:
        """CC/Codex deny -> exit 0 + permissionDecision/decision JSON;
        Antigravity deny -> exit 0 + {"allow_tool": false, ...}."""

    @classmethod
    def detect(cls, env: Mapping[str, str]) -> "HostHookShim":
        """Registry dispatch: CLAUDE_* env -> ClaudeHookShim (Phase 0's
        only registrant); unknown host -> ClaudeHookShim + warning row."""
```

One new dispatcher entry `bin/hook-entry <hook-name>` replaces the per-script
`python -m …` pipe: it calls `HostHookShim.detect(os.environ)`, parses the
event, routes to the named core hook function, renders the outcome. The 15
trampoline scripts shrink to `exec bin/hook-entry <name>`; per-host
registration files (`hooks/hooks.json`, later `.codex/hooks.json`,
`.agents/hooks.json`) all point at the same dispatcher.

### 2.C Port 2 — Transcript Provider: `bin/_host/transcript.py`

```python
@dataclass(frozen=True)
class FileTouch:
    path: str
    edits: int


@dataclass(frozen=True)
class SubagentRecord:
    subagent_id: str
    agent_type: str | None
    description: str | None
    last_activity_at: datetime.datetime | None
    tools_used_count: Mapping[str, int]
    files_touched: tuple[FileTouch, ...]


@dataclass(frozen=True)
class SessionFacts:
    """Everything hook_writer currently regex-scrapes, as one value object.
    Every field optional: hosts advertise what they can supply."""
    session_id: str
    custom_title: str | None = None
    git_branch: str | None = None
    workflow_stage: str | None = None        # last splock slash-command seen
    recent_prompts: tuple[str, ...] = ()
    todo_state: tuple[Mapping[str, str], ...] | None = None
    tools_used_count: Mapping[str, int] | None = None
    files_touched: tuple[FileTouch, ...] = ()
    live_status: Literal["idle", "busy", "closed"] | None = None


class TranscriptProvider(abc.ABC):
    """Read-only session-fact source for ONE host. Never raises on missing
    or malformed transcripts — degrade to empty SessionFacts (fail-open,
    per hook_writer's contract, hook_writer.py:12-13)."""

    host: ClassVar[str]

    @abc.abstractmethod
    def transcript_path(self, session_id: str,
                        cwd: pathlib.Path) -> pathlib.Path | None: ...

    @abc.abstractmethod
    def session_facts(self, session_id: str,
                      cwd: pathlib.Path) -> SessionFacts: ...

    @abc.abstractmethod
    def subagent_records(self, parent_session_id: str,
                         cwd: pathlib.Path) -> tuple[SubagentRecord, ...]: ...
```

Phase 0 extracts `_claude_project_dir`, `_read_tail`, the regex battery, and
`_live_status_for` out of `bin/_intent/hook_writer.py:41-281` into
`ClaudeTranscriptProvider`; `hook_writer` keeps only DB upserts, consuming
`SessionFacts`. Two design rules learned from the audit: (1) prefer facts
delivered *in the hook payload* over transcript scraping wherever a host
offers them (Antigravity ships `transcript_path` in-payload; a future
provider may fill `SessionFacts` without any file I/O); (2) the RDS columns
`claude_session_id` / `parent_claude_session_id` should be treated as
"host session id" going forward — schema rename is out of Phase 0 scope but
must be flagged to /plan.

### 2.D Port 3 — Model Transport: `bin/_host/transport.py`

Two capability tiers, matching how splock actually calls models:

```python
class ModelRole(enum.Enum):
    PLANNER = "planner"; QA = "qa"; CODER = "coder"
    REVIEWER = "reviewer"; VERIFIER = "verifier"


@dataclass(frozen=True)
class ModelPin:
    model_id: str
    fixed: bool = False        # True => env overrides are IGNORED (verifier)
    override_env: str | None = None   # e.g. OVERNIGHT_CHAIN_PLANNER_MODEL


class ModelCatalog(abc.ABC):
    """Role -> pin mapping for one model family. Encodes the verifier-
    independence invariant (agents/verifier.md:5, DESIGN.md §4) in code so
    every host family must state a fixed verifier pin."""
    @abc.abstractmethod
    def resolve(self, role: ModelRole) -> ModelPin: ...


@dataclass(frozen=True)
class CompletionRequest:
    role: ModelRole
    prompt: str
    system: str | None = None
    schema: Mapping[str, Any] | None = None   # constrained emission when set


@dataclass(frozen=True)
class CompletionResult:
    outcome: Literal["ok", "schema_exhausted", "error"]
    text: str = ""
    structured: Mapping[str, Any] | None = None
    resolved_model: str = ""                  # concrete id, not alias
    cost_usd: float = 0.0
    error_detail: str = ""


@dataclass(frozen=True)
class AgentSpawnSpec:
    """Agentic-session tier: the retry loop's coder/reviewer/verifier."""
    role: ModelRole
    prompt: str
    agent_definition: Mapping[str, Any]       # from agents/*.md frontmatter
    cwd: pathlib.Path
    env: Mapping[str, str] = field(default_factory=dict)
    schema: Mapping[str, Any] | None = None   # rubric binding (rubric_kind-resolved)
    allowed_tools: tuple[str, ...] | None = None


class ModelTransport(abc.ABC):
    """ONE host family's model-call surface. Owns its schema dialect and
    its auth policy (e.g. Claude's metered-key stripping,
    _sdk_bridge.py:286-306, stays inside ClaudeTransport)."""

    host: ClassVar[str]
    catalog: ModelCatalog

    @abc.abstractmethod
    def complete(self, request: CompletionRequest) -> CompletionResult: ...

    @abc.abstractmethod
    def stream(self, request: CompletionRequest) -> ContextManager["CompletionStream"]:
        """Context manager with .text_stream + .get_final() — the qa shape."""

    @abc.abstractmethod
    def spawn_agent(self, spec: AgentSpawnSpec) -> CompletionResult: ...

    @abc.abstractmethod
    def sanitize_schema(self, schema: Mapping[str, Any]) -> Mapping[str, Any]:
        """Per-dialect: Claude strips $schema/$id (strip_schema_meta_keys);
        OpenAI needs additionalProperties:false + all-required; Gemini needs
        OpenAPI-subset projection. ALWAYS called at the transport boundary;
        shipped schemas stay fully 2020-12-declared."""
```

Adapter strategy: `ClaudeTransport` *wraps* the proven code rather than
replacing it — `complete`/`stream` delegate to `_sdk_bridge.SubscriptionClient`
(its `_AdaptedMessage` maps 1:1 onto `CompletionResult`, including
`subtype == "error_max_structured_output_retries"` → `schema_exhausted`,
_sdk_bridge.py:86, 407-426); `spawn_agent` delegates to the existing
`sdk_spawners` functions. `two_call._default_client()` /
`_qa.invoke._default_client()` switch to returning the transport (or a thin
`.messages`-shaped view of it, keeping their extraction helpers byte-stable);
`phase_spawn.py:385-441` builds its injected `spawn_*_fn` callables from
`transport.spawn_agent`. The existing Protocols (`AnthropicClient`,
`ClaudeAgentSDKClient`) stay as internal typing of the Claude adapter.

## 3. Phase 0 work items (seam hardening only — no new hosts)

1. Create `bin/_host/` — `events.py`, `shim.py` (+ `ClaudeHookShim`),
   `transcript.py` (+ `ClaudeTranscriptProvider`), `transport.py`
   (+ `ClaudeTransport`, `ClaudeModelCatalog`), `registry.py` (host detection).
2. Add `bin/hook-entry` dispatcher; convert the trampoline scripts to it one
   hook at a time (security-dispatch first — it already centralizes four
   checks and its Python side, `bin._hooks.security_dispatch`, becomes the
   first `HookEvent -> HookDecision` consumer).
3. Extract Claude transcript scraping from `bin/_intent/hook_writer.py` into
   `ClaudeTranscriptProvider`; hook_writer consumes `SessionFacts`.
4. Route planner/qa `_default_client()` and `phase_spawn` DI through
   `ClaudeTransport`; move `strip_schema_meta_keys` invocation behind
   `sanitize_schema`.
5. Move model pins into `ClaudeModelCatalog` (fixed verifier pin asserted in
   code, not only frontmatter); existing `OVERNIGHT_*` env overrides preserved
   via `ModelPin.override_env`.
6. Conformance suite `tests/host_conformance/`: golden stdin payloads per
   dialect → expected `HookEvent`; `HookDecision` → expected `HookOutcome`
   per host; schema-sanitizer round-trips against every `schemas/*.json`;
   deny-parity test proving `bin/hook-entry` reproduces today's stdout
   byte-for-byte on the CC dialect (regression net for the cutover).
7. Zero behavior change on Claude Code; every step lands with CI green.

## 4. Gaps for /qa and /research follow-up

- G1 (research): empirical Codex hook payload field names/casing vs the docs
  (`tool_input`? `toolCall.args`?) — PORTABILITY_REVIEW.md's table is
  docs-derived, not verified against a live `~/.codex` install.
- G2 (research): can Antigravity `PreInvocation` deliver SessionStart-style
  context injection, and what replaces `SubagentStop` accounting there?
- G3 (qa): is `HookOutcome`'s exit-0-always assumption safe on Codex, where
  exit 2 is a documented deny path? (Proposed: render exit 0 + JSON on all
  hosts; treat exit codes as never-used.)
- G4 (qa): `claude plugin validate` gate (sdk_spawners.py:545) — what is the
  host-neutral seam? Likely a `HostTooling.validate_packaging()` fourth port,
  deferred.
- G5 (qa): RDS columns `claude_session_id`/`parent_claude_session_id` — rename
  vs alias vs add `host` column; migration owned by which phase?
- G6 (research): whether Codex/Antigravity expose subagent-level transcripts
  at all (SubagentRecord may be Claude-only; interface already tolerates an
  empty tuple).
- G7 (qa): `hooks/permissions.deny` settings-backstop — Phase 0 keeps it
  CC-only; confirm the sealed-path threat model tolerates hook-bypass gaps on
  Codex until its adapter phase adds the sandbox backstop.

## Recommendations for /plan

1. Adopt the three ports + shared vocabulary of §2 as Phase 0's deliverable
   surface; sequence work items as §3 (1→7), with item 6's deny-parity test
   as the acceptance gate for item 2's cutover.
2. Keep `SubscriptionClient`, `sdk_spawners`, and both `_default_client()`
   seams alive as the Claude adapter's internals — wrap, don't rewrite; the
   plan should forbid touching `iteration_loop.py` (already host-agnostic via
   DI, iteration_loop.py:151-153).
3. Encode the verifier-independence invariant as a `ModelCatalog` contract
   test (every registered family must return `fixed=True` for
   `ModelRole.VERIFIER`) so the invariant survives hosts whose agent
   frontmatter cannot pin models.
4. Defer packaging generation (commands/skills/agents → per-host artifacts)
   and the G4 tooling port to Phase 1; defer the G5 schema migration to its
   own slug — both are outside seam hardening.
5. Thread gaps G1-G3 into `/research portability_scaffolding` before
   `/implplan`, since HookEvent field normalization choices depend on them.
