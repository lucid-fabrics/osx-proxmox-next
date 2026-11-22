---
sidebar_position: 8
title: GPU passthrough (guided)
---

# GPU passthrough (guided)

One guided flow takes you from "there is a graphics card in my Proxmox server" to "macOS is using it". It works for every AMD and NVIDIA card macOS can drive, and for the Intel iGPU of recent processors (beta, see [Intel iGPU](intel-igpu-passthrough.md)). You never have to read a wiki, edit a file by hand or guess a command. Where a person has to act (the BIOS, plugging in a monitor, installing a driver inside macOS) the tool tells you exactly what to do and gives you a photo card, a QR code or a saved file to carry away.

![osx-next: your Macs, a new Mac in one screen, and the graphics card setup](/img/demo-tui.gif)

:::caution Beta
NVIDIA support relies on NullMoth, a community driver tested by its own community: this tool downloads the driver, verifies it and installs it for you, but we take no responsibility for the driver or its install. AMD cards and the host setup steps (kernel command line, vfio binding, host reboot) are covered by tests but not yet tried on real hardware. Every change is previewed, confirmed, backed up and undoable, but read the preview before you confirm it.
:::

## Start it

| Where | How |
|-------|-----|
| TUI | `osx-next`, then `g` on a Mac (or `ctrl+p` and **GPU passthrough**) |
| CLI | `osx-next-cli gpu-setup` (also `--status`, `--undo`, `--yes`, `--vm <vmid>`, `--gpu <address>`, `--display gpu\|console`, `--macos <release>`) |
| Standalone script | `osx-proxmox-next.sh gpu-setup` (uses whiptail screens) |

Run it again at any time: it resumes where it stopped. While a setup is paused, every login shows one line such as `GPU setup paused at step 3 of 8 (BIOS). Continue with: osx-next-cli gpu-setup`. The line goes away when the setup finishes or is undone.

`gpu-setup --status` only looks. It lists the cards, the host facts and what is still missing, and changes nothing.

## The eight steps

The TUI shows three moments instead of eight numbered steps: one screen to choose and approve, one restart, one question about the first boot. The CLI and the bash script still walk the eight steps:

```
 [1 Scan] [2 Choose] [3 BIOS] [4 Host] [5 Reboot] [6 Verify] [7 VM] [8 First boot]
                      done      done     <now>
```

In the TUI, the card, the Mac and the display are rows on one screen (`g` on a Mac preselects it), the right panel lists only what this host needs (BIOS settings, host setup, one restart, the attach), `p` shows every file line, command and VM setting, and `enter` approves the whole plan once, including the attach that runs after the restart. The BIOS screen only appears when a setting must change. After the restart, opening the flow again runs the check and the attach without asking again, unless the plan changed since you approved it. `u` puts everything back.

![The one restart box in the TUI, listing the VMs the restart shuts down](/img/tui/gpu_restart_onboot.png)

Steps that this host does not need show as `skipped`. Detection comes first: the tool reads your CPU, board, boot loader and IOMMU state itself and only asks what it cannot know.

