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
| Intel virtualization (VT-x) | `Virtualization Support > Intel Virtualization Technology (older manuals call it Virtualization)` | Enabled | yes ([source 1](https://www.dell.com/support/manuals/en-gb/optiplex-7020t-plus-desktop/optiplex-tower-plus-7020-owners-manual/system-setup-options), [source 2](https://www.dell.com/support/manuals/en-us/optiplex-7070-micro/opti7070_micro_setup_specs/virtualization-support), [source 3](https://www.dell.com/support/kbdoc/en-us/000195978/how-to-enable-or-disable-hardware-virtualization-on-dell-systems)) |
| AMD virtualization (SVM) | usually under `Virtualization Support > SVM Mode` | Enabled | no |
| Intel VT-d | `Virtualization Support > VT for Direct I/O` | Enabled | yes ([source 1](https://www.dell.com/support/manuals/en-gb/optiplex-7020t-plus-desktop/optiplex-tower-plus-7020-owners-manual/system-setup-options), [source 2](https://www.dell.com/support/manuals/en-us/optiplex-7070-micro/opti7070_micro_setup_specs/virtualization-support)) |
| AMD IOMMU | usually under `Virtualization Support > IOMMU` | Enabled | no |
| Above 4G Decoding | usually under `Settings > System Configuration > Above 4G Decoding (many Dell desktops have no such switch)` | Enabled | no |
| CSM (legacy boot support) | `Boot Configuration > Boot Sequence > Boot Mode: UEFI only (older BIOS: Enable Legacy Option ROMs off)` | UEFI only, legacy option ROMs off | yes ([source](https://www.dell.com/support/manuals/en-gb/optiplex-7020t-plus-desktop/optiplex-tower-plus-7020-owners-manual/system-setup-options)) |
| Primary display | `Display > Primary Display` | Auto (or Intel HD Graphics) | yes ([source](https://www.dell.com/support/manuals/en-gb/optiplex-7020t-plus-desktop/optiplex-tower-plus-7020-owners-manual/system-setup-options)) |
| Resizable BAR (optional) | `Performance > PCIe Resizable Base Address Register (BAR)` | Disabled if the VM will not start | yes ([source](https://www.dell.com/support/manuals/en-gb/optiplex-7020t-plus-desktop/optiplex-tower-plus-7020-owners-manual/system-setup-options)) |

Not checked against a manual:

- AMD virtualization (SVM): Not verified for Dell AMD desktops: the official pages found cover Intel OptiPlex only.
- AMD IOMMU: Not verified: no official Dell desktop page for an IOMMU switch was found.
- Above 4G Decoding: Not verified: the OptiPlex 7020 owner's manual lists no Above 4G or MMIO item, so most Dell desktops likely have none.

## Dell PowerEdge

Enter the BIOS with **F2 (System Setup)**. iDRAC: Next Boot > BIOS Setup.

| Setting | Where to look | Set it to | Checked against a source |
|---------|---------------|-----------|--------------------------|
| Intel virtualization (VT-x) | `System Setup Main Menu > System BIOS > Processor Settings > Virtualization Technology` | Enabled | yes ([source](https://www.dell.com/support/manuals/en-us/poweredge-r450/per450_bios_ism_pub/processor-settings)) |
| AMD virtualization (SVM) | `System Setup Main Menu > System BIOS > Processor Settings > Virtualization Technology (AMD servers have no separate SVM item)` | Enabled | yes ([source](https://www.dell.com/support/manuals/en-uk/poweredge-r7515/r7515_bios_pub/processor-settings)) |
| Intel VT-d | `System BIOS > Processor Settings > Virtualization Technology (Intel servers have no separate VT-d item in the guide)` | Enabled | yes ([source](https://www.dell.com/support/manuals/en-us/poweredge-r450/per450_bios_ism_pub/processor-settings)) |
| AMD IOMMU | `System BIOS > Processor Settings > IOMMU Support` | Enabled | yes ([source](https://www.dell.com/support/manuals/en-uk/poweredge-r7515/r7515_bios_pub/processor-settings)) |
| Above 4G Decoding | `System BIOS > Integrated Devices > Memory Mapped I/O above 4 GB` | Enabled | yes ([source](https://www.dell.com/support/manuals/en-gh/poweredge-r750xa/per750xa_bios_pub_ism/integrated-devices)) |
| CSM (legacy boot support) | `System BIOS > Boot Settings > Boot Mode` | UEFI | yes ([source](https://www.dell.com/support/manuals/en-us/poweredge-r740/r740_bios_ism_pub/boot-settings)) |

No path is recorded for: Primary display, Resizable BAR (optional). See the generic names on the [generic page](generic.md).

## Official manual

The vendor's manuals and support pages: [https://www.dell.com/support](https://www.dell.com/support).

Manuals the verified paths above were checked against:

- [https://www.dell.com/support/manuals/en-gb/optiplex-7020t-plus-desktop/optiplex-tower-plus-7020-owners-manual/system-setup-options](https://www.dell.com/support/manuals/en-gb/optiplex-7020t-plus-desktop/optiplex-tower-plus-7020-owners-manual/system-setup-options)
- [https://www.dell.com/support/manuals/en-us/optiplex-7070-micro/opti7070_micro_setup_specs/virtualization-support](https://www.dell.com/support/manuals/en-us/optiplex-7070-micro/opti7070_micro_setup_specs/virtualization-support)
- [https://www.dell.com/support/kbdoc/en-us/000195978/how-to-enable-or-disable-hardware-virtualization-on-dell-systems](https://www.dell.com/support/kbdoc/en-us/000195978/how-to-enable-or-disable-hardware-virtualization-on-dell-systems)
- [https://www.dell.com/support/manuals/en-us/poweredge-r450/per450_bios_ism_pub/processor-settings](https://www.dell.com/support/manuals/en-us/poweredge-r450/per450_bios_ism_pub/processor-settings)
- [https://www.dell.com/support/manuals/en-uk/poweredge-r7515/r7515_bios_pub/processor-settings](https://www.dell.com/support/manuals/en-uk/poweredge-r7515/r7515_bios_pub/processor-settings)
- [https://www.dell.com/support/manuals/en-gh/poweredge-r750xa/per750xa_bios_pub_ism/integrated-devices](https://www.dell.com/support/manuals/en-gh/poweredge-r750xa/per750xa_bios_pub_ism/integrated-devices)
- [https://www.dell.com/support/manuals/en-us/poweredge-r740/r740_bios_ism_pub/boot-settings](https://www.dell.com/support/manuals/en-us/poweredge-r740/r740_bios_ism_pub/boot-settings)

## Back to the walkthrough

After you save and exit (usually F10), run `osx-next-cli gpu-setup` again. It re-checks every item and shows a tick for the ones that are now on. See the [full GPU passthrough guide](../../gpu-passthrough.md).
