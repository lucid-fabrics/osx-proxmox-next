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

Enter the BIOS with **Del**. Press F7 to switch between EZ mode and Advanced mode. F11 opens the boot menu.

| Setting | Where to look | Set it to | Checked against a source |
|---------|---------------|-----------|--------------------------|
| Intel virtualization (VT-x) | `OC > CPU Features > Intel Virtualization Tech` | Enabled | yes ([source](https://download.msi.com/archive/mnu_exe/mb/Intel700BIOS.pdf)) |
| AMD virtualization (SVM) | `OC > Advanced CPU Configuration > SVM Mode` | Enabled | yes ([source](https://download-2.msi.com/archive/mnu_exe/mb/AMDAM5BIOS.pdf)) |
| Intel VT-d | `OC > CPU Features > Intel VT-D Tech` | Enabled | yes ([source](https://download.msi.com/archive/mnu_exe/mb/Intel700BIOS.pdf)) |
| AMD IOMMU | `OC > AMD CBS > IOMMU` | Enabled | yes ([source](https://download-2.msi.com/archive/mnu_exe/mb/AMDAM5BIOS.pdf)) |
| Above 4G Decoding | `Settings > Advanced > PCIe/PCI Sub-system Settings > Above 4G memory/ Crypto Currency mining` | Enabled | yes ([source 1](https://download-2.msi.com/archive/mnu_exe/mb/AMDAM5BIOS.pdf), [source 2](https://download.msi.com/archive/mnu_exe/mb/Intel700BIOS.pdf)) |
| CSM (legacy boot support) | `Settings > Advanced > BIOS CSM/UEFI Mode (some guides print it as BIOS UEFI/ CSM Mode)` | UEFI | yes ([source 1](https://download-2.msi.com/archive/mnu_exe/mb/AMDAM5BIOS.pdf), [source 2](https://download.msi.com/archive/mnu_exe/mb/Intel700BIOS.pdf)) |
| Primary display | `Settings > Advanced > Integrated Graphics Configuration > Initiate Graphic Adapter` | IGD (the onboard graphics) | yes ([source 1](https://download-2.msi.com/archive/mnu_exe/mb/AMDAM5BIOS.pdf), [source 2](https://download.msi.com/archive/mnu_exe/mb/Intel700BIOS.pdf)) |
| Resizable BAR (optional) | `Settings > Advanced > PCIe/PCI Sub-system Settings > Re-Size BAR Support` | Disabled if the VM will not start | yes ([source 1](https://download-2.msi.com/archive/mnu_exe/mb/AMDAM5BIOS.pdf), [source 2](https://download.msi.com/archive/mnu_exe/mb/Intel700BIOS.pdf)) |

## Official manual

The vendor's manuals and support pages: [https://www.msi.com/support](https://www.msi.com/support).

Manuals the verified paths above were checked against:

- [https://download.msi.com/archive/mnu_exe/mb/Intel700BIOS.pdf](https://download.msi.com/archive/mnu_exe/mb/Intel700BIOS.pdf)
- [https://download-2.msi.com/archive/mnu_exe/mb/AMDAM5BIOS.pdf](https://download-2.msi.com/archive/mnu_exe/mb/AMDAM5BIOS.pdf)

## Back to the walkthrough

After you save and exit (usually F10), run `osx-next-cli gpu-setup` again. It re-checks every item and shows a tick for the ones that are now on. See the [full GPU passthrough guide](../../gpu-passthrough.md).