1. **Scan.** One card per graphics card found, with a verdict: works natively, works with NullMoth (beta), or not supported with the reason in one line. Cards held by a running VM or by the host are flagged. `host console` only appears when the host really uses the card (it is the boot GPU and a host driver or `efifb`/`simplefb` holds it); a boot GPU that sits on `vfio-pci` just shows *The firmware shows its boot screen on this card.*
2. **Choose.** Pick the card, the macOS VM (or set the host up first and pick the VM later), and where you want to see macOS. *Monitor on the GPU* turns the emulated display off (`vga none`). *Keep the Proxmox console* keeps the web console and uses the card for acceleration only. NVIDIA is the exception: the Proxmox console goes black once the NullMoth driver loads (the driver takes the display even with `vga std`), so you need Remote Login and Screen Sharing in macOS and a monitor or HDMI/DisplayPort dummy plug connected to the card to get a desktop. A card that a stopped VM also lists is still offered, with the warning *Also assigned to VM 120 (stopped). Only one of them can run at a time; Proxmox refuses to start the second.*, and you confirm it before the choice is saved. A running VM (or a mapping used by one) blocks the card.
3. **BIOS.** Only the failing items are listed: virtualization, IOMMU, Above 4G Decoding, CSM off, and the primary display when the card is the boot display. You get a photo card to photograph, a QR code to the page for your motherboard vendor, and the same text saved in `/root/osx-proxmox-next-bios-steps.txt`. The tool can reboot straight into the BIOS when your firmware allows it, after it lists the running VMs that will stop.
4. **Host.** Kernel arguments (GRUB or systemd-boot, detected from what really booted), the VFIO modules, early binding of the card to `vfio-pci`, and for the AMD cards with the reset bug the `vendor-reset` module. You see the exact diff, every file is backed up with a timestamp, and an error rolls everything back.
5. **Reboot.** The tool never reboots the host by itself in an unattended run. It lists the VMs that will stop.
6. **Verify.** IOMMU and interrupt remapping, the card on `vfio-pci`, the IOMMU group, the reset method, the BAR size. Each failure points to the step that fixes it.
7. **VM.** The card is attached to the VM after the macOS install is finished, never during it. The VM config and the OpenCore config are saved first. For NVIDIA the tool does the rest by itself, with nothing to do inside macOS: it downloads the latest NullMoth release (you can pick an older one from a list, latest preselected), verifies it against the release's `SHA256SUMS` and GitHub's digest, boots recovery once to stage it, turns off the extension approval prompt, and the first boot installs it, turns on Remote Login and Screen Sharing and keeps the Mac awake. It then prints the `ssh` and `vnc://` lines for the VM. Once the driver loads the Proxmox console goes black; the tool asks for a monitor or an HDMI/DisplayPort dummy plug on the card only when none was detected.
8. **First boot.** For NVIDIA, a checklist for inside macOS (with a QR code). For AMD, the question "Do you see the macOS desktop on your monitor?" with a fix for each way it can go wrong.

## Supported cards

### AMD: native drivers, no third-party software

macOS ships its own AMD drivers, so nothing is installed in the guest. These families work on macOS Monterey 12, Ventura 13, Sonoma 14, Sequoia 15 and Tahoe 26 (the *Tahoe* column says whether a Mac that Tahoe supports uses this family).

| Family | Monterey to Sequoia | Tahoe 26 | Reset bug handling | Extra boot-arg |
|--------|--------------------|----------|--------------------|----------------|
| Polaris 10/20/30 (RX 470/480/570/580/590, WX 5100/7100) | Yes | Yes | vendor-reset module | `none` |
| Polaris 11/21 (RX 460/560, WX 4100, Pro 560X) | Yes | Not confirmed | vendor-reset module | `none` |
| Vega 10 (RX Vega 56/64, Vega Frontier, WX 8200/9100) | Yes | Not confirmed | vendor-reset module | `none` |
| Vega 20 (Radeon VII, Radeon Pro VII, Pro Vega II) | Yes | Yes | vendor-reset module, hard warning | `none` |
| Navi 10 (RX 5600 XT/5700/5700 XT, W5700) | Yes | Yes | vendor-reset module | `agdpmod=pikera` |
| Navi 14 (RX 5300/5500 XT, W5500) | Yes | Yes | vendor-reset module | `agdpmod=pikera` |
| Navi 21 (RX 6800/6800 XT/6900 XT, W6800) | Yes | Yes | early binding only | `agdpmod=pikera` |
| Navi 23 (RX 6600/6600 XT, W6600) | Yes | Yes | early binding only | `agdpmod=pikera` |

RX 6900 XT XTXH (`73AF`), RX 6950 XT (`73A5`) and RX 6650 XT (`73EF`) work too: the guest is shown the id of the supported sibling card.

Not supported, and never offered:

| Card | Why |
|------|-----|
| RX 6700 series (Navi 22) | RX 6700 series (Navi 22): Apple never shipped an accelerator for it. |
| RX 6300/6400/6500 XT (Navi 24) | RX 6300/6400/6500 XT (Navi 24): macOS has no driver for it. |
| RX 580 2048SP / RX 590 GME | RX 580 2048SP and RX 590 GME: Apple's driver does not match this variant. |
| RX 540/550 (Polaris 12) | RX 540/550 (Polaris 12): needs a device-id spoof and extra patches, not supported here. |
| RX 7000 series (RDNA3) | RX 7000 series: Apple never shipped a driver. |
| RX 9000 series (RDNA4) | RX 9000 series: Apple never shipped a driver. |
| Integrated graphics (APUs) | No Apple driver. |

