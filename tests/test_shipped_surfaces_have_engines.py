"""A shipped surface must have something behind it.

The Phase-2 backport found six surfaces this repo advertised and did not have:

- `/qa` — `skills/qa/SKILL.md` + `commands/qa.md` + `agents/qa.md`, no `bin/_qa`
- `bin/log` — allowlisted in `KNOWN_WRITERS`, no engine
- `bin/lessons` — allowlisted in `KNOWN_WRITERS`, and shelled out to by the
  planner; its schema and entry template shipped too
- `bin/render_spans` — named by FOUR registries, including `hooks/sealed_paths.txt`,
  which sealed the output file of a program that was never shipped
- `bin/chain-status`, `bin/state-divergence-check` — cli-lint exemptions for CLIs
  that did not exist

Each was invisible because nothing checked. These tests check.

The related guard on `bin/_cli_lint/exemptions.py` lives in
`test_chain_status_and_divergence.py`, next to the CLIs it un-orphaned.
`KNOWN_WRITERS` deliberately carries no such guard: its entries are emitter
LABELS, not paths (`chain_driver_auto` is not a file), which that same module
pins.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from bin._env_paths import plugin_root
from bin._hooks import sealed_paths_file
from bin._hooks.sealed_paths import is_sealed, load_sealed_paths

_ROOT = plugin_root()

#: `bin/activate` is the venv activation script every wrapper sources — not a CLI.
_NOT_A_CLI = {"activate"}

#: Directories whose contents an agent must never rewrite: the prompts it runs on,
#: and the hooks that constrain it.
_SUBSTRATE = ("agents", "commands", "hooks", "skills")


def _frontmatter(text: str) -> str | None:
    match = re.match(r"^---\n(.*?)\n---", text, re.S)
    return match.group(1) if match else None


# --------------------------------------------------------------------------- #
# 1. skills declare themselves correctly                                        #
# --------------------------------------------------------------------------- #


def _skill_dirs() -> list[Path]:
    dirs = sorted(p for p in (_ROOT / "skills").iterdir() if p.is_dir())
    assert dirs, "no skills shipped"
    return dirs


@pytest.mark.parametrize("skill_dir", _skill_dirs(), ids=lambda p: p.name)
def test_every_skill_has_a_parseable_manifest(skill_dir: Path) -> None:
    """A `SKILL.md` whose frontmatter does not parse is a surface nobody can load."""
    manifest = skill_dir / "SKILL.md"
    assert manifest.is_file(), f"{skill_dir.name}: no SKILL.md"

    front = _frontmatter(manifest.read_text(encoding="utf-8"))
    assert front is not None, f"{skill_dir.name}: SKILL.md has no frontmatter block"

    name = re.search(r"^name:\s*(.+)$", front, re.M)
    assert name and name.group(1).strip() == skill_dir.name, (
        f"{skill_dir.name}: frontmatter `name` must equal the directory name"
    )

    description = re.search(r"^description:\s*(.+)$", front, re.M)
    assert description and description.group(1).strip(), (
        f"{skill_dir.name}: frontmatter `description` is missing or empty"
    )


# --------------------------------------------------------------------------- #
# 2. every CLI a shipped prompt names actually exists                           #
# --------------------------------------------------------------------------- #


def _cli_references() -> dict[str, set[str]]:
    refs: dict[str, set[str]] = {}
    for surface in ("skills", "commands", "agents"):
        for prompt in sorted((_ROOT / surface).glob("**/*.md")):
            for cli in re.findall(r"\bbin/([A-Za-z0-9_-]+)", prompt.read_text(errors="ignore")):
                if cli in _NOT_A_CLI:
                    continue
                refs.setdefault(cli, set()).add(str(prompt.relative_to(_ROOT)))
    return refs


def test_the_shipped_prompts_reference_some_clis_at_all() -> None:
    """Guards the guard: a broken regex would make every assertion below vacuous."""
    assert len(_cli_references()) >= 10


def test_every_cli_named_by_a_shipped_prompt_exists() -> None:
    """This is the `/qa` check.

    `skills/qa/SKILL.md`, `commands/qa.md` and `agents/qa.md` all routed through
    a `bin/qa` that did not exist. Every published entry point was dangling, and
    no test noticed.
    """
    dangling = {
        cli: sorted(citers)
        for cli, citers in _cli_references().items()
        if not (_ROOT / "bin" / cli).exists()
    }
    assert not dangling, f"shipped prompts name CLIs that do not exist: {dangling}"


def test_every_engine_package_named_by_a_wrapper_exists() -> None:
    """A `bin/<name>` wrapper that execs `bin._<pkg>.main` needs that package."""
    missing: list[str] = []
    for wrapper in sorted((_ROOT / "bin").iterdir()):
        if not wrapper.is_file() or wrapper.suffix:
            continue
        for pkg in re.findall(r"-m\s+bin\.(_[A-Za-z0-9_]+)\.", wrapper.read_text(errors="ignore")):
            if not (_ROOT / "bin" / pkg).is_dir():
                missing.append(f"{wrapper.name} -> bin/{pkg}")
    assert not missing, f"wrappers exec engine packages that do not exist: {missing}"


# --------------------------------------------------------------------------- #
# 3. the substrate an agent must not rewrite is actually sealed                  #
# --------------------------------------------------------------------------- #


@pytest.fixture(scope="module")
def seal_patterns():
    return load_sealed_paths(sealed_paths_file())


@pytest.mark.parametrize(
    "path",
    [
        "agents/coder.md",
        "commands/code.md",
        "hooks/sealed-paths.sh",
        "skills/qa/SKILL.md",
    ],
)
def test_the_shipped_substrate_is_sealed(path: str, seal_patterns) -> None:
    """The prompts an agent runs on, and the hooks that constrain it.

    Only the `.claude/`-nested spellings were sealed, so in this repo's flattened
    layout every one of these was freely editable — including the sealed-paths
    hook itself.
    """
    matched, _ = is_sealed(path, seal_patterns)
    assert matched, f"{path} is not sealed: an agent could rewrite its own substrate"


def test_the_seal_list_seals_itself(seal_patterns) -> None:
    matched, _ = is_sealed("hooks/sealed_paths.txt", seal_patterns)
    assert matched


def test_ordinary_source_is_not_sealed(seal_patterns) -> None:
    """The guard must not become a blanket refusal."""
    for path in ("src/app.py", "bin/plan", "README.md"):
        matched, _ = is_sealed(path, seal_patterns)
        assert not matched, f"{path} should be editable"


def test_no_substrate_seal_glob_matches_zero_paths() -> None:
    """A glob that matches nothing is a seal that protects nothing.

    `.claude/hooks/**` and `.claude/commands/**` matched zero paths here for the
    life of the fork. The adopter-scoped sections below are exempt — `~/.aws/**`
    and `.env` legitimately match nothing in this tree — but the substrate
    section describes THIS repo's own files.
    """
    text = sealed_paths_file().read_text(encoding="utf-8")
    after_header = text.split("# --- splock shipped substrate", 1)[1].split("\n", 1)[1]
    section = after_header.split("\n# ---")[0]
    globs = [
        line.strip()
        for line in section.splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]
    assert globs, "the substrate seal section is empty"

    for glob in globs:
        assert list(_ROOT.glob(glob)), f"substrate seal glob matches nothing: {glob!r}"
