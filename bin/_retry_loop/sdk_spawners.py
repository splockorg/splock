"""SDK-backed spawners for the §F.3 test-step retry loop.

Per verifier_sdk_wiring plan §T1 — module skeleton; T2 fills in the live
SDK contract comment block, T3-T6 land the concrete spawners:

- T3: ``run_verify_subprocess`` (pytest direct, no bin/verify recursion)
- T4: ``spawn_reviewer_via_sdk`` + ``ReviewerEmissionExhausted``
- T5: ``spawn_opus_via_sdk`` (real Opus coder spawn with git-diff capture)
- T6: ``smoke_check_sdk_available`` (pre-flight)

These spawners are injected by
``bin._chain_overnight.phase_spawn.spawn_retry_loop_phase`` into
``bin._retry_loop.iteration_loop.run_test_step_loop`` so the retry loop
drives **real** Opus + pytest + Sonnet from inside the chain driver
process (Option A: in-process, not subprocess-per-iteration).

Today (T1) this module only exposes:

- ``SDK_REQUEST_TIMEOUT_S`` — call timeout constant, mirroring
  ``bin/_planner/two_call.SDK_REQUEST_TIMEOUT_S`` so plan-time SDK pacing
  matches retry-loop pacing.
- ``ClaudeAgentSDKClient`` — typing.Protocol documenting the call
  surface T3/T4/T5 will exercise. Type-hint shape only; not
  runtime-checkable. Tests assert the surface is introspectable via
  ``typing.get_type_hints``.

Lazy-import discipline
----------------------

``claude_agent_sdk`` is **not** imported at module load. Imports are
deferred to call sites (mirrors the precedent at
``bin/_planner/two_call._default_client``) so:

- This module imports cleanly in environments where the SDK is not yet
  installed (developer machines mid-rebase, CI lanes that don't exercise
  the chain driver, etc.).
- The T6 smoke-check can probe SDK availability without paying an
  import cost on every invocation of unrelated retry-loop entry points.

Tests enforce the lazy-import discipline by patching
``sys.modules['claude_agent_sdk'] = None`` BEFORE importing this module
and asserting the import succeeds.

======================================================================
Verified claude-agent-sdk v0.2.87 contract (T2, introspected 2026-05-23)
======================================================================

All claims below were verified by direct ``inspect``/``dataclasses``
introspection against the installed package at
``<venv>/lib/python3.10/site-packages/claude_agent_sdk/`` (version
``0.2.87`` per ``claude_agent_sdk.__version__``). File:line citations
point at that site-packages tree.

1. Entry point — flat module-level ``query`` async generator
----------------------------------------------------------------------

``claude_agent_sdk.query`` (``query.py:1-117``) is a flat module-level
async-generator function (``inspect.isasyncgenfunction(query) is True``,
``iscoroutinefunction(query) is False``). It is **not** a method on a
client class. Signature::

    async def query(
        *,
        prompt: str | AsyncIterable[dict[str, Any]],
        options: ClaudeAgentOptions | None = None,
        transport: Transport | None = None,
    ) -> AsyncIterator[Message]

T3/T4/T5 call it as::

    import claude_agent_sdk
    async for message in claude_agent_sdk.query(
        prompt=briefing_text,
        options=opts,
    ):
        ...

There is also ``ClaudeSDKClient`` (``client.py``) for stateful
bidirectional sessions (``connect`` / ``query`` / ``receive_response`` /
``interrupt`` / ``set_model``). The retry-loop spawners do NOT use it —
per-iteration spawns are stateless and the ``query`` async generator is
the right primitive.

Confirmed recon §4 named the right symbol but called it a coroutine; it
is actually an async-generator function (yields, not returns).

2. Options shape — ``ClaudeAgentOptions`` dataclass
----------------------------------------------------------------------

``claude_agent_sdk.ClaudeAgentOptions`` (``types.py:1565`` onward,
``@dataclass``). 45 fields total; the load-bearing ones for the
retry-loop spawners:

- ``system_prompt: str | SystemPromptPreset | SystemPromptFile | None``
  (``types.py:1605``). Plain ``str`` overrides the system prompt
  entirely. To load ``.claude/agents/coder.md`` we pass its rendered
  content as a ``str`` here. ``{"type": "preset", "preset":
  "claude_code"}`` would inherit Claude Code's default with optional
  ``append``; ``{"type": "file", "path": ...}`` would read a file.

- ``model: str | None`` (``types.py:1673-1677``). Accepts a CLI model
  alias (``"sonnet"`` / ``"opus"`` / ``"haiku"`` / ``"inherit"``) or a
  full model ID (e.g. ``"claude-sonnet-4-5"``, ``"claude-opus-4-5"``).
  The coder spawn passes ``"opus"`` (or ``"inherit"``); the reviewer
  spawn passes ``"sonnet"``.

- ``cwd: str | Path | None`` (``types.py:1699-1700``). Working directory
  for the spawned subprocess. Retry-loop spawners pass the repo root so
  pytest / git diff resolve correctly. Also sets ``PWD`` in the child
  env (``subprocess_cli.py:469``).

- ``env: dict[str, str]`` (``types.py:1722-1727``, ``field(
  default_factory=dict)``). Merged on top of the inherited process env
  (``subprocess_cli.py:431-436``). The actual merge order is:

  1. ``**inherited_env`` — process env with ``CLAUDECODE`` stripped
     (``subprocess_cli.py:430``; filtered so SDK-spawned subprocesses
     don't think they're running inside a Claude Code parent).
  2. ``"CLAUDE_CODE_ENTRYPOINT": "sdk-py"`` — set BEFORE ``options.env``
     (``subprocess_cli.py:433``). The SDK's own inline comment at
     ``subprocess_cli.py:425-427`` explicitly says ``options.env`` can
     override it. So ``CLAUDE_CODE_ENTRYPOINT`` is **overridable** via
     ``options.env``.
  3. ``**self._options.env`` — our env merges on top
     (``subprocess_cli.py:434``).
  4. ``"CLAUDE_AGENT_SDK_VERSION": __version__`` — set AFTER our env
     (``subprocess_cli.py:435``); this one is truly **un-overridable**.

  Practical T5/T8 hook-env-var implication: ``SPLOCK_PLAN_SLUG`` /
  ``SPLOCK_CHAIN_ID`` / ``SPLOCK_PHASE`` are operator-controlled names the
  SDK does NOT set itself, so they pass through ``options.env`` cleanly
  with zero collision risk against the two SDK-managed keys above.

- ``output_format: dict[str, Any] | None`` (``types.py:1889-1895``).
  Matches the Messages API structured-output shape, e.g.
  ``{"type": "json_schema", "schema": {"type": "object", "properties":
  {...}}}``. Used by the reviewer spawn to bind R1-R5 emission. When
  set, the structured payload is returned on
  ``ResultMessage.structured_output``.

- ``agents: dict[str, AgentDefinition] | None`` (``types.py:1794``).
  Maps an agent name to an ``AgentDefinition`` dataclass (``types.py:
  82-101``) with fields ``description``, ``prompt``, ``tools``,
  ``model``, ``permissionMode``, ``maxTurns``, ``effort``, etc. Lets a
  spawn pre-register sub-agents invokable via the Agent tool.

- ``permission_mode: PermissionMode | None`` (``types.py:1629``).
  ``Literal["default","acceptEdits","plan","bypassPermissions",
  "dontAsk","auto"]``. The chain-driver coder spawn typically wants
  ``"bypassPermissions"`` (matches how the human-driven loop runs);
  the reviewer (read-only) can use ``"default"`` or ``"dontAsk"``.

- ``hooks: dict[HookEvent, list[HookMatcher]] | None`` (``types.py:
  1760``). HookMatcher callbacks running in the SDK process — these
  are NOT the same surface as ``.claude/settings.json`` hooks, which
  the CLI subprocess loads itself via ``setting_sources``.

- ``setting_sources: list[Literal["user","project","local"]] | None``
  (``types.py:1800-1810``). When None (default), all are loaded.
  Pass ``[]`` to disable filesystem settings entirely. Must include
  ``"project"`` for CLAUDE.md files to load. T3-T5 use the default
  (None) so ``.claude/settings.json`` hooks fire on the spawned
  subprocess.

- ``max_turns: int | None`` (``types.py:1653``). Stops the query after
  N turns. Retry-loop spawns are single-turn so we leave it None.

- ``max_budget_usd: float | None`` (``types.py:1659``). Stops with an
  ``error_max_budget_usd`` result if exceeded.

- ``stderr: Callable[[str], None] | None`` (``types.py:1742``).
  Per-line callback for the CLI subprocess stderr. Useful for piping
  Sonnet/Opus stderr into our overnight log.

3. Streaming response shape — discriminated union ``Message``
----------------------------------------------------------------------

The iterator yields ``Message`` (``types.py``, ``UnionType``) — the
union of ``UserMessage``, ``AssistantMessage``, ``SystemMessage``,
``ResultMessage``, ``StreamEvent``, ``RateLimitEvent``. All are
``@dataclass``. Load-bearing fields:

- ``AssistantMessage`` (``types.py:~1095``): ``content:
  list[ContentBlock]`` (TextBlock / ThinkingBlock / ToolUseBlock /
  ToolResultBlock / ServerToolUseBlock / ServerToolResultBlock),
  ``model: str``, ``usage: dict | None``, ``stop_reason: str | None``,
  ``session_id: str | None``, ``error: Literal["authentication_failed",
  "billing_error","rate_limit","invalid_request","server_error",
  "unknown"] | None``, ``message_id``, ``parent_tool_use_id``, ``uuid``.

  T5 (coder) collects ``TextBlock.text`` from each AssistantMessage's
  content list to assemble the final coder narrative if the SDK doesn't
  also surface it on ``ResultMessage.result``.

- ``ResultMessage`` (``types.py:1144-1167``) — **the terminal
  message**. Required: ``subtype: str``, ``duration_ms: int``,
  ``duration_api_ms: int``, ``is_error: bool``, ``num_turns: int``,
  ``session_id: str``. Optional: ``stop_reason``, ``total_cost_usd:
  float | None``, ``usage: dict | None``, ``result: str | None`` (the
  final text answer when the run completed cleanly),
  ``structured_output: Any`` (populated when ``options.output_format``
  was set — this is where the reviewer reads the R1-R5 JSON),
  ``model_usage: dict | None``, ``permission_denials: list | None``,
  ``deferred_tool_use: DeferredToolUse | None``, ``errors: list[str] |
  None``, ``api_error_status: int | None`` (HTTP status code on
  ``is_error=True``, populated since CLI v2.1.110 — safe to log),
  ``uuid``.

  Confirmed recon §4 named ``total_cost_usd`` correctly. Recon also
  guessed at ``subtype == "error_max_structured_output_retries"`` —
  the real CLI vocabulary is broader. Observed subtype values from
  the source (not exhaustive — the CLI evolves these): ``"success"``,
  ``"error_max_turns"``, ``"error_during_execution"``,
  ``"error_max_budget_usd"``. T4 should treat ``is_error == True`` as
  the universal "did not complete" signal and inspect ``subtype`` for
  the categorical reason; the structured_output exhaustion subtype
  may or may not match the recon name and should be discovered
  empirically by the first reviewer call that exhausts retries.

- ``SystemMessage`` (``types.py:1042-...``): ``subtype: str``, ``data:
  dict[str, Any]``. Lifecycle / init / hook-event messages. Two
  subclassed variants exist with their own dataclasses (e.g.
  ``HookEventMessage``, ``TaskStartedMessage``,
  ``TaskProgressMessage``, ``TaskNotificationMessage``,
  ``MirrorErrorMessage``) — pattern-match on the concrete class for
  type-specific fields, fall back to ``SystemMessage`` for the rest.

- ``StreamEvent`` (``types.py``): partial-streaming events when
  ``include_partial_messages=True``. The retry-loop spawners leave
  partial streaming off, so these should not appear.

- ``RateLimitEvent`` (``types.py``): ``rate_limit_info: RateLimitInfo``
  plus session/uuid. Emitted when the SDK observes a 429.

- ``UserMessage`` (``types.py``): echo of the user-side input. Not
  load-bearing for the spawners but appears in the stream.

4. Cost field — ``ResultMessage.total_cost_usd``
----------------------------------------------------------------------

``ResultMessage.total_cost_usd: float | None`` (``types.py:1155``).
Confirmed exactly as recon §4 named. Per-call cost the retry-loop
tracks against the §G chain budget. May be ``None`` for free/cached
turns or local-tool-only runs.

5. Error surface — exception classes + ``is_error`` sentinel
----------------------------------------------------------------------

Two failure shapes:

- **Hard transport / process / connection failures** raise an
  exception out of the ``async for`` loop. All inherit from
  ``ClaudeSDKError`` (base): ``CLIConnectionError`` (cannot reach the
  CLI), ``CLINotFoundError`` (``CLIConnectionError`` subclass; CLI
  binary not on PATH or at ``options.cli_path``), ``CLIJSONDecodeError``
  (CLI stdout was not valid JSON), ``ProcessError`` (CLI exited
  non-zero). MRO confirmed by ``inspect``.

  The SDK rewrites ``ProcessError`` when the CLI emitted a structured
  ``is_error=True`` result before exiting non-zero
  (``_internal/query.py:334-353``) — the exception text carries the
  structured error context, not just ``"exit code 1"``.

- **Soft errors** arrive as a ``ResultMessage`` with ``is_error=True``
  and a categorical ``subtype`` (see §3 above). T3-T5 must check
  ``is_error`` on the terminal message before treating the run as
  successful; tampering-flag fires belong here.

T6 smoke-check imports ``claude_agent_sdk``, instantiates a default
``ClaudeAgentOptions``, and catches ``CLINotFoundError`` /
``ClaudeSDKError`` to surface install failures up front.

6. Auth model — operator's local ``claude`` CLI subscription
----------------------------------------------------------------------

The SDK does NOT make HTTP calls itself. It shells out to the
``claude`` CLI binary (Claude Code v2.0.0+, asserted at
``subprocess_cli.py:31``). ``ClaudeAgentOptions.cli_path`` overrides
the binary location; otherwise the SDK uses ``shutil.which("claude")``
then probes a fixed list of fallback paths (``subprocess_cli.py:89-
98``) including ``~/.npm-global/bin/claude``, ``/usr/local/bin/claude``,
``~/.local/bin/claude``, ``~/node_modules/.bin/claude``,
``~/.yarn/bin/claude``, ``~/.claude/local/claude``. Missing binary
raises ``CLINotFoundError`` with an install hint
(``subprocess_cli.py:107-111``).

Auth is whatever ``claude`` CLI is logged in as — the operator's
subscription. No ``ANTHROPIC_API_KEY`` is required by the SDK or
mentioned anywhere in transport sources (grep ``ANTHROPIC_API_KEY``
under ``_internal/transport/``: zero hits). Confirmed recon §15.9's
hedge resolves to subscription auth, not API key.

This is the right model for the chain driver: spawned coder/reviewer
runs bill against the operator's Claude Code subscription, no
separate API-key plumbing needed.

7. Stop conditions
----------------------------------------------------------------------

The async iterator closes naturally when the CLI subprocess exits.
``ResultMessage`` is the documented terminal message (``ResultMessage``
docstring: "Result message with cost and usage information"). After a
``ResultMessage`` arrives, the next iteration of the ``async for``
loop will exit cleanly (or raise — see §5). Callers should:

1. Collect AssistantMessage content blocks as they stream.
2. Capture the ``ResultMessage`` (typically the last yielded value)
   for cost / is_error / structured_output / final result text.
3. Trust loop exit to mean the run is done; do not look for a
   separate sentinel beyond ``ResultMessage`` itself.

End verified contract block — anchor for T3-T6 implementation work.
"""

