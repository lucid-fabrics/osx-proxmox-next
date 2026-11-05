---
sidebar_position: 5
title: ASRock Rack BIOS settings for GPU passthrough
sidebar_label: ASRock Rack
---

# ASRock Rack: BIOS settings for GPU passthrough

These are the settings the GPU walkthrough asks you to change in the BIOS. The walkthrough checks your host first and only shows the ones that are really missing, so you may need just one or two of them.

Menu names change between boards and BIOS versions. Entries marked "usually under" come from vendor manuals and model lines and have **not** been checked against your exact board. If you cannot find a setting, use the BIOS search (the footer of the screen shows the key, often F3, F7 or F9) and type the setting name from the first column.

Do the settings in this order: CSM off, then Above 4G Decoding, then Resizable BAR if you want it. Virtualization and IOMMU are independent.

## ASRock Rack

Enter the BIOS with **F2 or Del**. Boards with a BMC can be used through the IPMI remote console.

| Setting | Where to look | Set it to | Checked against a source |
|---------|---------------|-----------|--------------------------|
| Intel virtualization (VT-x) | `Advanced > CPU Configuration > Intel Virtualization Technology` | Enabled | yes ([source](https://download.asrock.com/Manual/W680D4U-2L2T.pdf)) |
| AMD virtualization (SVM) | `Advanced > CPU Configuration > SVM Mode` | Enabled | yes ([source 1](https://download.asrock.com/Manual/ROMED8-2T.pdf), [source 2](https://download.asrock.com/Manual/B650D4U.pdf)) |
| Intel VT-d | `Advanced > Chipset Configuration > VT-d` | Enabled | yes ([source](https://download.asrock.com/Manual/W680D4U-2L2T.pdf)) |
| AMD IOMMU | `Advanced > AMD CBS > NBIO Common Options > IOMMU` | Enabled | yes ([source](https://download.asrock.com/Manual/B650D4U.pdf)) |
| Above 4G Decoding | `Advanced > PCI Subsystem Settings > Above 4G Decoding (EPYC boards such as ROMED8-2T: Advanced > Chipset Configuration > Above 4G Decoding)` | Enabled | yes ([source 1](https://download.asrock.com/Manual/B650D4U.pdf), [source 2](https://download.asrock.com/Manual/ROMED8-2T.pdf)) |
| CSM (legacy boot support) | `Boot > CSM Parameters > CSM (ROMED8-2T). The B650D4U and W680D4U manuals show no CSM item` | Disabled | yes ([source 1](https://download.asrock.com/Manual/ROMED8-2T.pdf), [source 2](https://download.asrock.com/Manual/B650D4U.pdf), [source 3](https://download.asrock.com/Manual/W680D4U-2L2T.pdf)) |
| Primary display | `Advanced > Chipset Configuration > Primary Display (B650D4U). Server boards keep the BMC graphics as console` | Onboard | yes ([source](https://download.asrock.com/Manual/B650D4U.pdf)) |
| Resizable BAR (optional) | `Advanced > PCI Subsystem Settings > Re-Size BAR Support (W680D4U: Advanced > Chipset Configuration > C.A.M (Clever Access Memory))` | Disabled if the VM will not start | yes ([source 1](https://download.asrock.com/Manual/B650D4U.pdf), [source 2](https://download.asrock.com/Manual/W680D4U-2L2T.pdf)) |

## Official manual

The vendor's manuals and support pages: [https://www.asrockrack.com/support/](https://www.asrockrack.com/support/).

Manuals the verified paths above were checked against:

- [https://download.asrock.com/Manual/W680D4U-2L2T.pdf](https://download.asrock.com/Manual/W680D4U-2L2T.pdf)
- [https://download.asrock.com/Manual/ROMED8-2T.pdf](https://download.asrock.com/Manual/ROMED8-2T.pdf)
- [https://download.asrock.com/Manual/B650D4U.pdf](https://download.asrock.com/Manual/B650D4U.pdf)

## Back to the walkthrough

After you save and exit (usually F10), run `osx-next-cli gpu-setup` again. It re-checks every item and shows a tick for the ones that are now on. See the [full GPU passthrough guide](../../gpu-passthrough.md).
