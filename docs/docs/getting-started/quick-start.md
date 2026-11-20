---
sidebar_position: 3
title: Quick Start
---

# Quick Start

The fastest path from zero to a running macOS VM.

## Launch the TUI

```bash
osx-next
```

The TUI opens on your Macs, one card per macOS VM with a live picture of its screen. Press `n` for **New Mac**: one screen with everything prefilled from your host.

| Row | What it sets |
|-----|--------------|
| macOS | Monterey 12, Ventura 13, Sonoma 14, Sequoia 15 or Tahoe 26 (left and right change it) |
| Name | A live text field, `macos-<version>` until you change it |
| Size | Light, Balanced, Heavy or Custom (cores, memory in GB, disk in GB) |
| Lives on | A storage target from the auto-detected Proxmox storage pools |
| Install | **Hands-off (beta)** finishes the install by itself, **Manual** leaves it to you in the Proxmox console |
| Apple ID | Off, or On for iMessage, FaceTime and iCloud |
| More | VM ID, bridge, VLAN tag, ISO storage, installer path, existing UUID, CPU model, NIC model |

For most users: press `n`, look at the prefilled choices, and press `enter`. Everything else is auto-detected. The build screen then shows one timeline: **Check this host**, **Download macOS**, **Rehearse** (every command is run as a dry run first, and nothing on the host changes before it passes), **Create VM** and **Install macOS**. The Mac's live screen is beside it, and `l` shows the full log.

:::note
OpenCore and recovery images are downloaded once and cached. Creating a second VM with the same macOS version skips the download entirely.
:::

## What to Expect After Install

Once the build completes:

1. **The VM starts automatically** and boots into the OpenCore boot picker
2. **Press Enter on the macOS entry** in the VM console. The picker waits for you on purpose, so recovery never boots by itself mid-install
3. **The macOS installer appears** -- follow Apple's standard installation flow

The full macOS installation takes 20-45 minutes depending on your hardware.

## First Boot Checklist

After the macOS installer finishes and the VM reboots into macOS:

- [ ] **Format the disk** -- In the installer, open Disk Utility > View > Show All Devices > select the VirtIO disk > Erase as APFS with GUID Partition Map
- [ ] **Complete macOS setup** -- Create your user account, skip Apple ID if on Sequoia/Tahoe
- [ ] **Verify network** -- Open Safari and confirm internet access
- [ ] **Check display** -- Use `vga: std` during installation for stable VNC output

:::warning
If the macOS installer does not show your disk, you need to format it first. Open **Disk Utility** from the installer menu, click **View > Show All Devices**, select **QEMU VirtIO Block Device**, and erase it as **APFS** with **GUID Partition Map**.
:::

## Supported macOS Versions

| macOS | Status | Apple Services | Notes |
|-------|--------|---------------|-------|
| Monterey 12 | Stable | Works | Oldest supported release |
| Ventura 13 | Stable | Works | Lightweight, great for older hardware |
| Sonoma 14 | Stable | Works | Best tested, most reliable |
| Sequoia 15 | Stable | Works (with `--apple-services`) | Kernel patch applied automatically |
| Tahoe 26 | Stable | Works (with `--apple-services`) | Kernel patch applied automatically |

:::note
**Apple Services on Sequoia/Tahoe:** Pass `--apple-services` when creating the VM. A kernel-level patch is injected automatically that prevents Apple's DeviceCheck from detecting the VM, enabling full Apple ID, iCloud, iMessage, and FaceTime sign-in. See [Apple Services](../guides/apple-services) for details.
:::

## Managing Existing VMs

After a VM is created, use the TUI to manage it:

```bash
osx-next --manage
```

This opens a VM list where you can edit settings (CPU, memory, disk, network), start, stop, or destroy existing macOS VMs without using the CLI.

From the CLI, use the `edit` subcommand directly:

```bash
osx-next-cli edit --vmid 910 --cores 4 --memory 8192 --execute
```

## Next Steps

- **CLI usage** -- Run `osx-next-cli --help` for headless/scripted VM creation
- **Manage existing VMs** -- Open `osx-next` and use `s` to start or stop, `e` to edit and `d` to remove a Mac
- **GPU passthrough** -- Attach a discrete GPU for native graphics performance
- **Apple Services** -- Enable iCloud, iMessage, and FaceTime with `--apple-services`
- **Performance profiles** -- Apply guest-side tuning scripts for snappier UI
