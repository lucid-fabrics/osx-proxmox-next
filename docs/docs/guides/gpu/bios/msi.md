---
sidebar_position: 2
title: MSI BIOS settings for GPU passthrough
sidebar_label: MSI
---

# MSI: BIOS settings for GPU passthrough

These are the settings the GPU walkthrough asks you to change in the BIOS. The walkthrough checks your host first and only shows the ones that are really missing, so you may need just one or two of them.

Menu names change between boards and BIOS versions. Entries marked "usually under" come from vendor manuals and model lines and have **not** been checked against your exact board. If you cannot find a setting, use the BIOS search (the footer of the screen shows the key, often F3, F7 or F9) and type the setting name from the first column.

Do the settings in this order: CSM off, then Above 4G Decoding, then Resizable BAR if you want it. Virtualization and IOMMU are independent.

## MSI

Enter the BIOS with **Del**. Press F7 to switch between EZ and Advanced mode.

| Setting | Where to look | Set it to | Checked against a source |
|---------|---------------|-----------|--------------------------|
| Intel virtualization (VT-x) | usually under `Settings > Advanced > CPU Features > Intel Virtualization Tech` | Enabled | no |
| AMD virtualization (SVM) | usually under `Settings > Advanced > CPU Features > SVM Mode` | Enabled | no |
| Intel VT-d | usually under `Settings > Advanced > Northbridge or System Agent > VT-d (search for VT-d)` | Enabled | no |
| AMD IOMMU | usually under `Settings > Advanced > AMD CBS > NBIO Common Options > IOMMU (many AM5 boards: Settings > Advanced > IOMMU Configuration > IOMMU)` | Enabled | no |
| Above 4G Decoding | usually under `Settings > Advanced > PCI Subsystem Settings > Above 4G memory / Crypto Currency mining` | Enabled | no |
| CSM (legacy boot support) | usually under `Settings > Boot > CSM / UEFI-only (Click BIOS 5: Boot > BIOS CSM/UEFI Mode)` | UEFI | no |
| Primary display | usually under `Settings > Advanced > Integrated Graphics Configuration > Initial Graphics Adapter` | IGD (the onboard graphics) | no |
| Resizable BAR (optional) | usually under `Settings > Advanced > PCI Subsystem Settings > Re-Size BAR Support` | Disabled if the VM will not start | no |

## Official manual

The vendor's manuals and support pages: [https://www.msi.com/support](https://www.msi.com/support).

## Back to the walkthrough

After you save and exit (usually F10), run `osx-next-cli gpu-setup` again. It re-checks every item and shows a tick for the ones that are now on. See the [full GPU passthrough guide](../../gpu-passthrough.md).
