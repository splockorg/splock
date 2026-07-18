"""`bin/eli5` CLI — flags, exit codes, prompt-file mechanics, invoke seam.

The SDK call is monkeypatched throughout (`bin._eli5.main.invoke_eli5`);
one invoke-level test drives `invoke_eli5` with a fake streaming client
to pin the prompt-assembly contract (wrap kinds + the `<eli5-format>`
internal delimiter)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from bin._eli5 import exit_codes, invoke as invoke_mod, main as main_mod  # noqa: E402
from bin._eli5.format import MODES, build_format  # noqa: E402
from bin._eli5.invoke import Eli5Result, Eli5SdkFailed, invoke_eli5  # noqa: E402
from bin._eli5.main import main as eli5_main  # noqa: E402

DECISION_BRIEFING = """\
Subject: `the backend decision…`

### 1. Nobody picked a backend (source: B.1)

**ELI5:** plain words here.

**Example:** it bites like this.

**Impact:** planning stalls.

**TL;DR:** pick one.

**Options:**
- **1-A (recommended)** — pick discovery — cheapest decisive answer.
- **1-B** — extend the seed — real upstream work first.

### Decision sheet

Reply like: `1-A`
"""

INFORMATIVE_BRIEFING = """\
### 1. What the cache actually does

**ELI5:** it remembers answers.

**Example:** second lookup is instant.

**Impact:** none to decide.

