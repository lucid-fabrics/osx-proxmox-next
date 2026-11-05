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
| Intel virtualization (VT-x) | `Security > Security Features > Intel Virtualization Technology` | Enabled | yes ([source](https://www.intel.com/content/dam/support/us/en/documents/intel-nuc/NUC-AptioV-BIOS-Glossary.pdf)) |
| Intel VT-d | `Security > Security Features > Intel VT for Directed I/O (VT-d)` | Enabled | yes ([source](https://www.intel.com/content/dam/support/us/en/documents/intel-nuc/NUC-AptioV-BIOS-Glossary.pdf)) |
| Above 4G Decoding | usually under `Usually not offered. NUC 12/13 Extreme: Advanced > Devices > PCI > Above 4G Decoding` | Enabled | no |
| CSM (legacy boot support) | `Boot > Boot Priority > UEFI Boot (enabled) and Legacy Boot (disabled)` | UEFI Boot on, Legacy Boot off | yes ([source](https://www.intel.com/content/dam/support/us/en/documents/intel-nuc/NUC-AptioV-BIOS-Glossary.pdf)) |

Not checked against a manual:

- Above 4G Decoding: Not verified: the Aptio V glossary has no Above 4G item.

No path is recorded for: AMD virtualization (SVM), AMD IOMMU, Primary display, Resizable BAR (optional). See the generic names on the [generic page](generic.md).

## Official manual

The vendor's manuals and support pages: [https://www.intel.com/content/www/us/en/support/products/](https://www.intel.com/content/www/us/en/support/products/).

Manuals the verified paths above were checked against:

- [https://www.intel.com/content/dam/support/us/en/documents/intel-nuc/NUC-AptioV-BIOS-Glossary.pdf](https://www.intel.com/content/dam/support/us/en/documents/intel-nuc/NUC-AptioV-BIOS-Glossary.pdf)

## Back to the walkthrough

After you save and exit (usually F10), run `osx-next-cli gpu-setup` again. It re-checks every item and shows a tick for the ones that are now on. See the [full GPU passthrough guide](../../gpu-passthrough.md).
