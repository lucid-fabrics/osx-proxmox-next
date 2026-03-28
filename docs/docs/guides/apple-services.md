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

Starting with macOS Sequoia 15, Apple performs **hardware device attestation** (DeviceCheck/App Attest) during Apple ID sign-in. Standard VM detection — where `hv_vmm_present` sysctl returns `1` — causes Apple's servers to reject authentication.

### Kernel Patch (Applied Automatically)

When `--apple-services` is enabled, the tool now injects an OpenCore `Kernel/Patch` that redirects the `hv_vmm_present` sysctl to `hibernatecount` (always `0`). This makes DeviceCheck see what appears to be a physical machine.

:::note
This fix is community-attested on Sequoia 15 and Tahoe 26. It has not been officially verified by Apple or this project. Results may vary — report your experience on Discord or GitHub Issues.
:::

The error without this patch appears as:

```
Verification Failed -- An unknown error occurred.
```

:::info
`RestrictEvents.kext` with `revpatch=sbvmm` alone does **not** fix this. The kernel patch injected by `--apple-services` is required.
:::

### Fallback: Install Sonoma First

If the kernel patch does not work in your setup, the Sonoma upgrade path remains a reliable fallback:

1. Create a **Sonoma 14** VM with `--apple-services`
2. Complete macOS setup, sign into Apple ID in System Settings
3. Verify iCloud, iMessage, FaceTime all work
4. Upgrade in-place to Sequoia or Tahoe via **System Settings > Software Update**
5. Apple Services stay connected because the device identity was established on Sonoma

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
