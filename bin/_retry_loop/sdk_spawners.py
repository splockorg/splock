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
import hashlib
import importlib
import json
import logging
import os
import pathlib
import shlex
import subprocess
import sys
from typing import Any, AsyncIterator, Iterable, Protocol

from bin._verify_plan.strict import (
    TYPED_GATE_COMMAND_PREFIX,
    task_verification_exemption,
)

from .rubric import TEST_STEP_RUBRIC_SCHEMA_V1

logger = logging.getLogger(__name__)


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


def _persist_iter_output(
    plan_dir: pathlib.Path,
    iteration_n: int,
    result: subprocess.CompletedProcess,
) -> None:
    """Best-effort capture of a verify result to ``_test_output_iter{n}.txt``.

    The briefing builder reads this file on the next iteration. OSErrors are
    swallowed (the in-memory ``CompletedProcess`` still carries stdout/stderr)
    — matches the legacy ``_default_run_verify`` swallow-on-OSError shape.
    """
    output_path = plan_dir / _TEST_OUTPUT_FILENAME_TEMPLATE.format(n=iteration_n)
    try:
        output_path.write_text(
            (result.stdout or "") + "\n--- STDERR ---\n" + (result.stderr or ""),
            encoding="utf-8",
        )
    except OSError:
        pass


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
    4. Partition the union into runnable pytest selectors (node-IDs /
       paths that exist on disk) vs. skipped entries (design-prose or
       not-yet-authored files) via `partition_runnable_selectors`. Only
       runnable selectors enter the argv; skipped entries are logged.
       If **zero** selectors are runnable, return a synthetic exit-4
       ``CompletedProcess`` WITHOUT shelling out — a bare ``pytest`` with
       no test-ids would collect the entire repo suite. See #11 / #12.
    5. Build a HARDENED pytest argv via `build_pytest_argv` —
       ``[_test_interpreter(), "-m", "pytest", *PYTEST_HARDENING_FLAGS,
       <runnable...>, "-v"]`` — NEVER ``bin/verify`` (the recursion T3
       exists to prevent) — and run via ``subprocess.run`` with ``cwd``
       pinned to the ADOPTER's repo root, ``capture_output=True``, and the child
       env sanitized via `pytest_subprocess_env` (the env-channel
       injection clamp). The hardening flags (real_tests_at_junctions
       SC8) clamp cacheprovider state and ini-file ``addopts``
       injection; see `PYTEST_HARDENING_FLAGS`.
    6. If pytest exited 0, run the SC8 trust check
       (`untrusted_pytest_trust_surface`): a green exit is only a
       TRUSTED green when every ``conftest.py`` / pytest ini file the
       invocation would consult is committed-clean in git. No flag can
       neutralize a force-pass ``pytest_runtest_makereport`` hook in a
       conftest (it is arbitrary in-process code), so provenance is
       the gate: untracked/modified trust-surface files coerce the
       returncode to `UNTRUSTED_GREEN_RETURNCODE` with a loud
       ``UNTRUSTED-GREEN`` diagnostic appended to stderr. RED results
       pass through unmodified (red is red). See
       ``docs/plans/_closed/real_tests_at_junctions/trust_boundary_decision.md``.
    7. Persist captured stdout (with a stderr appendix matching the
       legacy ``_default_run_verify`` shape) to
       ``plan_dir / _test_output_iter{n}.txt`` so the briefing builder
       can read it on the next iteration.
    8. Return the ``CompletedProcess``. Apart from the SC8
       untrusted-green coercion above, the return code is NOT touched:
       pytest exit code 5 (no tests collected) must surface intact so
       callers can distinguish "ran but found nothing" from "ran and
       failed" (exit 1).

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
    union = read_tests_enabled_union(orchestrator_path)

    if not union:
        raise ValueError(
            f"no tests_enabled across orchestrator tasks for slug={slug!r} "
            f"(orchestrator_path={orchestrator_path!s})"
        )

    repo_root = _repo_root()
    runnable, skipped = partition_runnable_selectors(union, repo_root)

    if skipped:
        logger.warning(
            "run_verify_subprocess[%s]: skipping %d non-runnable tests_enabled "
            "entr%s (not a pytest node-ID / not on disk): %s",
            slug,
            len(skipped),
            "y" if len(skipped) == 1 else "ies",
            ", ".join(repr(s) for s in skipped),
        )

    if not runnable:
        # Zero runnable selectors. NEVER shell out to a bare ``pytest`` —
        # an empty test-id argv would collect the *entire* repo suite
        # (pytest's default ``testpaths``), grading the wrong thing. Return
        # a synthetic exit-4 (pytest's usage / collection-error code) so the
        # caller treats it as a failure. The operator-direct path is
        # fast-failed earlier by ``main._run_test_step``'s pre-flight (no
        # retry budget consumed there); this branch is the defence-in-depth
        # for the in-process chain-driver path. See outstanding-issues #11/#12.
        diagnostic = (
            f"no runnable pytest selectors among {len(union)} tests_enabled "
            f"entr{'y' if len(union) == 1 else 'ies'} for slug={slug!r}; every "
            "entry is non-node-ID prose or names a file not yet on disk: "
            + ", ".join(repr(s) for s in union)
        )
        result = subprocess.CompletedProcess(
            args=[_test_interpreter(), "-m", "pytest"],
            returncode=4,
            stdout="",
            stderr=diagnostic + "\n",
        )
        _persist_iter_output(plan_dir, iteration_n, result)
        return result

    argv = build_pytest_argv(runnable)

    result = subprocess.run(
        argv,
        cwd=str(repo_root),
        env=pytest_subprocess_env(),
        capture_output=True,
        text=True,
        timeout=_PYTEST_SUBPROCESS_TIMEOUT_S,
        check=False,
    )

    # real_tests_at_junctions T8 (SC8) — trust check on GREEN results
    # only. A force-pass conftest hook (pytest_runtest_makereport
    # flipping failed→passed) yields exit 0 that no flag set can
    # prevent; the gate therefore refuses to TRUST a green run whose
    # conftest/ini trust surface is not committed-clean. Red results
    # are never touched — a real failure must reach the retry loop
    # intact.
    if result.returncode == 0:
        untrusted = untrusted_pytest_trust_surface(runnable, repo_root)
        if untrusted:
            diagnostic = (
                "UNTRUSTED-GREEN: pytest exited 0 but the following "
                "pytest trust-surface files (conftest.py / pytest ini) "
                "are untracked or modified relative to git HEAD, so the "
                "green result is NOT trusted (a plan- or repair-authored "
                "conftest can force-pass failures in-process; invocation "
                "flags cannot neutralize that): "
                + ", ".join(untrusted)
                + f". Returncode coerced to {UNTRUSTED_GREEN_RETURNCODE}. "
                "Remedy: the operator reviews the listed files and "
                "commits them (an explicit trust grant), then re-runs "
                "/test. See docs/plans/_closed/real_tests_at_junctions/"
                "trust_boundary_decision.md."
            )
            logger.warning("run_verify_subprocess[%s]: %s", slug, diagnostic)
            result = subprocess.CompletedProcess(
                args=result.args,
                returncode=UNTRUSTED_GREEN_RETURNCODE,
                stdout=result.stdout,
                stderr=(result.stderr or "") + "\n" + diagnostic + "\n",
            )

    _persist_iter_output(plan_dir, iteration_n, result)
    return result


