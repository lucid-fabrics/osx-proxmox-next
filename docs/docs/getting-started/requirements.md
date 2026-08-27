---
sidebar_position: 1
title: Requirements
---

# Requirements

Before installing OSX Proxmox Next, verify your hardware and software meet the following requirements.

## Hardware

| Component | Minimum | Recommended |
|-----------|---------|-------------|
| CPU | 2 cores (power of 2), VT-x or AMD-V | 8+ cores |
| RAM | 8 GB host (4 GB allocated to VM) | 16+ GB host |
| Storage | 64 GB minimum (defaults: 80-160 GB by version) | 128+ GB SSD/NVMe |
| GPU | Integrated | Discrete (for passthrough) |

:::note
CPU core count **must be a power of 2** (2, 4, 8, 16). Non-power-of-2 values like 6 or 12 can cause the macOS kernel to hang at the Apple logo.
:::

### Host RAM Is Checked Before the VM Is Created

macOS has no balloon driver, so every VM is built with `--balloon 0` and its full allocation is pinned at `qm start`. Asking for more RAM than the host has free either fails the start or gets the VM OOM-killed mid-install, so both the TUI and the bash script refuse the request up front:

```text
Not enough free RAM on this host for a 32768 MiB VM
  Host has only 8192 MiB free for a VM (MemAvailable minus 1024 MiB host reserve).
  Lower the VM memory or free up host RAM, then run the script again.
```

The limit is `MemAvailable` from `/proc/meminfo` minus a 1024 MiB reserve kept for the Proxmox host itself. `MemAvailable` is used rather than `MemFree` because reclaimable page cache is genuinely available to a starting VM.

The suggested default is half the host's total RAM, capped to that same free-RAM limit and clamped to 4096-32768 MiB. When `/proc/meminfo` cannot be read the default is 8192 MiB and no cap is applied.

### CPU Compatibility

| CPU Type | How It Works |
|----------|-------------|
| Intel (Skylake+) | Native host passthrough -- best performance |
| Intel Xeon | Native host passthrough with `e1000` NIC (vmxnet3 unsupported on most Xeon hosts) |
| Intel (pre-Skylake) | Penryn mode with `e1000` NIC for install stability |
| AMD | Cascadelake-Server emulation -- functional but slower |

All CPU types are auto-detected. No manual configuration needed.

## Software

| Requirement | Details |
|------------|---------|
| Proxmox VE | Version 8.x or 9.x with root shell access (9.x is the most-tested target) |
| Python | 3.9+ (only for pipx/pip install method) |
| dmg2img | Installed automatically by the tool |
| Internet | Required for bootstrap and downloading macOS recovery images |

### ISO Storage

The tool needs access to a Proxmox ISO storage directory. Common locations:

- `/var/lib/vz/template/iso` (default local storage)
- `/mnt/pve/*/template/iso` (shared NAS or additional storage pools)

## BIOS Settings

These settings must be enabled in your motherboard BIOS/UEFI before creating a macOS VM.

| Setting | Where to Find It |
|---------|-----------------|
| **VT-x** (Intel) or **AMD-V** (AMD) | CPU or Advanced settings |
| **VT-d** (Intel) or **AMD IOMMU** (AMD) | Chipset or Advanced settings -- required for GPU passthrough |

:::warning
Without VT-x/AMD-V enabled, the VM will not start. If you plan to use GPU passthrough, VT-d/IOMMU is also required.
:::

## TSC Check (Recommended)

Stable TSC (Time Stamp Counter) flags reduce clock drift and VM lag. Verify with:

```bash
lscpu | grep -E 'Model name|Flags'
```

Look for `constant_tsc` and `nonstop_tsc` in the output. Most modern CPUs have these flags.

Once your hardware is confirmed, proceed to [Installation](./installation.md).
