---
sidebar_position: 2
title: OpenCore Bootloader
---

# OpenCore Bootloader

## Why OpenCore is Needed

Proxmox VE uses QEMU/KVM, which does not natively boot macOS. OpenCore is an open-source bootloader that:

- Provides the Apple Secure Boot chain macOS expects
- Injects SMBIOS data so macOS recognizes the VM as genuine Apple hardware
- Loads kernel extensions (kexts) needed for virtualized hardware
- Presents a graphical boot picker with Apple icons

Without OpenCore, the macOS installer kernel panics before reaching the setup screen.

## How the Tool Builds the OpenCore Disk

The OpenCore disk image is built as a **GPT-partitioned disk with an EFI System Partition (ESP)**, not a simple MBR+FAT32 image. This distinction is critical because OVMF firmware (used by Proxmox) cannot reliably boot MBR-formatted media.

### Build Process

The OpenCore boot image is assembled locally on the Proxmox host from pinned upstream releases. Every component is downloaded straight from its project's GitHub release at a fixed version and verified against a pinned SHA-256 before use, so the resulting image is reproducible and auditable. If assembly fails (upstream unreachable, checksum mismatch), the installer falls back to the prebuilt ISO from the project's release assets.

```mermaid
flowchart LR
    A[Download pinned components\nverify SHA-256] --> B[Assemble EFI tree\nOpenCore + kexts + config]
    B --> C[Create raw disk\nGPT partition table]
    C --> D[Create ESP\nFAT32 format]
    D --> E[Patch config.plist\nvia plistlib]
    E --> F[Import to Proxmox\nide0 media=disk]
```

The `config.plist` template, the SSDT tables, and the plist-only MCEReporterDisabler kext ship inside this repository (`src/osx_proxmox_next/data/opencore/`), so they can be reviewed like any other source file.

The `config.plist` patching is done programmatically via `plistlib` to safely modify XML property lists without risking malformed output.

### Disk Layout

| Proxmox Device | Content         | Format       |
|----------------|-----------------|--------------|
| `ide0`         | OpenCore EFI    | GPT + ESP, `media=disk` |
| `ide2`         | Recovery image  | Raw GPT+HFS+, `media=disk` |
| `virtio0`      | Main disk       | VM storage   |

Boot order: `ide2;virtio0;ide0`

## Required Kexts

Kexts are kernel extensions loaded by OpenCore before macOS boots. Each is pulled from its upstream release at the pinned version below and checksum-verified during assembly.

| Kext                  | Version | Purpose |
|-----------------------|---------|---------|
| Lilu                  | 1.7.2   | Patching framework required by most other kexts |
| VirtualSMC            | 1.3.7   | Emulates Apple SMC chip; macOS refuses to boot without it |
| WhateverGreen         | 1.7.0   | GPU/framebuffer patches for display output in VMs |
| CryptexFixup          | 1.0.5   | Fixes Cryptex (security update) loading on Sonoma 14+ and newer. Without it, boot hangs at `EXITBS:START` |
| AppleALC              | 1.9.7   | Audio codec support |
| MCEReporterDisabler   | 0.5     | Suppresses Machine Check Exception reports that crash VMs (plist-only, ships in this repo) |
| RestrictEvents        | 1.1.6   | Configured to silence the cosmetic MacPro7,1 memory notification |

The bootloader itself is OpenCore 1.0.7, with its OpenCanopy picker resources (fonts, labels, Syrah icon set) taken from a commit-pinned OcBinaryData archive.

### Required Drivers

| Driver               | Purpose |
|----------------------|---------|
| OpenRuntime.efi      | Memory and boot services runtime for OpenCore |
| OpenHfsPlus.efi      | HFS+ filesystem driver (reads macOS recovery partitions) |
| OpenPartitionDxe.efi | Partition map driver for Apple Partition Map and GPT |
| OpenCanopy.efi       | Graphical boot picker with Apple icons |
| ResetNvramEntry.efi  | Adds "Reset NVRAM" option to boot picker |

## Key config.plist Settings

| Setting              | Value    | Purpose |
|----------------------|----------|---------|
| `ScanPolicy`         | `0`      | Scan all disks and filesystems (no filtering) |
| `DmgLoading`         | `Any`    | Allow loading DMG images from any source |
| `Timeout`            | `0`      | Disable auto-boot during install so the picker waits for a choice |
| `csr-active-config`  | `0x0F67` | Disables SIP protections that block kext loading in VMs |

### CryptexFixup and macOS Sonoma+

Starting with macOS Sonoma 14, Apple introduced Cryptex-based security updates. In virtual machines, the Cryptex loading process fails during early boot, causing a hang at `EXITBS:START`. CryptexFixup.kext intercepts this process and applies the necessary patches.

**If you see `EXITBS:START` hang or `Err(0xE)` BootKernelExtensions:** the most likely cause is a missing CryptexFixup kext.

## Common Boot Failures

| Symptom | Cause |
|---------|-------|
| Only "Reset NVRAM" in picker | Wrong recovery image format or missing kexts/drivers |
| `EXITBS:START` hang | Missing CryptexFixup (Sonoma+), SIP still enabled, or MBR-formatted disk |
| `Err(0xE)` BootKernelExtensions | Missing CryptexFixup |
| "No bootable device" | OVMF cannot read MBR+FAT32 as cdrom; must be GPT+ESP with `media=disk` |
