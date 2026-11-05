---
sidebar_position: 13
title: Generic BIOS settings for GPU passthrough
sidebar_label: Other / generic
---

# Other boards: BIOS settings for GPU passthrough

The walkthrough could not match your motherboard or computer to a known vendor, so this page lists the usual names. Setting names differ per vendor. Use the BIOS search (the footer of the screen shows the key, often F3, F7 or F9) and type one of the names below.

Do the settings in this order: CSM off, then Above 4G Decoding, then Resizable BAR if you want it. Virtualization and IOMMU are independent.

| Setting | Names to look for | Usually found under |
|---------|-------------------|---------------------|
| Virtualization | Intel Virtualization Technology, VT-x, VMX, SVM Mode, AMD-V, Secure Virtual Machine | Usually under Advanced > CPU Configuration, Tweaker > Advanced CPU Settings, Settings > Advanced > CPU Features, or Security > Virtualization. |
| IOMMU | VT-d, Intel VT for Directed I/O, AMD IOMMU, IOMMU Support, AMD-Vi | Usually under Advanced > Chipset, North Bridge, System Agent (SA) Configuration, AMD CBS > NBIO Common Options, Miscellaneous (Gigabyte) or Processor Options (HPE). |
| Above 4G Decoding | Above 4G Decoding, Above 4GB MMIO, Memory Mapped I/O above 4GB, PCI 64-bit Resource Handling above 4G, Above 4G memory / Crypto Currency mining (MSI) | Usually under Advanced > PCI Subsystem Settings, PCIe/PCI/PnP Configuration, North Bridge or IO Ports (Gigabyte). |
| Resizable BAR (optional) | Re-Size BAR Support, Resizable BAR, Smart Access Memory, Clever Access Memory | Appears only after Above 4G Decoding is on and CSM is off. |
| CSM / legacy boot | CSM Support, Launch CSM, Legacy Support, Boot mode UEFI/Legacy, Legacy Option ROMs | Usually under Boot. |
| Primary display | Primary Display, Initial Display Output, Initial Graphics Adapter, Primary Graphics Adapter, VGA Priority, Primary Video Device, Select Active Video | Set it to IGD, IGFX, Onboard or iGPU. Servers keep the BMC graphics as the console and need no change. |

## Back to the walkthrough

After you save and exit (usually F10), run `osx-next-cli gpu-setup` again. It re-checks every item. See the [full GPU passthrough guide](../../gpu-passthrough.md).