#: ``SPLOCK_PHASE`` value at which the repair write-scope guard activates.
#: Phase 5 is the /test test-step retry loop — the spawned Opus there is
#: a REPAIR step and must not author test files / conftest. Phase 4
#: (/code) routes through the SAME spawner (phase_spawn.spawn_retry_loop_phase
#: dispatches both) but is exempt: the task coder legitimately authors
#: its tests_enabled files (TDD).
REPAIR_GUARD_PHASE = "5"

#: Directory components ignored by the repair-scope file walks —
#: interpreter/pytest byproducts the coder's own tool runs create
#: legitimately (they are not authored content).
_REPAIR_SCOPE_IGNORED_DIRS = frozenset({"__pycache__", ".pytest_cache", ".git"})
_REPAIR_SCOPE_IGNORED_SUFFIXES = (".pyc", ".pyo")


def _walk_file_hashes(
    root: pathlib.Path, base: pathlib.Path
) -> dict[str, str]:
    """``{rel_posix_path: sha256}`` for files under ``root`` (rel to ``base``).

    Skips `_REPAIR_SCOPE_IGNORED_DIRS` components, dot-directories, and
    `_REPAIR_SCOPE_IGNORED_SUFFIXES` files. Empty dict when ``root`` is
    missing. Unreadable files are skipped with a warning (they can
    neither be protected nor flagged).
    """
    out: dict[str, str] = {}
    if not root.is_dir():
        return out
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [
            d
            for d in dirnames
            if d not in _REPAIR_SCOPE_IGNORED_DIRS and not d.startswith(".")
        ]
        for fn in filenames:
            if fn.endswith(_REPAIR_SCOPE_IGNORED_SUFFIXES):
                continue
            p = pathlib.Path(dirpath) / fn
            try:
                digest = hashlib.sha256(p.read_bytes()).hexdigest()
            except OSError:
                logger.warning(
                    "repair-scope walk: unreadable file skipped: %s", p
                )
                continue
            try:
                out[p.relative_to(base).as_posix()] = digest
            except ValueError:
                continue
    return out


