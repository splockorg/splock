"""T-C (SC-C #3) — framework-internal settings resolver test.

The pre-extraction code path was:

    from console import settings_registry
    from src.DAL import DAL
    settings_registry.resolve(knob, default=..., mysql=DAL.from_pool())

Both of those imports must be removed from a fresh-repo install. This
test asserts:

  1. :func:`bin._intent.settings.resolve` returns the documented
     default with no overlay file and no env var present.
  2. The env-var override layer (``SPLOCK_SETTING__a__b__c``) wins
     over the default.
  3. The JSON overlay file at
     ``${CLAUDE_PLUGIN_DATA}/intent_settings.json`` wins over the
     default but loses to the env-var.
  4. A malformed overlay JSON falls through to the default (never
     raises).
  5. Importing :mod:`bin._intent.settings` succeeds with **no**
     ``console`` package and **no** ``src.DAL`` package on the
     ``sys.path`` — proving the framework-internal claim.
  6. The 4 in-scope callers
     (``main._resolve_ttl_minutes``,
      ``hook_resolver._ttl_minutes``,
      ``doctor_trigger._resolve_interval_minutes``,
      ``_chain_resume.main._resolve_inject_max_bytes``)
     all return their documented defaults with no overlay / env / DAL.
"""

from __future__ import annotations

import importlib
import json
import pathlib
import sys

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from bin._intent import settings as intent_settings  # noqa: E402


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch, tmp_path):
    monkeypatch.setenv("CLAUDE_PLUGIN_DATA", str(tmp_path))
    # Wipe any inherited settings-overrides + the in-process cache.
    for key in list(__import__("os").environ):
        if key.startswith(intent_settings.ENV_PREFIX):
            monkeypatch.delenv(key, raising=False)
    intent_settings.invalidate_cache()
    yield
    intent_settings.invalidate_cache()


# ---------------------------------------------------------------------------
# 1. Documented-default round-trip
# ---------------------------------------------------------------------------


def test_resolve_returns_default_when_no_overlay_no_env():
    assert intent_settings.resolve("intent.ttl_minutes", 240) == 240
    assert intent_settings.resolve("intent.something_bool", True) is True
    assert intent_settings.resolve("intent.something_str", "halt") == "halt"


# ---------------------------------------------------------------------------
# 2. Env-var override
# ---------------------------------------------------------------------------


def test_env_var_override_int(monkeypatch):
    monkeypatch.setenv("SPLOCK_SETTING__intent__ttl_minutes", "777")
    assert intent_settings.resolve("intent.ttl_minutes", 240) == 777


def test_env_var_override_bool(monkeypatch):
    monkeypatch.setenv(
        "SPLOCK_SETTING__intent__sentinel_area_skip_collision", "false"
    )
    assert intent_settings.resolve(
        "intent.sentinel_area_skip_collision", True
    ) is False


def test_env_var_override_invalid_falls_through(monkeypatch):
    monkeypatch.setenv("SPLOCK_SETTING__intent__ttl_minutes", "not-an-int")
    assert intent_settings.resolve("intent.ttl_minutes", 240) == 240


# ---------------------------------------------------------------------------
# 3. JSON overlay layer
# ---------------------------------------------------------------------------


def test_overlay_layer_wins_over_default(tmp_path):
    overlay = intent_settings.overlay_path()
    overlay.parent.mkdir(parents=True, exist_ok=True)
    overlay.write_text(json.dumps({"intent.ttl_minutes": 99}), encoding="utf-8")
    intent_settings.invalidate_cache()
    assert intent_settings.resolve("intent.ttl_minutes", 240) == 99


def test_env_var_wins_over_overlay(tmp_path, monkeypatch):
    overlay = intent_settings.overlay_path()
    overlay.parent.mkdir(parents=True, exist_ok=True)
    overlay.write_text(json.dumps({"intent.ttl_minutes": 99}), encoding="utf-8")
    intent_settings.invalidate_cache()
    monkeypatch.setenv("SPLOCK_SETTING__intent__ttl_minutes", "555")
    assert intent_settings.resolve("intent.ttl_minutes", 240) == 555


# ---------------------------------------------------------------------------
# 4. Malformed overlay falls through silently
# ---------------------------------------------------------------------------


def test_malformed_overlay_falls_through_to_default(tmp_path):
    overlay = intent_settings.overlay_path()
    overlay.parent.mkdir(parents=True, exist_ok=True)
    overlay.write_text("{not valid json", encoding="utf-8")
    intent_settings.invalidate_cache()
    assert intent_settings.resolve("intent.ttl_minutes", 240) == 240


def test_missing_overlay_falls_through_to_default():
    # tmp_path autouse fixture sets CLAUDE_PLUGIN_DATA to an empty dir.
    assert not intent_settings.overlay_path().exists()
    assert intent_settings.resolve("intent.ttl_minutes", 240) == 240


# ---------------------------------------------------------------------------
# 5. No console / no src.DAL on import path
# ---------------------------------------------------------------------------


