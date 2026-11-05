---
sidebar_position: 4
title: ASRock BIOS settings for GPU passthrough
sidebar_label: ASRock
---

# ASRock: BIOS settings for GPU passthrough

These are the settings the GPU walkthrough asks you to change in the BIOS. The walkthrough checks your host first and only shows the ones that are really missing, so you may need just one or two of them.

Menu names change between boards and BIOS versions. Entries marked "usually under" come from vendor manuals and model lines and have **not** been checked against your exact board. If you cannot find a setting, use the BIOS search (the footer of the screen shows the key, often F3, F7 or F9) and type the setting name from the first column.

Do the settings in this order: CSM off, then Above 4G Decoding, then Resizable BAR if you want it. Virtualization and IOMMU are independent.

## ASRock

Enter the BIOS with **F2 or Del**. Press F6 to switch between EZ Mode and Advanced Mode.

| Setting | Where to look | Set it to | Checked against a source |
|---------|---------------|-----------|--------------------------|
| Intel virtualization (VT-x) | `Advanced > CPU Configuration > Intel Virtualization Technology` | Enabled | yes ([source](https://download.asrock.com/Manual/Software/Intel%20Z790/Software_BIOS%20Setup%20Guide_English.pdf)) |
| AMD virtualization (SVM) | `Advanced > CPU Configuration > SVM Mode` | Enabled | yes ([source 1](https://download.asrock.com/Manual/Software/AMD%20X670/Software_BIOS%20Setup%20Guide_English.pdf), [source 2](https://download.asrock.com/Manual/Software/AMD%20X870/Software_BIOS%20Setup%20Guide_English.pdf)) |
| Intel VT-d | `Advanced > Chipset Configuration > VT-d` | Enabled | yes ([source](https://download.asrock.com/Manual/Software/Intel%20Z790/Software_BIOS%20Setup%20Guide_English.pdf)) |
| AMD IOMMU | `Advanced > AMD CBS > NBIO Common Options > IOMMU` | Enabled | yes ([source 1](https://download.asrock.com/Manual/Software/AMD%20X670/Software_BIOS%20Setup%20Guide_English.pdf), [source 2](https://download.asrock.com/Manual/Software/AMD%20X870/Software_BIOS%20Setup%20Guide_English.pdf)) |
| Above 4G Decoding | `Advanced > Chipset Configuration > Above 4G Decoding (AMD boards: Advanced > PCI Configuration > Above 4G Decoding)` | Enabled | yes ([source 1](https://download.asrock.com/Manual/Software/Intel%20Z790/Software_BIOS%20Setup%20Guide_English.pdf), [source 2](https://download.asrock.com/Manual/Software/AMD%20X870/Software_BIOS%20Setup%20Guide_English.pdf)) |
| CSM (legacy boot support) | `Boot > CSM (Compatibility Support Module) > CSM (AMD boards: Boot > CSM)` | Disabled | yes ([source 1](https://download.asrock.com/Manual/Software/Intel%20Z790/Software_BIOS%20Setup%20Guide_English.pdf), [source 2](https://download.asrock.com/Manual/Software/AMD%20X870/Software_BIOS%20Setup%20Guide_English.pdf)) |
| Primary display | `Advanced > Chipset Configuration > Primary Graphics Adapter (Intel boards). AMD boards: Advanced > Onboard Devices Configuration > Integrated Graphics Controller` | Onboard | yes ([source 1](https://download.asrock.com/Manual/Software/Intel%20Z790/Software_BIOS%20Setup%20Guide_English.pdf), [source 2](https://download.asrock.com/Manual/Software/AMD%20X670/Software_BIOS%20Setup%20Guide_English.pdf)) |
| Resizable BAR (optional) | `Advanced > Chipset Configuration > C.A.M (Clever Access Memory) (AMD boards: Advanced > PCI Configuration > Re-Size BAR Support)` | Disabled if the VM will not start | yes ([source 1](https://download.asrock.com/Manual/Software/Intel%20Z790/Software_BIOS%20Setup%20Guide_English.pdf), [source 2](https://download.asrock.com/Manual/Software/AMD%20X870/Software_BIOS%20Setup%20Guide_English.pdf)) |

## Official manual

The vendor's manuals and support pages: [https://www.asrock.com/support](https://www.asrock.com/support).

Manuals the verified paths above were checked against:

- [https://download.asrock.com/Manual/Software/Intel%20Z790/Software_BIOS%20Setup%20Guide_English.pdf](https://download.asrock.com/Manual/Software/Intel%20Z790/Software_BIOS%20Setup%20Guide_English.pdf)
- [https://download.asrock.com/Manual/Software/AMD%20X670/Software_BIOS%20Setup%20Guide_English.pdf](https://download.asrock.com/Manual/Software/AMD%20X670/Software_BIOS%20Setup%20Guide_English.pdf)
- [https://download.asrock.com/Manual/Software/AMD%20X870/Software_BIOS%20Setup%20Guide_English.pdf](https://download.asrock.com/Manual/Software/AMD%20X870/Software_BIOS%20Setup%20Guide_English.pdf)

## Back to the walkthrough

After you save and exit (usually F10), run `osx-next-cli gpu-setup` again. It re-checks every item and shows a tick for the ones that are now on. See the [full GPU passthrough guide](../../gpu-passthrough.md).