def snapshot_repair_write_scope(
    cwd: pathlib.Path, *, slug: str | None = None
) -> dict[str, Any]:
    """Pre-spawn snapshot for `enforce_repair_write_scope`.

    Captures, relative to ``cwd`` (the repo root the coder runs in):

    - ``protected`` — ``{rel_path: bytes | None}`` full-content backups
      of the gate's grading surface: every covering-set ``tests_enabled``
      path (the orchestrator union at ``docs/plans/<slug>/
      <slug>_orchestrator.json``), the conftest/ini trust surface for
      those selectors (`pytest_trust_surface`), and every
      ``conftest.py`` / pytest ini under ``tests/``. ``None`` records
      "did not exist pre-spawn" so a post-spawn creation is detected
      and deleted.
    - ``tests_files`` — ``{rel: sha256}`` of every file under
      ``tests/`` (creation/deletion/modification detection).
    - ``plan_files`` — ``{rel: sha256}`` of every file under
      ``docs/plans/<slug>/`` (the 2026-06-10 incident fabricated a
      decision doc there), or ``None`` when no slug is resolvable.
    """
    cwd = pathlib.Path(cwd)

    selectors: list[str] = []
    if slug:
        orch = cwd / "docs" / "plans" / slug / f"{slug}_orchestrator.json"
        if orch.is_file():
            try:
                selectors = read_tests_enabled_union(orch)
            except (ValueError, OSError, json.JSONDecodeError):
                logger.warning(
                    "snapshot_repair_write_scope: unreadable orchestrator "
                    "at %s; covering-set protection degraded to "
                    "conftest/tests-walk only",
                    orch,
                )

    protected_rel: set[str] = set()
    for selector in selectors:
        path_part = selector.split("::", 1)[0].strip()
        if path_part and not any(ch.isspace() for ch in path_part):
            protected_rel.add(pathlib.PurePosixPath(path_part).as_posix())
    protected_rel.update(pytest_trust_surface(selectors, cwd))

    tests_files = _walk_file_hashes(cwd / "tests", cwd)
    for rel in tests_files:
        name = pathlib.PurePosixPath(rel).name
        if name == _PYTEST_CONFTEST_BASENAME or name in _PYTEST_INI_BASENAMES:
            protected_rel.add(rel)

    protected: dict[str, bytes | None] = {}
    for rel in sorted(protected_rel):
        p = cwd / rel
        if p.is_file():
            try:
                protected[rel] = p.read_bytes()
            except OSError:
                logger.warning(
                    "snapshot_repair_write_scope: unreadable protected "
                    "file skipped: %s",
                    p,
                )
        else:
            protected[rel] = None

    plan_files: dict[str, str] | None = None
    if slug:
        plan_files = _walk_file_hashes(cwd / "docs" / "plans" / slug, cwd)

    return {
        "slug": slug,
        "protected": protected,
        "tests_files": tests_files,
        "plan_files": plan_files,
    }


def _safe_unlink(p: pathlib.Path, stop_at: pathlib.Path) -> None:
    """Unlink ``p`` and prune now-empty parent dirs up to ``stop_at``."""
    try:
        p.unlink()
    except OSError:
        return
    try:
        stop = stop_at.resolve()
        parent = p.parent
        while parent.resolve() != stop and parent.is_dir() and not any(
            parent.iterdir()
        ):
            parent.rmdir()
            parent = parent.parent
    except OSError:
        pass


def enforce_repair_write_scope(
    cwd: pathlib.Path, snapshot: dict[str, Any]
) -> list[dict[str, str]]:
    """Revert/flag repair-step writes to the gate's grading surface.

    The fixer-must-not-author-tests enforcement (SC8): given a
    `snapshot_repair_write_scope` snapshot taken BEFORE the repair Opus
    session, restore the protected surface and sweep fabrications:

    1. **Protected files** (covering-set tests, conftest.py, pytest
       ini): created → deleted; modified → content restored from
       snapshot; deleted → content restored from snapshot.
    2. **New files under ``tests/``** → deleted (the 2026-06-01 +
       2026-06-10 incidents both fabricated test files for
       not-yet-started tasks).
    3. **New files under ``docs/plans/<slug>/``** → deleted (the
       2026-06-10 incident unilaterally authored a decision doc).
    4. **Modified/deleted non-protected files** under the two sweep
       roots → reported (``action: reported_only``) without restore
       (no content snapshot is kept for the full tree); the
       post-session git diff already carries them to the reviewer's
       R4 tampering check.

    Returns the violation list ``[{path, kind, action}, ...]`` —
    ``kind`` ∈ {created, modified, deleted}; ``action`` ∈ {deleted,
    restored, restore_failed, reported_only}. Empty list = the repair
    step stayed inside its write scope.
    """
    cwd = pathlib.Path(cwd)
    violations: list[dict[str, str]] = []

    # 1. Protected surface — full restore.
    for rel, before in (snapshot.get("protected") or {}).items():
        p = cwd / rel
        try:
            after = p.read_bytes() if p.is_file() else None
        except OSError:
            after = None
        if after == before:
            continue
        if before is None:
            _safe_unlink(p, cwd)
            violations.append(
                {"path": rel, "kind": "created", "action": "deleted"}
            )
        else:
            kind = "deleted" if after is None else "modified"
            try:
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_bytes(before)
                action = "restored"
            except OSError:
                action = "restore_failed"
            violations.append({"path": rel, "kind": kind, "action": action})

    handled = {v["path"] for v in violations}

    # 2-4. Creation/deletion/modification sweeps.
    sweeps: list[tuple[str, dict[str, str] | None]] = [
        ("tests", snapshot.get("tests_files")),
    ]
    slug = snapshot.get("slug")
    if slug and snapshot.get("plan_files") is not None:
        sweeps.append((f"docs/plans/{slug}", snapshot.get("plan_files")))

    for root_rel, pre_hashes in sweeps:
        if pre_hashes is None:
            continue
        post_hashes = _walk_file_hashes(cwd / root_rel, cwd)
        pre_keys = set(pre_hashes)
        post_keys = set(post_hashes)
        for rel in sorted(post_keys - pre_keys):
            if rel in handled:
                continue
            _safe_unlink(cwd / rel, cwd)
            violations.append(
                {"path": rel, "kind": "created", "action": "deleted"}
            )
            handled.add(rel)
        for rel in sorted(pre_keys - post_keys):
            if rel in handled:
                continue
            violations.append(
                {"path": rel, "kind": "deleted", "action": "reported_only"}
            )
            handled.add(rel)
        for rel in sorted(pre_keys & post_keys):
            if rel in handled:
                continue
            if pre_hashes[rel] != post_hashes[rel]:
                violations.append(
                    {"path": rel, "kind": "modified", "action": "reported_only"}
                )
                handled.add(rel)

    return violations


