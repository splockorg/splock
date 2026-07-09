"""qa CLI directive plumbing — wrap behaviour at the prompt-template layer.

Per std_command_operator_extensions task TE (covers SC5 qa half + SC10 qa
half + research R4 option 1 — data-not-instructions). Three contracts:

1. Absent directive renders an empty slot — no `<operator-directive>`
   substring anywhere in the user prompt.
2. Present directive renders the wrap from `external_input_sanitize.wrap`
   verbatim — i.e., `<operator-directive>\\n{payload}\\n</operator-directive>`
   appears in the user prompt.
3. Directive over 8KB (8193+ bytes UTF-8) raises `_UsageError` from
   `bin._qa.main._build_inputs` BEFORE the SDK call, with the SC10 message
   `directive exceeds 8KB limit (<actual> bytes); shorten or split`.
4. Directive payload is NOT modified beyond the wrap (no escaping, no
   trimming, no normalization) — byte-equality between the wrapped slice
   in the prompt and `wrap(payload, "operator-directive")`.

These tests pin the contract at the boundary between `bin._qa.main` (CLI
+ size cap) and `bin._qa.prompt_templates.render_qa_user` (wrap +
template substitution) so future refactors keep the wrap shape stable
and the size cap impossible to bypass.
"""

from __future__ import annotations

import argparse

import pytest

from bin._planner.external_input_sanitize import wrap
from bin._qa import main as qa_main
from bin._qa.prompt_templates import render_qa_user


# ----------------------------------------------------------------------
# (a) Absent directive — no <operator-directive> slot rendered
# ----------------------------------------------------------------------

def test_render_qa_user_omits_directive_when_none() -> None:
    """When `directive=None`, the rendered user prompt contains no
    `<operator-directive>` substring anywhere.

    Defends against a regression where the template renders an empty
    `<operator-directive></operator-directive>` block (which would
    pollute the model's attention with an empty data envelope)."""
    rendered = render_qa_user(
        slug="example_slug",
        rubric_wrapped="<qa-rubric>\nrubric body\n</qa-rubric>",
        subject_wrapped="<subject-under-review>\nsubject body\n</subject-under-review>",
        repo_state="(no repo-state summary provided)",
        directive=None,
    )
    assert "<operator-directive>" not in rendered, (
        "directive=None must not render an <operator-directive> block; "
        "the slot should expand to an empty string"
    )
    assert "</operator-directive>" not in rendered


def test_render_qa_user_omits_directive_when_default() -> None:
    """When the `directive` kwarg is omitted entirely (default None), the
    rendered user prompt contains no `<operator-directive>` substring.

    Pins the default-None semantics so callers that haven't been updated
    for TE still get the pre-TE behaviour (empty slot)."""
    rendered = render_qa_user(
        slug="example_slug",
        rubric_wrapped="<qa-rubric>\nrubric body\n</qa-rubric>",
        subject_wrapped="<subject-under-review>\nsubject body\n</subject-under-review>",
        repo_state="(no repo-state summary provided)",
    )
    assert "<operator-directive>" not in rendered


# ----------------------------------------------------------------------
# (b) Present directive — verbatim wrap shape
# ----------------------------------------------------------------------

def test_render_qa_user_wraps_directive_via_external_input_sanitize() -> None:
    """When `directive='focus on the gate logic'`, the rendered user
    prompt contains the exact bytes
    `<operator-directive>\\nfocus on the gate logic\\n</operator-directive>`
    — i.e., the output of `external_input_sanitize.wrap` verbatim.

    Asserts byte-equality of the wrapped slice rather than a substring
    pattern so future edits to the wrap shape are caught at this seam."""
    payload = "focus on the gate logic"
    rendered = render_qa_user(
        slug="example_slug",
        rubric_wrapped="<qa-rubric>\nrubric body\n</qa-rubric>",
        subject_wrapped="<subject-under-review>\nsubject body\n</subject-under-review>",
        repo_state="(no repo-state summary provided)",
        directive=payload,
    )
    expected_wrapped = wrap(payload, "operator-directive")
    assert expected_wrapped in rendered, (
        f"expected the wrapped directive ({expected_wrapped!r}) to appear "
        f"verbatim in the rendered user prompt; got:\n{rendered!r}"
    )


def test_render_qa_user_preserves_directive_payload_bytes() -> None:
    """The directive payload must NOT be modified beyond the wrap — no
    HTML escaping, no whitespace trimming, no newline normalization.

    Uses a payload with whitespace + special characters + multi-line
    content to assert byte-equality of the inner content."""
    payload = "  line one\n  line two: <html>&entity;</html>\n  trailing  "
    rendered = render_qa_user(
        slug="example_slug",
        rubric_wrapped="<qa-rubric>\nrubric body\n</qa-rubric>",
        subject_wrapped="<subject-under-review>\nsubject body\n</subject-under-review>",
        repo_state="(no repo-state summary provided)",
        directive=payload,
    )
    expected_wrapped = wrap(payload, "operator-directive")
    assert expected_wrapped in rendered, (
        "directive payload must be wrapped byte-for-byte without "
        "escaping, trimming, or normalization"
    )
    # Sanity: the original payload bytes appear in the rendered prompt
    # exactly as supplied.
    assert payload in rendered


# ----------------------------------------------------------------------
# (c) Size cap — 8192 OK, 8193 refused with SC10 message
# ----------------------------------------------------------------------

