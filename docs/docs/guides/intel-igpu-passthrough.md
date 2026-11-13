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
- **Tahoe is experimental.** The automatic graphics package setup (`igpu-install` and `igpu-finish`) works on Tahoe 26. Its recovery Terminal is slower to take input than on the other releases, so if the staging step ever stops, the tool puts the VM config back as it was and you can run the same command again. Every restart or shut down from inside macOS hangs at the very end on Tahoe (WhateverXe issue 62). The tool installs a small power watchdog on the host that finishes them for you, see [Restart and shut down on Tahoe](#restart-and-shut-down-on-tahoe). Ventura, Sonoma and Sequoia restart normally.
- **Monterey 12 is not supported.** WhateverXe needs macOS 13 or newer, so the tool refuses a Monterey VM.
- A WindowServer crash leaves the GPU dead until the VM restarts. It is rare and logging out is safe.
- **The GPU can hang by itself (seen on Tahoe).** WhateverXe sometimes hangs the GPU right when macOS starts using it, a minute or two after boot. Apple's driver then resets it over and over, the screen stops updating, and under heavy load the kernel can panic about ten minutes later. In testing the hang hit 2 of 10 boots in one session and every boot in a later one, with or without the power watchdog, and neither `-wex-tgl-hold-render-wake` nor `-wex-no-host-rps` changed that. It is a WhateverXe bug (issues 25, 40, 44 and 75). On Tahoe the power watchdog restarts the VM after such a panic. If the screen freezes without a panic, restart the VM from Proxmox.

## How the right GPU is picked

The scan lists every graphics card in the host, each with a plain status for the macOS release you are setting up: "works with macOS Sequoia 15", "not supported by macOS Sequoia 15" or "in use by VM 100". The strongest card that works is preselected: a supported AMD card beats the Intel iGPU, and the iGPU beats an NVIDIA card. If exactly one card works it is preselected, and if none works the tool says so and changes nothing. You can switch the choice, and one VM gets one card. Nothing is enabled until you confirm, because the iGPU takes the host console.

## What the tool does

1. **Host (once):** blacklists `i915`, binds the iGPU id to vfio-pci at boot and rebuilds the boot image. A host that already holds the iGPU on vfio-pci is left alone.
2. **Checks:** the chip must be on the supported list of the WhateverXe release that gets installed (see [Which chips are supported](#which-chips-are-supported)), no running VM may hold it, and the memory layout must be usable.
3. **VM shape:** i440fx (`machine: pc`), no emulated display, the iGPU on `hostpci0` in legacy IGD mode with a pinned option ROM from [intel-igpu-passthru](https://github.com/LongQT-sea/intel-igpu-passthru), and a `max-ram-below-4g` computed from the host's graphics memory so WhateverXe accepts the guest's memory layout. The recovery disk sits on `sata1`, because the IDE controller of i440fx has no macOS driver.
4. **OpenCore:** adds WhateverXe (the latest release unless you pick another, see [Which WhateverXe version](#which-whateverxe-version)) and AMFIPass, turns WhateverGreen off, sets `FadtEnableReset` and the boot-args `-allow3d -disablegfxfirmware -wex-tgl-hold-render-wake`. AMFIPass and the option ROM are pinned and checked against a SHA-256, WhateverXe and its graphics package against the digest GitHub stores for the release file.
5. **Graphics package:** WhateverXe Graphics is copied into macOS from recovery, offline, and installed by a one-shot LaunchDaemon only after WhateverXe is loaded. Installing it earlier makes the guest panic at boot, so if WhateverXe never loads the package stays out and the VM keeps working unaccelerated (three tries at most). The package contains Apple binaries, so it is downloaded at setup time and never shipped with this project.

## Commands

```bash
osx-next-cli igpu-check                     # can this host do it, and what would the VM get
osx-next-cli igpu-check --wex-version 0.8.0 # the same, judged against an older WhateverXe release
osx-next-cli igpu-install --vmid 996 --name igpu-seq --macos sequoia \
  --cores 4 --memory 8192 --disk 64 --bridge vmbr0 --storage local-lvm
osx-next-cli igpu-finish 996                # an installed i440fx VM: attach the iGPU and install the package
osx-next-cli igpu-finish 996 --wex-version 0.8.0   # an older WhateverXe release instead of the latest
osx-next-cli igpu-versions                  # the published WhateverXe and graphics package releases
osx-next-cli igpu-report 996                # bundle the crash records of a VM for a bug report
osx-next-cli igpu-watchdog 996              # add the watchdog and crash recorder to a VM set up before (--remove takes it off)
osx-next-cli gpu-setup --undo               # put the VM and the host back
```

`igpu-install` creates the VM, installs macOS hands-off like [unattended install](unattended-install.md) and finishes the iGPU setup, 60 to 90 minutes in total. The same commands exist in the standalone script: `osx-proxmox-next.sh igpu-check`, `igpu-finish <vmid>`, `igpu-versions`, `igpu-report <vmid>` and `igpu-watchdog <vmid> [--remove]`, and the create-a-VM menu asks about the iGPU when the host is ready.

An already installed q35 VM is not converted: switching the machine type of an installed macOS has not been tested. Create a new VM instead.

## Which WhateverXe version

This tool installs the **latest published WhateverXe release** and the latest WhateverXe Graphics package. Only one Intel GPU was tested here and you have another, so the tool does not hold you to one tested version. It records what happened instead (see [When it crashes](#when-it-crashes)), and you send that upstream.

- `igpu-install` and `igpu-finish` look the releases up on GitHub when they run. On a terminal they show the published releases, newest first, with the latest preselected: press Enter for it or pick an older one. The standalone script shows the same list as a menu.
- Skip the question with a flag: `--wex-version <tag>` and `--graphics-version <tag>` (a tag from `osx-next-cli igpu-versions`, for example `0.8.0` or `v0.4.1`). Without a flag and without a terminal the latest is used. If a new release breaks things, run `igpu-finish` on a fresh VM with an older tag.
- Every download is checked against the sha256 digest GitHub stores for the release file. A release without a digest, or a file that does not match, stops the setup with a clear message.
- There is no fallback to an older version behind your back. If GitHub cannot be reached or answers with a rate limit, the setup stops and tells you to run it again in a few minutes. Set `GITHUB_TOKEN` to a GitHub token to raise the rate limit.
- AMFIPass and the option ROMs stay pinned, they are not part of WhateverXe's release stream.
- The tool needs the kext `WhateverXe.kext` (also inside one wrapping folder) and reads its executable name from the kext. A release laid out differently is refused with a message listing what the zip holds. If a release changed the kext's bundle id (`org.b00t0x.WhateverXe`) you get a warning, because the first-boot check that WhateverXe is loaded looks for that id and the graphics package then stays out.
- The chip is judged against the release you install, see [Which chips are supported](#which-chips-are-supported).
- The versions that went into a VM are written to `/var/lib/osx-proxmox-next/igpu/<vmid>.json`. `gpu-setup --status` and `igpu-report` show them.

## Which chips are supported

The list of supported chips comes from the WhateverXe release that will be installed, the latest unless you pick another. A release does not carry the list in its zip (the kext matches on `IOResources`, there is no `IOPCIMatch`), so the tool reads the device table of that release's source, `WhateverXe/kern_xelp_devices.hpp` at the release tag: exact PCI ids, chip family, whether an automatic backend exists, and the display name. The human table in WhateverXe's `docs/device-selection.md` lists the same ids as ranges without names, so the table in the source is the one that can be parsed exactly. The list is fetched once per release and cached in `/var/lib/osx-proxmox-next/igpu/devices/`; the newest tag is looked up again after ten minutes.

- Every verdict names the release it was checked against, for example "Checked against WhateverXe 0.8.2." A chip that a newer release adds is offered as soon as that release is the latest, and a chip a release drops is refused for it.
- A chip of a family the tool has no option ROM for is shown as listed by WhateverXe but not supported yet, with the release that lists it.
- When GitHub cannot be reached the tool falls back to the list built into this version (WhateverXe 0.8.0, `intel_devices.txt`) and says so: "Based on WhateverXe 0.8.0, could not check the newest release." That answer can be out of date in both directions. Run the scan again once the network is back. `IGPU_DEVICES_OFFLINE=1` forces the built-in list.
- `igpu-check --wex-version <tag>`, `igpu-finish --wex-version <tag>` and `igpu-install --wex-version <tag>` judge the chip against that release instead of the latest. A chip the latest release dropped is refused by a plain `igpu-install`, so pass the older tag up front.
- GitHub's API allows 60 anonymous requests an hour per address. When it refuses, the tool reads the newest tag from the release page instead, so the scan keeps working.

## When it crashes

A hung macOS guest cannot tell you anything, so the host records it. Every iGPU VM, on any macOS version, gets the watchdog (the Tahoe section below describes what else it does). It runs as `osx-next-power-watchdog@<vmid>` and keeps two kinds of records under `/var/log/osx-proxmox-next/<vmid>/`:

- `serial.log`: a copy of the macOS kernel log from the serial port (boot-arg `serial=3`), rotated at 10 MB with 5 files (50 MB at most).
- `incidents/<time>-<kind>/`: one folder per crash, written right before the watchdog stops the VM. `kind` is `panic`, `hang-restart` or `hang-shutdown`. It holds `panic.txt` (the panic text from the serial port), `vcpu-registers.txt` (`info registers -a` of every vCPU), `igpu-registers.txt` (ring heads and tails, plane surface, TLB fault registers and power limit reasons, read from the iGPU's BAR0, read-only), `host.txt` (CPU temperatures, thermal throttle counters, load, CPU and iGPU id), `dmesg.txt` (the vfio, i915, IOMMU, thermal and MCE lines of the host log), `vm.conf` (with MAC addresses, SMBIOS serial and uuid and the OSK blanked), `versions.json` and `serial-tail.txt`. The snapshot takes under 8 seconds and every file is capped, so recovery is never held up. The newest 10 incidents are kept.

To send a crash upstream:

```bash
osx-next-cli igpu-report 996
```

It packs the latest three incidents, the installed versions, the VM config and the end of the serial log into `igpu-report-996-<time>.tar.gz` (in `/var/log/osx-proxmox-next/996/`, or the folder of `--out`), and prints a short summary: macOS version, CPU and iGPU id, WhateverXe, Graphics and AMFIPass versions and the first panic line of each recorded crash. Look through the bundle, then [open an issue at WhateverXe](https://github.com/b00t0x/WhateverXe/issues), paste the summary and attach the file. **Nothing is ever posted or uploaded by this tool.**

## Restart and shut down on Tahoe

With the iGPU attached, macOS Tahoe faults the GPU while it closes the desktop, and the kernel then waits for the GPU forever. The VM stays on with a black screen. This is a WhateverXe bug (issue 62) that the tool cannot fix, so it finishes the job from the host instead.

`igpu-finish` installs the power watchdog for every VM, and on Tahoe it is what finishes these hangs: a systemd unit `osx-next-power-watchdog@<vmid>` that reads the VM's serial port. The VM gets `serial0: socket` and the boot-arg `serial=3`, which sends the macOS kernel console there. The watchdog acts only when all of this is true:

1. macOS printed `Will be calling reboot(2) with flags: ...`. launchd prints that line once every app and service has quit, right before the kernel restarts or powers off. The flags say which one you chose.
2. 60 seconds later the VM has neither powered off nor reset (a reset shows the firmware's `BdsDxe:` lines on the serial port within seconds).
3. The VM's disks then stay idle for 15 more seconds.

Then it runs `qm stop`, followed by `qm start` when you chose Restart. So a restart from the Apple menu takes about 75 seconds longer than usual and a shut down about 75 seconds, but neither needs Proxmox. A VM that is working, booting or busy never prints that line, and a guest that resets or powers off by itself is left alone.

The watchdog also restarts the VM after a kernel panic. A panicked macOS guest with the iGPU never resets on its own (its vCPUs spin forever), so when the serial port shows `panic(cpu ...` and the VM has not reset 60 seconds later with its disks idle for 15 more, the host stops and starts it. macOS shows the panic report after that boot. After three such restarts within an hour the watchdog stops the VM and leaves it off, so a guest that panics at every boot does not loop.

What it costs and how to check it:

- The Proxmox serial console (`qm terminal`) of that VM does not work while the watchdog holds the serial port.
- Every decision is logged: `journalctl -u osx-next-power-watchdog@<vmid>`.
- `osx-next-cli igpu-watchdog <vmid> --remove` stops it and takes `serial=3` off (the VM must be stopped). It also turns itself off once the VM is deleted or no longer has the iGPU.

## Troubleshooting

### "Acceleration was not confirmed"

After the last boot, `igpu-finish` waits up to 5 minutes and reads the iGPU's registers from the host (BAR0, read-only). It reports "Metal acceleration is active" only when the blitter or render ring tail (`BCS_TAIL`, `RCS_TAIL`) is non-zero, which means macOS submitted work to the GPU. If both stay at zero the graphics package is installed but the GPU is idle, and the command exits non-zero.

- Check that the VM's OpenCore disk was assembled by this tool (it carries WhateverXe and AMFIPass) and not an older prebuilt image. Deleting `opencore-osx-proxmox-vm.iso` from the ISO cache makes the next run rebuild it.
- The check needs the VM to be running with the iGPU on `hostpci0`. A slow Sequoia boot can need a few more minutes than the check waits, so look at the screen on the monitor before assuming it failed.
- With nothing at all moving, run `gpu-setup --undo` and repeat the setup on a fresh VM.

## Undo

`gpu-setup --undo` restores the VM's args, display setting and OpenCore config from the backup. The VM stays on the i440fx machine type, which boots fine without the card. The host files are restored from their backups and the host needs one more reboot to give the iGPU back to i915.
