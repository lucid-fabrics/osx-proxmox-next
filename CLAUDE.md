# OSX Proxmox Next — Claude Code Reference

## Project Overview

Python + Textual TUI tool that automates macOS VM creation on Proxmox VE 9. Guided wizard: Preflight → Configure → Review → Dry Run → Live Apply.

## Quick Commands

```bash
# Install (dev)
python -m venv .venv && source .venv/bin/activate
pip install -e .

# Run TUI wizard
osx-next

# Run CLI
osx-next-cli preflight
osx-next-cli apply --vmid 910 --name macos --macos sequoia --cores 8 --memory 16384 --disk 128 --bridge vmbr0 --storage local-lvm

# Tests
pytest tests/

# Setup git hooks
bash scripts/setup-hooks.sh
```

## Architecture

```
src/osx_proxmox_next/
  app.py            # Textual TUI wizard (NextApp)
  cli.py            # CLI entry point (osx-next-cli)
  domain.py         # VmConfig dataclass + validate_config()
  planner.py        # Generates PlanStep list of qm commands
  executor.py       # Dry-run preview + live apply_plan()
  assets.py         # ISO detection (OpenCore, recovery, installer)
  defaults.py       # Host hardware detection (CPU, RAM, storage)
  preflight.py      # Host readiness checks (qm, pvesm, kvm, root)
  rollback.py       # VM snapshot hints
  diagnostics.py    # Health status builder
  profiles.py       # VM config profile management
  infrastructure.py # Proxmox command adapter
```

### Flow

1. `preflight.py` checks host capabilities
2. `defaults.py` detects hardware → populates `domain.VmConfig`
3. `assets.py` scans ISO storage for OpenCore + installer
4. `planner.py` builds `PlanStep[]` of qm commands from config
5. `executor.py` runs steps (dry-run or live)

## Development Workflow

1. Create a feature branch from `main`: `git checkout -b feat/description`
2. Implement changes, commit with conventional commits
3. Push branch, create PR to `main`
4. On merge → CI auto-bumps version, creates tag + GitHub release

**Versioning is fully automatic (semver):**

| Commit Type | Bump | Example |
|-------------|------|---------|
| `fix:` | patch (0.0.1 → 0.0.2) | `fix(assets): handle missing ISO` |
| `feat:` | minor (0.0.2 → 0.1.0) | `feat(cli): add snapshot flag` |
| `feat!:` / `BREAKING CHANGE` | major (0.1.0 → 1.0.0) | `feat!: new config format` |
| `docs:` / `chore:` / `ci:` | no release | `docs: update README` |

**Never manually edit the version in `pyproject.toml`** — the CI handles it.

## Code Conventions

- Python 3.9+, type hints, dataclasses
- Textual for TUI, Rich for terminal output
- No AI attribution in code or commits (enforced by git hooks)
- Conventional commits: `type(scope): description`
  - Types: feat|fix|refactor|docs|test|chore|perf|ci

## Key Files

| File | Purpose |
|------|---------|
| `pyproject.toml` | Project metadata, deps, entry points |
| `install.sh` | One-line bootstrap for Proxmox hosts |
| `tests/test_domain.py` | Domain model tests |
| `.githooks/*` | Pre-commit, commit-msg, pre-push hooks |
| `scripts/profiles/` | Guest macOS performance tuning scripts |
