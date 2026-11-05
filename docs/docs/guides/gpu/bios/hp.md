---
sidebar_position: 8
title: HP workstations and desktops BIOS settings for GPU passthrough
sidebar_label: HP workstations and desktops
---

# HP workstations and desktops: BIOS settings for GPU passthrough

These are the settings the GPU walkthrough asks you to change in the BIOS. The walkthrough checks your host first and only shows the ones that are really missing, so you may need just one or two of them.

Menu names change between boards and BIOS versions. Entries marked "usually under" come from vendor manuals and model lines and have **not** been checked against your exact board. If you cannot find a setting, use the BIOS search (the footer of the screen shows the key, often F3, F7 or F9) and type the setting name from the first column.

Do the settings in this order: CSM off, then Above 4G Decoding, then Resizable BAR if you want it. Virtualization and IOMMU are independent.

## HP workstations and desktops

Enter the BIOS with **F10 (press repeatedly as soon as the power light turns white)**. F9 opens the boot menu.

| Setting | Where to look | Set it to | Checked against a source |
|---------|---------------|-----------|--------------------------|
| Intel virtualization (VT-x) | `Advanced > System Options > Virtualization Technology (VTx)` | Enabled | yes ([source 1](https://kaas.hpcloud.hp.com/pdf-public/pdf_7905584_en-US-1.pdf), [source 2](https://kaas.hpcloud.hp.com/pdf-public/pdf_5451169_en-US-1.pdf), [source 3](https://kaas.hpcloud.hp.com/pdf-public/pdf_7582484_en-US-1.pdf)) |
| AMD virtualization (SVM) | `Advanced > System Options > SVM CPU Virtualization` | Enabled | yes ([source](https://kaas.hpcloud.hp.com/pdf-public/pdf_7905584_en-US-1.pdf)) |
| Intel VT-d | `Advanced > System Options > Virtualization Technology for Directed I/O (VTd)` | Enabled | yes ([source 1](https://kaas.hpcloud.hp.com/pdf-public/pdf_7905584_en-US-1.pdf), [source 2](https://kaas.hpcloud.hp.com/pdf-public/pdf_7582484_en-US-1.pdf)) |
| AMD IOMMU | `Advanced > System Options > SVM CPU Virtualization (it enables AMD-V and AMD-Vi, there is no separate IOMMU switch)` | Enabled | yes ([source](https://kaas.hpcloud.hp.com/pdf-public/pdf_7905584_en-US-1.pdf)) |
| Above 4G Decoding | usually under `Advanced > Device Options > Above 4G MMIO (name varies)` | Enabled | no |
| CSM (legacy boot support) | `Advanced > Boot Options (the current guides show UEFI Boot Order and no Legacy Support or CSM item)` | UEFI | yes ([source 1](https://kaas.hpcloud.hp.com/pdf-public/pdf_5451169_en-US-1.pdf), [source 2](https://kaas.hpcloud.hp.com/pdf-public/pdf_7582484_en-US-1.pdf), [source 3](https://kaas.hpcloud.hp.com/pdf-public/pdf_7905584_en-US-1.pdf)) |
| Primary display | `Advanced > Built-In Device Options > VGA Boot Device (models with discrete graphics)` | Integrated graphics | yes ([source](https://kaas.hpcloud.hp.com/pdf-public/pdf_7582484_en-US-1.pdf)) |
| Resizable BAR (optional) | `Advanced > Slot Settings > Resizable BARs` | Enabled | yes ([source](https://kaas.hpcloud.hp.com/pdf-public/pdf_7582484_en-US-1.pdf)) |

Not checked against a manual:

- Above 4G Decoding: Not verified: the Z2 G9, Z4 G5 and Elite 800 G9 guides list no Above 4G or MMIO item.

## Official manual

The vendor's manuals and support pages: [https://support.hp.com](https://support.hp.com).

Manuals the verified paths above were checked against:

- [https://kaas.hpcloud.hp.com/pdf-public/pdf_7905584_en-US-1.pdf](https://kaas.hpcloud.hp.com/pdf-public/pdf_7905584_en-US-1.pdf)
- [https://kaas.hpcloud.hp.com/pdf-public/pdf_5451169_en-US-1.pdf](https://kaas.hpcloud.hp.com/pdf-public/pdf_5451169_en-US-1.pdf)
- [https://kaas.hpcloud.hp.com/pdf-public/pdf_7582484_en-US-1.pdf](https://kaas.hpcloud.hp.com/pdf-public/pdf_7582484_en-US-1.pdf)

## Back to the walkthrough

After you save and exit (usually F10), run `osx-next-cli gpu-setup` again. It re-checks every item and shows a tick for the ones that are now on. See the [full GPU passthrough guide](../../gpu-passthrough.md).
