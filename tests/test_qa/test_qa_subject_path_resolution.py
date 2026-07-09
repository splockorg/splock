"""qa CLI subject path resolution — `<slug>_<subject>.md`.

Per qa_review_target_generalization T1. The chosen `--subject` resolves
to a uniform `<slug>_<subject>.md` predecessor artifact under the plan
dir. These tests pin:

1. Each subject maps to exactly `<slug>_<subject>.md` (the path stem is
   the subject value verbatim).
2. The `plan` subject resolves to `<slug>_plan.md` (the rendered MD
   twin) and explicitly NOT `<slug>_plan.json` (the schema substrate) —
   the qa reviewer reviews prose, not JSON.
3. An unknown subject raises (the resolver is closed over `ALL_SUBJECTS`).

Resolution is exercised both at the pure helper level
(`subject_artifact_name`) and through `_build_inputs`, which is where the
CLI actually reads the artifact off disk.
"""

from __future__ import annotations

import argparse

import pytest

from bin._qa.main import _build_inputs
from bin._qa.subject import ALL_SUBJECTS, subject_artifact_name


# ----------------------------------------------------------------------
# Pure helper: subject_artifact_name
# ----------------------------------------------------------------------

@pytest.mark.parametrize("subject", sorted(ALL_SUBJECTS))
def test_artifact_name_is_slug_underscore_subject_md(subject: str) -> None:
    """`subject_artifact_name(slug, subject)` == `<slug>_<subject>.md`."""
    slug = "example_slug"
    assert subject_artifact_name(slug, subject) == f"{slug}_{subject}.md"


def test_plan_resolves_to_md_twin_not_json() -> None:
    """The `plan` subject resolves to `<slug>_plan.md` (rendered twin),
    NOT `<slug>_plan.json` (schema substrate). This is the load-bearing
    distinction called out in T1's test_plan."""
    slug = "example_slug"
    name = subject_artifact_name(slug, "plan")
    assert name == "example_slug_plan.md"
    assert name.endswith(".md")
    assert not name.endswith(".json"), (
        "qa-on-plan reviews the rendered MD twin, never the _plan.json "
        "substrate"
    )


def test_artifact_name_rejects_unknown_subject() -> None:
    """The resolver is closed over `ALL_SUBJECTS` — an unknown subject
    raises ValueError rather than silently fabricating a path."""
    with pytest.raises(ValueError):
        subject_artifact_name("example_slug", "implplan")


# ----------------------------------------------------------------------
# End-to-end: _build_inputs reads the subject-resolved artifact
# ----------------------------------------------------------------------

def _args_namespace(slug: str, *, subject: str):
    return argparse.Namespace(
        slug=slug,
        step="qa",
        subject=subject,
        repo_state="(no repo-state summary provided)",
        chain_id=None,
        stdout=False,
        reopen=False,
        new_file=False,
        directive=None,
    )


@pytest.mark.parametrize("subject", sorted(ALL_SUBJECTS))
def test_build_inputs_reads_the_subject_artifact(tmp_path, subject: str) -> None:
    """`_build_inputs` reads `<slug>_<subject>.md` for the chosen subject
    and packs both the body (subject_findings) and the selector (subject)."""
    slug = "pathres_slug"
    plan_dir = tmp_path / slug
    plan_dir.mkdir()
    body = f"# {subject} artifact\nbody-for-{subject}\n"
    (plan_dir / f"{slug}_{subject}.md").write_text(body, encoding="utf-8")

    args = _args_namespace(slug, subject=subject)
    result = _build_inputs(plan_dir, slug, args)

    assert result.subject == subject
    assert result.subject_findings == body


def test_build_inputs_plan_reads_md_not_json(tmp_path) -> None:
    """When `--subject plan`, `_build_inputs` reads `<slug>_plan.md`. A
    `<slug>_plan.json` present in the dir is ignored (must not be picked
    up as the review subject)."""
    slug = "planres_slug"
    plan_dir = tmp_path / slug
    plan_dir.mkdir()
    md_body = "# plan twin\nrendered prose\n"
    (plan_dir / f"{slug}_plan.md").write_text(md_body, encoding="utf-8")
    # A JSON substrate is present but must NOT be the subject.
    (plan_dir / f"{slug}_plan.json").write_text(
        '{"schema_version": 1}\n', encoding="utf-8"
    )

    args = _args_namespace(slug, subject="plan")
    result = _build_inputs(plan_dir, slug, args)

    assert result.subject == "plan"
    assert result.subject_findings == md_body
    assert "schema_version" not in result.subject_findings
