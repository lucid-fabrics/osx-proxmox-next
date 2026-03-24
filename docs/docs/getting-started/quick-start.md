---
sidebar_position: 3
title: Quick Start
---

# Quick Start

The fastest path from zero to a running macOS VM.

## Launch the Wizard

```bash
osx-next
```

The TUI wizard guides you through six steps:

| Step | What Happens |
|------|-------------|
| 1. Preflight | Auto-detects CPU vendor (Intel/AMD), checks host readiness |
| 2. Choose OS | Pick macOS version: Ventura 13, Sonoma 14, Sequoia 15, or Tahoe 26 |
| 3. Storage | Select a storage target from auto-detected Proxmox storage pools |
| 4. Config | Review/edit VM settings (VMID, cores, memory, disk) with auto-filled defaults |
| 5. Dry Run | Auto-downloads missing assets, then previews every `qm` command |
| 6. Install | Creates the VM, builds OpenCore, imports disks, and starts the VM |

For most users: pick your macOS version, pick your storage, and click through to **Install**. Everything else is auto-detected.

:::note
OpenCore and recovery images are downloaded once and cached. Creating a second VM with the same macOS version skips the download entirely.
:::

## What to Expect After Install

Once the wizard completes:

1. **The VM starts automatically** and boots into the OpenCore boot picker
2. **OpenCore loads the macOS Recovery installer** -- this is normal for a fresh install
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
| Ventura 13 | Stable | Works | Lightweight, great for older hardware |
| Sonoma 14 | Stable | Works | Best tested, most reliable |
| Sequoia 15 | Stable | Limited | Apple blocks Apple ID sign-in on VMs |
| Tahoe 26 | Stable | Limited | Apple blocks Apple ID sign-in on VMs |

:::note
**Apple Services on Sequoia/Tahoe:** Apple enforces hardware device attestation starting with Sequoia 15, which blocks Apple ID sign-in on all VM platforms. **Workaround:** Install Sonoma 14 first, sign into Apple ID, then upgrade in-place to Sequoia or Tahoe.
:::

## Next Steps

- **CLI usage** -- Run `osx-next-cli --help` for headless/scripted VM creation
- **GPU passthrough** -- Attach a discrete GPU for native graphics performance
- **Apple Services** -- Enable iCloud, iMessage, and FaceTime with `--apple-services`
- **Performance profiles** -- Apply guest-side tuning scripts for snappier UI
