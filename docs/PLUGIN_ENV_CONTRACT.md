# Plugin env-var interface contract: `${CLAUDE_PLUGIN_ROOT}` + `${CLAUDE_PLUGIN_DATA}`

> A stable, documented resolution surface so that the SQLite/state backend and
> the config / venv / `hooks.json` layers can rely on it — they must not read
> these env vars ad hoc before the fallback logic exists. The canonical
> implementation lives in `bin/_env_paths/__init__.py`; this doc is its
> human-readable specification.

## The two roots are NOT interchangeable

| Var | Meaning | Mutable? | Survives plugin update? | Use for |
|---|---|---|---|---|
| `CLAUDE_PLUGIN_ROOT` | Read-only install dir of the plugin (`agents/`, `commands/`, `hooks/`, `bin/`, `schemas/`). | No | No (may be relocated/refreshed) | Resolving shipped read-only assets from shell/hook scripts. |
| `CLAUDE_PLUGIN_DATA` | Persistent per-plugin data dir. | Yes | Yes | All mutable runtime state: SQLite intent DB, JSONL mirror, sealed local state. |

The historical `parents[2]` repo-root derivation that the in-tree framework used
for *state* points at an ephemeral cache directory that Claude Code can wipe
~7 days after a plugin update (SC-C #4). Therefore:

- **State moves to `CLAUDE_PLUGIN_DATA`.**
- **`parents[2]` is retained ONLY for read-only `schemas/` + `_roster.json`
  resolution** — never for writable state.

## Fallback semantics (defined once)

### `plugin_root()`
```
$CLAUDE_PLUGIN_ROOT   (installed-plugin mode)
  └─ else → repo root derived from bin/_env_paths/ location (parents[2])
            (sideloaded / in-tree `claude --plugin-dir ./` mode)
```

### `plugin_data_dir()`
```
$CLAUDE_PLUGIN_DATA           (installed-plugin mode)
  └─ else → $CLAUDE_PROJECT_DIR   (project-scoped fallback)
       └─ else → repo root        (last-resort fallback)
```
The resolved data directory is created on first resolution (`mkdir -p`
semantics) so callers can write immediately.

## Shell / hook usage

Shell scripts (hooks, `bin/` wrappers) reference the read-only root directly as
`${CLAUDE_PLUGIN_ROOT}/...` (double-quoted in shell form). `hooks/hooks.json`
expresses every script path this way. Scripts that need to write runtime state
read `${CLAUDE_PLUGIN_DATA}` with the same fallback chain (`${CLAUDE_PROJECT_DIR}`
then the script-derived repo root).

## Python usage

```python
from bin._env_paths import plugin_root, plugin_data_dir, schemas_dir

state_dir = plugin_data_dir()          # writable; created if missing
db_path   = plugin_data_dir() / "intent.db"
schema    = schemas_dir() / "plan_v1.schema.json"   # read-only asset
```

## Scope boundary (what T-A did NOT do)

This contract is the interface only. T-A did **not** rewire existing `parents[2]`
call sites and did **not** change any storage backend:

- **T-C** threads `plugin_data_dir()` through `intent_jsonl_path()` and the
  SQLite path, and keeps `parents[2]` only for `schemas/` + `_roster.json`.
- **T-D** wires `plugin_root()` into the venv helper and the `hooks.json` path
  resolution.