from __future__ import annotations

import asyncio
import importlib
import json
import os
import pathlib
import shlex
import subprocess
import sys
from typing import Any, AsyncIterator, Iterable, Protocol

from bin._verify_plan.strict import TYPED_GATE_COMMAND_PREFIX

from .rubric import TEST_STEP_RUBRIC_SCHEMA_V1


# ----------------------------------------------------------------------
# Module-level constants
# ----------------------------------------------------------------------

SDK_REQUEST_TIMEOUT_S = 1200
"""Per-call SDK request timeout (seconds).

Mirrors ``bin/_planner/two_call.SDK_REQUEST_TIMEOUT_S`` so the retry
loop's per-iteration SDK calls have the same generous ceiling as the
two-call planner. The constant exists so:

- Tests assert the value is pinned (defends against silent edits).
- T3/T4/T5/T6 reference the symbol rather than hard-coding 1200.
- A future tuning sweep can flip both call sites in lockstep by editing
  one constant in each module (deliberate duplication — neither module
  should depend on the other).

Rationale for 1200s: matches the two-call planner ceiling, which
bypasses the SDK 0.42+ non-streaming-timeout auto-calc guard
(``expected_time = 3600 * max_tokens / 128_000`` exceeding 600s for
max_tokens=32000 tripped the guard). The retry-loop spawners use
similar token budgets, so the same ceiling applies.
"""


# ----------------------------------------------------------------------
# SDK client Protocol — for DI / type-hinting
# ----------------------------------------------------------------------

class ClaudeAgentSDKClient(Protocol):
    """typing.Protocol covering the ``claude_agent_sdk`` surface T3-T5 use.

    Documents — for type-hint and DI purposes — the slice of the
    ``claude_agent_sdk`` 0.2.87 module-level API that the test-step
    retry-loop spawners depend on:

    - ``query(prompt, options)`` async generator yielding ``Message``
      objects (the SDK's ``ResultMessage`` is the terminal one carrying
      ``total_cost_usd`` and ``subtype``).
    - ``options`` is a ``ClaudeAgentOptions``-shaped dataclass; the
      retry-loop spawners construct it with ``output_format``,
      ``env``, ``agents``, and ``permission_mode`` keys at minimum.

    This Protocol is NOT runtime-checkable
    (no ``@runtime_checkable`` decorator). Its purpose is purely
    structural:

    1. T3-T5 functions accept ``client: ClaudeAgentSDKClient | None =
       None`` so tests can inject a fake recorder without touching the
       real ``claude_agent_sdk`` module.
    2. Static type-checkers see the surface and flag drift if a future
       SDK release changes the ``query`` signature in a load-bearing way.
    3. ``typing.get_type_hints(ClaudeAgentSDKClient)`` exposes the
       annotations so the T1 test ``test_protocol_surface_documented``
       can assert the surface is introspectable.

    Verified in T2 (2026-05-23) against ``claude_agent_sdk`` 0.2.87:
    the real ``claude_agent_sdk.query`` is a module-level
    **async-generator function** (``inspect.isasyncgenfunction(query)``
    returns True; ``iscoroutinefunction`` returns False). Callers invoke
    it as ``async for msg in claude_agent_sdk.query(prompt=..., options=
    ...): ...``. The Protocol method below documents the call shape; a
    plain ``async def`` returning ``AsyncIterator[Any]`` is structurally
    compatible with the real SDK's async-generator function for the
    purposes T3-T5 use it (they iterate via ``async for``).

    ``ClaudeSDKClient`` (``claude_agent_sdk.client``) is a separate,
    stateful, bidirectional client surface — the retry-loop spawners do
    NOT use it; per-iteration spawns want the flat stateless
    ``query`` primitive instead.
    """

    async def query(
        self,
        *,
        prompt: str,
        options: Any = None,
    ) -> AsyncIterator[Any]:
        """Issue a one-shot query and stream messages back.

        Mirrors ``claude_agent_sdk.query`` from claude-agent-sdk 0.2.87.

        Parameters
        ----------
        prompt : str
            The user-side prompt to send. The retry-loop spawners build
            this from the deterministic briefing emitted by
            ``bin._retry_loop.briefing.build_briefing``.
        options : ClaudeAgentOptions | None
            Optional configuration object. Real call sites pass a
            ``claude_agent_sdk.ClaudeAgentOptions`` instance carrying
            (at least) ``output_format`` for structured-rubric binding,
            ``env`` for SPLOCK_PLAN_SLUG / SPLOCK_CHAIN_ID / SPLOCK_PHASE
            propagation, and ``agents`` / ``permission_mode`` for
            sub-agent dispatch.

        Returns
        -------
        AsyncIterator[Message]
            Sequence of SDK ``Message`` objects. The terminal
            ``ResultMessage`` carries ``total_cost_usd``,
            ``structured_output``, and ``subtype`` (the latter is
            ``error_max_structured_output_retries`` on retry exhaustion).
        """
        ...


