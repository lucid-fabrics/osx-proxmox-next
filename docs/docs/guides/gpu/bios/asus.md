---
sidebar_position: 1
title: ASUS BIOS settings for GPU passthrough
sidebar_label: ASUS
---

# ASUS: BIOS settings for GPU passthrough

These are the settings the GPU walkthrough asks you to change in the BIOS. The walkthrough checks your host first and only shows the ones that are really missing, so you may need just one or two of them.

Menu names change between boards and BIOS versions. Entries marked "usually under" come from vendor manuals and model lines and have **not** been checked against your exact board. If you cannot find a setting, use the BIOS search (the footer of the screen shows the key, often F3, F7 or F9) and type the setting name from the first column.

Do the settings in this order: CSM off, then Above 4G Decoding, then Resizable BAR if you want it. Virtualization and IOMMU are independent.

## ASUS

Enter the BIOS with **Del or F2**. Press F7 to switch between EZ Mode and Advanced Mode.

| Setting | Where to look | Set it to | Checked against a source |
|---------|---------------|-----------|--------------------------|
| Intel virtualization (VT-x) | `Advanced > CPU Configuration > Intel (VMX) Virtualization Technology` | Enabled | yes ([source](https://dlcdnets.asus.com/pub/ASUS/mb/LGA1700/ROG_STRIX_Z790-E_GAMING_WIFI/E23863_ROG_STRIX_Z790_Series_BIOS_Manual_Intel_14th_EM_V2_WEB.pdf)) |
| AMD virtualization (SVM) | `Advanced > CPU Configuration > SVM Mode` | Enabled | yes ([source](https://dlcdnets.asus.com/pub/ASUS/mb/Socket%20AM5/ROG_STRIX_X670E-E_GAMING_WIFI/E20544_ROG_STRIX_X670E_Series_BIOS_manual_EM_WEB.pdf)) |
| Intel VT-d | `Advanced > System Agent (SA) Configuration > VT-d` | Enabled | yes ([source](https://dlcdnets.asus.com/pub/ASUS/mb/LGA1700/ROG_STRIX_Z790-E_GAMING_WIFI/E23863_ROG_STRIX_Z790_Series_BIOS_Manual_Intel_14th_EM_V2_WEB.pdf)) |
| AMD IOMMU | `Advanced > AMD CBS > IOMMU (if it is not listed there, look inside AMD CBS > NBIO Common Options)` | Enabled | yes ([source](https://dlcdnets.asus.com/pub/ASUS/mb/Socket%20AM5/ROG_STRIX_X670E-E_GAMING_WIFI/E20544_ROG_STRIX_X670E_Series_BIOS_manual_EM_WEB.pdf)) |
| Above 4G Decoding | `Advanced > PCI Subsystem Settings > Above 4G Decoding` | Enabled | yes ([source 1](https://dlcdnets.asus.com/pub/ASUS/mb/LGA1700/ROG_STRIX_Z790-E_GAMING_WIFI/E23863_ROG_STRIX_Z790_Series_BIOS_Manual_Intel_14th_EM_V2_WEB.pdf), [source 2](https://dlcdnets.asus.com/pub/ASUS/mb/Socket%20AM5/ROG_STRIX_X670E-E_GAMING_WIFI/E20544_ROG_STRIX_X670E_Series_BIOS_manual_EM_WEB.pdf)) |
| CSM (legacy boot support) | `Boot > CSM (Compatibility Support Module) > Launch CSM` | Disabled | yes ([source](https://dlcdnets.asus.com/pub/ASUS/mb/LGA1700/ROG_STRIX_Z790-E_GAMING_WIFI/E23863_ROG_STRIX_Z790_Series_BIOS_Manual_Intel_14th_EM_V2_WEB.pdf)) |
| Primary display | `Advanced > System Agent (SA) Configuration > Graphics Configuration > Primary Display (AMD boards: Advanced > NB Configuration > Primary Video Device)` | CPU Graphics (AMD: IGFX Video) | yes ([source 1](https://dlcdnets.asus.com/pub/ASUS/mb/LGA1700/ROG_STRIX_Z790-E_GAMING_WIFI/E23863_ROG_STRIX_Z790_Series_BIOS_Manual_Intel_14th_EM_V2_WEB.pdf), [source 2](https://dlcdnets.asus.com/pub/ASUS/mb/Socket%20AM5/ROG_STRIX_X670E-E_GAMING_WIFI/E20544_ROG_STRIX_X670E_Series_BIOS_manual_EM_WEB.pdf)) |
| Resizable BAR (optional) | `Advanced > PCI Subsystem Settings > Re-Size BAR Support (appears only after Above 4G Decoding is Enabled)` | Disabled if the VM will not start | yes ([source](https://dlcdnets.asus.com/pub/ASUS/mb/LGA1700/ROG_STRIX_Z790-E_GAMING_WIFI/E23863_ROG_STRIX_Z790_Series_BIOS_Manual_Intel_14th_EM_V2_WEB.pdf)) |

## Official manual

The vendor's manuals and support pages: [https://www.asus.com/support/](https://www.asus.com/support/).

Manuals the verified paths above were checked against:

- [https://dlcdnets.asus.com/pub/ASUS/mb/LGA1700/ROG_STRIX_Z790-E_GAMING_WIFI/E23863_ROG_STRIX_Z790_Series_BIOS_Manual_Intel_14th_EM_V2_WEB.pdf](https://dlcdnets.asus.com/pub/ASUS/mb/LGA1700/ROG_STRIX_Z790-E_GAMING_WIFI/E23863_ROG_STRIX_Z790_Series_BIOS_Manual_Intel_14th_EM_V2_WEB.pdf)
- [https://dlcdnets.asus.com/pub/ASUS/mb/Socket%20AM5/ROG_STRIX_X670E-E_GAMING_WIFI/E20544_ROG_STRIX_X670E_Series_BIOS_manual_EM_WEB.pdf](https://dlcdnets.asus.com/pub/ASUS/mb/Socket%20AM5/ROG_STRIX_X670E-E_GAMING_WIFI/E20544_ROG_STRIX_X670E_Series_BIOS_manual_EM_WEB.pdf)

## Back to the walkthrough

After you save and exit (usually F10), run `osx-next-cli gpu-setup` again. It re-checks every item and shows a tick for the ones that are now on. See the [full GPU passthrough guide](../../gpu-passthrough.md).
