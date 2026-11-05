---
sidebar_position: 10
title: Lenovo BIOS settings for GPU passthrough
sidebar_label: Lenovo
---

# Lenovo: BIOS settings for GPU passthrough

These are the settings the GPU walkthrough asks you to change in the BIOS. The walkthrough checks your host first and only shows the ones that are really missing, so you may need just one or two of them.

Menu names change between boards and BIOS versions. Entries marked "usually under" come from vendor manuals and model lines and have **not** been checked against your exact board. If you cannot find a setting, use the BIOS search (the footer of the screen shows the key, often F3, F7 or F9) and type the setting name from the first column.

Do the settings in this order: CSM off, then Above 4G Decoding, then Resizable BAR if you want it. Virtualization and IOMMU are independent.

## Lenovo ThinkCentre / ThinkStation

Enter the BIOS with **F1 (some models: Enter, then F1)**. F12 opens the boot menu.

| Setting | Where to look | Set it to | Checked against a source |
|---------|---------------|-----------|--------------------------|
| Intel virtualization (VT-x) | usually under `Security > Virtualization > Intel Virtualization Technology` | Enabled | no |
| AMD virtualization (SVM) | usually under `Security > Virtualization > AMD-V (SVM)` | Enabled | no |
| Intel VT-d | usually under `Security > Virtualization > Intel VT-d Feature` | Enabled | no |
| AMD IOMMU | usually under `Advanced > AMD CBS > NBIO > IOMMU (ThinkStation P620)` | Enabled | no |
| Above 4G Decoding | usually under `Advanced > PCI Configuration > Above 4G Decoding (ThinkStation; ThinkCentre often has none)` | Enabled | no |
| CSM (legacy boot support) | usually under `Startup > CSM Support and Startup > Boot Mode` | Disabled / UEFI Only | no |
| Primary display | usually under `Devices > Video Setup > Select Active Video` | Integrated Graphics | no |
| Resizable BAR (optional) | `Devices > PCIe Setup > Re-size BAR Support (ThinkStation PX)` | Enabled | yes ([source](https://download.lenovo.com/pccbbs/thinkcentre_pdf/workstationcomputesolutioninstallationguide.pdf)) |

Not checked against a manual:

- Intel virtualization (VT-x): Option names confirmed by Lenovo's ThinkStation PX guide (IOMMU, VT-d, Intel Virtualization Technology) but its menu path is only in screenshots. Previous path kept from memory.
- AMD virtualization (SVM): Vendor manual wording from memory, not re-checked against a current manual
- Intel VT-d: Same as above: name confirmed, menu path only in screenshots. Previous path kept from memory.
- AMD IOMMU: Inferred from the same vendor's other models, not checked
- Above 4G Decoding: Inferred from the same vendor's other models, not checked
- CSM (legacy boot support): Vendor manual wording from memory, not re-checked against a current manual
- Primary display: Vendor manual wording from memory, not re-checked against a current manual

## Lenovo ThinkSystem

Enter the BIOS with **F1**. F12 opens the boot menu.

| Setting | Where to look | Set it to | Checked against a source |
|---------|---------------|-----------|--------------------------|
| Intel virtualization (VT-x) | `System Settings > Processors > Intel Virtualization Technology` | Enabled | yes ([source](https://pubs.lenovo.com/uefi_iot/processors)) |
| AMD virtualization (SVM) | usually under `System Settings > Processors > SVM Mode` | Enabled | no |
| Intel VT-d | usually under `System Settings > Processors > VT-d` | Enabled | no |
| AMD IOMMU | usually under `System Settings > Devices and I/O Ports > IOMMU` | Enabled | no |
| Above 4G Decoding | usually under `System Settings > Devices and I/O Ports > MM Config (Memory Mapped I/O above 4GB)` | Enabled | no |
| CSM (legacy boot support) | usually under `Boot Manager > Boot Modes > System Boot Mode (Lenovo docs: Boot manager > System > Boot Mode)` | UEFI Mode | no |

Not checked against a manual:

- AMD virtualization (SVM): Vendor manual wording from memory, not re-checked against a current manual
- Intel VT-d: Vendor manual wording from memory, not re-checked against a current manual
- AMD IOMMU: Vendor manual wording from memory, not re-checked against a current manual
- Above 4G Decoding: Vendor manual wording from memory, not re-checked against a current manual
- CSM (legacy boot support): Partly confirmed: https://pubs.lenovo.com/uefi_iot/boot_manager says Boot manager > System > Boot Mode switches between UEFI and legacy mode, but the exact menu labels on a ThinkSystem were not confirmed.

No path is recorded for: Primary display, Resizable BAR (optional). See the generic names on the [generic page](generic.md).

## Official manual

The vendor's manuals and support pages: [https://pubs.lenovo.com](https://pubs.lenovo.com).

Manuals the verified paths above were checked against:

- [https://download.lenovo.com/pccbbs/thinkcentre_pdf/workstationcomputesolutioninstallationguide.pdf](https://download.lenovo.com/pccbbs/thinkcentre_pdf/workstationcomputesolutioninstallationguide.pdf)
- [https://pubs.lenovo.com/uefi_iot/processors](https://pubs.lenovo.com/uefi_iot/processors)

## Back to the walkthrough

After you save and exit (usually F10), run `osx-next-cli gpu-setup` again. It re-checks every item and shows a tick for the ones that are now on. See the [full GPU passthrough guide](../../gpu-passthrough.md).