# ----------------------------------------------------------------------
# T3 — run_verify_subprocess (pytest direct, no bin/verify recursion)
# ----------------------------------------------------------------------

#: Filename template for per-iteration pytest stdout capture under
#: ``docs/plans/<slug>/``. Matches the pattern emitted by the legacy
#: ``_default_run_verify`` in ``iteration_loop.py`` (which T9 will flip
#: to ``NotImplementedError``) and the manually-captured files visible
#: under ``docs/plans/_closed/ctm-graph-wiring/`` from yesterday's overnight run.
_TEST_OUTPUT_FILENAME_TEMPLATE = "_test_output_iter{n}.txt"

#: Default subprocess timeout (seconds) for the pytest invocation.
#: Picked to match the legacy ``_default_run_verify`` timeout in
#: ``bin/_retry_loop/iteration_loop.py`` so callers see equivalent
#: behaviour after T8 swaps the spawner.
_PYTEST_SUBPROCESS_TIMEOUT_S = 1800

#: Subprocess timeout for a typed gate command (mirrors the pytest run
#: ceiling — gate commands may do real work, e.g. ``claude plugin
#: validate .``).
_TYPED_GATE_COMMAND_TIMEOUT_S = _PYTEST_SUBPROCESS_TIMEOUT_S

COLLECT_TYPED_COMMAND = "typed_gate_command"
"""Entry starts with ``TYPED_GATE_COMMAND_PREFIX`` (imported from
``bin._verify_plan.strict`` — single source). Bypasses pytest entirely;
no collect probe.

The prefix is RESERVED, recognition-only defense-in-depth.
`run_typed_gate_command` is an unwired utility — no gate verdict path
executes it. Plan authors must not author ``gate_cmd:`` entries; the
supported convention for non-pytest tasks is ``tests_enabled: []`` plus
the ``verification_kind:`` test_plan exemption marker."""


def _repo_root() -> pathlib.Path:
    """The ADOPTER project's root — where its tests actually live.

    Upstream this walked ``parents[2]`` off ``__file__``. Under an installed
    plugin that is the plugin cache, so the on-disk selector check below and
    pytest's ``cwd`` would both resolve against the wrong tree (fork finding
    F3; same adopter-root class as F2 / OI-1). ``project_root()``'s in-tree
    fallback is byte-identical to the old ``parents[2]``, so this is a strict
    superset of the previous behaviour.

    Kept as a module-level function (rather than inlining ``project_root``) so
    tests can monkeypatch it and the selector check and pytest ``cwd`` always
    agree.
    """
    from bin._env_paths import project_root

    return project_root()


def read_tests_enabled_union(orchestrator_path: pathlib.Path) -> list[str]:
    """Deduplicated, sorted union of ``tasks[*].tests_enabled`` strings.

    Single source of truth for "what the retry loop grades against",
    shared by `run_verify_subprocess` and the `main._run_test_step`
    pre-flight guard so the two never disagree on the candidate set.
    """
    payload = json.loads(orchestrator_path.read_text(encoding="utf-8"))
    union: set[str] = set()
    for task in payload.get("tasks", []) or []:
        for test_id in task.get("tests_enabled", []) or []:
            if isinstance(test_id, str) and test_id:
                union.add(test_id)
    return sorted(union)


def is_runnable_pytest_selector(
    selector: str, repo_root: pathlib.Path | None = None
) -> bool:
    """True iff ``selector`` is a pytest node-ID/path that exists on disk.

    A runnable selector's path component (the part before any ``::``)
    must (a) be non-empty, (b) contain no whitespace — pytest node-ID
    *paths* never do, whereas design-prose entries like ``"CLI-version
    doc"`` or ``"claude plugin validate . clean"`` always do — and
    (c) resolve to a file or directory under ``repo_root`` (pytest's
    ``cwd``).

    This is the single guard that collapses two failure modes: a
    syntactically-valid node-ID for a file a not-yet-``done`` task hasn't
    created (→ pytest exits at collection), and a prose description splatted
    at pytest (→ same). A not-yet-authored file simply isn't on disk yet, and
    prose never looks like a path. Whitespace is checked only on the path
    component so parametrised node-IDs whose ``[param-id]`` contains spaces
    are not misclassified.
    """
    if repo_root is None:
        repo_root = _repo_root()
    path_part = selector.split("::", 1)[0].strip()
    if not path_part or any(ch.isspace() for ch in path_part):
        return False
    candidate = repo_root / path_part
    return candidate.is_file() or candidate.is_dir()


def partition_runnable_selectors(
    entries: Iterable[str], repo_root: pathlib.Path | None = None
) -> tuple[list[str], list[str]]:
    """Split ``entries`` into ``(runnable, skipped)`` pytest selectors.

    ``runnable`` preserves input order and feeds the pytest argv;
    ``skipped`` carries the non-selector / not-on-disk entries so callers
    can surface them in an operator-facing diagnostic instead of letting
    pytest exit 4 at collection (and burning the retry budget chasing an
    unfixable invocation). See `is_runnable_pytest_selector`.
    """
    if repo_root is None:
        repo_root = _repo_root()
    runnable: list[str] = []
    skipped: list[str] = []
    for entry in entries:
        target = runnable if is_runnable_pytest_selector(entry, repo_root) else skipped
        target.append(entry)
    return runnable, skipped


def run_typed_gate_command(
    entry: str,
    cwd: pathlib.Path | None = None,
    timeout_s: int = _TYPED_GATE_COMMAND_TIMEOUT_S,
) -> subprocess.CompletedProcess:
    """Run a typed gate command; exit-0 = pass (the Aider --test-cmd model).

    ``entry`` must start with ``TYPED_GATE_COMMAND_PREFIX`` (imported, not
    re-declared). The remainder is the command, split via ``shlex`` (no
    shell) and run from the repo root (or ``cwd``). The raw
    ``CompletedProcess`` is returned uncoerced — callers grade
    ``returncode == 0`` as pass, anything else as failure. NEVER piped
    through pytest.

    DELIBERATELY NOT WIRED into any gate verdict path. It is retained as a
    dormant, tested utility so the single-source prefix convention stays
    parked for a future slug that demonstrates real generalization demand.
    The supported convention for tasks with no pytest-expressible acceptance
    is ``tests_enabled: []`` + the ``verification_kind:`` test_plan marker
    (`bin._verify_plan.strict.VERIFICATION_KIND_MARKER_PREFIX`).
    """
    if not entry.startswith(TYPED_GATE_COMMAND_PREFIX):
        raise ValueError(
            f"not a typed gate command (missing "
            f"'{TYPED_GATE_COMMAND_PREFIX}' prefix): {entry!r}"
        )
    command = entry[len(TYPED_GATE_COMMAND_PREFIX):].strip()
    if not command:
        raise ValueError(f"typed gate command is empty: {entry!r}")
    if cwd is None:
        cwd = _repo_root()
    return subprocess.run(
        shlex.split(command),
        cwd=str(cwd),
        capture_output=True,
        text=True,
        timeout=timeout_s,
        check=False,
    )