AMD cards are placed on a PCI Express port (`pcie=1`) and the VM gets `-global ICH9-LPC.acpi-pci-hotplug-with-bridge-support=off`. Navi cards get `agdpmod=pikera` in the OpenCore boot-args.

### NVIDIA: third-party NullMoth driver (beta)

| Card generation | Supported |
|-----------------|-----------|
| Turing and newer (RTX 20 / 30 / 40 / 50 series) | Yes, on macOS Sequoia 15.5+ and Tahoe 26 |
| Kepler, Maxwell, Pascal, Volta (GTX 600 to GTX 10, Titan V) | No. Older than the RTX 20 series: NVIDIA's macOS drivers stopped at High Sierra and NullMoth needs Turing or newer. |
| Laptop (mobile) NVIDIA GPUs of any generation | No. The scan marks them "not supported (laptop NVIDIA)", so on a machine with an Intel iGPU the iGPU is recommended instead. A card counts as a laptop part when its lspci name says Laptop, Mobile or Max-Q, or its chip name ends in M (for example AD107M). The name comes from the host's PCI ID list, so run `update-pciids` if a new laptop card shows up as just "Device". |
| Monterey 12, Ventura 13, Sonoma 14 guests | No |

> **NVIDIA GPU support (Beta)**
>
> This feature sets up your VM for the NullMoth NVIDIA driver, a third-party project that osx-proxmox-next does not make, review or support. The tool downloads the driver from its GitHub releases, checks its checksum and installs it in your VM for you, at your own risk. We are not responsible for the driver, its installation, or any damage, data loss or instability that follows.
>
> NullMoth only runs with core macOS security turned off: code-signing checks (AMFI) are bypassed, System Integrity Protection is relaxed and Secure Boot is disabled. This is a real security threat: malware has a much easier time on a VM set up this way. Only enable it on a VM you are prepared to treat as untrusted, and keep personal accounts and sensitive data off it.
>
> NullMoth is licensed PolyForm Noncommercial 1.0.0. Driver and docs: https://github.com/nullmoth/nvidia-macos-driver

You have to acknowledge this notice before an NVIDIA card is attached. See [NVIDIA GPU (NullMoth, Beta)](nvidia-nullmoth.md) for what exactly is changed in the VM.

## BIOS pages

The QR code on the photo card points to the page for your motherboard or computer maker. Each page lists the same steps as the card and links the official manual. Entries that were not checked against a source say "usually under".

[ASUS](gpu/bios/asus.md) / [MSI](gpu/bios/msi.md) / [Gigabyte and Aorus](gpu/bios/gigabyte.md) / [ASRock](gpu/bios/asrock.md) / [ASRock Rack](gpu/bios/asrock-rack.md) / [Supermicro](gpu/bios/supermicro.md) / [Dell](gpu/bios/dell.md) / [HP](gpu/bios/hp.md) / [HPE](gpu/bios/hpe.md) / [Lenovo](gpu/bios/lenovo.md) / [Intel NUC](gpu/bios/intel-nuc.md) / [Mini PCs](gpu/bios/mini-pc.md) / [Other boards](gpu/bios/generic.md)

## What the host step changes

Only what is missing, and only these places:

| What | File |
|------|------|
| Kernel arguments (GRUB) | `/etc/default/grub.d/osx-proxmox-next.cfg`, then `update-grub` |
| Kernel arguments (systemd-boot) | `/etc/kernel/cmdline` (backed up), then `proxmox-boot-tool refresh` |
| VFIO modules | `/etc/modules-load.d/osx-proxmox-next-vfio.conf` |
| Binding the card | `/etc/modprobe.d/osx-proxmox-next-vfio.conf`, or your existing `vfio-pci ids=` line (backed up) |
| Another identical card in the host | `/etc/initramfs-tools/scripts/init-top/a-osx-proxmox-next-gpu-bind`, so only your card is taken |
| vendor-reset (affected AMD cards) | `/etc/modules-load.d/osx-proxmox-next-vendor-reset.conf`, a udev rule, `/etc/initramfs-tools/modules` (backed up), and a DKMS build pinned to one commit |

