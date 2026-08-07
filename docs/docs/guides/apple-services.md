---
sidebar_position: 3
title: Apple Services
---

# Apple Services (iCloud, iMessage, FaceTime)

Apple services require a complete, consistent SMBIOS identity chain spanning both QEMU and OpenCore.

## How It Works

macOS validates Apple ID through two identity sources that must carry identical values:

| Layer | What It Provides | How It's Set |
|-------|-----------------|--------------|
| QEMU SMBIOS | Serial, UUID, model visible to firmware | Proxmox `--smbios1` flag |
| OpenCore PlatformInfo | Serial, UUID, MLB, ROM visible to macOS | Patched into `config.plist` via `plistlib` |

The ROM field is derived from the NIC MAC address. macOS cross-checks ROM against the hardware NIC during Apple ID validation.

## The `--apple-services` Flag

When enabled, the tool automatically:

1. Generates Apple-format SMBIOS identity (serial, UUID, MLB, ROM, model) with valid manufacturing codes and checksummed MLB
2. Generates a stable static MAC address for the NIC
3. Derives ROM from the MAC address (first 6 bytes, no colons)
4. Applies SMBIOS via Proxmox's `--smbios1` flag
5. Patches OpenCore's `config.plist` PlatformInfo with matching values
6. Adds a `vmgenid` device for Apple service stability

### CLI

```bash
osx-next-cli apply --execute \
  --vmid 910 --name macos-sonoma --macos sonoma \
  --cores 8 --memory 16384 --disk 128 \
  --bridge vmbr0 --storage local-lvm \
  --apple-services
```

### TUI

Check "Enable Apple Services (iMessage, FaceTime, iCloud)" in step 4 of the wizard.

### Custom SMBIOS Values

Provide your own values instead of auto-generated ones:

```bash
osx-next-cli apply --execute \
  --vmid 910 --name macos-sonoma --macos sonoma \
  --cores 8 --memory 16384 --disk 128 \
  --bridge vmbr0 --storage local-lvm \
  --smbios-serial C02G3050P7QM --smbios-uuid "$(uuidgen)" \
  --smbios-model MacPro7,1
```

To skip SMBIOS generation entirely, use `--no-smbios`.

## Post-Install Steps

1. Verify NVRAM is writable and persists across reboots
2. Boot macOS, confirm date/time are correct and network/DNS works
3. Sign in order: **Apple ID** (System Settings) first, then **Messages**, then **FaceTime**
4. Reboot once after login to confirm session persistence

## Sequoia/Tahoe Apple Services

Starting with macOS Sequoia 15, Apple performs **hardware device attestation** (DeviceCheck/App Attest) during Apple ID sign-in. Standard VM detection, where the `hv_vmm_present` sysctl returns `1`, causes Apple's servers to reject authentication.

### Kernel Patch (Applied Automatically)

When `--apple-services` is enabled, the tool injects two OpenCore `Kernel/Patch` entries. The first renames the real `hv_vmm_present` sysctl so nothing can resolve it; the second renames `hibernatecount` into its place. That counter reads `0` on a default VM, so DeviceCheck sees what appears to be a physical machine.

Verify inside the VM:

```bash
sysctl -n kern.hv_vmm_present    # must print 0
```

Because it is a swap, `kern.hibernatecount` now carries what `hv_vmm_present` used to, so it reads `1` on a VM. That is the patch working, not a fault. Verified on a macOS Sequoia guest:

```console
$ sysctl kern.hv_vmm_present kern.hibernatecount
kern.hv_vmm_present: 0
kern.hibernatecount: 1

$ sysctl -d kern.hibernatecount
kern.hibernatecount: running on a vmm
```

The description travels with the name, which is why `hibernatecount` describes itself oddly. Harmless, and the same residue OpenCore Legacy Patcher leaves on `direct_handoff`.

:::warning
Releases that predate the fix for [#114](https://github.com/lucid-fabrics/osx-proxmox-next/issues/114) injected only the second patch. Both OIDs then carried the name `hv_vmm_present` and `sysctlbyname()` still resolved the real one, so the sysctl kept returning `1` and sign-in failed. Rebuild the VM on the latest release.
:::

:::note
The `sysctl` check above confirms the patches themselves are working. Whether Apple then accepts the sign-in is server-side and can change at any time, so results may vary. Report your experience on Discord or GitHub Issues.
:::

The error without this patch appears as:

```
Verification Failed -- An unknown error occurred.
```

:::info
`RestrictEvents.kext` with `revpatch=sbvmm` alone does **not** fix this. The kernel patch injected by `--apple-services` is required.
:::

### Fallback: Install Sonoma First

If the kernel patches do not work in your setup, you can establish the Apple ID session on Sonoma first:

1. Create a **Sonoma 14** VM with `--apple-services`
2. Complete macOS setup, sign into Apple ID in System Settings
3. Verify iCloud, iMessage, FaceTime all work
4. Upgrade in-place to Sequoia or Tahoe via **System Settings > Software Update**

:::warning
Build the Sonoma VM on a release that includes the [#114](https://github.com/lucid-fabrics/osx-proxmox-next/issues/114) fix. The patches carry `MinKernel 24.0.0`, so they sit dormant on Sonoma (Darwin 23) and activate on the first Sequoia or Tahoe boot. A VM built on an older release carries the *broken* patch across the upgrade, and an in-place macOS upgrade never rebuilds the OpenCore disk, so the fix cannot arrive that way. This is the likely cause of the upgrade failure reported in #114.

After upgrading, check `sysctl -n kern.hv_vmm_present` prints `0`.
:::

## Common Issues

| Problem | Fix |
|---------|-----|
| "This Mac cannot connect to iCloud" | Recheck serial/MLB/UUID/ROM uniqueness. Sign out, reboot, sign in again. |
| "iMessage activation failed" | Verify ROM matches NIC MAC and MAC is static. Check date/time sync. |
| Works once then breaks | VM config is regenerating SMBIOS or NIC MAC between boots. |
| PlatformInfo not applied | Ensure `--apple-services` flag is set. Check OpenCore `config.plist` for PlatformInfo section. |
| "Verification Failed" on Sequoia/Tahoe | The kernel patch via `--apple-services` should fix this. If it doesn't, use the Sonoma upgrade fallback above. |

:::note
Apple controls service activation server-side. Even with a correct setup, activation may require multiple attempts or a call to Apple Support. Never share SMBIOS values publicly or reuse them across VMs.
:::