# ----------------------------------------------------------------------
# real_tests_at_junctions T8 (SC8) — pytest-invocation hardening +
# repair-must-not-author-tests write-scope enforcement
# ----------------------------------------------------------------------
#
# Posture: fixer-must-not-author-tests / skip-not-repair. Full decision
# record (chosen flag set, threat model, two live incidents, residual
# vectors, phase-4 exemption rationale, the iteration-ordering follow-up)
# lives at docs/plans/_closed/real_tests_at_junctions/trust_boundary_decision.md.


#: SC8 hardening flags injected into every retry-loop pytest invocation
#: (`build_pytest_argv`). Two clamps:
#:
#: - ``-p no:cacheprovider`` — no ``.pytest_cache`` reads/writes: cache
#:   state (``--lf``-style selection, cached collection) can neither
#:   narrow nor skew what the gate grades, and verify runs stay
#:   side-effect-free (matches `collect_only_probe`).
#: - ``--override-ini addopts=`` — clamps ini-file ``addopts`` to empty.
#:   A plan-authored ``pytest.ini`` (nested in the selector subtree —
#:   pytest's rootdir search starts at the args' common ancestor — or at
#:   the repo root) could otherwise smuggle arbitrary argv into the run:
#:   ``-p <evil plugin>``, ``--co`` (collect-only exits 0 without running
#:   a single test), ``-k``/``--deselect`` narrowing, etc. Command-line
#:   ``--override-ini`` beats every ini source. The repo's own pytest.ini
#:   carries only ``markers`` (no addopts), so the clamp is behavior-
#:   neutral for legitimate runs.
#:
#: What flags deliberately do NOT attempt: neutralizing a force-pass
#: ``conftest.py`` hook. conftest files are arbitrary code loaded
#: in-process; ``--noconftest`` would disable the repo's legitimate
#: fixture conftests suite-wide (tests/CLAUDE.md helpers). The conftest
#: vector is closed by provenance (`untrusted_pytest_trust_surface`) +
#: the repair write-scope guard (`enforce_repair_write_scope`) instead.
#:
#: Env-channel siblings of the addopts vector (``PYTEST_ADDOPTS`` /
#: ``PYTEST_PLUGINS``) are out of argv reach — `pytest_subprocess_env`
#: clamps those at every retry-loop pytest subprocess site.
PYTEST_HARDENING_FLAGS: tuple[str, ...] = (
    "-p",
    "no:cacheprovider",
    "--override-ini",
    "addopts=",
)

#: Env vars stripped from every retry-loop pytest subprocess — exactly
#: the two documented env-channel injection vectors clamped by
#: `pytest_subprocess_env`. See its docstring for the threat model.
_PYTEST_ENV_INJECTION_VARS = ("PYTEST_ADDOPTS", "PYTEST_PLUGINS")


def pytest_subprocess_env() -> dict[str, str]:
    """Sanitized copy of ``os.environ`` for retry-loop pytest subprocesses.

    Threat model: pytest honors ``PYTEST_ADDOPTS`` (argv injection —
    e.g. ``--co`` turns every run collect-only and exits 0 without
    executing a single test ⇒ silent false-green) and ``PYTEST_PLUGINS``
    (arbitrary plugin import, firing even under ``--collect-only``);
    both env channels bypass the argv-level ``--override-ini addopts=``
    clamp, so they are removed from the child env here.

    Deliberately scoped to exactly those two channels: every other
    ``PYTEST_*`` var (notably ``PYTEST_DISABLE_PLUGIN_AUTOLOAD``) passes
    through — stripping autoload-disable could change collection
    behavior. ``os.environ`` itself is never mutated.
    """
    env = dict(os.environ)
    for name in _PYTEST_ENV_INJECTION_VARS:
        env.pop(name, None)
    return env