Rules it keeps:

- The boot loader comes from what booted (`efibootmgr`, `/proc/cmdline`), never from which files exist.
- `intel_iommu=on` is written for Intel CPUs, `iommu=pt` for all. `amd_iommu=on` is never written: it is not a valid value.
- `initcall_blacklist=sysfb_init` is added only when the chosen card is the host console GPU. If it is the only display adapter in the host, you must type a confirmation first.
- `pcie_acs_override` is never applied. Some guides suggest it to split the group, but it only hides the problem and can let the VM reach those devices. If the card shares its IOMMU group with other devices, move it to another slot.
- With Secure Boot on, an unsigned `vendor-reset` module is rejected, so the tool does not install it and prints the key enrollment steps instead.
- The Radeon VII with `vendor-reset` gets a hard warning that you must confirm by typing: stopping and starting a VM that holds it without rebooting the host has corrupted host storage on Proxmox 9.

## Updating the driver

A Mac that already has a card keeps its driver up to date with one command or one key. What gets updated depends on the card:

| Card | What is updated | How |
|---|---|---|
| NVIDIA | NullMoth inside macOS (latest release, or the one you pick) | The Mac stops, the card comes off, recovery stages the new NullMoth, the card goes back and the first boot installs it |
| Intel iGPU | WhateverXe on the OpenCore disk and the WhateverXe graphics package in macOS | The same staging as the NVIDIA update, with WhateverXe swapped on the OpenCore disk |
| AMD | Lilu and WhateverGreen on the OpenCore disk, to the versions this release pins | The disk is mounted and the kexts that are behind the pin are replaced (one that is newer than the pin is kept, never downgraded). No recovery and no card removal, because macOS has the AMD driver itself |

In the TUI press `g` on the Mac. The Driver block shows the installed version (`NullMoth v2.8.1`, one row per component for Intel and AMD, or `installed version unknown` for a Mac that was set up before the tool recorded versions) and, on its own Status row, `v2.9 available` or `✓ up to date`. A Mac that is up to date shows no button, only `Nothing to update.` `←` and `→` on the Version row pick another release (the newest ten, latest first). Enter opens "Before you continue" with the facts (how long the Mac stops, that the card comes off and goes back, the snapshot name) and `p` shows every change. One approval, then the update runs by itself in a separate process, so closing the terminal does not stop it. Home marks a Mac whose driver has a newer release with `● driver update`, and a Mac whose update was cut off with `● driver update stopped` (the newest releases are checked in the background at most once a day, and never while offline).

```bash
osx-next-cli driver-status --vmid 101              # kind, installed versions (or unknown), latest, driver snapshots (--json for scripts)
osx-next-cli driver-update --vmid 101 --dry-run    # the plan, nothing changed
osx-next-cli driver-update --vmid 101              # update to the latest, after a confirmation (--yes skips it)
osx-next-cli driver-update --vmid 101 --version v2.8.1       # NVIDIA: a NullMoth release
osx-next-cli driver-update --vmid 101 --wex-version v1.2.0 --graphics-version v0.9.1   # Intel: pick both
osx-next-cli driver-update --vmid 101 --reinstall  # install the same version again
osx-next-cli driver-update --vmid 101 --finish     # continue an update that was cut off
osx-next-cli driver-undo --vmid 101                # undo the driver update: the Mac's disks go back to the driver snapshot
osx-proxmox-next.sh driver-status 101              # the standalone installer has the same three commands
osx-proxmox-next.sh driver-update 101 [version]
osx-proxmox-next.sh driver-undo 101
osx-proxmox-next.sh driver-update 101 --finish    # the same Finish from the standalone installer
```

NVIDIA updates need `--accept-nullmoth-risk` (the TUI asks you to type the acknowledgement), the same notice as the first setup. When the Mac already has the target version the command says "Up to date" and changes nothing.

