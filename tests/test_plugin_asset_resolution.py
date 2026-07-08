"""Plugin-shipped asset resolution: templates + agent prompts.

Installed-plugin mode must find (a) the MD-twin templates the renderer
consumes and (b) the reviewer/coder agent prompts the SDK spawners load —
both shipped with the plugin, both overridable by an adopter repo that
commits its own copies. The template fallback is PER FILE so an adopter's
unrelated ``.claude/templates/`` never shadows the shipped set.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from bin._render_plan import md_renderer  # noqa: E402
from bin._retry_loop import sdk_spawners  # noqa: E402


def test_template_path_prefers_project_override_per_file(monkeypatch, tmp_path):
    project = tmp_path / "adopter"
    override = project / ".claude" / "templates"
    override.mkdir(parents=True)
    (override / "plan_md_canonical.md.template").write_text("mine", encoding="utf-8")
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(project))
    resolved = md_renderer._template_path("plan_md_canonical.md.template")
    assert resolved == override / "plan_md_canonical.md.template"


def test_template_path_falls_back_to_plugin_shipped(monkeypatch, tmp_path):
    project = tmp_path / "adopter"
    project.mkdir()
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(project))
    resolved = md_renderer._template_path("plan_md_canonical.md.template")
    assert resolved == REPO_ROOT / ".claude" / "templates" / "plan_md_canonical.md.template"


def test_unrelated_project_templates_dir_does_not_shadow_shipped_set(
    monkeypatch, tmp_path
):
    # Regression guard: the fallback must be per FILE. An adopter
    # .claude/templates/ carrying only its own unrelated templates must not
    # shadow the shipped splock set (a directory-level check would break
    # MD-twin rendering for exactly those adopters).
    project = tmp_path / "adopter"
    override = project / ".claude" / "templates"
    override.mkdir(parents=True)
    (override / "my_pr_template.md").write_text("unrelated", encoding="utf-8")
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(project))
    for kind in ("plan", "orchestrator", "state"):
        body = md_renderer._load_template(kind)
        assert body.strip(), f"kind={kind!r} failed to fall through per-file"


def test_agent_prompt_project_override_wins(tmp_path):
    agents = tmp_path / ".claude" / "agents"
    agents.mkdir(parents=True)
    (agents / "reviewer.md").write_text("override prompt body", encoding="utf-8")
    loaded = sdk_spawners._load_reviewer_system_prompt(tmp_path)
    assert loaded == "override prompt body"


def test_agent_prompt_falls_back_to_plugin_agents_dir(tmp_path):
    # cwd has no .claude/agents -> the plugin-shipped agents/<name>.md loads
    # (previously the terse inline fallback fired on every installed run).
    shipped = (REPO_ROOT / "agents" / "reviewer.md").read_text(encoding="utf-8")
    assert sdk_spawners._load_reviewer_system_prompt(tmp_path) == shipped
    shipped_coder = (REPO_ROOT / "agents" / "coder.md").read_text(encoding="utf-8")
    assert sdk_spawners._load_coder_system_prompt(tmp_path) == shipped_coder


def test_agent_prompt_inline_fallback_when_nothing_readable(monkeypatch, tmp_path):
    empty_cwd = tmp_path / "empty"
    empty_cwd.mkdir()
    monkeypatch.setattr(sdk_spawners, "_read_agent_prompt", lambda cwd, rel: None)
    loaded = sdk_spawners._load_reviewer_system_prompt(empty_cwd)
    assert "reviewer subagent" in loaded
