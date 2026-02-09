# OSX Proxmox Next

Noob-friendly macOS VM setup for Proxmox 9.
If you can run one command and follow a simple wizard, you can use this.

## Important Disclaimer
- This project is for testing, lab use, and learning.
- Respect Apple licensing and intellectual property.
- You are responsible for legal/compliance use in your region and environment.

## What You Get
- Guided TUI workflow (Preflight -> Configure -> Dry Run -> Live Apply)
- Auto-selected VMID (next free ID)
- Auto-detected CPU/RAM defaults from host hardware (with safe limits)
- Auto-detected Tahoe installer path (when found)
- Safer defaults that avoid common boot/display/disk issues
- Clear validation errors before live apply

## Supported macOS Targets
- `sonoma` (macOS 14)
- `sequoia` (macOS 15)
- `tahoe` (macOS 26 preview)

## Prerequisites

### Hardware Requirements
| Component | Minimum | Recommended | Notes |
|---|---|---|---|
| CPU | Intel/AMD 4 cores | 8+ cores | Hardware virtualization required (VT-x/AMD-V). IOMMU needed for passthrough. |
| RAM | 8GB host RAM | 16GB+ host RAM | Keep enough free RAM for Proxmox. Allocate at least 4GB to macOS VM. |
| Storage | 64GB free | 128GB+ SSD/NVMe | macOS install is large; APFS and updates need extra headroom. |
| GPU | Integrated/basic display | Discrete GPU passthrough | Passthrough can improve UX/performance but adds setup complexity. |

### Host Requirements
- Proxmox VE 9 host with admin access (`root` shell).
- Internet access for installer/bootstrap and dependencies.
- ISO storage path available (for example `/var/lib/vz/template/iso`).

### TSC Check (Recommended)
macOS guests are sensitive to unstable CPU timing. A stable TSC helps avoid clock drift, lag, and random install/runtime issues.

Quick check on host:

```bash
lscpu | grep -E 'Model name|Flags'
```

Look for timing-related flags such as `constant_tsc` and `nonstop_tsc`.
If your platform lacks stable timing behavior, reduce overcommit and avoid aggressive CPU tuning until baseline stability is confirmed.

## One Command Install
Run on your Proxmox host:

```bash
bash -c "$(curl -fsSL https://raw.githubusercontent.com/wmehanna/osx-proxmox-next/main/install.sh)"
```

## Super Simple Wizard Steps
1. Open `Preflight` and check for failures.
2. Open `Wizard`.
3. Click `Use Recommended`.
4. Choose your macOS version button.
5. Choose storage target button.
6. Click `Apply Dry` (safe test).
7. Click `Apply Live`.

That is enough for most users.

Wizard screenshot:

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
3. Builds a step-by-step `qm` plan.
4. Runs it in dry mode or live mode.
5. Stops live run early if required assets are missing.

## GPU Passthrough (Proxmox 9, Intel/AMD CPU, AMD GPU)
Do this on host first (manual):
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
- Use bridge networking (`vmbr0`)
- Use `virtio` network model
- Keep MTU consistent host/switch/VM
- Test throughput/latency before and after changes

References:
- https://pve.proxmox.com/wiki/Network_Configuration
- https://pve.proxmox.com/pve-docs/chapter-qm.html

## Suggested Speed Optimizations
After stable install:
- Put VM disks on fast SSD/NVMe storage.
- Do not over-allocate host CPU/RAM.
- Keep some host resources free for Proxmox itself.
- Keep main install disk on `sata0` (better installer visibility).
- Keep OpenCore on `ide2` and recovery on `ide3`.
- Use `vga: std` for stable noVNC during install.
- Measure one change at a time.

## Common Problems
### UEFI Shell
- Usually wrong boot media/order.
- Default boot order should be `ide2 -> ide3 -> sata0`.

### “Guest has not initialized the display”
- Usually boot/display profile mismatch.
- Use latest defaults and reboot VM.

### Live apply blocked: missing assets
- Place required files in:
- `/var/lib/vz/template/iso`
- `/mnt/pve/*/template/iso`

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

## CLI Usage
```bash
# Preflight
osx-next-cli preflight

# Plan only
osx-next-cli plan \
  --vmid 910 --name macos-sequoia --macos sequoia \
  --cores 8 --memory 16384 --disk 128 \
  --bridge vmbr0 --storage local-lvm

# Dry apply
osx-next-cli apply \
  --vmid 910 --name macos-sequoia --macos sequoia \
  --cores 8 --memory 16384 --disk 128 \
  --bridge vmbr0 --storage local-lvm

# Live apply
osx-next-cli apply --execute \
  --vmid 910 --name macos-sequoia --macos sequoia \
  --cores 8 --memory 16384 --disk 128 \
  --bridge vmbr0 --storage local-lvm
```

Tahoe example:

```bash
osx-next-cli apply --execute \
  --vmid 926 --name macos-tahoe --macos tahoe \
  --cores 8 --memory 16384 --disk 160 \
  --bridge vmbr0 --storage local-lvm \
  --installer-path /var/lib/vz/template/iso/macos-tahoe-full.iso
```

## Support The Project
- Ko-fi: https://ko-fi.com/lucidfabrics
- You can create a project-specific Ko-fi URL by using a project-specific Ko-fi username (if available).

## Project Layout
- `src/osx_proxmox_next/app.py`: TUI workflow
- `src/osx_proxmox_next/cli.py`: non-interactive CLI
- `src/osx_proxmox_next/domain.py`: VM model + validation
- `src/osx_proxmox_next/planner.py`: command generation
- `src/osx_proxmox_next/executor.py`: apply engine
- `src/osx_proxmox_next/assets.py`: OpenCore/installer detection
- `src/osx_proxmox_next/defaults.py`: host-aware defaults
- `src/osx_proxmox_next/rollback.py`: rollback hints