def run_verify_subprocess(
    *,
    slug: str,
    plan_dir: pathlib.Path,
    orchestrator_path: pathlib.Path,
    iteration_n: int,
) -> subprocess.CompletedProcess:
    """Run pytest directly against the orchestrator's ``tests_enabled`` set.

    Replaces the legacy ``bin/verify test-step`` subprocess so the retry
    loop never recurses back into ``bin/verify``: that wrapper would in
    turn shell out to pytest with logic that duplicates this function's
    semantics, and any future regression in the wrapper would silently
    poison every retry-loop iteration. Per verifier_sdk_wiring §T3.

    Behaviour
    ---------

    1. Read the orchestrator JSON at ``orchestrator_path``.
    2. Compute the **union** of ``tasks[*].tests_enabled`` across all
       tasks (deduplicated, sorted for stable argv). This is the full
       set the retry loop is grading against — not just the active
       task's slice — because the chain driver runs phases composed of
       multiple tasks and the failure surface for any of them counts.
    3. Raise ``ValueError`` if the union is empty (task contract has
       nothing to verify — fail loudly before shelling out).
    4. Build a pytest argv of the form
       ``[sys.executable, "-m", "pytest", <test_ids...>, "-v"]``.
       NEVER ``bin/verify``: that recursion is precisely what T3 exists
       to prevent.
    5. Run via ``subprocess.run`` with ``cwd`` pinned to the repo root
       (so relative test paths resolve) and ``capture_output=True``.
    6. Persist captured stdout (with a stderr appendix matching the
       legacy ``_default_run_verify`` shape) to
       ``plan_dir / _test_output_iter{n}.txt`` so the briefing builder
       can read it on the next iteration.
    7. Return the ``CompletedProcess`` **as-is**. The return code is
       NOT coerced: pytest exit code 5 (no tests collected) must
       surface intact so callers can distinguish "ran but found
       nothing" from "ran and failed" (exit 1).

    Parameters
    ----------
    slug : str
        Plan slug (e.g. ``"verifier_sdk_wiring"``). Used only in the
        ``ValueError`` message so the operator can grep the offending
        plan from the overnight log.
    plan_dir : pathlib.Path
        ``docs/plans/<slug>/`` — used as the target directory for the
        per-iteration stdout capture file.
    orchestrator_path : pathlib.Path
        ``docs/plans/<slug>/<slug>_orchestrator.json`` — the source of
        the ``tests_enabled`` union.
    iteration_n : int
        1-based iteration index. Drives the output filename suffix
        (``_test_output_iter{n}.txt``).

    Returns
    -------
    subprocess.CompletedProcess
        The raw ``CompletedProcess`` from ``subprocess.run`` — return
        code preserved, stdout/stderr captured as text.

    Raises
    ------
    ValueError
        If the union of ``tests_enabled`` across all orchestrator tasks
        is empty.
    """
    payload = json.loads(orchestrator_path.read_text(encoding="utf-8"))

    union: set[str] = set()
    for task in payload.get("tasks", []) or []:
        for test_id in task.get("tests_enabled", []) or []:
            if isinstance(test_id, str) and test_id:
                union.add(test_id)

    if not union:
        raise ValueError(
            f"no tests_enabled across orchestrator tasks for slug={slug!r} "
            f"(orchestrator_path={orchestrator_path!s})"
        )

    test_ids = sorted(union)

    # Resolve the ADOPTER's repo root + interpreter, not the plugin's. When
    # splock runs as an installed plugin, __file__/parents[2] is the plugin
    # tree and sys.executable is the plugin venv — neither can see nor import
    # the adopter's tests. Honour $CLAUDE_PROJECT_DIR + the adopter venv so
    # the retry loop grades the RIGHT suite (fork finding F3; same
    # adopter-root class as OI-1 / F2).
    from bin._env_paths import project_root as _project_root

    repo_root = _project_root()
    interpreter = os.environ.get("SPLOCK_TEST_PYTHON") or ""
    if not interpreter:
        adopter_py = repo_root / ".venv" / "bin" / "python"
        interpreter = str(adopter_py) if adopter_py.exists() else sys.executable

    # tests_enabled are bare pytest node NAMES, not paths. Pass them as a
    # `-k` selection expression — bare positional args are treated as file
    # paths and collect nothing (the original bug).
    selection = " or ".join(test_ids)
    argv = [interpreter, "-m", "pytest", "-k", selection, "-v"]

    result = subprocess.run(
        argv,
        cwd=str(repo_root),
        capture_output=True,
        text=True,
        timeout=_PYTEST_SUBPROCESS_TIMEOUT_S,
        check=False,
    )

    output_path = plan_dir / _TEST_OUTPUT_FILENAME_TEMPLATE.format(n=iteration_n)
    try:
        output_path.write_text(
            (result.stdout or "")
            + "\n--- STDERR ---\n"
            + (result.stderr or ""),
            encoding="utf-8",
        )
    except OSError:
        # Capture is best-effort; the retry loop's briefing builder
        # tolerates a missing file and the returned CompletedProcess
        # still carries stdout/stderr in-memory for the caller. Match
        # the legacy ``_default_run_verify`` swallow-on-OSError shape.
        pass

    return result


# ----------------------------------------------------------------------
# T4 — spawn_reviewer_via_sdk + ReviewerEmissionExhausted
# ----------------------------------------------------------------------

#: Default Sonnet model alias used when ``OVERNIGHT_SONNET_REVIEW_MODEL``
#: is not set. The SDK accepts either the short alias (``"sonnet"``) or
#: the dated model ID (``"claude-sonnet-4-6-20260101"``); we pass the
#: alias so the SDK / CLI binary picks the current Sonnet 4.6 build per
#: the operator's local subscription. The env var overrides this.
_DEFAULT_REVIEWER_MODEL = "sonnet"

#: Name under which the reviewer ``AgentDefinition`` is registered in
#: ``ClaudeAgentOptions.agents``. The reviewer subagent on disk lives at
#: ``.claude/agents/reviewer.md`` per recon §15.11; this key matches the
#: file's basename so the SDK's AgentDefinition override semantics line
#: up cleanly with the on-disk source-of-truth.
_REVIEWER_AGENT_NAME = "reviewer"

#: Path (repo-relative) to the reviewer subagent definition. Loaded as
#: the ``AgentDefinition.prompt`` body when the file exists; otherwise a
#: minimal inline fallback prompt is used so the SDK call still has a
#: system prompt anchored on the rubric contract.
_REVIEWER_AGENT_FILE = pathlib.Path(".claude/agents/reviewer.md")


class ReviewerEmissionExhausted(Exception):
    """Raised when the SDK signals structured-output retry exhaustion.

    Per verifier_sdk_wiring §T4 + F4.c: parallels
    ``bin/_planner/two_call.PlannerEmissionExhausted``. The chain driver
    (T8) catches this and maps to exit code 16
    (``EXIT_PLANNER_EMISSION_EXHAUSTED``-equivalent in the chain-overnight
    registry).

    The SDK + CLI emit a categorical ``subtype`` on the terminal
    ``ResultMessage`` to flag this condition. The exact subtype string
    lives in the ``claude`` CLI binary (not the Python SDK source) and
    was empirically undiscoverable at code-time; T2's verified contract
    block §3 documents the SDK-side vocabulary (``success`` /
    ``error_max_turns`` / ``error_during_execution`` /
    ``error_max_budget_usd``) which is NOT exhaustive. We therefore
    match on:

      (a) the literal ``error_max_structured_output_retries`` (the name
          carried by the planner's PlannerEmissionExhausted precedent),
      (b) any ``is_error=True`` ResultMessage whose subtype string
          contains ``structured_output``,
      (c) any ``is_error=True`` ResultMessage whose subtype string
          contains ``retries``.

    Strategies (b) + (c) are forward-compatible — when the actual CLI
    subtype string is discovered empirically by the first reviewer call
    that exhausts retries, this exception still fires as long as the
    string contains the substring ``structured_output`` or ``retries``.

    Attributes
    ----------
    subtype : str
        The raw subtype string from the SDK ``ResultMessage``. Logged
        verbatim so the operator can grep the morning-review entry for
        the actual CLI vocabulary.
    cwd : str | None
        Working directory the reviewer ran in (repo root in practice).
    plan_slug : str | None
        Plan slug if the spawner could infer it from ``hook_env``
        (forensic context for the morning-review entry).
    """

    def __init__(
        self,
        *,
        subtype: str,
        cwd: str | None = None,
        plan_slug: str | None = None,
    ) -> None:
        super().__init__(
            f"reviewer SDK signalled structured-output retry exhaustion: "
            f"subtype={subtype!r} cwd={cwd!r} plan_slug={plan_slug!r}"
        )
        self.subtype = subtype
        self.cwd = cwd
        self.plan_slug = plan_slug


def _is_structured_output_retry_exhaustion(result_message: Any) -> bool:
    """Return True if the SDK signalled retry exhaustion.

    Pattern-matches on the ``ResultMessage.subtype`` string because the
    exact subtype label the ``claude`` CLI emits for this condition lives
    in the CLI binary (not the Python SDK source) and was empirically
    undiscoverable at code-time. Three match strategies:

    (a) explicit known fixture: ``'error_max_structured_output_retries'``
        (the name the test fixtures use and the
        PlannerEmissionExhausted precedent uses).
    (b) ``is_error=True`` sentinel + ``'structured_output'`` substring
        in subtype — forward-compat if the CLI uses a different but
        similar name (e.g., ``error_structured_output_failed``).
    (c) ``is_error=True`` sentinel + ``'retries'`` substring in subtype
        — second forward-compat hedge if the CLI uses a name like
        ``error_max_retries`` for the structured-output exhaustion case.

    Strategies (b) + (c) are the "this is not superstition" hedge per
    the T4 brief. They cost nothing: if the CLI ever lands on the exact
    fixture name we already match (a); if it lands on something
    different but semantically equivalent, we still match.
    """
    subtype = getattr(result_message, "subtype", None) or ""
    if subtype == "error_max_structured_output_retries":
        return True
    is_error = getattr(result_message, "is_error", False)
    if is_error and ("structured_output" in subtype or "retries" in subtype):
        return True
    return False


def _read_agent_prompt(
    cwd: pathlib.Path, rel: pathlib.Path
) -> str | None:
    """Load an agent prompt: project override first, plugin-shipped second.

    ``<cwd>/.claude/agents/<name>.md`` is the adopter-repo override (and the
    in-tree location for embedded installs). The plugin package ships its
    agent prompts at ``agents/<name>.md`` under the plugin root — without
    this second candidate an installed plugin silently used the terse inline
    fallback on every live run. Returns None when neither file is readable.
    """
    plugin_root = pathlib.Path(__file__).resolve().parents[2]
    candidates = (
        cwd / rel,
        plugin_root / "agents" / rel.name,
    )
    for candidate in candidates:
        if candidate.is_file():
            try:
                return candidate.read_text(encoding="utf-8")
            except OSError:
                continue
    return None


def _load_reviewer_system_prompt(cwd: pathlib.Path) -> str:
    """Read ``.claude/agents/reviewer.md`` if present; else fall back inline.

    Per recon §15.11: the reviewer subagent contract is documented at
    ``.claude/agents/reviewer.md``. We load its body as the
    ``AgentDefinition.prompt`` so the on-disk file is the source of
    truth. If the file is missing (operator running outside a chain-
    driver workspace, or the repo hasn't been initialized), fall back
    to a minimal inline prompt that still anchors the rubric contract.
    """
    loaded = _read_agent_prompt(cwd, _REVIEWER_AGENT_FILE)
    if loaded is not None:
        return loaded
    # Fallback inline prompt — keep terse; the rubric schema binding via
    # ClaudeAgentOptions.output_format does the heavy structural lifting.
    return (
        "You are the reviewer subagent for the §F.3 test-step retry "
        "loop. Emit a structured R1-R5 verdict against the bound "
        "TEST_STEP_RUBRIC_SCHEMA_V1 JSON schema. The chain driver has "
        "constructed the prompt deterministically from CLI output; do "
        "not paraphrase it. R4='yes-flagged' is load-bearing and "
        "triggers an immediate halt — only emit it when the iteration "
        "weakened test assertions (removed assertions, broadened "
        "acceptable inputs, added skips/xfails/sys.exits, etc.)."
    )


