---
sidebar_position: 2
title: CLI Reference
---

# CLI Reference

The CLI (`osx-next-cli`) provides non-interactive, scriptable VM management. It bypasses the TUI entirely.

```bash
osx-next-cli --version
```

## Subcommands

| Subcommand | Description |
|------------|-------------|
| `apply` | Create a macOS VM (dry-run by default, `--execute` to run) |
| `plan` | Preview the command plan without creating anything |
| `edit` | Modify an existing macOS VM (stop, apply changes, optionally restart) |
| `download` | Download OpenCore and recovery images |
| `preflight` | Check host readiness |
| `status` | Show info about an existing VM |
| `uninstall` | Destroy an existing VM |
| `clone` | Clone a VM with a fresh SMBIOS identity |
| `bundle` | Export diagnostic log bundle |
| `guide` | Show recovery guide for a given issue |

## Common Flags

These flags are shared by `apply` and `plan`:

| Flag | Type | Required | Description |
|------|------|----------|-------------|
| `--vmid` | int | Yes | VM ID (100-999999) |
| `--name` | string | Yes | VM display name |
| `--macos` | string | Yes | Target version: `ventura`, `sonoma`, `sequoia`, `tahoe` |
| `--cores` | int | Yes | CPU cores (power of 2) |
| `--memory` | int | Yes | RAM in MB (minimum 4096) |
| `--disk` | int | Yes | Disk size in GB (minimum 64) |
| `--bridge` | string | Yes | Network bridge (e.g., `vmbr0`) |
| `--storage` | string | Yes | Proxmox storage target (e.g., `local-lvm`) |
| `--iso-dir` | string | No | Custom directory for ISO/recovery images |
| `--cpu-model` | string | No | Override QEMU CPU model (default: auto-detect) |
| `--net-model` | string | No | NIC model: `vmxnet3` or `e1000-82545em` (default: auto-detect) |
| `--apple-services` | flag | No | Enable iCloud/iMessage/FaceTime support |
| `--verbose-boot` | flag | No | Show kernel log instead of Apple logo |
| `--no-smbios` | flag | No | Skip SMBIOS generation entirely |
| `--no-download` | flag | No | Skip auto-download of missing assets |
| `--smbios-serial` | string | No | Custom serial number |
| `--smbios-uuid` | string | No | Custom UUID |
| `--smbios-mlb` | string | No | Custom MLB (Main Logic Board) |
| `--smbios-rom` | string | No | Custom ROM value |
| `--smbios-model` | string | No | Custom Mac model (e.g., `MacPro7,1`) |
| `--installer-path` | string | No | Path to installer image |

## edit -- Flags

| Flag | Type | Required | Description |
|------|------|----------|-------------|
| `--vmid` | int | Yes | VM ID to modify |
| `--name` | string | No | New VM display name |
| `--cores` | int | No | New CPU core count |
| `--memory` | int | No | New RAM in MB |
| `--bridge` | string | No | New network bridge (e.g. `vmbr1`) |
| `--add-disk` | int | No | Extend the target disk by N GB |
| `--disk-name` | string | No | Disk device to resize (default: `virtio0`) |
| `--nic-model` | string | No | NIC model when updating bridge (default: preserve existing) |
| `--start` | flag | No | Start VM after changes are applied |
| `--execute` | flag | No | Actually run (default is dry run) |

At least one change flag (`--name`, `--cores`, `--memory`, `--bridge`, `--add-disk`) is required.

## clone -- Flags

| Flag | Type | Required | Description |
|------|------|----------|-------------|
| `--source-vmid` | int | Yes | VMID of the VM to clone (100-999999) |
| `--new-vmid` | int | Yes | VMID for the cloned VM (must differ from source) |
| `--name` | string | No | Display name for the clone (3-63 chars, alphanumeric/dot/hyphen) |
| `--macos` | string | No | macOS version hint for SMBIOS model selection (default: `sequoia`) |
| `--no-apple-services` | flag | No | Skip vmgenid and MAC regeneration (not recommended) |
| `--execute` | flag | No | Actually run (default is dry run) |

Without `--no-apple-services` (the default), the clone step regenerates serial, UUID, MLB, ROM, vmgenid, and MAC address so both VMs remain fully independent on iCloud, iMessage, and FaceTime.

## plan -- Flags

These flags apply only to the `plan` subcommand:

| Flag | Type | Description |
|------|------|-------------|
| `--json` | flag | Output the plan as JSON (useful for scripting and CI) |
| `--script-out` | string | Write the plan as an executable shell script to the given path |

## Usage Examples

### apply -- Create a VM

Dry-run (preview commands):

```bash
osx-next-cli apply \
  --vmid 910 --name macos-sequoia --macos sequoia \
  --cores 8 --memory 16384 --disk 128 \
  --bridge vmbr0 --storage local-lvm
```

Execute for real:

