---
sidebar_position: 6
title: Supermicro BIOS settings for GPU passthrough
sidebar_label: Supermicro
---

# Supermicro: BIOS settings for GPU passthrough

These are the settings the GPU walkthrough asks you to change in the BIOS. The walkthrough checks your host first and only shows the ones that are really missing, so you may need just one or two of them.

Menu names change between boards and BIOS versions. Entries marked "usually under" come from vendor manuals and model lines and have **not** been checked against your exact board. If you cannot find a setting, use the BIOS search (the footer of the screen shows the key, often F3, F7 or F9) and type the setting name from the first column.

Do the settings in this order: CSM off, then Above 4G Decoding, then Resizable BAR if you want it. Virtualization and IOMMU are independent.

## Supermicro

Enter the BIOS with **Del**. Boot menu is F11. The IPMI remote console works for all of this.

| Setting | Where to look | Set it to | Checked against a source |
|---------|---------------|-----------|--------------------------|
| Intel virtualization (VT-x) | `Advanced > CPU Configuration > Intel (VMX) Virtualization Technology` | Enabled | yes ([source](https://www.supermicro.com/manuals/motherboard/X12/MNL-2353.pdf)) |
| AMD virtualization (SVM) | `Advanced > CPU Configuration > SVM Mode` | Enabled | yes ([source](https://www.supermicro.com/manuals/motherboard/H13/MNL-2564.pdf)) |
| Intel VT-d | `Advanced > Chipset > System Agent (SA) Configuration > VT-d (Xeon Scalable boards may use Chipset Configuration > North Bridge > IIO Configuration > Intel VT for Directed I/O (VT-d))` | Enabled | yes ([source](https://www.supermicro.com/manuals/motherboard/X12/MNL-2353.pdf)) |
| AMD IOMMU | `Advanced > NB Configuration > IOMMU` | Enabled | yes ([source](https://www.supermicro.com/manuals/motherboard/H13/MNL-2564.pdf)) |
| Above 4G Decoding | `Advanced > PCIe/PCI/PnP Configuration > Above 4G Decoding (Intel Xeon E boards: Above 4GB MMIO BIOS Assignment)` | Enabled | yes ([source 1](https://www.supermicro.com/manuals/motherboard/H13/MNL-2564.pdf), [source 2](https://www.supermicro.com/manuals/motherboard/X12/MNL-2353.pdf)) |
| CSM (legacy boot support) | `Security > Secure Boot > CSM Support. Also Boot > Boot Mode Select (Legacy, UEFI, Dual)` | Disabled / UEFI | yes ([source 1](https://www.supermicro.com/manuals/motherboard/H13/MNL-2564.pdf), [source 2](https://www.supermicro.com/manuals/motherboard/X12/MNL-2353.pdf)) |
| Primary display | `Advanced > PCIe/PCI/PnP Configuration > VGA Priority` | Onboard | yes ([source](https://www.supermicro.com/manuals/motherboard/H13/MNL-2564.pdf)) |
| Resizable BAR (optional) | `Advanced > PCIe/PCI/PnP Configuration > Re-Size BAR Support (not present on the X12STL-F)` | Disabled if the VM will not start | yes ([source](https://www.supermicro.com/manuals/motherboard/H13/MNL-2564.pdf)) |

## Official manual

The vendor's manuals and support pages: [https://www.supermicro.com/en/support/resources/downloadcenter/manual](https://www.supermicro.com/en/support/resources/downloadcenter/manual).

Manuals the verified paths above were checked against:

- [https://www.supermicro.com/manuals/motherboard/X12/MNL-2353.pdf](https://www.supermicro.com/manuals/motherboard/X12/MNL-2353.pdf)
- [https://www.supermicro.com/manuals/motherboard/H13/MNL-2564.pdf](https://www.supermicro.com/manuals/motherboard/H13/MNL-2564.pdf)

## Back to the walkthrough

After you save and exit (usually F10), run `osx-next-cli gpu-setup` again. It re-checks every item and shows a tick for the ones that are now on. See the [full GPU passthrough guide](../../gpu-passthrough.md).
