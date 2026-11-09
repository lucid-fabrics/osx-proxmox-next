---
sidebar_position: 10
title: Intel iGPU (WhateverXe, Beta)
---

# Intel iGPU (WhateverXe, Beta)

If your Proxmox host has a recent Intel processor, its built-in graphics can give a macOS VM full Metal acceleration. The guided flow, [GPU passthrough (guided)](gpu-passthrough.md), finds the iGPU on its own and recommends it. This page explains what that costs and what the tool does for you.

:::caution Beta
The whole recipe was proven on one machine: an i5-1240P (Alder Lake-P, Iris Xe, 8086:46a6) on macOS Ventura 13.7.8, Sonoma 14.8.9, Sequoia 15.8.2 and Tahoe 26.7.2. Every other chip in WhateverXe's list is shown as untested. It should work, but nobody has run it through this tool yet.
:::

## What you need to know first

- **The host gives up its screen.** The iGPU goes to the VM for good. Whatever you plug into the host's HDMI port shows macOS, not the Proxmox console. Use the web UI or SSH from then on.
- **You need a monitor or an HDMI dummy plug** on the host's native HDMI port. HDMI that goes through an LSPCON converter and USB-C display outputs do not work. Laptop built-in panels mostly do not work either (WhateverXe issue 49).
- **One host reboot** is needed the first time, so the host can hand the iGPU to vfio-pci. If the host is already prepared the tool skips it.
- **The iGPU shares the CPU's power budget.** When the host is busy (a video transcode, for example) the package hits its power limit and the iGPU slows down. Metal stays on, the desktop just gets sluggish.
- **Tahoe is experimental, and the automatic graphics package setup does not work on it yet.** In our runs the staging script stalled in the recovery Terminal on Tahoe, so `igpu-install` and `igpu-finish` stop with a clear message and put the VM config back as it was. Ventura, Sonoma and Sequoia are fine. If you run Tahoe anyway, every restart or shutdown from inside macOS also hangs (WhateverXe issue 62). Restart the VM from Proxmox instead (`qm stop`, then `qm start`). Ventura, Sonoma and Sequoia restart normally.
- A WindowServer crash leaves the GPU dead until the VM restarts. It is rare and logging out is safe.

## How the right GPU is picked

The scan lists every graphics card in the host, each with a plain status for the macOS release you are setting up: "works with macOS Sequoia 15", "not supported by macOS Sequoia 15" or "in use by VM 100". The strongest card that works is preselected: a supported AMD card beats the Intel iGPU, and the iGPU beats an NVIDIA card. If exactly one card works it is preselected, and if none works the tool says so and changes nothing. You can switch the choice, and one VM gets one card. Nothing is enabled until you confirm, because the iGPU takes the host console.

## What the tool does

1. **Host (once):** blacklists `i915`, binds the iGPU id to vfio-pci at boot and rebuilds the boot image. A host that already holds the iGPU on vfio-pci is left alone.
2. **Checks:** the chip must be on the supported list, no running VM may hold it, and the memory layout must be usable.
3. **VM shape:** i440fx (`machine: pc`), no emulated display, the iGPU on `hostpci0` in legacy IGD mode with a pinned option ROM from [intel-igpu-passthru](https://github.com/LongQT-sea/intel-igpu-passthru), and a `max-ram-below-4g` computed from the host's graphics memory so WhateverXe accepts the guest's memory layout. The recovery disk sits on `sata1`, because the IDE controller of i440fx has no macOS driver.
4. **OpenCore:** adds WhateverXe 0.8.0 and AMFIPass, turns WhateverGreen off, sets `FadtEnableReset` and the boot-args `-allow3d -disablegfxfirmware -wex-tgl-hold-render-wake`. Every download is pinned and checked against a SHA-256.
5. **Graphics package:** WhateverXe Graphics is copied into macOS from recovery, offline, and installed by a one-shot LaunchDaemon only after WhateverXe is loaded. Installing it earlier makes the guest panic at boot, so if WhateverXe never loads the package stays out and the VM keeps working unaccelerated (three tries at most). The package contains Apple binaries, so it is downloaded at setup time and never shipped with this project.

## Commands

```bash
osx-next-cli igpu-check                     # can this host do it, and what would the VM get
osx-next-cli igpu-install --vmid 996 --name igpu-seq --macos sequoia \
  --cores 4 --memory 8192 --disk 64 --bridge vmbr0 --storage local-lvm
osx-next-cli igpu-finish 996                # an installed i440fx VM: attach the iGPU and install the package
osx-next-cli gpu-setup --undo               # put the VM and the host back
```

`igpu-install` creates the VM, installs macOS hands-off like [unattended install](unattended-install.md) and finishes the iGPU setup, 60 to 90 minutes in total. The same commands exist in the standalone script: `osx-proxmox-next.sh igpu-check` and `igpu-finish <vmid>`, and the create-a-VM menu asks about the iGPU when the host is ready.

An already installed q35 VM is not converted: switching the machine type of an installed macOS has not been tested. Create a new VM instead.

## Troubleshooting

### "Acceleration was not confirmed"

After the last boot, `igpu-finish` waits up to 5 minutes and reads the iGPU's registers from the host (BAR0, read-only). It reports "Metal acceleration is active" only when the blitter or render ring tail (`BCS_TAIL`, `RCS_TAIL`) is non-zero, which means macOS submitted work to the GPU. If both stay at zero the graphics package is installed but the GPU is idle, and the command exits non-zero.

- Check that the VM's OpenCore disk was assembled by this tool (it carries WhateverXe and AMFIPass) and not an older prebuilt image. Deleting `opencore-osx-proxmox-vm.iso` from the ISO cache makes the next run rebuild it.
- The check needs the VM to be running with the iGPU on `hostpci0`. A slow Sequoia boot can need a few more minutes than the check waits, so look at the screen on the monitor before assuming it failed.
- With nothing at all moving, run `gpu-setup --undo` and repeat the setup on a fresh VM.

## Undo

`gpu-setup --undo` restores the VM's args, display setting and OpenCore config from the backup. The VM stays on the i440fx machine type, which boots fine without the card. The host files are restored from their backups and the host needs one more reboot to give the iGPU back to i915.
