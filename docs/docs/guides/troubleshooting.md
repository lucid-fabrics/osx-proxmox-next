---
sidebar_position: 5
title: Troubleshooting
---

# Troubleshooting

## Common Boot Failures

| Symptom | Cause | Fix |
|---------|-------|-----|
| "Reset NVRAM" only option | Wrong recovery format or missing kexts/drivers | Re-download recovery with `osx-next-cli download --macos <version>`. Ensure OpenCore includes all required kexts. |
| `EXITBS:START` hang | Missing CryptexFixup (Sonoma+), SIP misconfigured, or MBR disk format | Verify OpenCore has CryptexFixup.kext. Ensure disk is GPT + EFI partition, not MBR+FAT32. |
| `Err(0xE)` BootKernelExtensions | Missing CryptexFixup kext | Add CryptexFixup.kext to OpenCore. Required for macOS Sonoma 14 and later. |
| "No bootable device" | OVMF cannot read MBR+FAT32 as cdrom | OpenCore must be GPT with an EFI System Partition. Rebuild with the tool. |
| UEFI Shell instead of macOS boot | Boot media path or order mismatch | Ensure OpenCore is on `ide0`, recovery on `ide2`, boot order: `ide2;virtio0;ide0`. |
| "Guest has not initialized the display" | Display profile mismatch during early boot | Use `vga: std` for stable noVNC during installation. |
| Stuck on Apple logo (flat CPU) | Non-power-of-2 CPU core count | Use 2, 4, 8, or 16 cores. Values like 6 or 12 cause kernel hangs. |
| Freeze at "X minutes remaining" during install, CPU 100%, network/disk IO both flat zero, mouse/keyboard unresponsive | Real Xeon E5/E7 v2-v4 (HEDT/dual-socket) CPUs leak their genuine multi-package topology through `-cpu host`. Combined with the `MacPro7,1` SMBIOS, XNU's scheduler can livelock during heavy multithreaded I/O like the installer copy phase | The bash installer auto-detects these CPUs and uses a fixed emulated model (`Broadwell-noTSX,model=158` or `Haswell-noTSX,model=158,stepping=3`) instead of `-cpu host`. Confirm on the Proxmox host with `qm config <vmid>`: the args line should show `Broadwell-noTSX` or `Haswell-noTSX`, not `host`. |
| Install never finishes, VM keeps landing back in Recovery | The OpenCore picker lists `macOS Base System` (the attached recovery disk) before `macOS Installer`, and `Timeout=15` auto-boots the first entry. Every installer reboot therefore restarts Recovery instead of resuming | Detach recovery once the installer has rebooted once: `osx-next-cli post-install --vmid <id> --execute`. The picker then only lists the installer and auto-boot resumes the install. Picking **macOS Installer** by hand works for that boot; **Ctrl+Enter** to pin it as default is inconsistent in testing, so do not rely on it. |
| "Verification Failed", Apple ID on Sequoia/Tahoe | `hv_vmm_present` sysctl returning `1` causes DeviceCheck to reject sign-in | Run `sysctl -n kern.hv_vmm_present` in the VM: it must print `0`. If it prints `1`, first check `pmset -g \| grep hibernatemode`. If that is not `0`, run `sudo pmset -a hibernatemode 0` and cold-boot the VM, the patch reroutes the flag to the hibernate counter, which a hibernate cycle increments. If hibernatemode was already `0`, the patch is not in effect: releases that predate the fix for [#114](https://github.com/lucid-fabrics/osx-proxmox-next/issues/114) injected an incomplete patch that left the real sysctl registered, so rebuild the VM on the latest release with `--apple-services`. Note: `RestrictEvents.kext revpatch=sbvmm` alone does **not** fix this. |
| macOS is slow on AMD | Expected -- AMD uses CPU emulation | AMD hosts use `Cascadelake-Server` emulation instead of native passthrough. Intel hosts get native performance. |
| Installer doesn't show disk | Disk not formatted | Open Disk Utility > View > Show All Devices > Select QEMU VirtIO > Erase as APFS + GUID Partition Map. |

## Verbose Boot

Enable kernel logging to diagnose boot hangs:

```bash
osx-next-cli apply --execute --verbose-boot \
  --vmid 910 --name macos-sequoia --macos sequoia \
  --cores 8 --memory 16384 --disk 128 \
  --bridge vmbr0 --storage local-lvm
```

This adds `-v` to OpenCore boot arguments, replacing the Apple logo with a text log.

## Diagnostics

### Export Log Bundle

```bash
osx-next-cli bundle
```

Collects diagnostic information into a shareable bundle for troubleshooting.

### Check Host Readiness

```bash
osx-next-cli preflight
```

Validates CPU vendor, virtualization extensions, dependencies, and TSC flags. Automatically installs missing build dependencies if detected.

### Recovery Guide

```bash
osx-next-cli guide "boot issue"
```

Prints step-by-step recovery instructions for a given issue description.

### Check VM Status

```bash
osx-next-cli status --vmid 910
```

Shows VM name, running state, and key configuration (cores, memory, CPU model, network, SMBIOS).

## Missing Assets

If `apply` is blocked by missing assets, the tool scans these directories:

- `/var/lib/vz/template/iso`
- `/mnt/pve/*/template/iso`
- Custom path from `--iso-dir`

It looks for:

| Asset | Filename Pattern |
|-------|-----------------|
| OpenCore | `opencore-osx-proxmox-vm.iso` or `opencore-{version}.iso` |
| Recovery | `{version}-recovery.img` or `{version}-recovery.iso` |

Auto-download missing assets:

```bash
osx-next-cli download --macos <version>
```

:::note
The TUI wizard (step 5) auto-downloads missing assets before the dry-run preview.
:::

## Post-Install Boot Order

Run this as soon as the installer reboots the VM for the first time, not only once macOS is fully installed:

```bash
osx-next-cli post-install --vmid <id> --execute
```

It detaches the recovery disk and sets boot order `ide0;virtio0`. Until you do, the OpenCore picker lists recovery ahead of the installer and auto-boots it after 15s, so each reboot restarts the installer instead of resuming it, and the install never finishes.

## MSR Kernel Panics

If macOS panics with MSR-related errors, ensure the host has:

```bash
echo "options kvm ignore_msrs=Y" > /etc/modprobe.d/kvm.conf
```

Then reboot the Proxmox host.

## Getting Help

- **GitHub Issues**: [lucid-fabrics/osx-proxmox-next/issues](https://github.com/lucid-fabrics/osx-proxmox-next/issues)
- **Discord**: [Join the community](https://discord.gg/Ub6TunHYre)

When reporting an issue, include:

1. Output of `osx-next-cli preflight`
2. Output of `osx-next-cli bundle`
3. The macOS version and CPU vendor (Intel/AMD)
4. The exact error message or symptom