**TL;DR:** memory, not magic.
"""


def _stub_result(md: str) -> Eli5Result:
    return Eli5Result(briefing_md=md, cost_usd=0.01, model_id="stub-model")


@pytest.fixture()
def subject_file(tmp_path) -> Path:
    p = tmp_path / "subject.md"
    p.write_text("## findings\n\nsomething dense worth translating\n",
                 encoding="utf-8")
    return p


# ---------------------------------------------------------------------------
# deterministic helper surfaces
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("mode", MODES)
def test_print_format_emits_build_format_bytes(mode, capsys) -> None:
    assert eli5_main(["--print-format", mode]) == exit_codes.EXIT_OK
    assert capsys.readouterr().out == build_format(mode)


def test_next_prompt_path_helper(tmp_path, capsys) -> None:
    (tmp_path / "_eli5_prompt_2.txt").write_text("x", encoding="utf-8")
    assert eli5_main(["--next-prompt-path", str(tmp_path)]) == exit_codes.EXIT_OK
    assert capsys.readouterr().out.strip().endswith("_eli5_prompt_3.txt")
    assert eli5_main(["--next-prompt-path", str(tmp_path / "nope")]) == \
        exit_codes.EXIT_USAGE


# ---------------------------------------------------------------------------
# usage + subject gates
# ---------------------------------------------------------------------------


def test_missing_subject_file_is_usage(capsys) -> None:
    assert eli5_main([]) == exit_codes.EXIT_USAGE
    err = json.loads(capsys.readouterr().err.strip().splitlines()[-1])
    assert err["error"] == "usage" and "--subject-file" in err["detail"]


def test_prompt_file_without_out_is_usage_and_names_the_path(
        subject_file, capsys) -> None:
    rc = eli5_main(["--subject-file", str(subject_file), "--prompt-file"])
    assert rc == exit_codes.EXIT_USAGE
    err = json.loads(capsys.readouterr().err.strip().splitlines()[-1])
    assert "eli5_prompt_" in err["detail"]  # prints the path it would have used


def test_unknown_mode_is_usage(subject_file) -> None:
    assert eli5_main(["--subject-file", str(subject_file),
                      "--mode", "verbose"]) == exit_codes.EXIT_USAGE


def test_missing_subject_path_exits_49(tmp_path, capsys) -> None:
    rc = eli5_main(["--subject-file", str(tmp_path / "absent.md")])
    assert rc == exit_codes.EXIT_SUBJECT_UNREADABLE
    err = json.loads(capsys.readouterr().err.strip().splitlines()[-1])
    assert err["error"] == "subject_unreadable"


def test_empty_subject_exits_49(tmp_path) -> None:
    p = tmp_path / "empty.md"
    p.write_text("   \n\n  ", encoding="utf-8")
    assert eli5_main(["--subject-file", str(p)]) == \
        exit_codes.EXIT_SUBJECT_UNREADABLE


# ---------------------------------------------------------------------------
# the run (SDK stubbed)
# ---------------------------------------------------------------------------


def test_run_prints_briefing_and_writes_out(subject_file, tmp_path,
                                            monkeypatch, capsys) -> None:
    seen: dict = {}

    def fake_invoke(subject_md, *, mode, focus=None, client=None):
        seen.update(subject=subject_md, mode=mode, focus=focus)
        return _stub_result(INFORMATIVE_BRIEFING)

    monkeypatch.setattr(main_mod, "invoke_eli5", fake_invoke)
    out = tmp_path / "brief.md"
    rc = eli5_main(["--subject-file", str(subject_file),
                    "--focus", "just the cache part",
                    "--mode", "informative", "--out", str(out)])
    assert rc == exit_codes.EXIT_OK
    assert "What the cache actually does" in capsys.readouterr().out
    assert out.read_text(encoding="utf-8") == INFORMATIVE_BRIEFING
    assert seen["mode"] == "informative"
    assert seen["focus"] == "just the cache part"
    assert "something dense" in seen["subject"]


def test_over_cap_subject_reaches_invoke_truncated(tmp_path, monkeypatch,
                                                   capsys) -> None:
    big = tmp_path / "big.md"
    big.write_text("\n\n".join("w" * 300 for _ in range(60)), encoding="utf-8")
    seen: dict = {}

    def fake_invoke(subject_md, *, mode, focus=None, client=None):
        seen["subject"] = subject_md
        return _stub_result(INFORMATIVE_BRIEFING)

    monkeypatch.setattr(main_mod, "invoke_eli5", fake_invoke)
    assert eli5_main(["--subject-file", str(big)]) == exit_codes.EXIT_OK
    assert "chars omitted]" in seen["subject"]
    assert len(seen["subject"].encode("utf-8")) <= 8192
    assert "subject truncated at 8KB" in capsys.readouterr().err


def test_prompt_file_writes_sheet_beside_out(subject_file, tmp_path,
                                             monkeypatch, capsys) -> None:
    monkeypatch.setattr(main_mod, "invoke_eli5",
                        lambda *a, **k: _stub_result(DECISION_BRIEFING))
    out = tmp_path / "brief.md"
    rc = eli5_main(["--subject-file", str(subject_file),
                    "--out", str(out), "--prompt-file"])
    assert rc == exit_codes.EXIT_OK
    sheet = tmp_path / "brief_prompt.txt"
    body = sheet.read_text(encoding="utf-8")
    assert body.startswith("Reply with option codes")
    assert "**1-A (recommended)**" in body  # full briefing rides in the sheet


def test_prompt_file_zero_decisions_writes_nothing(subject_file, tmp_path,
                                                   monkeypatch, capsys) -> None:
    monkeypatch.setattr(main_mod, "invoke_eli5",
                        lambda *a, **k: _stub_result(INFORMATIVE_BRIEFING))
    out = tmp_path / "brief.md"
    rc = eli5_main(["--subject-file", str(subject_file),
                    "--out", str(out), "--prompt-file"])
    assert rc == exit_codes.EXIT_OK
    assert not (tmp_path / "brief_prompt.txt").exists()
    assert "no decisions — nothing to sheet" in capsys.readouterr().err


def test_sdk_failure_exits_17(subject_file, monkeypatch, capsys) -> None:
    def exploding(*a, **k):
        raise Eli5SdkFailed(detail="messages.stream raised: boom")

    monkeypatch.setattr(main_mod, "invoke_eli5", exploding)
    rc = eli5_main(["--subject-file", str(subject_file)])
    assert rc == exit_codes.EXIT_SDK_CALL_FAILED == 17  # same code as bin/qa
    err = json.loads(capsys.readouterr().err.strip().splitlines()[-1])
    assert err["error"] == "eli5_sdk_failed"


# ---------------------------------------------------------------------------
# invoke-level prompt assembly (fake streaming client)
# ---------------------------------------------------------------------------


class _FakeStream:
    def __init__(self, capture: dict, text: str):
        self._capture = capture
        self._text = text
        self.text_stream = iter(())

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def get_final_message(self):
        class _Block:
            def __init__(self, text): self.text = text

        class _Msg:
            content = [_Block(self._text)]
            usage = None
            model = "fake-model"

        return _Msg()


class _FakeClient:
    def __init__(self, capture: dict, text: str = "### 1. ok\n"):
        self._capture = capture
        self._text = text

        class _Messages:
            def stream(inner, **kwargs):
                capture.update(kwargs)
                return _FakeStream(capture, text)

        self.messages = _Messages()


def test_invoke_assembles_wrapped_prompt() -> None:
    capture: dict = {}
    result = invoke_eli5(
        "dense subject body",
        mode="decision",
        focus="only item B.2",
        client=_FakeClient(capture),
    )
    assert result.briefing_md == "### 1. ok\n"
    user = capture["messages"][0]["content"]
    # external inputs ride the WrapKind envelope…
    assert "<eli5-subject>\ndense subject body\n</eli5-subject>" in user
    assert "<operator-directive>\nonly item B.2\n</operator-directive>" in user
    # …the internal format rides the plain structural delimiter
    assert "<eli5-format>\n" in user
    assert build_format("decision") in user
    # system prompt pins the translation contract
    assert "NOTHING NEW" in capture["system"]


def test_invoke_refuses_empty_subject() -> None:
    with pytest.raises(Eli5SdkFailed):
        invoke_eli5("   ", client=_FakeClient({}))
