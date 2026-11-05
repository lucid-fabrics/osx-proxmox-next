---
sidebar_position: 9
title: HPE ProLiant BIOS settings for GPU passthrough
sidebar_label: HPE ProLiant
---

# HPE ProLiant: BIOS settings for GPU passthrough

These are the settings the GPU walkthrough asks you to change in the BIOS. The walkthrough checks your host first and only shows the ones that are really missing, so you may need just one or two of them.

Menu names change between boards and BIOS versions. Entries marked "usually under" come from vendor manuals and model lines and have **not** been checked against your exact board. If you cannot find a setting, use the BIOS search (the footer of the screen shows the key, often F3, F7 or F9) and type the setting name from the first column.

Do the settings in this order: CSM off, then Above 4G Decoding, then Resizable BAR if you want it. Virtualization and IOMMU are independent.

## HPE ProLiant

Enter the BIOS with **F9 (System Utilities)**. iLO remote console: Server > One-Button Boot > System Utilities.

| Setting | Where to look | Set it to | Checked against a source |
|---------|---------------|-----------|--------------------------|
| Intel virtualization (VT-x) | usually under `System Configuration > BIOS/Platform Configuration (RBSU) > Processor Options > Virtualization Technology` | Enabled | no |
| AMD virtualization (SVM) | usually under `RBSU > Processor Options > AMD Virtualization Technology (SVM)` | Enabled | no |
| Intel VT-d | usually under `RBSU > Processor Options > Intel(R) VT-d` | Enabled | no |
| AMD IOMMU | usually under `RBSU > Processor Options > AMD I/O Virtualization Technology (IOMMU)` | Enabled | no |
| Above 4G Decoding | usually under `RBSU > Advanced Options > Memory Mapped I/O Above 4GB (also called PCI 64-bit Resource Handling above 4GB; the name differs per generation)` | Enabled | no |
| CSM (legacy boot support) | usually under `RBSU > Boot Options > Boot Mode` | UEFI Mode | no |

No path is recorded for: Primary display, Resizable BAR (optional). See the generic names at the bottom of this page.

## Official manual

The vendor's manuals and support pages: [https://www.hpe.com/support](https://www.hpe.com/support).

## Back to the walkthrough

After you save and exit (usually F10), run `osx-next-cli gpu-setup` again. It re-checks every item and shows a tick for the ones that are now on. See the [full GPU passthrough guide](../../gpu-passthrough.md).
