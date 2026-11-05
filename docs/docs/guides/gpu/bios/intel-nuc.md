---
sidebar_position: 11
title: Intel NUC BIOS settings for GPU passthrough
sidebar_label: Intel NUC
---

# Intel NUC: BIOS settings for GPU passthrough

These are the settings the GPU walkthrough asks you to change in the BIOS. The walkthrough checks your host first and only shows the ones that are really missing, so you may need just one or two of them.

Menu names change between boards and BIOS versions. Entries marked "usually under" come from vendor manuals and model lines and have **not** been checked against your exact board. If you cannot find a setting, use the BIOS search (the footer of the screen shows the key, often F3, F7 or F9) and type the setting name from the first column.

Do the settings in this order: CSM off, then Above 4G Decoding, then Resizable BAR if you want it. Virtualization and IOMMU are independent.

## Intel NUC

Enter the BIOS with **F2**. F10 opens the boot menu. Newer NUCs (13 and later) are made by ASUS and use the ASUS layout.

| Setting | Where to look | Set it to | Checked against a source |
|---------|---------------|-----------|--------------------------|
| Intel virtualization (VT-x) | usually under `Advanced > Security > Intel Virtualization Technology` | Enabled | no |
| Intel VT-d | usually under `Advanced > Security > Intel VT-d` | Enabled | no |
| Above 4G Decoding | usually under `Usually not offered. NUC 12/13 Extreme: Advanced > Devices > PCI > Above 4G Decoding` | Enabled | no |
| CSM (legacy boot support) | usually under `Advanced > Boot > Boot Configuration > UEFI Boot` | Enabled, legacy boot off | no |

No path is recorded for: AMD virtualization (SVM), AMD IOMMU, Primary display, Resizable BAR (optional). See the generic names at the bottom of this page.

## Official manual

The vendor's manuals and support pages: [https://www.intel.com/content/www/us/en/support/products/](https://www.intel.com/content/www/us/en/support/products/).

## Back to the walkthrough

After you save and exit (usually F10), run `osx-next-cli gpu-setup` again. It re-checks every item and shows a tick for the ones that are now on. See the [full GPU passthrough guide](../../gpu-passthrough.md).