#: Returncode `run_verify_subprocess` substitutes for a pytest exit 0
#: whose conftest/ini trust surface is not committed-clean. Outside
#: pytest's reserved 0-5 vocabulary so the operator/loop can tell
#: "untrusted green" from every real pytest outcome. Non-zero on
#: purpose: the retry loop grades it as a failure and the diagnostic
#: lands in the reviewer briefing.
UNTRUSTED_GREEN_RETURNCODE = 13

#: Ini basenames pytest consults during rootdir/config discovery. Any of
#: these on a selector's directory chain can alter collection/grading,
#: so they share conftest.py's trust treatment.
_PYTEST_INI_BASENAMES = ("pytest.ini", "tox.ini", "setup.cfg", "pyproject.toml")

_PYTEST_CONFTEST_BASENAME = "conftest.py"

def _test_interpreter() -> str:
    """The ADOPTER's python for retry-loop pytest runs (fork finding F3).

    Upstream hardcodes ``sys.executable``. When splock runs as an installed
    plugin that is the PLUGIN's venv: it cannot import the adopter's test deps,
    so every graded run fails for the wrong reason. Resolution order mirrors
    the rest of the adopter-root layer:

    1. ``$SPLOCK_TEST_PYTHON`` — explicit operator override.
    2. ``<project_root>/.venv/bin/python`` when present.
    3. ``sys.executable`` — correct in sideloaded / in-tree mode, where the
       adopter repo IS the plugin repo.
    """
    override = os.environ.get("SPLOCK_TEST_PYTHON", "").strip()
    if override:
        return override
    adopter_py = _repo_root() / ".venv" / "bin" / "python"
    return str(adopter_py) if adopter_py.is_file() else sys.executable


def build_pytest_argv(runnable: Iterable[str]) -> list[str]:
    """Assemble the hardened pytest argv for the retry-loop verify run.

    Single source of the invocation shape (SC8): ``[_test_interpreter(), -m,
    pytest, *PYTEST_HARDENING_FLAGS, *runnable, -v]``. argv[0] is the
    ADOPTER's python, never the plugin venv's (F3) — see `_test_interpreter`. Named + exported
    so the T8 test introspects the builder rather than scraping a
    subprocess recorder, and so future flag changes happen in exactly
    one place (`PYTEST_HARDENING_FLAGS`).
    """
    return [
        _test_interpreter(),
        "-m",
        "pytest",
        *PYTEST_HARDENING_FLAGS,
        *runnable,
        "-v",
    ]


def pytest_trust_surface(
    selectors: Iterable[str], repo_root: pathlib.Path | None = None
) -> list[str]:
    """conftest/ini files pytest would consult for ``selectors``.

    For each selector's path component, collects every ``conftest.py``
    and pytest ini basename (`_PYTEST_INI_BASENAMES`) that EXISTS on the
    directory chain from ``repo_root`` down to the selector's directory
    (pytest loads conftests along collected paths' ancestry, and its
    rootdir/ini discovery walks the args' ancestor chain). Directory
    selectors additionally contribute nested ``conftest.py`` /
    ``pytest.ini`` files under the directory (pytest loads per-subdir
    conftests during collection).

    Returns sorted repo-root-relative POSIX paths of EXISTING files
    only — this is the surface whose provenance
    `untrusted_pytest_trust_surface` grades.
    """
    if repo_root is None:
        repo_root = _repo_root()
    repo_root = pathlib.Path(repo_root)

    dirs: set[pathlib.Path] = {repo_root}
    files_direct: set[pathlib.Path] = set()
    for selector in selectors:
        path_part = selector.split("::", 1)[0].strip()
        if not path_part or any(ch.isspace() for ch in path_part):
            continue
        candidate = repo_root / path_part
        rel = pathlib.PurePosixPath(path_part)
        parts = rel.parts if candidate.is_dir() else rel.parts[:-1]
        cur = repo_root
        for part in parts:
            cur = cur / part
            dirs.add(cur)
        if candidate.is_dir():
            files_direct.update(candidate.rglob(_PYTEST_CONFTEST_BASENAME))
            files_direct.update(candidate.rglob("pytest.ini"))

    for d in dirs:
        for name in (_PYTEST_CONFTEST_BASENAME, *_PYTEST_INI_BASENAMES):
            f = d / name
            if f.is_file():
                files_direct.add(f)

    found: set[str] = set()
    resolved_root = repo_root.resolve()
    for f in files_direct:
        if not f.is_file():
            continue
        try:
            found.add(f.resolve().relative_to(resolved_root).as_posix())
        except ValueError:
            continue
    return sorted(found)