async def _drive_reviewer_async(
    *,
    prompt: str,
    options: Any,
    client: ClaudeAgentSDKClient | None,
) -> Any:
    """Async-iterate the SDK query stream and return the terminal ResultMessage.

    Lazy-imports ``claude_agent_sdk`` ONLY when ``client`` is None — the
    DI test path supplies a fake recorder that never touches the real
    module. The lazy-import discipline is enforced by T1's
    ``test_sdk_spawners_module_imports_without_sdk``.
    """
    final_result: Any = None
    if client is None:
        import claude_agent_sdk  # local — preserve lazy-import discipline

        stream = claude_agent_sdk.query(prompt=prompt, options=options)
    else:
        stream = client.query(prompt=prompt, options=options)
    async for msg in stream:
        # The terminal ResultMessage is the last yielded value. We
        # capture it explicitly rather than relying on loop-exit-final
        # because the SDK is documented to yield UserMessage /
        # AssistantMessage / SystemMessage / ResultMessage in that
        # order. Detect ResultMessage by class name to avoid an SDK
        # import on the fake-client test path.
        if type(msg).__name__ == "ResultMessage":
            final_result = msg
    return final_result


def spawn_reviewer_via_sdk(
    *,
    prompt: str,
    cwd: pathlib.Path | None = None,
    hook_env: dict[str, str] | None = None,
    timeout_s: int = SDK_REQUEST_TIMEOUT_S,
    client: ClaudeAgentSDKClient | None = None,
) -> dict[str, Any]:
    """Spawn a Sonnet reviewer via the claude-agent-sdk; return the rubric dict.

    Per verifier_sdk_wiring §T4. Replaces the legacy
    ``_default_spawn_reviewer`` stub in ``iteration_loop.py`` once T9
    flips that stub to ``NotImplementedError`` (this task does NOT touch
    iteration_loop — T8 wires the DI; T9 flips the default).

    Behaviour
    ---------

    1. Resolve the Sonnet model alias from ``OVERNIGHT_SONNET_REVIEW_MODEL``
       (env var), defaulting to ``"sonnet"`` (the SDK's current-Sonnet
       alias per the operator's CLI subscription).
    2. Construct an ``AgentDefinition`` for the ``reviewer`` agent name,
       carrying the resolved model + the system prompt loaded from
       ``.claude/agents/reviewer.md`` (or a minimal inline fallback).
    3. Build ``ClaudeAgentOptions`` with:

       - ``output_format = {"type": "json_schema", "schema":
         TEST_STEP_RUBRIC_SCHEMA_V1}`` — binds the SDK's structured-
         output retry layer to the rubric contract per §F.4.
       - ``agents = {"reviewer": <AgentDefinition>}`` — pre-registers
         the reviewer subagent.
       - ``model = <resolved Sonnet>`` — also pinned on the options
         level so the top-level SDK conversation runs against Sonnet
         (the agents= override picks up at agent-dispatch time).
       - ``cwd = <cwd>`` — pins working directory for the spawned CLI
         subprocess.
       - ``env = {**hook_env}`` — propagates ``SPLOCK_PLAN_SLUG`` /
         ``SPLOCK_CHAIN_ID`` / ``SPLOCK_PHASE`` so the hook stack fires on
         the spawned subprocess.

    4. Lazy-import ``claude_agent_sdk`` inside the async driver (NOT at
       module top — T1's lazy-import discipline holds).
    5. Async-iterate ``claude_agent_sdk.query(prompt, options)`` (or the
       injected ``client.query`` for tests). Capture the terminal
       ``ResultMessage``.
    6. If ``_is_structured_output_retry_exhaustion(result)`` →
       raise ``ReviewerEmissionExhausted``.
    7. If ``result.is_error`` (any other reason) → raise ``RuntimeError``
       with the subtype + raw error info so callers can distinguish
       "we got an error that ISN'T retry-exhaustion".
    8. Otherwise, parse ``result.structured_output`` (already a dict per
       the SDK contract when ``output_format`` is set). Inject
       ``'_sdk_cost_usd': result.total_cost_usd`` (underscore-prefixed
       to mark non-rubric; the iteration loop's R4 + version-check
       consumer ignores keys outside the schema). Return the dict.

    Parameters
    ----------
    prompt : str
        The deterministically-constructed review prompt from
        ``bin._retry_loop.briefing.build_briefing``. The reviewer's
        system prompt lives in the ``AgentDefinition``; this kwarg is
        the user-side prompt the SDK submits.
    cwd : pathlib.Path | None
        Working directory for the spawned CLI subprocess. Defaults to
        the repo root (resolved from this module's location) so tests
        running from arbitrary cwds still locate ``.claude/agents/``.
    hook_env : dict[str, str] | None
        Hook env vars to propagate (``SPLOCK_PLAN_SLUG`` / ``SPLOCK_CHAIN_ID``
        / ``SPLOCK_PHASE``). Merged into ``ClaudeAgentOptions.env``.
    timeout_s : int
        Per-call SDK request timeout (seconds). Defaults to
        ``SDK_REQUEST_TIMEOUT_S`` (1200s). NOT directly enforced here
        (the SDK's own non-streaming-timeout guard handles this); the
        kwarg exists so a future tuning sweep can pass per-call values.
    client : ClaudeAgentSDKClient | None
        DI seam for tests. When provided, the spawner drives
        ``client.query`` instead of ``claude_agent_sdk.query``; tests
        pass a ``_FakeSDKClient`` recorder that captures the
        constructed ``ClaudeAgentOptions``.

    Returns
    -------
    dict[str, Any]
        The parsed rubric dict from ``result.structured_output`` with
        an extra ``'_sdk_cost_usd'`` key carrying
        ``result.total_cost_usd`` (or ``0.0`` if None). Downstream
        ``iteration_loop.run_iteration`` reads ``rubric_version`` and
        ``R4_tampering``; the cost key is consumed by T7.

    Raises
    ------
    ReviewerEmissionExhausted
        If the SDK's structured-output retry layer is exhausted (per
        ``_is_structured_output_retry_exhaustion`` predicate).
    RuntimeError
        If the SDK returns a non-retry-exhaustion error (any other
        ``is_error=True`` subtype), or if no ResultMessage arrived
        before the stream closed.
    """
    # Resolve cwd default to the adopter-repo root: the reviewer session
    # reads project tests/diffs, and the agent-prompt lookup falls back to
    # the plugin-shipped agents/ dir via _read_agent_prompt.
    if cwd is None:
        from bin._env_paths import project_root

        cwd = project_root()
    cwd = pathlib.Path(cwd)

    # Resolve env vars.
    model = os.environ.get("OVERNIGHT_SONNET_REVIEW_MODEL", _DEFAULT_REVIEWER_MODEL)
    merged_env: dict[str, str] = {}
    if hook_env:
        merged_env.update({k: v for k, v in hook_env.items() if v is not None})

    plan_slug = merged_env.get("SPLOCK_PLAN_SLUG")

    # Build the AgentDefinition + ClaudeAgentOptions. Lazy-import the
    # SDK types at function-call time (NOT module top) per the §T1
    # lazy-import discipline so this module imports cleanly when
    # ``claude_agent_sdk`` is not installed. Both the DI test path
    # (which passes a fake ``client``) and the real-SDK path use the
    # same option types, so the import sits before the branch.
    from claude_agent_sdk import (  # local — preserve lazy-import
        AgentDefinition,
        ClaudeAgentOptions,
    )

    system_prompt = _load_reviewer_system_prompt(cwd)

    reviewer_agent_def = AgentDefinition(
        description=(
            "reviewer for constrained-rubric review of test-step retry "
            "iterations; emits structured-output R1-R5 verdicts from a "
            "deterministically-constructed prompt"
        ),
        prompt=system_prompt,
        model=model,
    )

    options = ClaudeAgentOptions(
        model=model,
        output_format={
            "type": "json_schema",
            "schema": TEST_STEP_RUBRIC_SCHEMA_V1,
        },
        agents={_REVIEWER_AGENT_NAME: reviewer_agent_def},
        cwd=str(cwd),
        env=merged_env,
    )

    result_message = asyncio.run(
        _drive_reviewer_async(
            prompt=prompt,
            options=options,
            client=client,
        )
    )

    if result_message is None:
        raise RuntimeError(
            "reviewer SDK stream closed without yielding a ResultMessage "
            f"(cwd={str(cwd)!r}, plan_slug={plan_slug!r}); the CLI "
            "subprocess may have exited abnormally without emitting a "
            "terminal message."
        )

    if _is_structured_output_retry_exhaustion(result_message):
        raise ReviewerEmissionExhausted(
            subtype=getattr(result_message, "subtype", "") or "",
            cwd=str(cwd),
            plan_slug=plan_slug,
        )

    if getattr(result_message, "is_error", False):
        # Non-retry-exhaustion error — surface raw context. T8 maps
        # ReviewerEmissionExhausted to exit 16; this RuntimeError path
        # is recovered by the retry-loop's normal failure semantics or
        # by the chain driver's outer error handler.
        raise RuntimeError(
            "reviewer SDK returned is_error=True with non-retry "
            f"subtype={getattr(result_message, 'subtype', '')!r} "
            f"(cwd={str(cwd)!r}, plan_slug={plan_slug!r})"
        )

    structured = _extract_structured_rubric(result_message)

    # Inject the SDK cost into the returned dict under an
    # underscore-prefixed key. The rubric schema's
    # ``additionalProperties: false`` excludes this key from validation
    # by convention (iteration_loop's consumer reads only the schema-
    # required keys + ``_sdk_cost_usd`` for T7's cost aggregation).
    cost = getattr(result_message, "total_cost_usd", None)
    structured["_sdk_cost_usd"] = float(cost) if cost is not None else 0.0
    return structured


