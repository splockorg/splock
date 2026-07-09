"""M — Exit-code discipline for `bin/_orchestrator_query/` (code_next_ready_pick T2).

Addresses failure modes F2.1 / F2.2 / F2.3 from the slug's plan (per
implplan call-1 §T2). Four checks:

  M.1 (`test_every_exit_code_referenced_from_main_py`) — F2.1 forward:
      every `EXIT_*` constant defined in `bin/_orchestrator_query/exit_codes.py`
      is referenced from `bin/_orchestrator_query/main.py`. Catches the
      "added a code, forgot to wire it" drift mode.

  M.2 (`test_every_exit_path_uses_declared_code`) — F2.1 reverse:
      every exit path in `main.py` resolves to a declared `EXIT_*`
      constant (either via `exit_codes.EXIT_*` attribute access or via
      `from .exit_codes import EXIT_*` name reference). Raw int literals
      are refused with the documented allowlist exception for SIGINT
      sentinel 130 (Unix convention 128 + 2; not part of the picker's
      closed-enum semantic) and 0 (universal OK fallthrough).

  M.3 (`test_disjoint_from_sibling_modules`) — F2.2: the picker's
      numeric exit-code set is disjoint from sibling chain-orchestrated
      CLI exit-code modules. Note: `bin/_jsonl_log/exit_codes.py` does
      not currently exist in the tree (referenced in the orchestrator
      JSON's call_sites speculatively); the disjoint check tolerates
      absence by skipping non-existent sibling modules per the picker
      module docstring §F2 note.

  M.4 (`test_each_code_has_docstring_rationale`) — F2.3: each
      `EXIT_*` constant has either (a) a `# ...` comment line
      immediately preceding the assignment, OR (b) appears in the
      module docstring's family table. Mirrors the convention found in
      `bin/_update_orchestrator/exit_codes.py` (docstring table) and
      the per-line `#` comments shipped in this module.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest


pytestmark = pytest.mark.acceptance


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


PICKER_MODULE_DIR = Path("bin") / "_orchestrator_query"
EXIT_CODES_REL = PICKER_MODULE_DIR / "exit_codes.py"
MAIN_REL = PICKER_MODULE_DIR / "main.py"


# Sibling chain-orchestrated CLI exit-code modules whose numeric sets the
# picker must NOT collide with. Per the picker module docstring's stated
# disjoint targets: `_update_orchestrator` (the write-side companion to
# the picker's read-side `_state.json` access) and `_jsonl_log` (named in
# the orchestrator JSON's call_sites; does not currently exist in tree
# — the test skips missing siblings).
#
# `_render_plan` is intentionally NOT included here even though its
# numeric set DOES overlap (code 11 = `EXIT_DRIFT` vs the picker's
# `EXIT_ORCHESTRATOR_JSON_MISSING`). That cross-CLI collision is
# handled at the J-registry master test layer (`INTENTIONAL_COLLISIONS`
# table) where the operator-facing context disambiguates by calling
# binary; the M test's scope is the picker's narrow local-namespace
# contract against its named sibling targets.
SIBLING_MODULES_REL = [
    Path("bin") / "_update_orchestrator" / "exit_codes.py",
    Path("bin") / "_jsonl_log" / "exit_codes.py",  # may not exist; skipped if so
]


# Raw int literals allowed in `return ...` / `sys.exit(...)` within main.py
# WITHOUT being declared in `exit_codes.py`. These are signal-class /
# universal sentinels that the closed enum doesn't own.
#
#   0   = universal OK fallthrough (e.g., argparse `--help` short-circuit)
#   130 = SIGINT sentinel per Unix convention (128 + 2). The picker emits
#         this on KeyboardInterrupt; it is NOT a picker exit code per
#         §F2 of code_next_ready_pick_plan.md.
RAW_INT_ALLOWLIST = frozenset({0, 130})


def _exit_code_constants(source: str) -> dict[str, int]:
    """Parse the module source; return {EXIT_* name: int value} for every
    top-level assignment whose target name starts with `EXIT_`."""
    tree = ast.parse(source)
    out: dict[str, int] = {}
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        if len(node.targets) != 1 or not isinstance(node.targets[0], ast.Name):
            continue
        name = node.targets[0].id
        if not name.startswith("EXIT_"):
            continue
        if not isinstance(node.value, ast.Constant) or not isinstance(node.value.value, int):
            continue
        out[name] = node.value.value
    return out


def _referenced_exit_names(source: str) -> set[str]:
    """Walk an AST; return every `EXIT_*` symbol referenced as either an
    `Attribute(value=Name('exit_codes'), attr='EXIT_*')` access or a bare
    `Name('EXIT_*')` reference (covers `from .exit_codes import EXIT_*`)."""
    tree = ast.parse(source)
    refs: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute):
            if (
                isinstance(node.value, ast.Name)
                and node.value.id == "exit_codes"
                and node.attr.startswith("EXIT_")
            ):
                refs.add(node.attr)
        elif isinstance(node, ast.Name):
            if node.id.startswith("EXIT_"):
                refs.add(node.id)
    return refs


def _exit_path_values(source: str) -> list[tuple[int, ast.expr]]:
    """Walk an AST; return [(lineno, expr_node)] for every value that
    flows to a process exit:

      - `return <expr>` inside any function definition (any function in
        main.py whose return value either is or feeds into the CLI exit
        code path)
      - `sys.exit(<arg>)` calls — collect the arg

    The reverse-coverage test then asserts each collected expr resolves
    to either a declared EXIT_* or an allowlisted raw int sentinel.
    """
    tree = ast.parse(source)
    out: list[tuple[int, ast.expr]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Return) and node.value is not None:
            out.append((node.lineno, node.value))
        elif isinstance(node, ast.Call):
            func = node.func
            if (
                isinstance(func, ast.Attribute)
                and func.attr == "exit"
                and isinstance(func.value, ast.Name)
                and func.value.id == "sys"
            ):
                if node.args:
                    out.append((node.lineno, node.args[0]))
    return out


def _classify_exit_value(value: ast.expr, declared_exits: set[str]) -> tuple[bool, str]:
    """Return (ok, reason). `ok=True` means the value is an acceptable
    exit-path expression. `reason` describes the verdict for assertion
    failure messages."""
    # Attribute: exit_codes.EXIT_*
    if isinstance(value, ast.Attribute):
        if (
            isinstance(value.value, ast.Name)
            and value.value.id == "exit_codes"
            and value.attr.startswith("EXIT_")
        ):
            if value.attr in declared_exits:
                return True, f"exit_codes.{value.attr}"
            return False, f"exit_codes.{value.attr} (not declared)"
        # An unrelated Attribute (e.g., `report.first_ready`, `exc.code`).
        # These are operator-data, not exit codes — exempt.
        return True, "non-exit-code Attribute"

    # Bare Name: imported via `from .exit_codes import EXIT_*`
    if isinstance(value, ast.Name):
        if value.id.startswith("EXIT_"):
            if value.id in declared_exits:
                return True, f"Name({value.id})"
            return False, f"Name({value.id}) (not declared)"
        # An unrelated local variable (e.g., `return parser` — non-int).
        return True, f"non-exit-code Name({value.id})"

    # Literal int — allowed only if in RAW_INT_ALLOWLIST.
    if isinstance(value, ast.Constant) and isinstance(value.value, int):
        if value.value in RAW_INT_ALLOWLIST:
            return True, f"allowlisted raw int {value.value}"
        return False, f"raw int literal {value.value} (code drift)"

    # Call: e.g., `sys.exit(main())` — delegating to another function
    # whose return value the test is independently checking. Exempt.
    if isinstance(value, ast.Call):
        return True, "delegating Call"

    # Anything else (e.g., conditional expressions, dict accesses,
    # `report.first_ready` for stdout payloads): non-exit-code; exempt.
    return True, f"non-exit-code expr ({type(value).__name__})"


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_every_exit_code_referenced_from_main_py(repo_root):
    """M.1 (F2.1 forward): each EXIT_* constant in exit_codes.py is referenced from main.py."""
    exit_codes_src = (repo_root / EXIT_CODES_REL).read_text(encoding="utf-8")
    main_src = (repo_root / MAIN_REL).read_text(encoding="utf-8")

    declared = set(_exit_code_constants(exit_codes_src))
    assert declared, (
        f"No EXIT_* constants found in {EXIT_CODES_REL} — exit_codes module "
        f"is empty or malformed"
    )

    referenced = _referenced_exit_names(main_src)

    unused = declared - referenced
    assert not unused, (
        f"EXIT_* constants declared in {EXIT_CODES_REL} but never referenced "
        f"from {MAIN_REL}:\n"
        + "\n".join(f"  {name}" for name in sorted(unused))
        + "\n\nPer F2.1 forward: each declared exit code MUST be wired into "
        + "the CLI dispatch. Unused constants are dead code that operator "
        + "documentation may claim is reachable."
    )


def test_every_exit_path_uses_declared_code(repo_root):
    """M.2 (F2.1 reverse): every exit path in main.py uses a declared EXIT_* or
    an allowlisted raw-int sentinel."""
    exit_codes_src = (repo_root / EXIT_CODES_REL).read_text(encoding="utf-8")
    main_src = (repo_root / MAIN_REL).read_text(encoding="utf-8")

    declared = set(_exit_code_constants(exit_codes_src))
    paths = _exit_path_values(main_src)
    assert paths, (
        f"AST walk of {MAIN_REL} produced zero exit-path expressions — "
        f"either main.py has no return / sys.exit() paths (unexpected for "
        f"a CLI) or the AST walker is broken"
    )

    bad: list[tuple[int, str]] = []
    for lineno, value in paths:
        ok, reason = _classify_exit_value(value, declared)
        if not ok:
            bad.append((lineno, reason))

    assert not bad, (
        f"Exit paths in {MAIN_REL} that do NOT resolve to a declared EXIT_* "
        f"constant (or allowlisted sentinel 0/130):\n"
        + "\n".join(f"  line {ln}: {r}" for ln, r in bad)
        + "\n\nPer F2.1 reverse: every exit-path return must reference the "
        + "closed-enum constants, not a raw int. Raw ints break the "
        + "operator-facing exit-code contract (the closed enum is the "
        + "single source of truth for `$?` semantics)."
    )


def test_disjoint_from_sibling_modules(repo_root):
    """M.3 (F2.2): orchestrator_query exit codes are disjoint from sibling
    chain-orchestrated CLI modules (where present)."""
    picker_src = (repo_root / EXIT_CODES_REL).read_text(encoding="utf-8")
    picker_codes = set(_exit_code_constants(picker_src).values())

    # Universal codes (0 = OK, 2 = USAGE) are shared by convention across
    # every CLI surface and don't constitute a "collision" in the operator-
    # facing sense. The disjoint check filters these out.
    UNIVERSAL = frozenset({0, 1, 2})

    overlaps: dict[str, set[int]] = {}
    for sibling_rel in SIBLING_MODULES_REL:
        sibling_path = repo_root / sibling_rel
        if not sibling_path.exists():
            # Per picker module docstring: `bin/_jsonl_log/exit_codes.py`
            # does not exist at time of writing. Skip silently.
            continue
        sibling_src = sibling_path.read_text(encoding="utf-8")
        sibling_codes = set(_exit_code_constants(sibling_src).values())
        shared = (picker_codes & sibling_codes) - UNIVERSAL
        if shared:
            overlaps[str(sibling_rel)] = shared

    # Per T2 brief: the picker IS out-of-chain (not invoked by
    # bin/chain-overnight), so the disjoint requirement is about
    # operator-cognitive load when the operator sees a non-zero `$?`
    # from `bin/orchestrator-next-ready` and grep's the wrong module's
    # exit_codes.py for the meaning. The acceptance suite's J-registry
    # master test (`test_acceptance_J_exit_code_registries_master.py`)
    # handles the cross-chain registry-level collision allowlist; this
    # test enforces the picker's local-namespace contract.
    #
    # By design the picker's codes 10/11/12/13/20/21/22/23 OVERLAP with
    # several chain-orchestrated modules' codes — that's the documented
    # F2.2 "scope-disambiguated by calling binary" pattern. The disjoint
    # test asserts the picker's set does not collide with the universe
    # of in-scope sibling modules' NON-UNIVERSAL codes; per the T2 brief
    # the only siblings to check are `_update_orchestrator` and
    # `_jsonl_log` (which is absent), since those are the two named
    # references in the orchestrator JSON's call_sites.
    #
    # `_update_orchestrator` uses 4, 5, 8, 10, 19, 29, 30; the picker
    # uses 10, 11, 12, 13, 20, 21, 22, 23. The intersection is {10}.
    # Both 10s mean different things by intent (picker = SLUG_NOT_FOUND,
    # update_orch = PHASE_BOUNDARY_HALT); this is a documented collision
    # already allowlisted in the J-registry master test's
    # INTENTIONAL_COLLISIONS table.
    #
    # Per T2 brief acceptance: this test enforces the local-module
    # disjoint contract — sibling modules check is a regression net for
    # "did someone shrink the picker's reserved-gap window into a new
    # collision class that the J-registry's allowlist has not yet
    # blessed". The check filters _update_orchestrator's intentional
    # PHASE_BOUNDARY_HALT collision from the failure list since the
    # J-registry test owns that allowlist surface.
    EXPECTED_INTENTIONAL_OVERLAPS = {
        str(Path("bin") / "_update_orchestrator" / "exit_codes.py"): {10},
    }
    unexpected: dict[str, set[int]] = {}
    for path, codes in overlaps.items():
        expected = EXPECTED_INTENTIONAL_OVERLAPS.get(path, set())
        extras = codes - expected
        if extras:
            unexpected[path] = extras

    assert not unexpected, (
        "orchestrator_query exit codes collide with sibling modules outside "
        "the documented intentional-collision allowlist:\n"
        + "\n".join(f"  {p}: shared codes {sorted(c)}" for p, c in unexpected.items())
        + "\n\nResolve by either:\n"
        + "  (1) renumbering the picker's offending code to a reserved-gap "
        + "slot (per §F2: codes 1, 3-9, 14-19, 24+ are reserved), OR\n"
        + "  (2) adding the collision to "
        + "`test_acceptance_J_exit_code_registries_master.py`'s "
        + "INTENTIONAL_COLLISIONS table AND updating "
        + "EXPECTED_INTENTIONAL_OVERLAPS above to reflect the cross-CLI "
        + "operator-facing semantic."
    )


def test_each_code_has_docstring_rationale(repo_root):
    """M.4 (F2.3): each EXIT_* constant has either a preceding `#` comment OR
    a documented entry in the module docstring's family table."""
    src_path = repo_root / EXIT_CODES_REL
    text = src_path.read_text(encoding="utf-8")
    tree = ast.parse(text)
    lines = text.splitlines()

    # Extract module docstring (first node if it's an Expr(Constant(str))).
    module_doc = ast.get_docstring(tree) or ""

    constants = _exit_code_constants(text)
    assert constants, (
        f"No EXIT_* constants found in {EXIT_CODES_REL} — exit_codes module "
        f"is empty or malformed"
    )

    missing: list[str] = []
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        if len(node.targets) != 1 or not isinstance(node.targets[0], ast.Name):
            continue
        name = node.targets[0].id
        if name not in constants:
            continue

        # Check (a): preceding `#` comment line(s) — walk upward from
        # the assignment line until a non-comment / non-blank line is hit.
        # `node.lineno` is 1-indexed; lines list is 0-indexed.
        preceding_idx = node.lineno - 2  # the line ABOVE the assignment
        has_comment = False
        while preceding_idx >= 0:
            stripped = lines[preceding_idx].strip()
            if stripped.startswith("#"):
                has_comment = True
                break
            if stripped == "":
                preceding_idx -= 1
                continue
            break
        if has_comment:
            continue

        # Check (b): family name appears in the module docstring.
        # The family is derived as `name[len('EXIT_'):].lower()`, matching
        # the J-registry test's `_constant_to_family_name` convention.
        family = name[len("EXIT_"):].lower()
        if family in module_doc.lower() or name in module_doc:
            continue

        missing.append(name)

    assert not missing, (
        f"EXIT_* constants in {EXIT_CODES_REL} that lack BOTH a preceding "
        f"`# ...` comment AND a docstring-table entry:\n"
        + "\n".join(f"  {name}" for name in missing)
        + "\n\nPer F2.3: each closed-enum exit code MUST carry a rationale "
        + "so future operators / debuggers can disambiguate the meaning "
        + "from $? alone. Acceptable forms: (a) a `# ...` comment line "
        + "directly above the assignment, or (b) an entry in the module "
        + "docstring's family/source table."
    )
