---
sidebar_position: 12
title: Minisforum / Beelink and other mini PCs BIOS settings for GPU passthrough
sidebar_label: Minisforum / Beelink and other mini PCs
---

# Minisforum / Beelink and other mini PCs: BIOS settings for GPU passthrough

These are the settings the GPU walkthrough asks you to change in the BIOS. The walkthrough checks your host first and only shows the ones that are really missing, so you may need just one or two of them.

Menu names change between boards and BIOS versions. Entries marked "usually under" come from vendor manuals and model lines and have **not** been checked against your exact board. If you cannot find a setting, use the BIOS search (the footer of the screen shows the key, often F3, F7 or F9) and type the setting name from the first column.

Do the settings in this order: CSM off, then Above 4G Decoding, then Resizable BAR if you want it. Virtualization and IOMMU are independent.

## Minisforum / Beelink and other mini PCs

Enter the BIOS with **Del or F2**. F7 opens the boot menu on many models. Mini PCs often hide advanced menus.

| Setting | Where to look | Set it to | Checked against a source |
|---------|---------------|-----------|--------------------------|
| Intel virtualization (VT-x) | usually under `Advanced > CPU Configuration > Intel Virtualization Technology` | Enabled | no |
| AMD virtualization (SVM) | usually under `Advanced > CPU Configuration > SVM Mode` | Enabled | no |
| Intel VT-d | usually under `Advanced > System Agent (SA) Configuration > VT-d` | Enabled | no |
| AMD IOMMU | usually under `Advanced > AMD CBS > NBIO Common Options > IOMMU (some models: Advanced > IOMMU)` | Enabled | no |
| Above 4G Decoding | usually under `Advanced > PCI Subsystem Settings > Above 4G Decoding (often hidden)` | Enabled | no |
| CSM (legacy boot support) | usually under `Boot > CSM Support` | Disabled | no |

Not checked against a manual:

- Intel virtualization (VT-x): Not verified: Minisforum and Beelink publish no BIOS menu documentation. Forum posts suggest Advanced > CPU Configuration > SVM Mode and Advanced > AMD CBS > NBIO Common Options > IOMMU on AMD models, but forums are not accepted sources.
- AMD virtualization (SVM): Not verified: Minisforum and Beelink publish no BIOS menu documentation. Forum posts suggest Advanced > CPU Configuration > SVM Mode and Advanced > AMD CBS > NBIO Common Options > IOMMU on AMD models, but forums are not accepted sources.
- Intel VT-d: Not verified: Minisforum and Beelink publish no BIOS menu documentation. Forum posts suggest Advanced > CPU Configuration > SVM Mode and Advanced > AMD CBS > NBIO Common Options > IOMMU on AMD models, but forums are not accepted sources.
- AMD IOMMU: Not verified: Minisforum and Beelink publish no BIOS menu documentation. Forum posts suggest Advanced > CPU Configuration > SVM Mode and Advanced > AMD CBS > NBIO Common Options > IOMMU on AMD models, but forums are not accepted sources.
- Above 4G Decoding: Not verified: Minisforum and Beelink publish no BIOS menu documentation. Forum posts suggest Advanced > CPU Configuration > SVM Mode and Advanced > AMD CBS > NBIO Common Options > IOMMU on AMD models, but forums are not accepted sources.
- CSM (legacy boot support): Not verified: Minisforum and Beelink publish no BIOS menu documentation. Forum posts suggest Advanced > CPU Configuration > SVM Mode and Advanced > AMD CBS > NBIO Common Options > IOMMU on AMD models, but forums are not accepted sources.

No path is recorded for: Primary display, Resizable BAR (optional). See the generic names on the [generic page](generic.md).

## Official manual

This vendor publishes no BIOS manual for these machines. The paths above follow the standard AMI Aptio menu tree and user reports.

## Back to the walkthrough

After you save and exit (usually F10), run `osx-next-cli gpu-setup` again. It re-checks every item and shows a tick for the ones that are now on. See the [full GPU passthrough guide](../../gpu-passthrough.md).
