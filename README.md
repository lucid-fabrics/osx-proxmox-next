# OSX Proxmox Next

<p align="center">
  <a href="https://github.com/wmehanna/osx-proxmox-next">
    <img alt="Proxmox" src="https://img.shields.io/badge/Proxmox-9%20Ready-E57000?logo=proxmox&logoColor=white">
  </a>
  <img alt="macOS" src="https://img.shields.io/badge/macOS-Sonoma%2014%20%7C%20Sequoia%2015%20%7C%20Tahoe%2026-111111?logo=apple&logoColor=white">
  <a href="https://ko-fi.com/lucidfabrics">
    <img alt="Support on Ko-fi" src="https://img.shields.io/badge/Support-Ko--fi-FF5E5B?logo=ko-fi&logoColor=white">
  </a>
</p>

<p align="center">
  <a href="https://ko-fi.com/lucidfabrics">
    <img src="https://ko-fi.com/img/githubbutton_sm.svg" alt="Support me on Ko-fi">
  </a>
</p>

> This project is actively maintained through community support.  
> If this tool saved you time, please support it: **https://ko-fi.com/lucidfabrics**

Noob-friendly macOS VM setup for Proxmox 9.
If you can run one command and follow a simple wizard, you can use this.

## Important Disclaimer
- This project is for testing, lab use, and learning.
- Respect Apple licensing and intellectual property.
- You are responsible for legal/compliance use in your region and environment.

## Support This Project
This project is built and maintained with donation support.

If this helped you:
- saved troubleshooting time
- made macOS on Proxmox easier
- replaced hours of manual trial/error

please consider supporting development:

## 👉 Donate on Ko-fi: https://ko-fi.com/lucidfabrics

## What You Get
- Guided TUI workflow (Preflight -> Configure -> Dry Run -> Live Apply)
- Auto-selected VMID (next free ID)
- Auto-detected CPU/RAM defaults from host hardware (safe limits)
- Auto-detected Tahoe installer path (when found)
- Safer defaults for boot/display/disk visibility
- Clear validation before live apply

## Supported OS Versions
| macOS | Channel | TSC Required | Notes |
|---|---|---|---|
| Sonoma 14 | Stable | Recommended | Best experience with stable TSC flags (`constant_tsc`, `nonstop_tsc`). |
| Sequoia 15 | Stable | Recommended | Stable timing reduces clock drift and random VM lag. |
| Tahoe 26 | Preview | Recommended | Preview target; stable timing strongly recommended. |

## Prerequisites

### Hardware Requirements
| Component | Minimum | Recommended | Notes |
|---|---|---|---|
| CPU | Intel/AMD 4 cores | 8+ cores | VT-x/AMD-V required. IOMMU required for passthrough. |
| RAM | 8GB host RAM | 16GB+ host RAM | Keep headroom for Proxmox. Allocate at least 4GB to VM. |
| Storage | 64GB free | 128GB+ SSD/NVMe | macOS + APFS updates need extra free space. |
| GPU | Integrated/basic display | Discrete passthrough GPU | Passthrough can improve UX/perf but needs manual host setup. |

### Host Requirements
- Proxmox VE 9 host with root shell access.
- Internet access for bootstrap and dependencies.
- ISO storage available (for example `/var/lib/vz/template/iso`).

### TSC Check (Recommended)
```bash
lscpu | grep -E 'Model name|Flags'
```
Look for `constant_tsc` and `nonstop_tsc` in CPU flags.

## Quick Start (One Command)
```bash
bash -c "$(curl -fsSL https://raw.githubusercontent.com/wmehanna/osx-proxmox-next/main/install.sh)"
```

## Super Simple Wizard Steps
1. Open `Preflight` and check for failures.
2. Open `Wizard`.
3. Click `Use Recommended`.
4. Choose macOS version button.
5. Choose storage target button.
6. Click `Apply Dry`.
7. Click `Apply Live`.

That is enough for most users.

### Wizard Preview
![Wizard Step 2](docs/images/wizard-step2.png)

