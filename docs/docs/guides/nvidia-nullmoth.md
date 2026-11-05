---
sidebar_position: 9
title: NVIDIA GPU (NullMoth, Beta)
---

# NVIDIA GPU (NullMoth, Beta)

The easiest way is the guided flow, [GPU passthrough (guided)](gpu-passthrough.md), which also configures the host and checks the BIOS. This page describes what is changed in the VM. This guide attaches an NVIDIA Turing-or-newer GPU to an installed macOS VM and prepares OpenCore for the NullMoth driver. This tool never installs the driver in the guest. You do that yourself.

> **NVIDIA GPU support (Beta)**
>
> This feature prepares your VM for the NullMoth NVIDIA driver, a third-party project that osx-proxmox-next does not make, review or support. You download and install that driver yourself, at your own risk. We are not responsible for the driver, its installation, or any damage, data loss or instability that follows.
>
> NullMoth only runs with core macOS security turned off: code-signing checks (AMFI) are bypassed, System Integrity Protection is relaxed and Secure Boot is disabled. This is a real security threat: malware has a much easier time on a VM set up this way. Only enable it on a VM you are prepared to treat as untrusted, and keep personal accounts and sensitive data off it.
>
> NullMoth is licensed PolyForm Noncommercial 1.0.0. Driver and docs: https://github.com/nullmoth/nvidia-macos-driver

## Supported

| Item | Supported |
|------|-----------|
| Cards | Turing and newer (RTX 20 / 30 / 40 / 50 series) |
| Not supported | Kepler, Maxwell, Pascal, Volta |
| Guest | macOS Sequoia 15.5+ or Tahoe 26. Ventura and Sonoma are refused |

## Host requirements

The command checks all of these and refuses with a clear message when one fails. `nvidia-attach` itself never writes host configuration; the guided flow does that part, with a preview and a backup.

- IOMMU on (VT-d or AMD-Vi), and the card in its own IOMMU group (bridges are fine, other devices are not).
- Every function of the card (`01:00.*`) bound to `vfio-pci`. `nvidia` and `nouveau` must not hold it. When it is not bound, the command prints the `options vfio-pci ids=...` and `softdep` lines for `/etc/modprobe.d/vfio.conf`, then you run `update-initramfs -u -k all` and reboot.
- Warnings, not failures: the card is the host console GPU (boot GPU with a host driver or `efifb`/`simplefb` on it) without `initcall_blacklist=sysfb_init`, or BAR1 is larger than 256 MiB (the one Proxmox report of NullMoth ran at 256 MiB; there is no automatic resize).

## Usage

```bash
# Preview: preflight result and plan, nothing changes
osx-next-cli nvidia-attach 106 --gpu 01:00 --dry-run

# Apply (VM must be stopped; you must accept the notice)
osx-next-cli nvidia-attach 106 --gpu 01:00 --accept-nullmoth-risk

# Undo: removes hostpci0, the MMIO window and the OpenCore changes
osx-next-cli nvidia-detach 106
```

The standalone script has the same two commands (`osx-proxmox-next.sh nvidia-attach <vmid> <pci-address>` and `nvidia-detach <vmid>`). It asks you to type `I accept`, or take `--accept-nullmoth-risk` for non-interactive use. In the TUI, open **Step 2 > GPU Passthrough** and pick the NVIDIA card: it goes through the same guided steps as an AMD card. The acknowledgement is typed (`I accept`) every time the screen opens, and the Choose button unlocks only when the phrase matches.

The VM must be stopped and must be a macOS VM made by this tool. The macOS release is read from the VM description or name; pass `--macos sequoia` or `--macos tahoe` when it cannot be read.

## What changes

On the VM:

- `qm set <vmid> --hostpci0 <bdf>,pcie=0` (all functions).
- `-global q35-pcihost.pci-hole64-size=64G` appended to `args` (never duplicated).
- `vga std`, `balloon 0` and everything else stay as they are.

On the OpenCore disk (`ide0`):

- `config.plist` is backed up as `config.plist.pre-nullmoth` next to it.
- `AMFIPass.kext` (pinned release, SHA-256 verified) is added after Lilu.
- boot-args gain `nvfb=1 nvaccel=1 nvfbheads=4 -nvkmsnosmooth amfi_get_out_of_my_way=0x1 amfi=0x80 ipc_control_port_options=0 gfxrst=4`; `nv_disable=1` and `-wegnoegpu` are removed.
- `Kernel > Block` gets `com.apple.iokit.IONDRVSupport` with strategy `Exclude`.
- The SMBIOS model is set to `MacPro7,1` (the OpenCore template ships `iMacPro1,1`, which macOS 26 refuses).
- `csr-active-config` must already include `0x0A43` and `SecureBootModel` must be `Disabled`; the command stops without changing anything if not.

## Before applying: get a way into macOS

Once the NullMoth driver loads, the Proxmox console goes black. The driver takes the display even with `vga std`, so the web console shows nothing. The card needs a display of its own: connect a monitor or an HDMI/DisplayPort dummy plug to it. Without one, both the Proxmox console and Screen Sharing stay black (Screen Sharing mirrors the display on the card). SSH always works. `vga std` stays set because the firmware and OpenCore screens and recovery still use it. Set up remote access in the guest **before** you attach the card:

1. In macOS open System Settings > General > Sharing and turn on **Remote Login** and **Screen Sharing**.
2. Note the VM's IP address.
3. Connect with `ssh <user>@<ip>` or any VNC client pointed at the VM's IP (from a Mac: `open vnc://<ip>`).
4. Connect a monitor or an HDMI/DisplayPort dummy plug to the card. The guided flow asks you to confirm this too.

If `systemsetup -setremotelogin on` is refused because it needs Full Disk Access, run `sudo launchctl load -w /System/Library/LaunchDaemons/ssh.plist` instead. The guided flow asks you to confirm this checklist before it attaches the card.

## After applying

1. Start the VM. Once the NullMoth driver loads, the Proxmox console goes black; to get a desktop, a monitor or an HDMI/DisplayPort dummy plug must be connected to the card (Screen Sharing then mirrors that display). SSH always works.
2. Download the NullMoth release from https://github.com/nullmoth/nvidia-macos-driver/releases, check it with `shasum -a 256 -c SHA256SUMS`, then run `sudo ./install.sh` in the guest.
3. Approve the extensions in System Settings > Privacy & Security and reboot the guest.
4. Verify with `kmutil showloaded --list-only | grep -i nullmoth`. Expect 4 or more lines (NVRM, NVAccel, NVRMFB, NVRMAGDC; newer releases add more, for example NMXAudio in 2.7.2).
5. Turn off system sleep: `sudo pmset -a sleep 0`.

Escape hatches: add `-nvoff` to the boot-args, or run `gpu-setup --undo` (or `nvidia-detach <vmid>`, the same code path and record) on the host, which removes `hostpci0`, the MMIO window and restores the saved `config.plist`.

:::warning
The NullMoth 1401 app edits the OpenCore config itself. Rebuilding the OpenCore disk with this tool later, or running `gpu-setup --undo`, wipes those edits.
:::

## Status

Beta. Tested end to end on an RTX 3060 with macOS Tahoe 26.7.2 and NullMoth 2.7.2: the driver loads, Metal 3 works, it survives reboots and the undo restores the VM exactly. Other cards and macOS versions have not been tried yet.
