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
| "Verification Failed" — Apple ID on Sequoia/Tahoe | `hv_vmm_present` sysctl returning `1` causes DeviceCheck to reject sign-in | Ensure `--apple-services` was used — it injects a kernel patch redirecting `hv_vmm_present` to `hibernatecount`. Note: `RestrictEvents.kext revpatch=sbvmm` alone does **not** fix this. If the patch doesn't work, use the Sonoma upgrade path (see [Apple Services guide](./apple-services.md)). |
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

After macOS finishes installing, fix the boot order so the main disk boots first:

```bash
qm set <vmid> --boot order=virtio0;ide0
```

Without this, the VM boots into the recovery installer on every start.

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
