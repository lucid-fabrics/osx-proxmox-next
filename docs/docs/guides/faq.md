---
sidebar_position: 6
title: FAQ
---

# FAQ

Common questions about OSX Proxmox Next that don't fall under troubleshooting.

---

<details>
<summary><strong>Can I run multiple macOS VMs at once?</strong></summary>

Yes. Each VM gets its own VMID, SMBIOS identity, and disk. Run the wizard or CLI multiple times with different VMIDs. Smart caching means OpenCore and recovery images are downloaded once and reused across VM installs.

```bash
osx-next-cli apply --execute --vmid 910 --name macos-dev --macos sequoia ...
osx-next-cli apply --execute --vmid 911 --name macos-test --macos sonoma ...
```

Each VM needs its own CPU, RAM, and disk allocation. Don't overcommit host resources.

</details>

<details>
<summary><strong>Can I use GPU passthrough?</strong></summary>

Yes, but host-side setup is manual and must be done before the VM can use a discrete GPU:

1. Enable **VT-d / IOMMU** in BIOS/UEFI
2. Add to kernel cmdline:
   - Intel: `intel_iommu=on iommu=pt`
   - AMD: `amd_iommu=on iommu=pt`
3. Bind GPU + GPU audio to `vfio-pci`
4. Reboot host
5. Attach both PCI functions to VM (`hostpci0`, `hostpci1`)

Reference: [Proxmox PCI(e) Passthrough Wiki](https://pve.proxmox.com/wiki/PCI(e)_Passthrough)

</details>

<details>
<summary><strong>Can I run macOS on AMD CPUs?</strong></summary>

Yes. AMD CPUs are fully supported. The tool auto-detects your CPU vendor and applies `Cascadelake-Server` emulation for AMD hosts. This is required for macOS compatibility but adds overhead compared to Intel's native host passthrough.

No manual configuration is needed.

</details>

<details>
<summary><strong>Can I use iCloud, iMessage, and FaceTime?</strong></summary>

It depends on the macOS version:

| macOS | Apple Services |
|-------|---------------|
| Ventura 13 | Works |
| Sonoma 14 | Works |
| Sequoia 15 | Works (with `--apple-services`) |
| Tahoe 26 | Works (with `--apple-services`) |

Use the `--apple-services` flag when creating the VM. It auto-generates the required SMBIOS identity, static MAC, and OpenCore PlatformInfo, and injects a kernel-level patch that prevents Apple's DeviceCheck from detecting the VM. This enables full Apple ID, iCloud, iMessage, and FaceTime sign-in on all supported macOS versions.

See the [Apple Services guide](./apple-services.md) for details.

</details>

<details>
<summary><strong>Which macOS version should I choose?</strong></summary>

| Use Case | Version |
|----------|---------|
| Best stability | **Sonoma 14** |
| Lightweight, older hardware | **Ventura 13** |
| Latest features | **Sequoia 15** or **Tahoe 26** |
| Full Apple Services on latest macOS | **Sequoia 15** or **Tahoe 26** with `--apple-services` |

Sonoma 14 is the best-tested and most reliable option. Sequoia 15 and Tahoe 26 require `--apple-services` for Apple ID sign-in but are otherwise fully functional.

</details>

<details>
<summary><strong>Can I upgrade macOS inside the VM?</strong></summary>

Yes. In-place upgrades via System Settings > Software Update work. Snapshot your VM before upgrading to preserve a rollback point.

Snapshot your VM before upgrading so you can roll back if something goes wrong.

</details>

<details>
<summary><strong>Do I need a real Mac to use this?</strong></summary>

No. The tool runs entirely on Proxmox. It downloads macOS recovery images directly from Apple's servers using the osrecovery API. No existing Mac or macOS installation is required.

</details>

<details>
<summary><strong>Can I use this on Proxmox 8?</strong></summary>

The tool is built and tested for **Proxmox VE 9**. Proxmox 8 is not officially supported and may have differences in `qm` command behavior or storage APIs. Upgrade to Proxmox 9 for the best experience.

</details>

<details>
<summary><strong>How much disk space do I need?</strong></summary>

| Component | Size |
|-----------|------|
| Recovery image | ~0.9 GB |
| OpenCore ISO | ~30 MB |
| VM disk (minimum) | 64 GB |
| VM disk (recommended) | 128+ GB |

You need at least 64 GB free on your storage target for the VM disk, plus space for the recovery and OpenCore images. Use SSD or NVMe-backed storage for best performance.

</details>

<details>
<summary><strong>Can I use NVMe/SSD passthrough?</strong></summary>

Yes, via PCI passthrough. Pass the NVMe controller to the VM the same way you would a GPU -- bind it to `vfio-pci` and attach it via `hostpci`. The VM disk layout uses `virtio0` by default, but a passed-through NVMe drive gives native performance.

Note: if you pass through your only NVMe drive, Proxmox itself needs to be on a different disk.

</details>

<details>
<summary><strong>Is this legal?</strong></summary>

This project is for **testing, lab use, and learning**. Apple's macOS EULA permits virtualization only on Apple hardware. Running macOS VMs on non-Apple hardware is a gray area that varies by jurisdiction. You are responsible for legal and compliance use in your region.

</details>

<details>
<summary><strong>Can I use this in production?</strong></summary>

Not recommended. This is designed for labs, testing, and development. Apple's licensing restricts macOS virtualization, and VM performance (especially on AMD) has overhead compared to bare metal. For production macOS workloads, use Apple hardware.

</details>

<details>
<summary><strong>How do I back up my macOS VM?</strong></summary>

Use Proxmox's built-in backup tools:

```bash
# Snapshot (instant, no downtime)
qm snapshot <vmid> <snapshot-name>

# Full backup to storage
vzdump <vmid> --storage <backup-storage> --mode snapshot
```

Snapshot before any major changes (macOS upgrades, profile changes, kext updates). Keep the matching revert scripts ready if you applied a performance profile.

</details>

<details>
<summary><strong>Can I resize the disk after creation?</strong></summary>

Yes. Use the `edit` subcommand to extend the disk without manual `qm` commands:

```bash
osx-next-cli edit --vmid 910 --add-disk 50 --execute
```

Or use Proxmox directly (grow only, not shrink):

```bash
qm resize <vmid> virtio0 +50G
```

After resizing, boot macOS and use Disk Utility to expand the APFS container to fill the new space. Snapshot the VM before resizing.

</details>
