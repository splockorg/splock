"""T-D (SC-D #7) — REQUIRED verifier model pin.

The verifier subagent (the Ralph completion gate) MUST run on a pinned,
dated Haiku model. This is NOT an adopter-tunable knob: the gate's
determinism + cost contract depend on the exact pin. SC-D explicitly
calls out "preserve that test" — this file is that test, and it must
keep asserting the pin verbatim.

The pin lives in the verifier agent's YAML frontmatter
(``agents/verifier.md``: ``model: claude-haiku-4-5-20251001``). Any
config/templating/rename work in T-D MUST NOT weaken or parameterize it.

Run from the splock repo root with the project venv active.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
VERIFIER_MD = REPO_ROOT / "agents" / "verifier.md"

# The REQUIRED pin — a dated Haiku model id. Asserted verbatim.
REQUIRED_VERIFIER_MODEL = "claude-haiku-4-5-20251001"


def _frontmatter(text: str) -> str:
    """Return the YAML frontmatter block (between the first two ``---``)."""
    parts = text.split("---", 2)
    assert len(parts) >= 3, "verifier.md missing a YAML frontmatter block"
    return parts[1]


def test_verifier_md_exists():
    assert VERIFIER_MD.exists(), (
        f"verifier agent definition missing: {VERIFIER_MD} — the Ralph "
        "completion gate has no pinned model"
    )


def test_verifier_frontmatter_pins_required_dated_haiku():
    """The frontmatter ``model:`` field MUST equal the REQUIRED pin verbatim."""
    fm = _frontmatter(VERIFIER_MD.read_text(encoding="utf-8"))
    m = re.search(r"^\s*model:\s*(\S+)\s*$", fm, re.M)
    assert m is not None, "verifier.md frontmatter has no `model:` field"
    assert m.group(1) == REQUIRED_VERIFIER_MODEL, (
        f"verifier model pin is {m.group(1)!r}; REQUIRED {REQUIRED_VERIFIER_MODEL!r}. "
        "This pin is non-negotiable — do not weaken or parameterize it."
    )


def test_verifier_pin_is_dated_haiku_shape():
    """Defensive shape check: the pin is a *dated* Haiku id (not a bare alias).

    Guards against a future refactor swapping the dated pin for a floating
    alias like ``haiku`` (which would let the resolved model drift).
    """
    assert REQUIRED_VERIFIER_MODEL.startswith("claude-haiku-"), (
        "verifier pin must be a dated Haiku model id"
    )
    assert re.search(r"-\d{8}$", REQUIRED_VERIFIER_MODEL), (
        "verifier pin must carry an 8-digit date suffix (no floating alias)"
    )


def test_verifier_model_not_env_tunable():
    """The verifier model must NOT be read from an env var at runtime.

    The REQUIRED pin is a frontmatter contract. No ``os.environ`` /
    ``getenv`` read of an ``OVERNIGHT_VERIFIER_MODEL`` (or any verifier
    model var) may exist in the shipped Python — otherwise an adopter
    could silently downgrade the gate's model.
    """
    bin_dir = REPO_ROOT / "bin"
    offenders: list[str] = []
    pat = re.compile(r"(os\.environ(\.get)?\s*[\[(]|getenv\s*\()\s*['\"]\w*VERIFIER_MODEL")
    for py in bin_dir.rglob("*.py"):
        if "_env_inventory" in py.parts:
            # The env-inventory registry CATALOGS the var (documents its
            # default = the pin) but does not consume it; that is allowed.
            continue
        text = py.read_text(encoding="utf-8")
        for i, line in enumerate(text.splitlines(), start=1):
            if pat.search(line):
                offenders.append(f"{py.relative_to(REPO_ROOT)}:{i}: {line.strip()[:100]}")
    assert not offenders, (
        "verifier model read from env (would let adopters downgrade the gate):\n"
        + "\n".join(offenders)
    )
