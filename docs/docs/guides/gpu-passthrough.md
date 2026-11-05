---
sidebar_position: 8
title: GPU passthrough (guided)
---

# GPU passthrough (guided)

One guided flow takes you from "there is a graphics card in my Proxmox server" to "macOS is using it". It works for every AMD and NVIDIA card macOS can drive. You never have to read a wiki, edit a file by hand or guess a command. Where a person has to act (the BIOS, plugging in a monitor, installing a driver inside macOS) the tool tells you exactly what to do and gives you a photo card, a QR code or a saved file to carry away.

:::caution Beta
The host setup and the GPU attach have not been tested on real hardware yet. Every change is previewed, confirmed, backed up and undoable, but read the preview before you confirm it.
:::

## Start it

| Where | How |
|-------|-----|
| TUI | `osx-next`, then step 2, **GPU Passthrough** |
| CLI | `osx-next-cli gpu-setup` (also `--status`, `--undo`, `--yes`, `--vm <vmid>`, `--gpu <address>`, `--display gpu\|console`) |
| Standalone script | `osx-proxmox-next.sh gpu-setup` (uses whiptail screens) |

Run it again at any time: it resumes where it stopped. While a setup is paused, every login shows one line such as `GPU setup paused at step 3 of 8 (BIOS). Continue with: osx-next-cli gpu-setup`. The line goes away when the setup finishes or is undone.

`gpu-setup --status` only looks. It lists the cards, the host facts and what is still missing, and changes nothing.

## The eight steps

```
 GPU passthrough   [1 Scan] [2 Choose] [3 BIOS] [4 Host] [5 Reboot] [6 Verify] [7 VM] [8 First boot]
                      done      done     <now>
```

Steps that this host does not need show as `skipped`. Detection comes first: the tool reads your CPU, board, boot loader and IOMMU state itself and only asks what it cannot know.

1. **Scan.** One card per graphics card found, with a verdict: works natively, works with NullMoth (beta), or not supported with the reason in one line. Cards used by a VM or by the host are flagged.
2. **Choose.** Pick the card, the macOS VM (or set the host up first and pick the VM later), and where you want to see macOS. *Monitor on the GPU* turns the emulated display off (`vga none`). *Keep the Proxmox console* keeps the web console and uses the card for acceleration only. NVIDIA always uses the Proxmox console.
3. **BIOS.** Only the failing items are listed: virtualization, IOMMU, Above 4G Decoding, CSM off, and the primary display when the card is the boot display. You get a photo card to photograph, a QR code to the page for your motherboard vendor, and the same text saved in `/root/osx-proxmox-next-bios-steps.txt`. The tool can reboot straight into the BIOS when your firmware allows it, after it lists the running VMs that will stop.
4. **Host.** Kernel arguments (GRUB or systemd-boot, detected from what really booted), the VFIO modules, early binding of the card to `vfio-pci`, and for the AMD cards with the reset bug the `vendor-reset` module. You see the exact diff, every file is backed up with a timestamp, and an error rolls everything back.
5. **Reboot.** The tool never reboots the host by itself in an unattended run. It lists the VMs that will stop.
6. **Verify.** IOMMU and interrupt remapping, the card on `vfio-pci`, the IOMMU group, the reset method, the BAR size. Each failure points to the step that fixes it.
7. **VM.** The card is attached to the VM after the macOS install is finished, never during it. The VM config and the OpenCore config are saved first.
8. **First boot.** For NVIDIA, a checklist for inside macOS (with a QR code). For AMD, the question "Do you see the macOS desktop on your monitor?" with a fix for each way it can go wrong.

## Supported cards

### AMD: native drivers, no third-party software

macOS ships its own AMD drivers, so nothing is installed in the guest. These families work on macOS Ventura 13, Sonoma 14, Sequoia 15 and Tahoe 26 (the *Tahoe* column says whether a Mac that Tahoe supports uses this family).

| Family | Ventura to Sequoia | Tahoe 26 | Reset bug handling | Extra boot-arg |
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
| Ventura 13, Sonoma 14 guests | No |

> **NVIDIA GPU support (Beta)**
>
> This feature prepares your VM for the NullMoth NVIDIA driver, a third-party project that osx-proxmox-next does not make, review or support. You download and install that driver yourself, at your own risk. We are not responsible for the driver, its installation, or any damage, data loss or instability that follows.
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
- `pcie_acs_override` is never applied. If the card shares its IOMMU group with other devices, move it to another slot.
- With Secure Boot on, an unsigned `vendor-reset` module is rejected, so the tool does not install it and prints the key enrollment steps instead.
- The Radeon VII with `vendor-reset` gets a hard warning that you must confirm by typing: stopping and starting a VM that holds it without rebooting the host has corrupted host storage on Proxmox 9.

## Undo

`osx-next-cli gpu-setup --undo` (a button in the TUI) restores the VM settings exactly as they were before the attach (the original `hostpci0`, `args` and `vga`), the OpenCore config, and every host file from its backup, newest first. It rebuilds the boot configuration and the initramfs when it touched them and says whether a reboot is needed. Running it twice is safe.

Undo only reverts what this tool wrote. A VM field (`hostpci0`, `args`, `vga`) or a host file that you changed afterwards keeps your version and is reported; for `args` only the arguments this tool added are removed. The OpenCore `config.plist` goes back only while it is still the file this tool wrote: if it changed since (for example the NullMoth 1401 app edited it), your version stays and the original is left next to it as `config.plist.pre-nullmoth` or `config.plist.pre-gpu`. Undo also finishes when there is nothing to revert: the VM is gone, or an earlier attach was refused or failed before it changed anything.

## If the card moves

The tool remembers the card by its address, its vendor and device id, its subsystem ids and its PCI class, and checks all of them again before every step that reads or writes the host or a VM. PCIe addresses shift when you move a card to another slot or change a BIOS option, so a saved address can end up on an NVMe drive or a network card. In that case the tool stops with "The card at 0000:06:00 is now a different device. Your PCI layout changed. Run the scan again." and sends you back to the scan. The early-boot binding script checks the same things at boot and skips any device that does not match, and storage or network devices are never bound to `vfio-pci`.

## Troubleshooting the first boot

**No signal on the monitor.** Use an HDMI cable first: DisplayPort outputs have stayed black on several cards. Plug the monitor into the card, not the motherboard. Make sure the VM has `vga none` and the card sits on the PCI Express port (run `gpu-setup` again and pick *Monitor on the GPU*). Reset NVRAM from the OpenCore picker.

**Black screen after the Apple logo.** The VM must not use the emulated display. Navi cards need `agdpmod=pikera` (added for you). Turn Resizable BAR off in the BIOS: some RX 6600 cards stay black with it on. Try HDMI instead of DisplayPort.

**Works once, then fails until the host reboots.** This is the AMD reset bug. Polaris, Vega and Navi 10 and 14 need `vendor-reset`; let the Host step install it. Shut the VM down from inside macOS, not with `qm stop`. If it still fails, reboot the host between runs.

**NVIDIA.** In the guest: download the NullMoth release yourself, check `SHA256SUMS`, run `sudo ./install.sh`, approve the extensions, reboot, then verify with `kmutil showloaded --list-only | grep nullmoth` and run `sudo pmset -a sleep 0`. If the guest will not boot, add `-nvoff` to the boot-args or run `gpu-setup --undo`.
