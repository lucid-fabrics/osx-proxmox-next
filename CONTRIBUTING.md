# Contributing to OSX Proxmox Next

Thanks for your interest in contributing! This guide covers the basics.

## Development Setup

```bash
git clone https://github.com/lucid-fabrics/osx-proxmox-next.git
cd osx-proxmox-next
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

## Running Tests

```bash
pytest tests/ -v
```

## Code Style

- Python 3.9+ with type hints
- Dataclasses over dicts for structured data
- No third-party dependencies beyond `textual` and `rich`
- All shell commands go through `ProxmoxAdapter` (never raw `subprocess` in business logic)

## Commit Messages

We use [Conventional Commits](https://www.conventionalcommits.org/):

| Type | Description |
|------|-------------|
| `feat` | New user-facing feature |
| `fix` | Bug fix |
| `docs` | Documentation only |
| `test` | Adding or updating tests |
| `chore` | Maintenance, CI, tooling |
| `refactor` | Code change that doesn't fix a bug or add a feature |

Examples:
```
feat(cli): add --version flag
fix(smbios): correct base64 encoding for Proxmox
docs: add CONTRIBUTING guide
```

## Pull Request Process

1. Fork the repo and create a branch from `main`
2. Make your changes with tests
3. Ensure `pytest tests/ -v` passes
4. Open a PR against `main`
5. CI runs tests automatically; releases are automated via conventional commits

## Reporting Issues

Use [GitHub Issues](https://github.com/lucid-fabrics/osx-proxmox-next/issues) with the provided templates.

## Architecture

```
src/osx_proxmox_next/
  app.py           # TUI wizard (Textual)
  cli.py           # CLI interface (argparse)
  domain.py        # VmConfig dataclass + validation
  defaults.py      # Hardware detection (CPU, RAM, storage)
  preflight.py     # System readiness checks
  planner.py       # Build qm command sequences
  executor.py      # Run or dry-run plan steps
  downloader.py    # OpenCore + recovery downloads
  assets.py        # ISO/image resolution
  smbios.py        # SMBIOS identity generation
  rollback.py      # Snapshot + rollback hints
  diagnostics.py   # Health checks + log bundles
  profiles.py      # Save/load VM profiles
  infrastructure.py # ProxmoxAdapter (subprocess wrapper)
```

## License

By contributing, you agree that your contributions will be licensed under the Apache 2.0 License.
