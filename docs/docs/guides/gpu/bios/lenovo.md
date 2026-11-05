---
sidebar_position: 10
title: Lenovo BIOS settings for GPU passthrough
sidebar_label: Lenovo
---

# Lenovo: BIOS settings for GPU passthrough

These are the settings the GPU walkthrough asks you to change in the BIOS. The walkthrough checks your host first and only shows the ones that are really missing, so you may need just one or two of them.

Menu names change between boards and BIOS versions. Entries marked "usually under" come from vendor manuals and model lines and have **not** been checked against your exact board. If you cannot find a setting, use the BIOS search (the footer of the screen shows the key, often F3, F7 or F9) and type the setting name from the first column.

Do the settings in this order: CSM off, then Above 4G Decoding, then Resizable BAR if you want it. Virtualization and IOMMU are independent.

## Lenovo ThinkCentre / ThinkStation

Enter the BIOS with **F1 (some models: Enter, then F1)**. F12 opens the boot menu.

| Setting | Where to look | Set it to | Checked against a source |
|---------|---------------|-----------|--------------------------|
| Intel virtualization (VT-x) | usually under `Security > Virtualization > Intel Virtualization Technology` | Enabled | no |
| AMD virtualization (SVM) | usually under `Security > Virtualization > AMD-V (SVM)` | Enabled | no |
| Intel VT-d | usually under `Security > Virtualization > Intel VT-d Feature` | Enabled | no |
| AMD IOMMU | usually under `Advanced > AMD CBS > NBIO > IOMMU (ThinkStation P620)` | Enabled | no |
| Above 4G Decoding | usually under `Advanced > PCI Configuration > Above 4G Decoding (ThinkStation; ThinkCentre often has none)` | Enabled | no |
| CSM (legacy boot support) | usually under `Startup > CSM Support and Startup > Boot Mode` | Disabled / UEFI Only | no |
| Primary display | usually under `Devices > Video Setup > Select Active Video` | Integrated Graphics | no |

No path is recorded for: Resizable BAR (optional). See the generic names at the bottom of this page.

## Lenovo ThinkSystem

Enter the BIOS with **F1**. F12 opens the boot menu.

| Setting | Where to look | Set it to | Checked against a source |
|---------|---------------|-----------|--------------------------|
| Intel virtualization (VT-x) | usually under `System Settings > Processors > Intel Virtualization Technology` | Enabled | no |
| AMD virtualization (SVM) | usually under `System Settings > Processors > SVM Mode` | Enabled | no |
| Intel VT-d | usually under `System Settings > Processors > VT-d` | Enabled | no |
| AMD IOMMU | usually under `System Settings > Devices and I/O Ports > IOMMU` | Enabled | no |
| Above 4G Decoding | usually under `System Settings > Devices and I/O Ports > MM Config (Memory Mapped I/O above 4GB)` | Enabled | no |
| CSM (legacy boot support) | usually under `Boot Manager > Boot Modes > System Boot Mode` | UEFI Mode | no |

No path is recorded for: Primary display, Resizable BAR (optional). See the generic names at the bottom of this page.

## Official manual

The vendor's manuals and support pages: [https://pubs.lenovo.com](https://pubs.lenovo.com).

## Back to the walkthrough

After you save and exit (usually F10), run `osx-next-cli gpu-setup` again. It re-checks every item and shows a tick for the ones that are now on. See the [full GPU passthrough guide](../../gpu-passthrough.md).
