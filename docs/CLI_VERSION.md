# Minimum Claude Code CLI version + CI pinning

> This document records the minimum Claude Code CLI version the plugin requires
> and how CI acquires and pins it. The smoke-test battery consumes this
> contract.

## Minimum required CLI version

**`claude` (Claude Code) `>= 2.1.160`.**

This is the floor required for the two plugin-tooling capabilities the build and
the smoke-test battery depend on:

| Capability | Command | Why it is required |
|---|---|---|
| Manifest validation | `claude plugin validate <path>` (and `--strict` for CI) | Validates `.claude-plugin/plugin.json` and `.claude-plugin/marketplace.json`. The `--strict` flag (treat warnings as errors, exit 1) is the CI gate. |
| Sideload / load test | `claude --plugin-dir ./` | Loads the plugin directly from the working tree for the load test. |
| Self-hosted marketplace round-trip | `claude plugin marketplace add <source>` | Adds this repo as a self-hosted marketplace; exercised by the consumer round-trip. |

The version floor was validated against the development host CLI
(`2.1.160`), on which `claude plugin validate . --strict` and the
plugin/marketplace manifests pass clean.

## How CI acquires + pins the CLI

CI must install a **pinned, exact** CLI version (never `@latest`, which would
let an upstream change silently break the gate):

```bash
# Pin the exact version in CI (npm global install).
npm install -g @anthropic-ai/claude-code@2.1.160

# Verify the pinned version before running any plugin-tooling step.
claude --version    # must print "2.1.160 (Claude Code)" or the pinned value

# Gate: strict manifest validation.
claude plugin validate . --strict
```

Pinning rules:

- Pin the **exact** version (`@2.1.160`), not a range and not `@latest`.
- Bump the pin deliberately in a dedicated commit when adopting a newer CLI, and
  re-run the full smoke-test battery against the new pin before merging.
- The pinned value here is the single source of truth; any CI workflow file must
  reference this version, not hardcode a divergent one.

## POSIX requirement

The plugin's hooks and `bin/` wrappers are POSIX shell (`#!/usr/bin/env bash`).
CI must run on a POSIX environment (Linux, macOS, or WSL2). No Windows-native
shell is supported in v1.
