"""eli5 subject resolution + the 8KB truncation rule."""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from bin._eli5.subject import (  # noqa: E402
    DEFAULT_PRECEDENCE,
    SUBJECT_BYTE_CAP,
    SUBJECTS,
    artifact_name,
    resolve_slug_subject,
    truncate_subject,
)
from bin._planner.external_input_sanitize import wrap  # noqa: E402

SLUG = "some_slug"


def test_enum_is_eli5s_own_five_members() -> None:
    # deliberately NOT bin._qa.subject.ALL_SUBJECTS (4 members, no `qa`)
    assert SUBJECTS == ("recon", "qna", "research", "plan", "qa")
    assert DEFAULT_PRECEDENCE == ("qa", "plan", "recon")
    assert artifact_name(SLUG, "qa") == f"{SLUG}_qa.md"
    with pytest.raises(ValueError):
        artifact_name(SLUG, "orchestrator")


# ---------------------------------------------------------------------------
# slug-bound precedence
# ---------------------------------------------------------------------------


def _mk(tmp_path: Path, name: str, mtime: float | None = None) -> Path:
    p = tmp_path / name
    p.write_text("body\n", encoding="utf-8")
    if mtime is not None:
        os.utime(p, (mtime, mtime))
    return p


def test_precedence_is_stage_order_not_global_mtime(tmp_path) -> None:
    now = time.time()
    _mk(tmp_path, f"{SLUG}_recon.md", now)          # newest globally
    qa = _mk(tmp_path, f"{SLUG}_qa.md", now - 999)  # oldest globally
    assert resolve_slug_subject(tmp_path, SLUG) == qa  # qa stage still wins


def test_precedence_falls_through_qa_plan_recon(tmp_path) -> None:
    recon = _mk(tmp_path, f"{SLUG}_recon.md")
    assert resolve_slug_subject(tmp_path, SLUG) == recon
    plan = _mk(tmp_path, f"{SLUG}_plan.md")
    assert resolve_slug_subject(tmp_path, SLUG) == plan
    qa = _mk(tmp_path, f"{SLUG}_qa.md")
    assert resolve_slug_subject(tmp_path, SLUG) == qa


def test_within_stage_newest_numeric_variant_wins(tmp_path) -> None:
    now = time.time()
    _mk(tmp_path, f"{SLUG}_qa.md", now - 100)
    newest = _mk(tmp_path, f"{SLUG}_qa_2.md", now)
    # qa's per-subject variants are NOT candidates
    _mk(tmp_path, f"{SLUG}_qa_plan.md", now + 100)
    assert resolve_slug_subject(tmp_path, SLUG) == newest


def test_explicit_subject_pins_the_stage(tmp_path) -> None:
    _mk(tmp_path, f"{SLUG}_qa.md")
    research = _mk(tmp_path, f"{SLUG}_research.md")
    assert resolve_slug_subject(tmp_path, SLUG, "research") == research
    # research/qna unreachable by default — inputs, not verdicts
    assert resolve_slug_subject(tmp_path, SLUG, None) != research
    with pytest.raises(ValueError):
        resolve_slug_subject(tmp_path, SLUG, "verdict")


def test_nothing_resolves_returns_none(tmp_path) -> None:
    assert resolve_slug_subject(tmp_path, SLUG) is None


# ---------------------------------------------------------------------------
# truncation
# ---------------------------------------------------------------------------


def test_under_cap_is_untouched() -> None:
    text = "short subject\n\nwith two paragraphs"
    assert truncate_subject(text) == (text, 0)


def test_over_cap_truncates_at_paragraph_boundary_with_marker() -> None:
    paragraphs = [f"paragraph {i} " + "x" * 200 for i in range(80)]
    text = "\n\n".join(paragraphs)
    out, omitted = truncate_subject(text)
    assert omitted > 0
    assert out.endswith(f"[subject truncated at 8KB — {omitted} chars omitted]")
    kept = out.rsplit("\n\n", 1)[0]
    assert text.startswith(kept)              # tail-first: prefix preserved
    assert kept == kept.rstrip()              # clean paragraph-boundary cut
    assert omitted == len(text) - len(kept)   # N is chars omitted, exactly


def test_truncated_subject_fits_the_wrap_cap() -> None:
    text = "\n\n".join("y" * 300 for _ in range(60))
    out, _ = truncate_subject(text)
    # the WRAPPED bytes must clear bin/wrap's 8KB refusal
    assert len(out.encode("utf-8")) <= SUBJECT_BYTE_CAP
    wrapped = wrap(out, "eli5-subject")
    assert wrapped.startswith("<eli5-subject>\n")


def test_pathological_no_paragraph_breaks_still_fits() -> None:
    out, omitted = truncate_subject("z" * 20000)
    assert len(out.encode("utf-8")) <= SUBJECT_BYTE_CAP
    assert omitted > 0 and "chars omitted]" in out
