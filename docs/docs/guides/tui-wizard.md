---
sidebar_position: 1
title: TUI Wizard
---

# TUI Wizard

The TUI wizard guides you through macOS VM creation in 6 steps. Launch it by running the install script on your Proxmox host:

```bash
bash -c "$(curl -fsSL https://raw.githubusercontent.com/lucid-fabrics/osx-proxmox-next/main/install.sh)"
```

## Step 1: Preflight Checks

Automatically validates your Proxmox host before proceeding.

![Preflight checks](/img/screenshots/step1-preflight.svg)

| Check | What It Verifies |
|-------|-----------------|
| CPU vendor | Intel or AMD detected, applies correct config |
| Virtualization | VT-x / AMD-V enabled |
| Root access | Running as root on Proxmox |
| Dependencies | Required packages installed |
| TSC flags | `constant_tsc` and `nonstop_tsc` for clock stability |

No user input required. The wizard auto-detects your hardware and moves forward.

:::note
Xeon and pre-Skylake Intel CPUs are detected automatically. Xeon keeps `-cpu host`, older consumer Intel gets Penryn mode with `e1000` NIC.
:::

## Step 2: Choose macOS Version

Select your target macOS version from the list.

![Choose macOS version](/img/screenshots/step2-choose-os.svg)

| macOS | Apple Services | Notes |
|-------|---------------|-------|
| Ventura 13 | Works | Lightweight, good for older hardware |
| Sonoma 14 | Works | Best tested, most reliable |
| Sequoia 15 | Limited | Apple blocks VM sign-in (see [Apple Services](./apple-services.md)) |
| Tahoe 26 | Limited | Apple blocks VM sign-in (see [Apple Services](./apple-services.md)) |

SMBIOS identity (serial, UUID, model) is auto-generated when you select a version.

## Step 3: Select Storage

Choose a storage target from auto-detected Proxmox storage pools. The wizard scans for available pools (e.g., `local-lvm`, NAS mounts under `/mnt/pve/`).

![Storage selection](/img/screenshots/step3-storage.svg)

## Step 4: VM Configuration

Review and edit VM settings with auto-filled defaults based on your hardware.

![VM configuration](/img/screenshots/step4-config.svg)

| Field | Default | Description |
|-------|---------|-------------|
| VMID | Next available | Must be unique, 100-999999 |
| Name | `macos-{version}` | VM display name |
| CPU Cores | Auto-detected | Must be power of 2 (2, 4, 8, 16) |
| Memory (MB) | Auto-detected | Minimum 4096 MB |
| Disk (GB) | Varies by version (80-160) | Minimum 64 GB |
| Network Bridge | `vmbr0` | Proxmox bridge interface |

Additional options available in this step:

- **Generate SMBIOS** -- regenerate identity values
- **Enable Apple Services** -- adds `vmgenid`, static MAC, and PlatformInfo patching
- **Verbose Boot** -- shows kernel log instead of Apple logo
- **Existing UUID** -- enter a UUID to preserve identity for re-runs

:::warning
macOS requires power-of-2 CPU core counts. Non-power-of-2 values (6, 12) can cause the kernel to hang at the Apple logo.
:::

## Step 5: Dry Run

The wizard auto-downloads any missing assets (OpenCore ISO, recovery image), then previews every `qm` command that will be executed.

![Dry run review](/img/screenshots/step5-review.svg)

This step shows:

- Each command with its title and risk level
- The full `qm create`, `qm set`, and `qm importdisk` sequence
- SMBIOS values that will be applied
- Boot order configuration

Nothing is executed yet. Review the commands before proceeding.

:::note
Smart caching: OpenCore and recovery images are downloaded once and reused. Creating a second VM with the same macOS version skips the download entirely.
:::

## Step 6: Install

Executes all commands from the dry-run preview:

1. Creates the VM with `qm create`
2. Builds the OpenCore bootloader (GPT + EFI partition)
3. Imports OpenCore and recovery disks via `qm importdisk`
4. Configures boot order (`ide2;virtio0;ide0`)
5. Starts the VM

After completion, open the VM console via Proxmox web UI (noVNC) to continue macOS installation.

### Post-Install

After macOS finishes installing, fix the boot order so the main disk boots first:

```bash
qm set <vmid> --boot order=virtio0;ide0
```
