# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.11.1] - 2026-03-06

### Fixed
- Replace hardcoded `/tmp/oc-*` paths with `mktemp -d` to prevent symlink attacks

## [0.11.0] - 2026-03-06

### Added
- CLI `status` subcommand to query VM info
- `--version` flag and `--json` plan output
- PEP 561 `py.typed` marker for type checker support
- Python 3.9/3.11/3.12/3.13 CI test matrix

### Fixed
- Harden command injection with `shlex.quote()` on all path interpolations
- Enforce `validate_config()` at `build_plan()` boundary (defense-in-depth)
- Replace `random.choices()` with `secrets.token_hex()` for session IDs
- Narrow bare `except Exception` to specific types

### Changed
- Extract OC disk script into composable helpers
- CI badge and CLI documentation in README

## [0.10.2] - 2025-02-21

### Fixed
- Pass apple_services flag during OS selection and add bash toggle

### Changed
- Document Apple Services limitation on Sequoia/Tahoe VMs
- Restore screenshot images in README

## [0.10.1] - 2025-02-21

### Fixed
- Pass apple_services flag during OS selection and add bash toggle

## [0.10.0] - 2025-02-20

### Added
- Apple Services support (vmgenid, static MAC) for iMessage/FaceTime/iCloud

## [0.9.0] - 2025-02-19

### Added
- Option to preserve existing SMBIOS UUID during VM creation

## [0.8.2] - 2025-02-18

### Fixed
- Default SMBIOS model to MacPro7,1 for OCLP compatibility

## [0.8.1] - 2025-02-17

### Fixed
- Bash script brought to full parity with Python TUI

## [0.8.0] - 2025-02-16

### Added
- ISO storage selection for downloads (`--iso-dir`)

### Changed
- Renamed community-scripts to scripts/bash
- Decoupled bash script from community-scripts

## [0.7.0] - 2025-02-15

### Added
- macOS Tahoe 26 support (recovery via Apple osrecovery API)
- Auto-download of OpenCore ISOs and recovery images from GitHub releases
- Uninstall command (`osx-next-cli uninstall --vmid <id>`)

### Changed
- Recovery images use `dmg2img` conversion (GPT+HFS+ format)
- Boot order uses `ide2;virtio0;ide0` for proper recovery boot

## [0.6.0] - 2025-02-14

### Added
- AMD CPU support with Cascadelake-Server emulation
- Intel hybrid CPU detection (12th gen+)
- Verbose boot flag (`--verbose-boot`)

### Fixed
- OpenCore config.plist patching via plistlib (not sed)
- GPT+ESP disk format for OpenCore (not MBR+FAT32)

## [0.5.0] - 2025-02-13

### Added
- SMBIOS identity auto-generation (serial, UUID, MLB, ROM)
- Apple-format serial/MLB with mod-34 checksum
- Dry-run mandatory before live install
- Real-time form validation in TUI

### Changed
- Main disk switched from sata0 to virtio0
- NIC switched from virtio to vmxnet3

## [0.4.0] - 2025-02-12

### Added
- TUI wizard with 6-step flow
- Hardware auto-detection (CPU vendor, cores, RAM)
- Shared storage support (`--iso-dir`)
- VM profiles (save/load)

## [0.3.0] - 2025-02-11

### Added
- CLI interface with plan/apply/preflight commands
- OpenCore bootloader setup automation
- Recovery image download from Apple osrecovery API

[0.11.1]: https://github.com/lucid-fabrics/osx-proxmox-next/compare/v0.11.0...v0.11.1
[0.11.0]: https://github.com/lucid-fabrics/osx-proxmox-next/compare/v0.10.2...v0.11.0
[0.10.2]: https://github.com/lucid-fabrics/osx-proxmox-next/compare/v0.10.1...v0.10.2
[0.10.1]: https://github.com/lucid-fabrics/osx-proxmox-next/compare/v0.10.0...v0.10.1
[0.10.0]: https://github.com/lucid-fabrics/osx-proxmox-next/compare/v0.9.0...v0.10.0
[0.9.0]: https://github.com/lucid-fabrics/osx-proxmox-next/compare/v0.8.2...v0.9.0
[0.8.2]: https://github.com/lucid-fabrics/osx-proxmox-next/compare/v0.8.1...v0.8.2
[0.8.1]: https://github.com/lucid-fabrics/osx-proxmox-next/compare/v0.8.0...v0.8.1
[0.8.0]: https://github.com/lucid-fabrics/osx-proxmox-next/compare/v0.7.0...v0.8.0
[0.7.0]: https://github.com/lucid-fabrics/osx-proxmox-next/compare/v0.6.0...v0.7.0
[0.6.0]: https://github.com/lucid-fabrics/osx-proxmox-next/compare/v0.5.0...v0.6.0
[0.5.0]: https://github.com/lucid-fabrics/osx-proxmox-next/compare/v0.4.0...v0.5.0
[0.4.0]: https://github.com/lucid-fabrics/osx-proxmox-next/compare/v0.3.0...v0.4.0
[0.3.0]: https://github.com/lucid-fabrics/osx-proxmox-next/releases/tag/v0.3.0