def _extract_structured_rubric(result_message: Any) -> dict[str, Any]:
    """Pull the structured rubric dict from a ``ResultMessage``.

    Per Tier-1 follow-up fix (2026-05-25). The original implementation
    assumed ``ResultMessage.structured_output`` is populated when
    ``ClaudeAgentOptions.output_format`` is set — that's what the SDK's
    own ``types.py`` and dataclass-introspection suggested at recon time
    (2026-05-23). Empirically, with claude-agent-sdk 0.2.87 + Claude
    Code CLI v2.1.150, the CLI propagates the ``--json-schema`` flag
    correctly (constrained decoding works — the model output is
    guaranteed to conform to the schema) but ``structured_output`` is
    NEVER copied into the result-message JSON. The structured payload
    arrives as a Markdown-fenced JSON block in ``ResultMessage.result``
    instead. Verified by running a minimal repro
    (``/tmp/repro_structured_output.py``) against both options-only and
    options+agents= configurations — both return ``structured_output=
    None`` and a fenced JSON ``result`` string.

    This helper implements the two-source extraction:

    1. ``structured_output`` dict (the intended path; tests use it via
       ``_FakeResultMessage(structured_output=...)``; if a future
       CLI/SDK release starts populating the field, this branch fires
       first and the fallback never runs).
    2. ``result`` string with embedded JSON (the actual live behavior).
       Tolerant of:
         - bare JSON: ``'{"R1_root_cause": ...}'``
         - Markdown-fenced JSON: ``'```json\\n{...}\\n```'`` or
           ``'```\\n{...}\\n```'``
         - prose + fenced JSON: ``'Here is the answer:\\n```json\\n{...}\\n```'``
         - prose + bare JSON: ``'Here:\\n{...}\\nThat is all.'``

    The extraction strategy is greedy-but-safe: find the first ``{`` and
    walk character-by-character tracking brace depth, respecting string
    literals (including escapes), until depth returns to zero. This
    matches a top-level JSON object regardless of nested braces inside.

    Mirrors the spirit of ``bin/_planner/two_call._extract_structured_output``,
    which faced the analogous "SDK doesn't expose structured_output —
    parse content[0].text as JSON" problem for the anthropic SDK lineage.

    Raises
    ------
    RuntimeError
        If neither ``structured_output`` nor a JSON-extractable ``result``
        is available. The message preserves the original "binding may
        have been silently dropped" wording so morning-review grep
        patterns keep matching, but also names ``result`` as the
        fallback site that was attempted.
    """
    # Path 1 — preserved behavior.
    structured = getattr(result_message, "structured_output", None)
    if isinstance(structured, dict):
        return structured

    # Path 2 — extract JSON from result text.
    result_text = getattr(result_message, "result", None)
    if isinstance(result_text, str) and result_text.strip():
        parsed = _try_extract_json_object(result_text)
        if parsed is not None:
            return parsed

    raise RuntimeError(
        "reviewer SDK ResultMessage carried no structured rubric: "
        f"structured_output type={type(structured).__name__!r}, "
        f"result type={type(result_text).__name__!r}. "
        "The SDK's output_format binding may have been silently dropped, "
        "AND the result text did not contain an extractable JSON object."
    )


def _try_extract_json_object(text: str) -> dict[str, Any] | None:
    """Extract the first complete top-level JSON object from a text blob.

    Returns the parsed dict, or None if no extractable object is found
    or if extraction yields a non-dict value.

    Strategy
    --------

    1. Find the first ``{`` in the text.
    2. Walk forward tracking brace depth + whether we're inside a JSON
       string literal. JSON strings can contain ``{`` and ``}`` which
       must NOT count toward depth; escape sequences inside strings
       (``\\"``, ``\\\\``, etc.) must be skipped.
    3. When depth returns to 0, slice the substring and ``json.loads``
       it. On parse failure, return None (don't fall through to a
       second candidate — the strategy is "first complete top-level
       object wins"; a malformed first object means the response is
       structurally broken and the caller should raise).

    Tolerant of:
    - Markdown fences (the leading ```` ```json `` / ```` ``` `` is
      skipped naturally — the first ``{`` we find is inside the fence).
    - Leading/trailing prose.
    - Trailing fences after the closing ``}``.

    NOT a general-purpose JSON5 / lenient parser — only enough to pull
    a single top-level object from a Markdown-emitting LLM's text
    response. The schema-bound CLI output is constrained-decoded so the
    object itself is well-formed JSON.
    """
    if not text:
        return None
    start = text.find("{")
    if start < 0:
        return None
    depth = 0
    in_string = False
    escape = False
    i = start
    while i < len(text):
        ch = text[i]
        if in_string:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
        else:
            if ch == '"':
                in_string = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    candidate = text[start : i + 1]
                    try:
                        parsed = json.loads(candidate)
                    except json.JSONDecodeError:
                        return None
                    return parsed if isinstance(parsed, dict) else None
        i += 1
    return None


# ----------------------------------------------------------------------
# T5 — spawn_opus_via_sdk (Opus coder spawn with git-diff capture)
# ----------------------------------------------------------------------

#: Maximum size in bytes for the captured git diff excerpt. Anything
#: beyond this gets truncated with ``_DIFF_TRUNCATION_SENTINEL`` appended.
#: Picked as 50KB — clearly bigger than typical retry-loop iteration
#: diffs (which average single-digit KB) but small enough that
#: mega-diffs hit truncation before they bloat the briefing prompt the
#: reviewer subsequently consumes. Exposed in ``__all__`` so consumers
#: and the test fixture can import it directly.
DIFF_EXCERPT_MAX_BYTES = 50_000

#: Trailing sentinel appended to a truncated diff excerpt. Carries the
#: cap value verbatim so operators reading the morning-review log can
#: tell instantly what was lost. The leading newline ensures the
#: sentinel renders on its own line regardless of where the truncation
#: boundary fell.
_DIFF_TRUNCATION_SENTINEL = (
    "\n... [diff truncated at DIFF_EXCERPT_MAX_BYTES bytes] ..."
)

#: Hook env var names propagated from the chain driver's process
#: environment into the spawned Opus subprocess. Caller may also pass
#: these explicitly via the ``hook_env`` kwarg — caller wins.
_HOOK_ENV_VAR_NAMES = ("SPLOCK_PLAN_SLUG", "SPLOCK_CHAIN_ID", "SPLOCK_PHASE")

#: SDK alias for the Opus coder model. The current default is the same
#: ``"opus"`` alias the CLI subscription resolves to per CLAUDE.md
#: (Opus 4.7 at code-time, 2026-05-23). The env var
#: ``OVERNIGHT_OPUS_CODER_MODEL`` overrides this.
_DEFAULT_CODER_MODEL = "opus"

#: Name under which the coder ``AgentDefinition`` is registered in
#: ``ClaudeAgentOptions.agents``. Matches the on-disk subagent file
#: basename so the SDK's AgentDefinition override semantics line up
#: with the source-of-truth file at ``.claude/agents/coder.md``.
_CODER_AGENT_NAME = "coder"

#: Path (cwd-relative) to the coder subagent definition. Loaded as the
#: ``AgentDefinition.prompt`` body when present; otherwise a minimal
#: inline fallback prompt anchors the coder contract.
_CODER_AGENT_FILE = pathlib.Path(".claude/agents/coder.md")


def _load_coder_system_prompt(cwd: pathlib.Path) -> str:
    """Read ``.claude/agents/coder.md`` if present; else fall back inline.

    Mirrors ``_load_reviewer_system_prompt`` (T4) for the coder. The
    on-disk file is the source of truth per the verifier_sdk_wiring
    recon; the inline fallback only fires when the file is missing
    (operator running outside a chain-driver workspace, or the repo
    hasn't been initialized).
    """
    loaded = _read_agent_prompt(cwd, _CODER_AGENT_FILE)
    if loaded is not None:
        return loaded
    return (
        "You are the coder subagent for the §F.3 test-step retry loop. "
        "Implement the task at file_paths_touched; run tests via "
        "bin/verify; iterate per the retry loop until the verifier "
        "subagent answers READY. Refuse to declare completion without a "
        "green test run."
    )


def _git_capture_head(cwd: pathlib.Path) -> str | None:
    """Return the current HEAD commit SHA, or None if the call fails.

    Failures are tolerated (return None) so the spawner doesn't crash
    on edge cases like an empty repo (no commits yet) or a missing
    ``.git`` directory. The diff-capture step downstream short-circuits
    on a None baseline.
    """
    try:
        completed = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=str(cwd),
            capture_output=True,
            text=True,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if completed.returncode != 0:
        return None
    sha = (completed.stdout or "").strip()
    return sha or None


def _git_diff_args(baseline_sha: str | None, post_sha: str | None) -> list[str]:
    """Build the ``git diff`` range arg list for the capture step.

    Three cases:

    - Baseline + post both present + different → ``[baseline, post]``
      (covers the coder-committed case).
    - Baseline + post both present + equal → ``[]`` (HEAD didn't move;
      diff against working tree, which covers the no-commit case where
      changes are uncommitted).
    - Either missing → ``[]`` (best-effort working-tree diff).
    """
    if baseline_sha and post_sha and baseline_sha != post_sha:
        return [baseline_sha, post_sha]
    return []


def _parse_diff_shortstat(stdout: str) -> tuple[int, int]:
    """Parse a ``git diff --shortstat`` line into (added, removed) ints.

    Sample inputs handled::

        " 2 files changed, 15 insertions(+), 3 deletions(-)\\n"
        " 1 file changed, 0 insertions(+), 5 deletions(-)\\n"
        " 1 file changed, 12 insertions(+)\\n"      # additions only
        " 1 file changed, 4 deletions(-)\\n"        # deletions only
        ""                                          # no changes

    Returns ``(0, 0)`` for the empty case; partial-stat lines (one of
    insertions or deletions missing) return the present count + 0 for
    the other. No regex — a simple substring scan keeps the parser
    transparent.
    """
    added = 0
    removed = 0
    text = (stdout or "").strip()
    if not text:
        return (0, 0)
    # Find "<N> insertion(s)" — git uses singular/plural depending on N.
    for token in ("insertions(+)", "insertion(+)"):
        idx = text.find(token)
        if idx >= 0:
            # Walk back from idx to find the integer.
            head = text[:idx].rstrip()
            digits = ""
            for ch in reversed(head):
                if ch.isdigit():
                    digits = ch + digits
                else:
                    break
            if digits:
                try:
                    added = int(digits)
                except ValueError:
                    added = 0
            break
    for token in ("deletions(-)", "deletion(-)"):
        idx = text.find(token)
        if idx >= 0:
            head = text[:idx].rstrip()
            digits = ""
            for ch in reversed(head):
                if ch.isdigit():
                    digits = ch + digits
                else:
                    break
            if digits:
                try:
                    removed = int(digits)
                except ValueError:
                    removed = 0
            break
    return (added, removed)


