---
sidebar_position: 3
title: Gigabyte / Aorus BIOS settings for GPU passthrough
sidebar_label: Gigabyte / Aorus
---

# Gigabyte / Aorus: BIOS settings for GPU passthrough

These are the settings the GPU walkthrough asks you to change in the BIOS. The walkthrough checks your host first and only shows the ones that are really missing, so you may need just one or two of them.

Menu names change between boards and BIOS versions. Entries marked "usually under" come from vendor manuals and model lines and have **not** been checked against your exact board. If you cannot find a setting, use the BIOS search (the footer of the screen shows the key, often F3, F7 or F9) and type the setting name from the first column.

Do the settings in this order: CSM off, then Above 4G Decoding, then Resizable BAR if you want it. Virtualization and IOMMU are independent.

## Gigabyte / Aorus

Enter the BIOS with **Del**. Press F2 to switch between Easy Mode and Advanced Mode.

| Setting | Where to look | Set it to | Checked against a source |
|---------|---------------|-----------|--------------------------|
| Intel virtualization (VT-x) | `Tweaker > Advanced CPU Settings > Intel (VMX) Virtualization Technology` | Enabled | yes ([source](https://download.gigabyte.com/FileList/Manual/mb_manual_Z790-AORUS-XTREME-X-ICE_BIOS_e.pdf)) |
| AMD virtualization (SVM) | `Tweaker > Advanced CPU Settings > SVM Mode` | Enabled | yes ([source](https://download.gigabyte.com/FileList/Manual/mb_manual_b550-aorus-elite-ax_e.pdf)) |
| Intel VT-d | `Settings > Miscellaneous > VT-d` | Enabled | yes ([source](https://download.gigabyte.com/FileList/Manual/mb_manual_Z790-AORUS-XTREME-X-ICE_BIOS_e.pdf)) |
| AMD IOMMU | `Settings > Miscellaneous > IOMMU` | Enabled | yes ([source](https://download.gigabyte.com/FileList/Manual/mb_manual_b550-aorus-elite-ax_e.pdf)) |
| Above 4G Decoding | `Settings > IO Ports > Above 4G Decoding` | Enabled | yes ([source 1](https://download.gigabyte.com/FileList/Manual/mb_manual_Z790-AORUS-XTREME-X-ICE_BIOS_e.pdf), [source 2](https://download.gigabyte.com/FileList/Manual/mb_manual_b550-aorus-elite-ax_e.pdf)) |
| CSM (legacy boot support) | `Boot > CSM Support` | Disabled | yes ([source 1](https://download.gigabyte.com/FileList/Manual/mb_manual_Z790-AORUS-XTREME-X-ICE_BIOS_e.pdf), [source 2](https://download.gigabyte.com/FileList/Manual/mb_manual_b550-aorus-elite-ax_e.pdf)) |
| Primary display | `Settings > IO Ports > Initial Display Output` | IGFX (the onboard graphics) | yes ([source 1](https://download.gigabyte.com/FileList/Manual/mb_manual_Z790-AORUS-XTREME-X-ICE_BIOS_e.pdf), [source 2](https://download.gigabyte.com/FileList/Manual/mb_manual_b550-aorus-elite-ax_e.pdf)) |
| Resizable BAR (optional) | `Settings > IO Ports > Re-Size BAR Support` | Disabled if the VM will not start | yes ([source](https://download.gigabyte.com/FileList/Manual/mb_manual_Z790-AORUS-XTREME-X-ICE_BIOS_e.pdf)) |

## Official manual

The vendor's manuals and support pages: [https://www.gigabyte.com/Support](https://www.gigabyte.com/Support).

Manuals the verified paths above were checked against:

- [https://download.gigabyte.com/FileList/Manual/mb_manual_Z790-AORUS-XTREME-X-ICE_BIOS_e.pdf](https://download.gigabyte.com/FileList/Manual/mb_manual_Z790-AORUS-XTREME-X-ICE_BIOS_e.pdf)
- [https://download.gigabyte.com/FileList/Manual/mb_manual_b550-aorus-elite-ax_e.pdf](https://download.gigabyte.com/FileList/Manual/mb_manual_b550-aorus-elite-ax_e.pdf)

## Back to the walkthrough

After you save and exit (usually F10), run `osx-next-cli gpu-setup` again. It re-checks every item and shows a tick for the ones that are now on. See the [full GPU passthrough guide](../../gpu-passthrough.md).
