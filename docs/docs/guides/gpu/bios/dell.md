---
sidebar_position: 7
title: Dell BIOS settings for GPU passthrough
sidebar_label: Dell
---

# Dell: BIOS settings for GPU passthrough

These are the settings the GPU walkthrough asks you to change in the BIOS. The walkthrough checks your host first and only shows the ones that are really missing, so you may need just one or two of them.

Menu names change between boards and BIOS versions. Entries marked "usually under" come from vendor manuals and model lines and have **not** been checked against your exact board. If you cannot find a setting, use the BIOS search (the footer of the screen shows the key, often F3, F7 or F9) and type the setting name from the first column.

Do the settings in this order: CSM off, then Above 4G Decoding, then Resizable BAR if you want it. Virtualization and IOMMU are independent.

## Dell OptiPlex / Precision

Enter the BIOS with **F2 at the Dell logo**. F12 opens the boot menu.

| Setting | Where to look | Set it to | Checked against a source |
|---------|---------------|-----------|--------------------------|
| Intel virtualization (VT-x) | `Virtualization Support > Virtualization` | Enabled | yes ([source](https://www.dell.com/support/kbdoc/en-us/000195978/how-to-enable-or-disable-hardware-virtualization-on-dell-systems)) |
| AMD virtualization (SVM) | usually under `Virtualization Support > SVM Mode` | Enabled | no |
| Intel VT-d | `Virtualization Support > VT for Direct I/O` | Enabled | yes ([source](https://www.dell.com/support/kbdoc/en-us/000195978/how-to-enable-or-disable-hardware-virtualization-on-dell-systems)) |
| AMD IOMMU | usually under `Virtualization Support > IOMMU` | Enabled | no |
| Above 4G Decoding | usually under `Settings > System Configuration > Above 4G Decoding (many Dell desktops have no such switch)` | Enabled | no |
| CSM (legacy boot support) | usually under `Settings > General > Boot Sequence > Boot List Option, and Advanced Boot Options > Enable Legacy Option ROMs` | UEFI, legacy option ROMs off | no |
| Primary display | usually under `Settings > Video > Primary Display` | Intel HD Graphics / Auto | no |
| Resizable BAR (optional) | usually under `Settings > System Configuration > Re-Size BAR Support` | Disabled if the VM will not start | no |

## Dell PowerEdge

Enter the BIOS with **F2 (System Setup)**. iDRAC: Next Boot > BIOS Setup.

| Setting | Where to look | Set it to | Checked against a source |
|---------|---------------|-----------|--------------------------|
| Intel virtualization (VT-x) | usually under `System BIOS > Processor Settings > Virtualization Technology` | Enabled | no |
| AMD virtualization (SVM) | usually under `System BIOS > Processor Settings > Virtualization Technology (SVM)` | Enabled | no |
| Intel VT-d | usually under `System BIOS > Processor Settings > Virtualization Technology (VT-d is on by default on 14G and newer)` | Enabled | no |
| AMD IOMMU | usually under `System BIOS > Processor Settings > IOMMU Support` | Enabled | no |
| Above 4G Decoding | usually under `System BIOS > Integrated Devices > Memory Mapped I/O Above 4GB` | Enabled | no |
| CSM (legacy boot support) | usually under `System BIOS > Boot Settings > Boot Mode` | UEFI | no |

No path is recorded for: Primary display, Resizable BAR (optional). See the generic names at the bottom of this page.

## Official manual

The vendor's manuals and support pages: [https://www.dell.com/support](https://www.dell.com/support).

## Back to the walkthrough

After you save and exit (usually F10), run `osx-next-cli gpu-setup` again. It re-checks every item and shows a tick for the ones that are now on. See the [full GPU passthrough guide](../../gpu-passthrough.md).