def _capture_post_session_diff(
    cwd: pathlib.Path,
    baseline_sha: str | None,
) -> dict[str, Any]:
    """Snapshot the git diff after the coder session returns.

    Returns a dict with ``test_files_edited``, ``diff_lines_added``,
    ``diff_lines_removed``, ``diff_excerpt`` keys — matching the
    consumer contract at ``iteration_loop.py:439-466``.

    Diff range selection per ``_git_diff_args``:

    - HEAD moved: ``git diff <baseline> <new HEAD>`` (committed changes)
    - HEAD unchanged: ``git diff`` (working-tree changes — handles the
      no-commit case where the coder edited files but did not commit)
    - Either SHA unavailable: best-effort working-tree diff

    Test-file filter: any path under ``tests/`` (the consumer at
    iteration_loop.py reads ``test_files_edited`` to detect tampering;
    matching the leading-``tests/`` substring catches the project's
    actual test layout, where every test lives under ``tests/...``).
    """
    post_sha = _git_capture_head(cwd)
    range_args = _git_diff_args(baseline_sha, post_sha)

    # Shortstat for added/removed line counts. ``--shortstat`` collapses
    # the per-file stats into a single trailing summary line, which is
    # cheap to parse.
    try:
        shortstat = subprocess.run(
            ["git", "diff", "--shortstat", *range_args],
            cwd=str(cwd),
            capture_output=True,
            text=True,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        shortstat = None
    added = 0
    removed = 0
    if shortstat is not None and shortstat.returncode == 0:
        added, removed = _parse_diff_shortstat(shortstat.stdout or "")

    # Raw diff excerpt — capped at DIFF_EXCERPT_MAX_BYTES.
    try:
        diff_full = subprocess.run(
            ["git", "diff", *range_args],
            cwd=str(cwd),
            capture_output=True,
            text=True,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        diff_full = None
    if diff_full is not None and diff_full.returncode == 0:
        raw = diff_full.stdout or ""
    else:
        raw = ""
    encoded = raw.encode("utf-8", errors="replace")
    if len(encoded) > DIFF_EXCERPT_MAX_BYTES:
        # Truncate at the byte cap. We re-decode with ``errors='replace'``
        # so a mid-codepoint cut doesn't crash; then append the sentinel.
        head = encoded[:DIFF_EXCERPT_MAX_BYTES].decode("utf-8", errors="replace")
        excerpt = head + _DIFF_TRUNCATION_SENTINEL
    else:
        excerpt = raw

    # Names-only list — filter to paths under ``tests/``.
    try:
        names = subprocess.run(
            ["git", "diff", "--name-only", *range_args],
            cwd=str(cwd),
            capture_output=True,
            text=True,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        names = None
    test_files: list[str] = []
    if names is not None and names.returncode == 0:
        for line in (names.stdout or "").splitlines():
            path = line.strip()
            if not path:
                continue
            # Match either a leading ``tests/`` prefix OR an embedded
            # ``/tests/`` segment (nested subdirs sometimes carry their
            # own tests/ tree; both should count).
            if path.startswith("tests/") or "/tests/" in path:
                test_files.append(path)

    return {
        "test_files_edited": test_files,
        "diff_lines_added": added,
        "diff_lines_removed": removed,
        "diff_excerpt": excerpt,
    }


async def _drive_opus_async(
    *,
    prompt: str,
    options: Any,
    client: ClaudeAgentSDKClient | None,
) -> Any:
    """Async-iterate the SDK query stream and return the terminal ResultMessage.

    Lazy-imports ``claude_agent_sdk`` ONLY when ``client`` is None — the
    DI test path supplies a fake recorder that never touches the real
    module. Mirrors ``_drive_reviewer_async`` (T4) exactly; the spawner
    surface is symmetric across the two coder/reviewer paths.
    """
    final_result: Any = None
    if client is None:
        import claude_agent_sdk  # local — preserve lazy-import discipline

        stream = claude_agent_sdk.query(prompt=prompt, options=options)
    else:
        stream = client.query(prompt=prompt, options=options)
    async for msg in stream:
        if type(msg).__name__ == "ResultMessage":
            final_result = msg
    return final_result


def spawn_opus_via_sdk(
    *,
    prompt: str,
    cwd: pathlib.Path,
    hook_env: dict[str, str] | None = None,
    timeout_s: int = SDK_REQUEST_TIMEOUT_S,
    client: ClaudeAgentSDKClient | None = None,
    model_env_var: str = "OVERNIGHT_OPUS_CODER_MODEL",
) -> dict[str, Any]:
    """Spawn the Opus coder subagent via claude-agent-sdk; capture git diff.

    Per verifier_sdk_wiring §T5. Replaces the legacy
    ``_default_spawn_opus`` stub in ``iteration_loop.py:439-466`` once
    T9 flips that stub to ``NotImplementedError`` (this task does NOT
    touch iteration_loop — T8 wires the DI; T9 flips the default).

    Behaviour
    ---------

    1. Resolve the Opus model alias from ``OVERNIGHT_OPUS_CODER_MODEL``
       (env var name passed via ``model_env_var`` kwarg), defaulting to
       ``"opus"`` (the SDK alias for the operator's current-Opus build).
    2. Capture baseline ``HEAD`` SHA via ``git rev-parse HEAD`` BEFORE
       calling the SDK so the post-session diff is anchored on the
       pre-session commit (handles both the coder-commits-during-session
       case AND the coder-edits-but-doesn't-commit case).
    3. Build hook_env: if caller passed ``hook_env``, use it verbatim
       (caller wins); otherwise gather ``SPLOCK_PLAN_SLUG`` /
       ``SPLOCK_CHAIN_ID`` / ``SPLOCK_PHASE`` from ``os.environ``.
    4. Construct an ``AgentDefinition`` for the ``coder`` agent name,
       carrying the resolved model + the system prompt loaded from
       ``.claude/agents/coder.md`` (or the minimal inline fallback).
    5. Build ``ClaudeAgentOptions`` with ``model``, ``agents``, ``cwd``,
       ``env``. NO ``output_format`` — the coder emits code via tools,
       not structured output.
    6. Lazy-import ``claude_agent_sdk`` inside ``_drive_opus_async``
       (NOT at module top — T1 lazy-import discipline holds).
    7. Async-iterate the SDK query stream (or the injected ``client``).
       Capture the terminal ``ResultMessage``.
    8. If ``result.is_error`` → raise ``RuntimeError`` with subtype +
       forensic context. (No dedicated coder-side exception — coder
       errors are rarer than reviewer retry-exhaustion and don't have a
       dedicated exit code mapping like T4's ReviewerEmissionExhausted.)
    9. Capture post-session diff via ``_capture_post_session_diff``:
       shortstat for added/removed line counts; raw diff for the
       excerpt (truncated at ``DIFF_EXCERPT_MAX_BYTES`` with the
       sentinel appended); ``--name-only`` for the test_files_edited
       list (filtered to paths under ``tests/``).
    10. Return a dict with ``cost_usd``, ``test_files_edited``,
        ``diff_lines_added``, ``diff_lines_removed``, ``diff_excerpt``.

    Note on cost key naming
    -----------------------

    The coder spawner returns ``cost_usd`` (NOT ``_sdk_cost_usd`` like
    T4's reviewer spawner). The two keys are intentionally different
    because the two return dicts have different shapes — the reviewer
    returns a rubric dict whose schema has ``additionalProperties:
    false``, so non-schema keys need the underscore prefix to avoid
    invalidating the payload. The coder returns a plain dict with no
    schema attached. T7's cost aggregator reads ``cost_usd`` from the
    coder side and ``_sdk_cost_usd`` from the reviewer side; the two
    naming conventions stay distinct on purpose.

    Parameters
    ----------
    prompt : str
        The task-specific coder prompt. The chain driver constructs
        this from the orchestrator's active task entry; the spawner
        sends it verbatim as the SDK's user-side prompt.
    cwd : pathlib.Path
        Working directory for the spawned CLI subprocess — repo root
        where ``.git`` lives. The git-diff capture relies on ``.git``
        being a child of this directory.
    hook_env : dict[str, str] | None
        Hook env vars to propagate. Caller-explicit overrides win over
        the ``os.environ`` defaults; if None, the spawner gathers
        ``SPLOCK_PLAN_SLUG`` / ``SPLOCK_CHAIN_ID`` / ``SPLOCK_PHASE`` from
        ``os.environ`` automatically.
    timeout_s : int
        Per-call SDK request timeout (seconds). Defaults to
        ``SDK_REQUEST_TIMEOUT_S`` (1200s). NOT directly enforced here
        (the SDK's own non-streaming-timeout guard handles this); the
        kwarg exists so a future tuning sweep can pass per-call values.
    client : ClaudeAgentSDKClient | None
        DI seam for tests. When provided, the spawner drives
        ``client.query`` instead of ``claude_agent_sdk.query``; tests
        pass a ``_FakeSDKClient`` recorder that captures the
        constructed ``ClaudeAgentOptions``.
    model_env_var : str
        Env var name the spawner reads to resolve the Opus model alias.
        Defaults to ``OVERNIGHT_OPUS_CODER_MODEL``. The kwarg exists so
        tests + future tuning sweeps can override the env var name
        without monkeypatching ``os.environ`` globally.

    Returns
    -------
    dict[str, Any]
        ``{'cost_usd': float, 'test_files_edited': list[str],
        'diff_lines_added': int, 'diff_lines_removed': int,
        'diff_excerpt': str}``. ``cost_usd`` comes from
        ``ResultMessage.total_cost_usd`` (0.0 if None);
        ``test_files_edited`` is the post-session git diff's file list
        filtered to paths under ``tests/``; ``diff_lines_added`` /
        ``diff_lines_removed`` come from ``git diff --shortstat``;
        ``diff_excerpt`` is the raw diff truncated at
        ``DIFF_EXCERPT_MAX_BYTES`` with ``_DIFF_TRUNCATION_SENTINEL``
        appended if truncated.

    Raises
    ------
    RuntimeError
        If the SDK returned an error (any ``is_error=True`` subtype),
        or if no ResultMessage arrived before the stream closed.
    """
    cwd = pathlib.Path(cwd)

    # Resolve model + hook_env.
    model = os.environ.get(model_env_var, _DEFAULT_CODER_MODEL)
    if hook_env is None:
        merged_env = {
            k: os.environ[k] for k in _HOOK_ENV_VAR_NAMES if k in os.environ
        }
    else:
        # Caller wins — pass through verbatim, with a None filter so the
        # SDK doesn't see None-valued env entries (would crash the merge
        # in ``subprocess_cli.py:434``).
        merged_env = {k: v for k, v in hook_env.items() if v is not None}

    plan_slug = merged_env.get("SPLOCK_PLAN_SLUG")

    # Capture baseline HEAD BEFORE the SDK call so the post-session
    # diff range is anchored correctly. None on failure (empty repo,
    # missing .git, etc.) — the diff capture step degrades gracefully.
    baseline_sha = _git_capture_head(cwd)

    # Lazy-import the SDK dataclass types at function-call time (NOT
    # module top) per T1 lazy-import discipline.
    from claude_agent_sdk import (  # local — preserve lazy-import
        AgentDefinition,
        ClaudeAgentOptions,
    )

    system_prompt = _load_coder_system_prompt(cwd)

    coder_agent_def = AgentDefinition(
        description=(
            "coder for per-task code work under the §A Ralph completion "
            "gate; writes code at file_paths_touched, runs tests at "
            "tests_enabled, refuses to declare completion until the "
            "verifier subagent answers READY"
        ),
        prompt=system_prompt,
        model=model,
    )

    options = ClaudeAgentOptions(
        model=model,
        agents={_CODER_AGENT_NAME: coder_agent_def},
        cwd=str(cwd),
        env=merged_env,
    )

    result_message = asyncio.run(
        _drive_opus_async(
            prompt=prompt,
            options=options,
            client=client,
        )
    )

    if result_message is None:
        raise RuntimeError(
            "coder SDK stream closed without yielding a ResultMessage "
            f"(cwd={str(cwd)!r}, plan_slug={plan_slug!r}); the CLI "
            "subprocess may have exited abnormally without emitting a "
            "terminal message."
        )

    if getattr(result_message, "is_error", False):
        raise RuntimeError(
            "coder SDK returned is_error=True "
            f"subtype={getattr(result_message, 'subtype', '')!r} "
            f"(cwd={str(cwd)!r}, plan_slug={plan_slug!r})"
        )

    # Capture post-session diff — this is the load-bearing T5 contract.
    diff_payload = _capture_post_session_diff(cwd, baseline_sha)

    cost = getattr(result_message, "total_cost_usd", None)
    return {
        "cost_usd": float(cost) if cost is not None else 0.0,
        "test_files_edited": diff_payload["test_files_edited"],
        "diff_lines_added": diff_payload["diff_lines_added"],
        "diff_lines_removed": diff_payload["diff_lines_removed"],
        "diff_excerpt": diff_payload["diff_excerpt"],
    }


# ----------------------------------------------------------------------
# T6 — smoke_check_sdk_available (pre-flight SDK probe)
# ----------------------------------------------------------------------


#: Sentinel for the ``sys.modules.get`` "key absent" branch in
#: ``smoke_check_sdk_available``. The smoke check distinguishes two
#: states: (1) ``sys.modules['claude_agent_sdk'] is None`` — explicitly
#: marked as unimportable (the canonical "package not installed" pattern
#: in tests that monkey-patch via ``sys.modules[name] = None``); (2)
#: import succeeds and yields a real module. The sentinel is a private
#: object identity guaranteed not to collide with any actual stored
#: value.
_SDK_MISSING_SENTINEL = object()


def smoke_check_sdk_available() -> tuple[bool, str]:
    """Pre-flight check: ``claude_agent_sdk`` importable + ``query`` callable.

    Per verifier_sdk_wiring §T6. Runs BEFORE the chain driver dispatches
    a test-step retry-loop phase. If the SDK isn't installed or fails to
    import cleanly, returns ``(False, diagnostic)`` so the chain driver
    can short-circuit to exit code 16 ('SDK retry exhausted' / unable to
    proceed) without burning a chain budget on a never-going-to-succeed
    retry loop.

    Behaviour
    ---------

    1. Probe ``sys.modules['claude_agent_sdk']`` for the explicit-None
       sentinel. Tests / operator chains mark the SDK as explicitly
       unimportable via ``sys.modules['claude_agent_sdk'] = None``; this
       is the canonical "the package is not installed" pattern and the
       smoke check must catch it BEFORE attempting ``import_module``
       (which would re-raise the implicit ImportError with a less
       informative message).
    2. Lazy-import ``claude_agent_sdk`` via ``importlib.import_module``
       (NOT a top-level ``import`` statement — T1 lazy-import discipline
       holds). ``ImportError`` / ``ModuleNotFoundError`` → return
       ``(False, msg)`` naming the package + failure mode.
    3. Verify the call surface: ``claude_agent_sdk.query`` exists and is
       callable. The smoke check does NOT actually invoke ``query`` —
       that would require operator credentials and ping the live API.
       Static surface check only ("is the package importable and the
       symbol present?") is the right grain for a pre-flight.
    4. Note on ``inspect.isasyncgenfunction``: the strict check rejects
       test fixtures that patch ``claude_agent_sdk.query`` to a regular
       function returning an async iterator (NOT an async-generator
       function). To stay tolerant of mocks while still catching real
       breakage, we use the looser ``callable()`` check — when the real
       SDK is installed, ``query`` IS an async-generator function and
       ``callable(query)`` returns True; when a test patches it to a
       fake, ``callable(fake)`` also returns True; when the symbol is
       missing or wrong type, ``callable()`` returns False.

    Returns
    -------
    tuple[bool, str]
        - ``(True, '')`` — SDK importable and ``query`` symbol present
          + callable.
        - ``(False, msg)`` — SDK missing or broken; ``msg`` always
          mentions the literal string ``'claude-agent-sdk'`` (the
          package name) so the diagnostic is greppable in the
          chain-driver log.

    Notes
    -----
    The diagnostic strings ALWAYS include the literal ``'claude-agent-
    sdk'`` substring. Test #1 asserts this property; downstream
    morning-review entries rely on it for grep-based triage. Even if
    the failure mode is unrelated to the package itself (e.g. ``query``
    is missing despite the module importing), the diagnostic surface
    still names the package so operators can locate the offending plan.
    """
    # Step 1: distinguish "key in sys.modules but explicitly None"
    # (the test #1 fixture pattern) from "key absent" (importlib will
    # attempt a real import). The sentinel object is a private identity
    # that cannot collide with any actual stored value.
    cached = sys.modules.get("claude_agent_sdk", _SDK_MISSING_SENTINEL)
    if cached is None:
        return (
            False,
            "claude-agent-sdk package is None in sys.modules "
            "(probably uninstalled or explicitly marked unimportable)",
        )

    # Step 2: lazy import. Catch the canonical "package not installed"
    # exceptions and surface a diagnostic that names the package.
    try:
        sdk = importlib.import_module("claude_agent_sdk")
    except (ImportError, ModuleNotFoundError) as exc:
        return (
            False,
            f"claude-agent-sdk package is not installed or is "
            f"unimportable: {exc} "
            f"(install it into the project venv: pip install claude-agent-sdk)",
        )
    except Exception as exc:  # noqa: BLE001 — pre-flight must not crash
        # Defensive: any other import-time exception (e.g. a syntax
        # error in the installed package, a side-effecting top-level
        # __init__ that raises) should also surface as "broken" rather
        # than crashing the chain driver. Pre-flight is allowed to
        # over-trigger on the False side.
        return (
            False,
            f"claude-agent-sdk import raised unexpected exception: "
            f"{type(exc).__name__}: {exc}",
        )

    # Step 3: verify the call surface. ``query`` is the only symbol the
    # retry-loop spawners exercise (T3/T4/T5 all reach for
    # ``claude_agent_sdk.query``); checking it is sufficient. We use
    # ``callable()`` rather than ``inspect.isasyncgenfunction()`` so the
    # smoke check stays tolerant of test mocks (which patch ``query`` to
    # a regular function returning an async iterator, NOT an
    # async-generator function — see test #2 fixture below).
    query = getattr(sdk, "query", None)
    if query is None:
        return (
            False,
            "claude-agent-sdk surface invalid: module is importable but "
            "missing the 'query' symbol",
        )
    if not callable(query):
        return (
            False,
            "claude-agent-sdk surface invalid: 'query' attribute exists "
            "but is not callable",
        )

    return (True, "")


# ----------------------------------------------------------------------
# Public exports
# ----------------------------------------------------------------------

__all__ = [
    "DIFF_EXCERPT_MAX_BYTES",
    "SDK_REQUEST_TIMEOUT_S",
    "ClaudeAgentSDKClient",
    "ReviewerEmissionExhausted",
    "run_verify_subprocess",
    "smoke_check_sdk_available",
    "spawn_opus_via_sdk",
    "spawn_reviewer_via_sdk",
]