def _make_args(directive: str | None) -> argparse.Namespace:
    """Build a minimal argparse.Namespace matching the qa subparser shape."""
    return argparse.Namespace(
        step="qa",
        slug="example_slug",
        repo_state="(no repo-state summary provided)",
        directive=directive,
        chain_id=None,
        stdout=False,
    )


def test_qa_build_inputs_accepts_directive_at_8192_bytes(tmp_path) -> None:
    """A directive of exactly 8192 UTF-8 bytes must be accepted (boundary
    is inclusive — `> 8192` refuses, `== 8192` passes)."""
    # Construct a plan dir with a valid recon and no pre-existing qa.md
    plan_dir = tmp_path / "example_slug"
    plan_dir.mkdir()
    (plan_dir / "example_slug_recon.md").write_text(
        "# recon\nsome body\n", encoding="utf-8"
    )

    payload = "x" * 8192  # ASCII, so 8192 bytes UTF-8
    assert len(payload.encode("utf-8")) == 8192
    args = _make_args(payload)

    inputs = qa_main._build_inputs(plan_dir, "example_slug", args)

    assert inputs.directive == payload, (
        "8192-byte directive must pass through unchanged"
    )


def test_qa_build_inputs_refuses_directive_at_8193_bytes(tmp_path) -> None:
    """A directive of 8193 UTF-8 bytes must raise `_UsageError` with the
    SC10 message `directive exceeds 8KB limit (<actual> bytes); shorten
    or split`."""
    plan_dir = tmp_path / "example_slug"
    plan_dir.mkdir()
    (plan_dir / "example_slug_recon.md").write_text(
        "# recon\nsome body\n", encoding="utf-8"
    )

    payload = "x" * 8193
    assert len(payload.encode("utf-8")) == 8193
    args = _make_args(payload)

    with pytest.raises(qa_main._UsageError) as excinfo:
        qa_main._build_inputs(plan_dir, "example_slug", args)

    msg = str(excinfo.value)
    # Exact SC10 stderr message format
    assert "directive exceeds 8KB limit" in msg, (
        f"expected SC10 message in _UsageError; got: {msg!r}"
    )
    assert "(8193 bytes)" in msg, (
        f"SC10 message must include actual byte count; got: {msg!r}"
    )
    assert "shorten or split" in msg, (
        f"SC10 message must include guidance; got: {msg!r}"
    )


def test_qa_build_inputs_refuses_multibyte_directive_over_cap(tmp_path) -> None:
    """The cap is enforced on UTF-8 BYTE length, not character count.

    A multibyte-character payload whose char count is well under 8192 but
    whose byte length exceeds 8192 must be refused."""
    plan_dir = tmp_path / "example_slug"
    plan_dir.mkdir()
    (plan_dir / "example_slug_recon.md").write_text(
        "# recon\nsome body\n", encoding="utf-8"
    )

    # Each character is 3 bytes in UTF-8 (e.g., the en-dash U+2013 = 0xE2
    # 0x80 0x93). 2731 chars × 3 = 8193 bytes.
    payload = "–" * 2731
    assert len(payload) == 2731  # well under 8192 chars
    assert len(payload.encode("utf-8")) == 8193  # but over 8192 bytes
    args = _make_args(payload)

    with pytest.raises(qa_main._UsageError) as excinfo:
        qa_main._build_inputs(plan_dir, "example_slug", args)

    msg = str(excinfo.value)
    assert "directive exceeds 8KB limit" in msg
    assert "(8193 bytes)" in msg


def test_qa_build_inputs_accepts_none_directive(tmp_path) -> None:
    """`directive=None` (default) must pass through _build_inputs without
    a size check — the size cap only applies to non-None payloads."""
    plan_dir = tmp_path / "example_slug"
    plan_dir.mkdir()
    (plan_dir / "example_slug_recon.md").write_text(
        "# recon\nsome body\n", encoding="utf-8"
    )

    args = _make_args(None)
    inputs = qa_main._build_inputs(plan_dir, "example_slug", args)
    assert inputs.directive is None


# ----------------------------------------------------------------------
# (d) Refusal fires BEFORE any SDK call
# ----------------------------------------------------------------------

def test_qa_directive_size_cap_fires_before_sdk_call(tmp_path, monkeypatch) -> None:
    """The size cap is enforced in `_build_inputs` (synchronous, no SDK).

    Verifies the refusal happens before any code path that would invoke
    `client.messages.stream` or `client.messages.create` — pin via a
    monkeypatch that would raise if reached."""

    def _fail_if_called(*_a, **_kw):  # pragma: no cover — must NOT be called
        raise AssertionError(
            "SDK call must not be reached when directive exceeds the size cap"
        )

    # Patch the lazy default client constructor — if `invoke_qa` is reached,
    # this will be called and raise.
    import bin._qa.invoke as qa_invoke
    monkeypatch.setattr(qa_invoke, "_default_client", _fail_if_called)

    plan_dir = tmp_path / "example_slug"
    plan_dir.mkdir()
    (plan_dir / "example_slug_recon.md").write_text(
        "# recon\nsome body\n", encoding="utf-8"
    )

    args = _make_args("x" * 8193)
    with pytest.raises(qa_main._UsageError):
        qa_main._build_inputs(plan_dir, "example_slug", args)