def test_settings_module_has_no_console_or_dal_imports():
    """The settings module's source MUST NOT contain ``from console``
    or ``from src.DAL`` import statements. Regression guard for SC-C #3.
    """
    src = pathlib.Path(intent_settings.__file__).read_text(encoding="utf-8")
    # Strip docstrings/comments by deleting their lines starts with #
    code_lines = [ln for ln in src.splitlines() if not ln.lstrip().startswith("#")]
    code_lines = [ln for ln in code_lines if "from console import" not in ln
                  or "replaces" in ln or "mirror" in ln]
    code_lines = [ln for ln in code_lines if "from src.DAL" not in ln
                  or "replaces" in ln or "no MySQL" in ln]
    # Authoritative regression check: an actual import statement
    # rather than a docstring mention. Split by line and check raw.
    import re
    raw = pathlib.Path(intent_settings.__file__).read_text(encoding="utf-8")
    assert not re.search(r"^\s*from\s+console\s+import", raw, re.M), (
        "settings.py still imports from console — SC-C #3 unmet"
    )
    assert not re.search(r"^\s*from\s+src\.DAL\s+import", raw, re.M), (
        "settings.py still imports from src.DAL — SC-C #3 unmet"
    )


def test_resolve_works_with_console_and_dal_absent(tmp_path):
    """Force ``console`` + ``src.DAL`` to be unimportable in a fresh
    Python subprocess; settings.resolve still returns the default.

    Subprocess isolation is the strongest version of this test: it
    proves the module imports + resolves cleanly when ``console`` /
    ``src.DAL`` aren't on the Python path at all, not merely when
    they're shadowed in the current interpreter.
    """
    import subprocess
    import textwrap

    script = textwrap.dedent(f"""
        import sys, builtins
        # Block any attempt to import console / src.DAL.
        _real_import = builtins.__import__
        def _blocked(name, *a, **kw):
            if name == "console" or name == "src.DAL" \\
                    or name.startswith("console."):
                raise ImportError("blocked for fresh-repo test: " + name)
            return _real_import(name, *a, **kw)
        builtins.__import__ = _blocked
        sys.path.insert(0, {str(REPO_ROOT)!r})
        from bin._intent import settings as s
        assert s.resolve("intent.ttl_minutes", 240) == 240
        assert s.resolve("intent.collision_halt_action", "halt") == "halt"
        print("OK")
    """)
    env = {
        "CLAUDE_PLUGIN_DATA": str(tmp_path),
        "PATH": "/usr/bin:/bin",
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    result = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True, text=True, env=env, timeout=15,
    )
    assert result.returncode == 0, (
        f"subprocess failed: stdout={result.stdout!r} stderr={result.stderr!r}"
    )
    assert "OK" in result.stdout


# ---------------------------------------------------------------------------
# 6. The 4 in-scope callers route through the framework-internal resolver
# ---------------------------------------------------------------------------


def test_main_resolve_ttl_minutes_returns_default():
    from bin._intent import main as intent_main
    assert intent_main._resolve_ttl_minutes() == 240


def test_hook_resolver_ttl_minutes_returns_default():
    from bin._intent import hook_resolver
    assert hook_resolver._ttl_minutes() == 240


def test_hook_resolver_soft_warn_enabled_default_true():
    from bin._intent import hook_resolver
    assert hook_resolver._soft_warn_enabled() is True


def test_doctor_trigger_interval_returns_default():
    from bin._intent import doctor_trigger
    assert doctor_trigger._resolve_interval_minutes() == 60


def test_chain_resume_inject_max_bytes_returns_default():
    from bin._chain_resume import main as chain_main
    assert chain_main._resolve_inject_max_bytes() == chain_main.INJECT_MAX_BYTES


def test_callers_pick_up_env_override(monkeypatch):
    """Override propagates from settings.resolve through every caller."""
    monkeypatch.setenv("SPLOCK_SETTING__intent__ttl_minutes", "1337")
    intent_settings.invalidate_cache()
    from bin._intent import main as intent_main
    from bin._intent import hook_resolver
    assert intent_main._resolve_ttl_minutes() == 1337
    assert hook_resolver._ttl_minutes() == 1337


# ---------------------------------------------------------------------------
# Source-level absence-grep for the 5 in-scope files (SC-C #3)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("module_relpath", [
    "bin/_intent/db.py",
    "bin/_intent/main.py",
    "bin/_intent/hook_resolver.py",
    "bin/_intent/settings.py",
    "bin/_intent/doctor_trigger.py",
    "bin/_chain_resume/main.py",
])
def test_no_actual_src_dal_or_console_imports(module_relpath):
    """None of the 6 in-scope files may carry a real ``from src.DAL``
    or ``from console`` import (docstring mentions are fine)."""
    import re
    src = (REPO_ROOT / module_relpath).read_text(encoding="utf-8")
    assert not re.search(
        r"^\s*from\s+src\.DAL\s+import", src, re.M
    ), f"{module_relpath} still has 'from src.DAL import'"
    assert not re.search(
        r"^\s*from\s+console\s+import\s+settings_registry", src, re.M
    ), f"{module_relpath} still has 'from console import settings_registry'"
