---
sidebar_position: 9
title: NVIDIA GPU (NullMoth, Beta)
---

# NVIDIA GPU (NullMoth, Beta)

The easiest way is the guided flow, [GPU passthrough (guided)](gpu-passthrough.md), which also configures the host and checks the BIOS. This page describes what is changed in the VM. This guide attaches an NVIDIA Turing-or-newer GPU to an installed macOS VM, prepares OpenCore and installs the NullMoth driver in the guest for you. There is nothing to do inside macOS.

> **NVIDIA GPU support (Beta)**
>
> This feature sets up your VM for the NullMoth NVIDIA driver, a third-party project that osx-proxmox-next does not make, review or support. The tool downloads the driver from its GitHub releases, checks its checksum and installs it in your VM for you, at your own risk. We are not responsible for the driver, its installation, or any damage, data loss or instability that follows.
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

The command checks all of these and refuses with a clear message when one fails. `nvidia-attach` itself never writes host configuration; the guided flow does that part, with a preview and a backup. It does write files to the ISO storage (the driver, NVIDIA's firmware package and a temporary payload disk), so that storage needs about 1.5 GB free.

- IOMMU on (VT-d or AMD-Vi), and the card in its own IOMMU group (bridges are fine, other devices are not).
- Every function of the card (`01:00.*`) bound to `vfio-pci`. `nvidia` and `nouveau` must not hold it. When it is not bound, the command prints the `options vfio-pci ids=...` and `softdep` lines for `/etc/modprobe.d/vfio.conf`, then you run `update-initramfs -u -k all` and reboot.
- Warnings, not failures: the card is the host console GPU (boot GPU with a host driver or `efifb`/`simplefb` on it) without `initcall_blacklist=sysfb_init`, or BAR1 is larger than 256 MiB (the one Proxmox report of NullMoth ran at 256 MiB; there is no automatic resize).

## Usage

```bash
# Preview: preflight result and plan, nothing changes
osx-next-cli nvidia-attach 106 --gpu 01:00 --dry-run

# Apply (VM must be stopped and installed; you must accept the notice).
# On a terminal it lists the NullMoth releases and preselects the latest.
osx-next-cli nvidia-attach 106 --gpu 01:00 --accept-nullmoth-risk

# Pick the release up front (or --yes for the latest without asking)
osx-next-cli nullmoth-versions
osx-next-cli nvidia-attach 106 --gpu 01:00 --accept-nullmoth-risk --nullmoth-version v2.8.1

# Undo: removes hostpci0, the MMIO window and the OpenCore changes
osx-next-cli nvidia-detach 106
```

The standalone script has the same commands (`osx-proxmox-next.sh nvidia-attach <vmid> <pci-address> [--nullmoth-version <tag>]`, `nullmoth-versions` and `nvidia-detach <vmid>`). It asks you to type `I accept`, or take `--accept-nullmoth-risk` for non-interactive use. In the TUI, open **Step 2 > GPU Passthrough** and pick the NVIDIA card: it goes through the same guided steps as an AMD card, and the VM step has a release picker with the latest release preselected. The acknowledgement is typed (`I accept`) every time the screen opens, and the Choose button unlocks only when the phrase matches.

The VM must be stopped, installed (no recovery disk attached) and made by this tool. The macOS release is read from the VM description or name; pass `--macos sequoia` or `--macos tahoe` when it cannot be read.

## What happens

After you accept the notice nothing else is asked. The setup takes about 15 minutes, most of it the driver's own installer.

1. **Download and verify.** The host downloads the latest NullMoth release from GitHub (or the one you picked), and checks the tarball against the release's `SHA256SUMS.txt` and against the sha256 digest GitHub stores for the asset. A mismatch, a missing checksum or an unexpected download address stops everything. There is no pinned version and no silent fallback. NullMoth installs NVIDIA's GPU firmware from NVIDIA's own driver package; the host downloads that package (the address and checksum come from the release) so the guest needs no network.
2. **Stage from recovery.** The VM boots macOS recovery once with the emulated display (the card is not attached yet), the recovery Terminal is driven by key presses, and one command copies the driver and a first-boot script into the installed macOS. It also runs `spctl kext-consent disable`, so macOS never asks you to approve the extensions in System Settings.
3. **Attach and first boot.** The card is attached (OpenCore changes, `hostpci0`, MMIO window) and the VM boots normally. A one-shot LaunchDaemon in the guest turns on Remote Login and Screen Sharing, runs `pmset -a sleep 0`, checks the driver's checksum again and runs its `install.sh`. It then restarts, waits until the NullMoth extensions (4 or more) are loaded, tells the host and powers off.
4. **Report.** The host drops the payload disk, starts the VM, finds its address from the neighbour table and prints the connection lines:

```
Done. VM 106: NullMoth v2.9 is installed and loaded (5 extensions).
  ssh <user>@192.168.1.57
  vnc://192.168.1.57
```

The only thing the tool cannot do is plug a monitor in. It reads the card's display from inside the guest (a monitor's EDID) and prints one line asking for a monitor or an HDMI/DisplayPort dummy plug only when none was seen. Without one, the Proxmox console and Screen Sharing both stay black; SSH always works.

If the install fails, the report says why (the last lines of the driver's installer), the VM keeps running without the driver, and the log is `/var/log/osx-proxmox-next-nullmoth.log` in the guest. Remote Login, Screen Sharing and sleep 0 stay switched on and the extension approval prompt stays off. To retry, run `nvidia-detach <vmid>` first (the card is attached by then), then the same command: it stages again from scratch.

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

## After applying

Once the NullMoth driver loads, the Proxmox console goes black. The driver takes the display even with `vga std`. Use the `ssh` or `vnc://` line the command printed, or the monitor on the card. `vga std` stays set because the firmware and OpenCore screens and recovery still use it.

To check by hand: `kmutil showloaded --list-only | grep -i nullmoth` in the guest should list 4 or more lines (NVRM, NVAccel, NVRMFB, NVRMAGDC; newer releases add more, for example NMXAudio in 2.7.2).

Escape hatches: add `-nvoff` to the boot-args, or run `gpu-setup --undo` (or `nvidia-detach <vmid>`, the same code path and record) on the host, which removes `hostpci0`, the MMIO window and restores the saved `config.plist`.

:::warning
The NullMoth 1401 app edits the OpenCore config itself. Rebuilding the OpenCore disk with this tool later, or running `gpu-setup --undo`, wipes those edits.
:::

## Status

Beta. NullMoth is a community project, and its compatibility is tested by its community, not by us. This tool downloads the driver from its GitHub releases, verifies it and installs it for you, but we do not make, ship or support the driver, and we take no responsibility for installing it or for what it does.
