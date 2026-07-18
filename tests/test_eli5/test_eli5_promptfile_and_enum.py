"""Prompt-file numbering, wrap-enum membership, roster identity, terminology doc."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import get_args

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from bin._eli5.promptfile import (  # noqa: E402
    SHEET_HEADER,
    build_prompt_sheet,
    count_decision_items,
    next_prompt_path,
)
from bin._planner.external_input_sanitize import (  # noqa: E402
    DELIMITER_INSTRUCTION,
    WrapKind,
    wrap,
)


# ---------------------------------------------------------------------------
# prompt-file numbering (§6 contract)
# ---------------------------------------------------------------------------


def test_first_file_is_number_one(tmp_path) -> None:
    assert next_prompt_path(tmp_path).name == "_eli5_prompt_1.txt"


def test_numbering_is_one_plus_max_not_count(tmp_path) -> None:
    (tmp_path / "_eli5_prompt_1.txt").write_text("x", encoding="utf-8")
    (tmp_path / "_eli5_prompt_3.txt").write_text("x", encoding="utf-8")
    assert next_prompt_path(tmp_path).name == "_eli5_prompt_4.txt"


def test_numbering_ignores_non_conforming_names(tmp_path) -> None:
    for name in ("_eli5_prompt_04.txt",      # padded — not the contract
                 "_eli5_prompt_.txt",
                 "_eli5_prompt_2.md",
                 "eli5_prompt_9.txt"):
        (tmp_path / name).write_text("x", encoding="utf-8")
    assert next_prompt_path(tmp_path).name == "_eli5_prompt_1.txt"


def test_count_decision_items_counts_options_headers() -> None:
    md = ("### 1. a\n**Options:**\n- **1-A** — x — y\n"
          "### 2. b\n**TL;DR:** informative, no options\n"
          "### 3. c\n**Options:**\n- **3-A** — x — y\n")
    assert count_decision_items(md) == 2
    assert count_decision_items("no options anywhere") == 0
    # an inline mention is not a header
    assert count_decision_items("the **Options:** label mid-sentence") == 0


def test_sheet_is_header_plus_full_briefing() -> None:
    sheet = build_prompt_sheet("### 1. body\n")
    assert sheet.startswith(SHEET_HEADER)
    assert sheet.endswith("### 1. body\n")
    assert "Reply with option codes" in SHEET_HEADER


# ---------------------------------------------------------------------------
# wrap enum extension (§4 boundary)
# ---------------------------------------------------------------------------


def test_eli5_subject_is_a_wrap_kind() -> None:
    kinds = get_args(WrapKind)
    assert "eli5-subject" in kinds
    assert "operator-directive" in kinds
    assert len(kinds) == 8  # was 7; eli5-subject is the eighth


def test_delimiter_instruction_names_the_new_tag() -> None:
    assert "<eli5-subject>" in DELIMITER_INSTRUCTION


def test_wrap_emits_the_kebab_delimiter() -> None:
    assert wrap("body", "eli5-subject") == "<eli5-subject>\nbody\n</eli5-subject>"


def test_bin_wrap_cli_accepts_the_new_kind(capsys) -> None:
    from bin._wrap.main import main as wrap_main

    rc = wrap_main(["--kind", "eli5-subject", "--content", "hello"])
    assert rc == 0
    assert capsys.readouterr().out.strip() == "<eli5-subject>\nhello\n</eli5-subject>"


# ---------------------------------------------------------------------------
# roster + terminology doc (§2, §5)
# ---------------------------------------------------------------------------


def test_both_roster_copies_identical_and_carry_eli5() -> None:
    a = (REPO_ROOT / "agents" / "_roster.json").read_bytes()
    b = (REPO_ROOT / ".claude" / "agents" / "_roster.json").read_bytes()
    assert a == b, "roster copies drifted"
    roster = json.loads(a)
    assert "eli5" in roster["subagents"]
    assert roster["schema_version"] == 3
    assert "2026-07-18" in roster["_comment"]  # dated bump per protocol


def test_terminology_doc_pins_all_three_terms() -> None:
    doc = (REPO_ROOT / "docs" / "feedback_eli5_terminology.md")
    assert doc.is_file()
    text = doc.read_text(encoding="utf-8")
    for needle in ("**qa**", "**qna**", "**eli5**", "nothing new"):
        assert needle in text


def test_no_dangling_qa_vs_qna_citations_remain() -> None:
    for rel in ("agents/qna.md", "commands/qna.md",
                "agents/_roster.json", ".claude/agents/_roster.json"):
        text = (REPO_ROOT / rel).read_text(encoding="utf-8")
        assert "feedback_qa_vs_qna_terminology.md" not in text, rel