**Safety.** Before anything changes the tool saves a snapshot called `osx-next-driver-<YYYYMMDD-HHMMSS>` with `qm snapshot`. **Undo driver update** (`r` in the TUI, `driver-undo` on the command line) stops the Mac, runs `qm rollback` to it, puts the driver record back to the previous version and starts the Mac. The key is shown only while a driver snapshot of that Mac exists, and its dialog says that the Mac's disks go back to the snapshot of that date and that everything you did in macOS since is lost, with Cancel focused. `u` is not on this screen: it undoes the card setup for every Mac that has the card, so that undo stays in the Graphics card flow and the Mac's own view ("Put everything back"). Undo is refused, before the Mac is touched, when you took a newer snapshot yourself (Proxmox cannot roll back past it) or when the card setup changed since the snapshot (putting the snapshot back would point the Mac at a card the host no longer prepares). The snapshot name is made once, when the plan is built, and the update uses exactly that name (the plan, the confirmation, the run screen and `qm listsnapshot` all show the same one). Before the rollback, the removal of a disk or the removal of a snapshot the tool takes its own leftovers off the Mac's volumes (loop devices and the partition maps udev or kpartx make) and checks that none remain; when one does, it stops with a line that names the map and the exact `dmsetup remove` command, and never leaves a Mac with a `lock: rollback`. A Mac keeps at most one snapshot of this tool, on every outcome: the new snapshot replaces a leftover as soon as it is saved (the plan says "The previous driver snapshot is removed once the new one is saved"), so a failed update and a Finish keep exactly one, an undo removes the snapshot it went back to once it succeeded, and after a "Yes" on the first boot question the TUI offers to remove the new one too (the default is Keep). Snapshots that are not the tool's are never touched. The recovery and payload disks an update imports are written down the moment they exist: when a manual `qm rollback` leaves one as an `unused` disk, the tool's undo and Finish remove exactly that disk and no other. The tool only ever lists, rolls back or removes snapshots it made itself, found by their exact name prefix. If the Mac's storage cannot take snapshots (for example thick LVM or a raw disk on a directory storage), the plan says so as a warning and you have to type `no snapshot` (or pass `--no-snapshot`) to go on without one.

The Mac stops with a normal macOS shutdown request. If macOS does not shut down within 2 minutes the Mac is stopped by force, and the plan tells you that before you approve. An update is refused while the Mac is still being installed, while a card setup for it is waiting to finish, while another update runs for the same Mac (a lock that the system frees when the process ends, so a crash or a host restart never leaves a stuck one), and when the card in the Mac is not the kind its driver record was made for. The kind always comes from the graphics card (PCI class 0x03) that is passed through on this host: a USB controller or a NIC is not one. Only the Mac being updated is touched, and the OpenCore disk is only mounted while the Mac is stopped (read only when the tool just reads versions).

**What the Intel update cannot fix.** The update ends with a first boot of macOS with the iGPU, in which the guest installs the graphics package once WhateverXe is loaded. If the Mac already stops at startup with the iGPU before the update (macOS panics in `IntelAccelerator::probe` with `Failed to map Device ID`, which happens when the iGPU does not start under WhateverXe), the first boot never reports and the update cannot finish: the plan says so as a warning. The tool watches the Mac while it waits: a Mac whose disks do nothing for 10 minutes and that has not reported is stopped instead of waited for 25 minutes, and the failure says what was seen (the staged package never ran, the disks were idle, macOS most likely stopped at startup), that the Mac was left stopped, and what to do: read the kernel console (`osx-next-cli igpu-watchdog <vmid>`, then start the Mac) and, if the Mac does the same after `r`, fix the iGPU setup first. A guest that did run and reported a failure keeps the Mac running.

**An update that stopped.** If the terminal closes, the connection drops, the host restarts or a step fails, Home shows `● driver update stopped` on that Mac and `g` opens its Graphics card screen even when the card is off the Mac. The screen says whether the card is off and offers **Finish** (enter, or `driver-update --finish`) and **Undo driver update** (`r`). Finish goes on from the step that stopped, with the same releases, and looks at the Mac again first: it is stopped and its card taken off before anything is staged or any OpenCore disk is written, and put back when it is not in place. The screen of a stopped update shows only that state (`update stopped`, and on the right what Finish does: no new snapshot, the one of the run stays), never the plan of a new update, and a Finish starts a fresh step list with no clock of the run before. A failure says what it left behind (nothing changed, the OpenCore disk half changed, the driver staged inside macOS) and whether the Mac is stopped or running. If the Mac cannot be stopped, Finish refuses with that line and the way back through undo.

