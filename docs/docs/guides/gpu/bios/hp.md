---
sidebar_position: 8
title: HP workstations and desktops BIOS settings for GPU passthrough
sidebar_label: HP workstations and desktops
---

# HP workstations and desktops: BIOS settings for GPU passthrough

These are the settings the GPU walkthrough asks you to change in the BIOS. The walkthrough checks your host first and only shows the ones that are really missing, so you may need just one or two of them.

Menu names change between boards and BIOS versions. Entries marked "usually under" come from vendor manuals and model lines and have **not** been checked against your exact board. If you cannot find a setting, use the BIOS search (the footer of the screen shows the key, often F3, F7 or F9) and type the setting name from the first column.

Do the settings in this order: CSM off, then Above 4G Decoding, then Resizable BAR if you want it. Virtualization and IOMMU are independent.

## HP workstations and desktops

Enter the BIOS with **Esc, then F10**. F9 opens the boot menu.

| Setting | Where to look | Set it to | Checked against a source |
|---------|---------------|-----------|--------------------------|
| Intel virtualization (VT-x) | usually under `Advanced > System Options > Virtualization Technology (VTx)` | Enabled | no |
| AMD virtualization (SVM) | usually under `Advanced > System Options > SVM CPU Virtualization` | Enabled | no |
| Intel VT-d | usually under `Advanced > System Options > Virtualization Technology for Directed I/O (VTd)` | Enabled | no |
| AMD IOMMU | usually under `Advanced > System Options > IOMMU (name varies)` | Enabled | no |
| Above 4G Decoding | usually under `Advanced > Device Options > Above 4G MMIO (name varies)` | Enabled | no |
| CSM (legacy boot support) | usually under `Advanced > Boot Options > Legacy Support` | Disabled | no |
| Primary display | usually under `Advanced > Built-in Device Options > Video: Integrated Graphics` | Integrated | no |

No path is recorded for: Resizable BAR (optional). See the generic names at the bottom of this page.

## Official manual

The vendor's manuals and support pages: [https://support.hp.com](https://support.hp.com).

## Back to the walkthrough

After you save and exit (usually F10), run `osx-next-cli gpu-setup` again. It re-checks every item and shows a tick for the ones that are now on. See the [full GPU passthrough guide](../../gpu-passthrough.md).