def _git_capture_lines(
    args: list[str], cwd: pathlib.Path
) -> list[str] | None:
    """Run a git command; return stdout lines, or None on any failure.

    None (as opposed to ``[]``) signals "git could not answer" — callers
    in the SC8 trust check fail CLOSED on None (everything untrusted)
    because a repo where provenance cannot be established cannot grant
    trust.
    """
    try:
        completed = subprocess.run(
            ["git", *args],
            cwd=str(cwd),
            capture_output=True,
            text=True,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if completed.returncode != 0:
        return None
    return (completed.stdout or "").splitlines()


def _strip_porcelain_quotes(path_field: str) -> str:
    """Unquote a ``git status --porcelain`` path field (best-effort)."""
    path_field = path_field.strip()
    if len(path_field) >= 2 and path_field[0] == '"' and path_field[-1] == '"':
        path_field = path_field[1:-1]
    return path_field


def untrusted_pytest_trust_surface(
    selectors: Iterable[str], repo_root: pathlib.Path | None = None
) -> list[str]:
    """Trust-surface files whose green run must NOT be trusted.

    A `pytest_trust_surface` file is untrusted unless it is BOTH
    tracked in git (``git ls-files`` — immune to .gitignore games:
    an ignored conftest never shows in status but is also not
    tracked) AND clean in ``git status --porcelain`` (covers
    unstaged + staged modifications + deletions vs HEAD).

    Fail-closed: if git itself cannot answer (not a repo, git missing),
    the ENTIRE existing surface is returned untrusted — provenance that
    cannot be established cannot grant trust. An EMPTY surface (no
    conftest/ini anywhere on the chains) returns ``[]``: with nothing
    to collude through, green is green.
    """
    if repo_root is None:
        repo_root = _repo_root()
    repo_root = pathlib.Path(repo_root)
    surface = pytest_trust_surface(selectors, repo_root)
    if not surface:
        return []

    tracked = _git_capture_lines(["ls-files", "--", *surface], repo_root)
    status = _git_capture_lines(
        ["status", "--porcelain", "--untracked-files=all", "--", *surface],
        repo_root,
    )
    if tracked is None or status is None:
        return list(surface)

    tracked_set = {line.strip() for line in tracked if line.strip()}
    dirty: set[str] = set()
    for line in status:
        if len(line) < 4:
            continue
        path_field = line[3:]
        if " -> " in path_field:
            old, _, new = path_field.partition(" -> ")
            dirty.add(_strip_porcelain_quotes(old))
            dirty.add(_strip_porcelain_quotes(new))
        else:
            dirty.add(_strip_porcelain_quotes(path_field))

    return [p for p in surface if p not in tracked_set or p in dirty]




#: Closed classification vocabulary for ``tests_enabled`` entries at
#: execution/junction time (real_tests_at_junctions SC4). Plain strings
#: so they serialize directly into the CLI JSON envelopes.
COLLECT_COLLECTABLE = "collectable"
"""``pytest --collect-only`` exit 0 — at least one test collected. NOTE:
a collected-but-FAILING test is COLLECTABLE (it exits 1 at run time but
0 at collect time); the oracle grades resolvability, not greenness."""

COLLECT_NOT_COLLECTABLE = "not_collectable"
"""``pytest --collect-only`` exit 5 (no tests collected) or exit 4 with
an unrecognized-selector error ("ERROR: not found" — a phantom node-ID
inside an existing file, or a vanished path). The qa C.9 oracle signal:
this selector can NEVER pass because pytest cannot even find it."""

COLLECT_ERROR = "collect_error"
"""``pytest --collect-only`` exit 2-4 import/usage errors (e.g. the
module raises at import). Distinct from NOT_COLLECTABLE: the selector
RESOLVES but its module is broken — a real failure surface the retry
loop can legitimately iterate on, so it is NOT refused at entry."""


COLLECT_NOT_SELECTOR = "not_selector_shaped"
"""Failed the cheap `is_runnable_pytest_selector` pre-flight (design
prose, or names a path not on disk). The oracle is layered AFTER that
pre-flight (plan SC4: "the cheap shape check stays as pre-flight; the
oracle adds collectability truth") — entries classified here never
reach the collect probe."""

#: Classifications that satisfy a junction test_gate (advance-ok): a
#: selector that resolves, or a recognized typed gate command. Everything
#: else refuses advance.
ADVANCE_OK_CLASSIFICATIONS = frozenset(
    {COLLECT_COLLECTABLE, COLLECT_TYPED_COMMAND}
)

#: Subprocess timeout for one ``pytest --collect-only`` probe. Collection
#: is import-time-only work; 120s is generous even for heavy conftests.
_COLLECT_ONLY_PROBE_TIMEOUT_S = 120


def collect_only_probe(
    selector: str,
    cwd: pathlib.Path | None = None,
    timeout_s: int = _COLLECT_ONLY_PROBE_TIMEOUT_S,
) -> str:
    """Classify ``selector`` collectability via ``pytest --collect-only -q``.

    The qa C.9 resolvability oracle (plan SC4): pure function over
    ``(selector, cwd)`` — runs a deterministic subprocess and maps its
    exit code to the closed classification vocabulary above:

    - exit 0  → `COLLECT_COLLECTABLE` (≥1 test collected; pytest exits 5,
      never 0, when nothing collects)
    - exit 5  → `COLLECT_NOT_COLLECTABLE` (no tests collected)
    - exit 4 + "not found" in output → `COLLECT_NOT_COLLECTABLE`
      (unrecognized selector: phantom node-ID in an existing file —
      pytest reports ``ERROR: not found: <selector>`` — or a path that
      vanished between the shape check and the probe)
    - exit 2-4 otherwise → `COLLECT_ERROR` (import/usage errors)

    Handles parametrized node-IDs with whitespace inside ``[...]``
    natively — the selector is passed as ONE argv element, no shell, so
    ``tests/x.py::test_p[has space id]`` reaches pytest intact (research
    F2.2/A4; the B.3 conditional resolved in plan SC4).

    ``-p no:cacheprovider`` keeps the probe side-effect-free (no
    ``.pytest_cache`` writes from probe runs). The child env is
    sanitized via `pytest_subprocess_env` — a ``PYTEST_PLUGINS`` import
    fires even on ``--collect-only``, and ``PYTEST_ADDOPTS`` could
    inject argv that skews the exit-code oracle.
    """
    if cwd is None:
        cwd = _repo_root()
    argv = [
        sys.executable,
        "-m",
        "pytest",
        "--collect-only",
        "-q",
        "-p",
        "no:cacheprovider",
        selector,
    ]
    result = subprocess.run(
        argv,
        cwd=str(cwd),
        env=pytest_subprocess_env(),
        capture_output=True,
        text=True,
        timeout=timeout_s,
        check=False,
    )
    if result.returncode == 0:
        return COLLECT_COLLECTABLE
    if result.returncode == 5:
        return COLLECT_NOT_COLLECTABLE
    if result.returncode == 4:
        output = (result.stdout or "") + (result.stderr or "")
        if "not found" in output:
            return COLLECT_NOT_COLLECTABLE
        return COLLECT_ERROR
    return COLLECT_ERROR


def classify_tests_enabled_entry(
    entry: str, repo_root: pathlib.Path | None = None
) -> str:
    """Layered classification of one ``tests_enabled`` entry (plan SC4).

    Ordering is load-bearing (the "pure upgrade" contract):

    1. Typed gate command — recognized by prefix, NO collect probe
       (it isn't pytest; probing it would be a category error).
    2. Cheap pre-flight — `is_runnable_pytest_selector` (shape + on-disk).
       Failures classify `COLLECT_NOT_SELECTOR` and NEVER reach the
       probe — the pre-flight is retained, the oracle layered after it.
    3. Collect-only probe — collectability truth for shape-valid
       selectors via `collect_only_probe`.
    """
    if entry.startswith(TYPED_GATE_COMMAND_PREFIX):
        return COLLECT_TYPED_COMMAND
    if repo_root is None:
        repo_root = _repo_root()
    if not is_runnable_pytest_selector(entry, repo_root):
        return COLLECT_NOT_SELECTOR
    return collect_only_probe(entry, cwd=repo_root)


def junction_collect_check(
    plan_dir: pathlib.Path,
    *,
    slug: str,
    junction_id: str,
    repo_root: pathlib.Path | None = None,
) -> dict[str, Any]:
    """Junction-time consolidated collect-check (plan SC4 + SC6).

    Loads the orchestrator, resolves the junction's covering set via
    T4's `junction_covering_set` (explicit ``covers[]`` verbatim, else
    the documented all-prior-tasks-through-``after_task`` default),
    unions the covered tasks' ``tests_enabled`` through T2's
    `resolve_tests_enabled` (the canonical `<slug>_orchestrator.json`
    source — no split source), classifies every entry, and returns a
    structured verdict.

    advance-ok iff the union is NON-empty AND every entry classifies
    into `ADVANCE_OK_CLASSIFICATIONS` (collectable selector / recognized
    typed command). An empty union is NOT advance-ok — a test_gate whose
    covering set carries zero verifiable entries is exactly the
    silent-partial-coverage failure mode this slug exists to kill.

    A selector belonging to a task OUTSIDE the covering set never
    enters the union, so it can neither satisfy nor block the gate
    (closes qa C.3).

    Narrowed SC3 exemption (T6): covered tasks that declare the
    ``verification_kind:`` test_plan marker (per
    `bin._verify_plan.strict.task_verification_exemption`) are surfaced
    in the verdict's ``exempt_tasks`` list — recognized, with NO command
    dispatched against them. Advance semantics are deliberately
    UNCHANGED: an exempt task contributes nothing to the union (it
    neither satisfies nor blocks), and a covering set of ONLY exempt
    tasks still refuses with ``empty_union`` — a *test_gate* over zero
    runnable tests is vacuous regardless of why; plans whose junction
    coverage is purely exempt/doc work should use a ``review_gate``
    junction instead. The ``exempt_tasks`` surfacing exists so the
    refusal diagnostic shows the operator/planner WHY the union is
    empty.

    Returns
    -------
    dict
        ``{slug, junction_id, junction_kind, covering_set, entries:
        [{entry, task_ids, classification}, ...], exempt_tasks:
        [{task_id, verification_kind}, ...], advance_ok,
        refusal_reason}`` — ``refusal_reason`` is ``None`` on
        advance-ok, else ``"empty_union"`` or
        ``"not_collectable_entries"``.

    Raises
    ------
    ValueError
        ``junction_id`` not present in the orchestrator's
        ``junctions[]``; or covering-set resolution failed (bogus
        ``covers[]`` entry / unresolvable ``after_task`` — propagated
        from `junction_covering_set`).
    """
    # Lazy imports: both modules are SDK-free, but keeping them local
    # mirrors how main.py defers cross-package imports to call time.
    from bin._orchestrator_query.orchestrator_loader import load_orchestrator
    from bin._orchestrator_query.orchestrator_loader import (
        junction_covering_set,
    )
    from bin._retry_loop.briefing import resolve_tests_enabled

    plan_dir = pathlib.Path(plan_dir)
    orchestrator = load_orchestrator(plan_dir, slug)

    junctions = orchestrator.get("junctions", []) or []
    junction = next(
        (j for j in junctions if isinstance(j, dict) and j.get("id") == junction_id),
        None,
    )
    if junction is None:
        known = [j.get("id", "<unknown>") for j in junctions if isinstance(j, dict)]
        raise ValueError(
            f"junction {junction_id!r} not found in {slug!r} orchestrator "
            f"(known junctions: {known})"
        )

    covering_set = junction_covering_set(orchestrator, junction)

    # Narrowed SC3 exemption surfacing (T6): covered tasks declaring the
    # verification_kind: marker are reported, never dispatched (there is
    # no runner behind the marker — that is the point of the narrow
    # branch). Pure recognition via the strict.py single-source helper.
    tasks_by_id = {
        t.get("id"): t
        for t in orchestrator.get("tasks", []) or []
        if isinstance(t, dict)
    }
    exempt_tasks: list[dict[str, str]] = []
    for task_id in covering_set:
        kind = task_verification_exemption(tasks_by_id.get(task_id) or {})
        if kind is not None:
            exempt_tasks.append(
                {"task_id": task_id, "verification_kind": kind}
            )

    # Union the covering set's tests_enabled, first-seen order, tracking
    # which task(s) contributed each entry. Falsy entries are dropped
    # (mirrors read_tests_enabled_union — only non-empty strings count).
    contributed_by: dict[str, list[str]] = {}
    for task_id in covering_set:
        for entry in resolve_tests_enabled(plan_dir, slug, task_id):
            if not entry:
                continue
            contributed_by.setdefault(entry, []).append(task_id)

    entries = [
        {
            "entry": entry,
            "task_ids": task_ids,
            "classification": classify_tests_enabled_entry(entry, repo_root),
        }
        for entry, task_ids in contributed_by.items()
    ]

    failing = [
        e for e in entries
        if e["classification"] not in ADVANCE_OK_CLASSIFICATIONS
    ]
    if not entries:
        advance_ok, refusal_reason = False, "empty_union"
    elif failing:
        advance_ok, refusal_reason = False, "not_collectable_entries"
    else:
        advance_ok, refusal_reason = True, None

    return {
        "slug": slug,
        "junction_id": junction_id,
        "junction_kind": junction.get("kind"),
        "covering_set": covering_set,
        "entries": entries,
        "exempt_tasks": exempt_tasks,
        "advance_ok": advance_ok,
        "refusal_reason": refusal_reason,
    }


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
        'diff_excerpt': str, 'repair_scope_violations': list[dict]}``.
        ``repair_scope_violations`` is empty unless ``SPLOCK_PHASE`` equals
        ``REPAIR_GUARD_PHASE``, in which case it carries whatever the
        fixer-must-not-author-tests guard reverted or flagged.
        ``cost_usd`` comes from
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

    # fixer-must-not-author-tests (SC8): the Phase-5 repair spawn must not
    # author the very files the gate grades it against. Snapshot the protected
    # surface before the session; revert/flag after. Phase 4 (/code) routes
    # through this same spawner but is EXEMPT — the task coder legitimately
    # authors its tests_enabled files (TDD).
    guard_phase = merged_env.get("SPLOCK_PHASE")
    scope_snapshot: dict[str, Any] | None = None
    if guard_phase == REPAIR_GUARD_PHASE:
        scope_snapshot = snapshot_repair_write_scope(cwd, slug=plan_slug)
    repair_scope_violations: list[dict[str, str]] = []

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

    try:
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
    finally:
        # Enforcement runs even when the SDK call errored: a session that
        # crashed AFTER fabricating files must still get them reverted.
        if scope_snapshot is not None:
            repair_scope_violations = enforce_repair_write_scope(cwd, scope_snapshot)
            if repair_scope_violations:
                logger.warning(
                    "spawn_opus_via_sdk[%s]: repair write-scope violations "
                    "reverted/flagged (fixer-must-not-author-tests): %s",
                    plan_slug,
                    repair_scope_violations,
                )

    cost = getattr(result_message, "total_cost_usd", None)
    return {
        "cost_usd": float(cost) if cost is not None else 0.0,
        "test_files_edited": diff_payload["test_files_edited"],
        "diff_lines_added": diff_payload["diff_lines_added"],
        "diff_lines_removed": diff_payload["diff_lines_removed"],
        "diff_excerpt": diff_payload["diff_excerpt"],
        "repair_scope_violations": repair_scope_violations,
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