**Records.** The tool keeps one record per Mac in `/var/lib/osx-proxmox-next/drivers/<vmid>.json` (the kind, and for each component its name, version, tag, SHA-256, source and install time), written on every setup and every update. A Mac that only has an Intel iGPU record from an older version is read from it. An NVIDIA Mac set up before this feature shows "installed version unknown" until its first update. A card undo ("Put everything back") removes the driver record, since the next card may be another kind. The versions come from the Mac itself where it can be read: for AMD (Lilu and WhateverGreen) and Intel (WhateverXe) the kexts on the OpenCore disk win over the record, read only and only while the Mac is stopped; a record that the disk contradicts is dropped. While an Intel or AMD Mac runs its OpenCore disk is not read, so the versions come from the record and are marked `(from the last update)`. A record also stores the identity of the Mac's logical volumes: after a manual `qm rollback` they differ, so the installed version shows as unknown instead of "Up to date" (on storage that gives no such identity the record stands).

## Undo

`osx-next-cli gpu-setup --undo` (a button in the TUI) restores the VM settings exactly as they were before the attach (the original `hostpci0`, `args` and `vga`), the OpenCore config, and every host file from its backup, newest first. It rebuilds the boot configuration and the initramfs when it touched them and says whether a reboot is needed. Running it twice is safe. It also undoes an attach made with `nvidia-attach` (both write the same record in `/var/lib/osx-proxmox-next/gpu-attach-<vmid>.json`), and `nvidia-detach <vmid>` undoes one made by the walkthrough. When no record exists it says *Nothing to undo: no GPU setup record found* (or *Removed an empty setup record; nothing else to undo.* when a setup record with no changes existed) and, if a VM still carries NullMoth settings, points at `nvidia-detach <vmid>`.

Undo only reverts what this tool wrote. A VM field (`hostpci0`, `args`, `vga`) or a host file that you changed afterwards keeps your version and is reported; for `args` only the arguments this tool added are removed. The OpenCore `config.plist` goes back only while it is still the file this tool wrote: if it changed since (for example the NullMoth 1401 app edited it), your version stays and the original is left next to it as `config.plist.pre-nullmoth` or `config.plist.pre-gpu`. Undo also finishes when there is nothing to revert: the VM is gone, or an earlier attach was refused or failed before it changed anything.

## If the card moves

The tool remembers the card by its address, its vendor and device id, its subsystem ids and its PCI class, and checks all of them again before every step that reads or writes the host or a VM. PCIe addresses shift when you move a card to another slot or change a BIOS option, so a saved address can end up on an NVMe drive or a network card. In that case the tool stops with "The card at 0000:06:00 is now a different device. Your PCI layout changed. Run the scan again." and sends you back to the scan. The early-boot binding script checks the same things at boot and skips any device that does not match, and storage or network devices are never bound to `vfio-pci`.

## Troubleshooting the first boot

**No signal on the monitor.** Use an HDMI cable first: DisplayPort outputs have stayed black on several cards. Plug the monitor into the card, not the motherboard. Make sure the VM has `vga none` and the card sits on the PCI Express port (run `gpu-setup` again and pick *Monitor on the GPU*). Reset NVRAM from the OpenCore picker.

**Black screen after the Apple logo.** The VM must not use the emulated display. Navi cards need `agdpmod=pikera` (added for you). Turn Resizable BAR off in the BIOS: some RX 6600 cards stay black with it on. Try HDMI instead of DisplayPort.

**Works once, then fails until the host reboots.** This is the AMD reset bug. Polaris, Vega and Navi 10 and 14 need `vendor-reset`; let the Host step install it. Shut the VM down from inside macOS, not with `qm stop`. If it still fails, reboot the host between runs.

**NVIDIA.** Nothing to do in macOS: the driver, Remote Login, Screen Sharing and the sleep setting are done for you, and the command prints the `ssh` and `vnc://` lines. The Proxmox console goes black once the driver loads, and a monitor or HDMI/DisplayPort dummy plug must be connected to the card to get a desktop (the tool says so when it sees no display). If the install failed, the report shows why and the log is `/var/log/osx-proxmox-next-nullmoth.log` in the guest. If the guest will not boot, add `-nvoff` to the boot-args or run `gpu-setup --undo`.