## If Installer Does Not Show Your Disk
In macOS installer:
1. Open Disk Utility.
2. Click `View -> Show All Devices`.
3. Select `QEMU HARDDISK Media`.
4. Erase as:
- Format: `APFS`
- Scheme: `GUID Partition Map`

Then continue installation.

## How This Tool Works (Plain English)
1. Reads your wizard settings.
2. Finds OpenCore + recovery/installer assets.
3. Builds a deterministic `qm` command plan.
4. Runs it in dry mode or live mode.
5. Blocks unsafe live runs if required assets are missing.

## GPU Passthrough (Proxmox 9, Intel/AMD CPU, AMD GPU)
Host setup is manual:
1. Enable virtualization + IOMMU in BIOS/UEFI.
2. Enable IOMMU in kernel cmdline:
- Intel: `intel_iommu=on iommu=pt`
- AMD: `amd_iommu=on iommu=pt`
3. Bind GPU + GPU audio function to `vfio-pci`.
4. Reboot host.
5. Attach both functions to VM (`hostpci0`, `hostpci1`).

Reference:
- https://pve.proxmox.com/wiki/PCI(e)_Passthrough

## Network Optimization
- Use bridge networking (`vmbr0`).
- Use `virtio` NIC model.
- Keep MTU consistent across host/switch/VM.
- Measure before/after tuning (throughput and latency).

References:
- https://pve.proxmox.com/wiki/Network_Configuration
- https://pve.proxmox.com/pve-docs/chapter-qm.html

## Suggested Performance Optimizations
- Use SSD/NVMe-backed storage for VM disks.
- Avoid overcommitting host CPU/RAM.
- Keep main install disk on `sata0`.
- Keep OpenCore on `ide2` and recovery on `ide3`.
- Use `vga: std` for stable noVNC during install.
- Change one setting at a time and re-measure.

## Enable Apple Services (iCloud, iMessage, FaceTime)
1. Use unique SMBIOS identity per VM.
2. Ensure serial values are valid and consistent.
3. Verify macOS date/time and network.
4. Sign in to iCloud first, then iMessage/FaceTime.

Important:
- Do not share SMBIOS identity values publicly.
- Do not reuse the same identity across multiple VMs.

## FAQ
### Is this production-ready?
Primarily designed for testing/lab use.

### Why is live apply blocked with missing assets?
Because required OpenCore/installer files were not found.

### Why do I see UEFI Shell?
Usually boot media path/order mismatch.

### Why do I see “Guest has not initialized the display”?
Usually boot/display profile mismatch during early boot.

### Do I need to set VMID manually?
No, it auto-selects the next available VMID.

### Can I use GPU passthrough?
Yes, after host passthrough setup is completed.

## CLI Usage
```bash
# preflight
osx-next-cli preflight

# dry apply
osx-next-cli apply \
  --vmid 910 --name macos-sequoia --macos sequoia \
  --cores 8 --memory 16384 --disk 128 \
  --bridge vmbr0 --storage local-lvm

# live apply
osx-next-cli apply --execute \
  --vmid 910 --name macos-sequoia --macos sequoia \
  --cores 8 --memory 16384 --disk 128 \
  --bridge vmbr0 --storage local-lvm
```

## Support The Project
<p align="left">
  <a href="https://ko-fi.com/lucidfabrics">
    <img src="https://ko-fi.com/img/githubbutton_sm.svg" alt="Support me on Ko-fi">
  </a>
</p>

Primary support link:
- **https://ko-fi.com/lucidfabrics**

Want a project-specific Ko-fi URL?
- Create (or rename to) a project-specific Ko-fi username, if available.
- Ko-fi link format is `https://ko-fi.com/<username>`.

## Project Layout
- `src/osx_proxmox_next/app.py`: TUI workflow
- `src/osx_proxmox_next/cli.py`: non-interactive CLI
- `src/osx_proxmox_next/domain.py`: VM model + validation
- `src/osx_proxmox_next/planner.py`: command generation
- `src/osx_proxmox_next/executor.py`: apply engine
- `src/osx_proxmox_next/assets.py`: OpenCore/installer detection
- `src/osx_proxmox_next/defaults.py`: host-aware defaults
- `src/osx_proxmox_next/rollback.py`: rollback hints