```bash
osx-next-cli apply --execute \
  --vmid 910 --name macos-sequoia --macos sequoia \
  --cores 8 --memory 16384 --disk 128 \
  --bridge vmbr0 --storage local-lvm
```

With verbose boot:

```bash
osx-next-cli apply --execute --verbose-boot \
  --vmid 910 --name macos-sequoia --macos sequoia \
  --cores 8 --memory 16384 --disk 128 \
  --bridge vmbr0 --storage local-lvm
```

### plan -- Preview the Plan

Human-readable output:

```bash
osx-next-cli plan \
  --vmid 910 --name macos-sequoia --macos sequoia \
  --cores 8 --memory 16384 --disk 128 \
  --bridge vmbr0 --storage local-lvm
```

JSON output (for scripting/CI):

```bash
osx-next-cli plan --json \
  --vmid 910 --name macos-sequoia --macos sequoia \
  --cores 8 --memory 16384 --disk 128 \
  --bridge vmbr0 --storage local-lvm
```

Export as shell script:

```bash
osx-next-cli plan --script-out ./create-vm.sh \
  --vmid 910 --name macos-sequoia --macos sequoia \
  --cores 8 --memory 16384 --disk 128 \
  --bridge vmbr0 --storage local-lvm
```

### download -- Fetch Assets

```bash
# Download both OpenCore and recovery
osx-next-cli download --macos ventura

# OpenCore only
osx-next-cli download --macos sonoma --opencore-only

# Recovery only, custom destination
osx-next-cli download --macos sequoia --recovery-only --dest /mnt/pve/nas/template/iso
```

### preflight -- Check Host

```bash
osx-next-cli preflight
```

Outputs OK/FAIL for each host check. Automatically installs missing build dependencies if detected.

### status -- Query a VM

```bash
osx-next-cli status --vmid 910
```

Shows VM name, status, and key config values (cores, memory, CPU model, network, SMBIOS).

### uninstall -- Destroy a VM

Dry-run (preview):

```bash
osx-next-cli uninstall --vmid 910
```

Execute with disk cleanup:

```bash
osx-next-cli uninstall --vmid 910 --purge --execute
```

### edit -- Modify an Existing VM

Dry-run (preview what will change):

```bash
osx-next-cli edit --vmid 910 --cores 4 --memory 8192
```

Execute for real:

```bash
osx-next-cli edit --vmid 910 --cores 4 --memory 8192 --execute
```

Rename a VM and extend its disk:

```bash
osx-next-cli edit --vmid 910 --name macos-sequoia-v2 --add-disk 64 --execute
```

Change network bridge (preserves existing NIC model and MAC):

```bash
osx-next-cli edit --vmid 910 --bridge vmbr1 --execute
```

Change bridge with an explicit NIC model:

```bash
osx-next-cli edit --vmid 910 --bridge vmbr1 --nic-model e1000 --execute
```

Apply changes and restart the VM automatically:

```bash
osx-next-cli edit --vmid 910 --cores 8 --memory 16384 --start --execute
```

:::note
The `edit` subcommand always stops the VM before making changes. A config snapshot is saved to `generated/snapshots/` before any modifications. On failure, rollback hints are printed so you can restore manually.
:::

### clone -- Clone a VM with Fresh Identity

Cloning a macOS VM on Proxmox duplicates its SMBIOS — both VMs share the same serial number, UUID, and MLB, which causes Apple to block both from iCloud, iMessage, and FaceTime. The `clone` subcommand handles this automatically.

Dry-run (preview commands):

```bash
osx-next-cli clone --source-vmid 910 --new-vmid 911 --name macos-sequoia-clone
```

Execute for real:

```bash
osx-next-cli clone --source-vmid 910 --new-vmid 911 --name macos-sequoia-clone --execute
```

With explicit macOS version hint:

```bash
osx-next-cli clone --source-vmid 910 --new-vmid 911 --macos sonoma --execute
```

Without Apple services identity reset (not recommended):

```bash
osx-next-cli clone --source-vmid 910 --new-vmid 911 --no-apple-services --execute
```

:::note
The clone always performs a full disk copy (`qm clone --full`). The bridge and NIC model are preserved from the source VM. The new VM gets a fresh serial, UUID, MLB, ROM, vmgenid, and MAC address.
:::

### bundle -- Export Diagnostics

```bash
osx-next-cli bundle
```

Exports a log bundle for troubleshooting.

### guide -- Recovery Guide

```bash
osx-next-cli guide "boot issue"
```

Prints recovery steps for the given issue description.

## Exit Codes

| Code | Meaning |
|------|---------|
| 0 | Success |
| 2 | Validation error (bad VMID, invalid config, VM not found) |
| 3 | Missing assets (OpenCore or recovery image not found) |
| 4 | Apply failed |
| 5 | Download failed |
| 6 | Destroy failed |
| 7 | Edit failed |
| 8 | Clone failed |
