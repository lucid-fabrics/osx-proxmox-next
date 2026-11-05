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

Enter the BIOS with **F2 or Del**.

| Setting | Where to look | Set it to | Checked against a source |
|---------|---------------|-----------|--------------------------|
| Intel virtualization (VT-x) | usually under `Advanced > CPU Configuration > Intel Virtualization Technology` | Enabled | no |
| AMD virtualization (SVM) | usually under `Advanced > CPU Configuration > SVM Mode` | Enabled | no |
| Intel VT-d | usually under `Advanced > Chipset Configuration > VT-d (older boards: Advanced > North Bridge Configuration > VT-d)` | Enabled | no |
| AMD IOMMU | usually under `Advanced > AMD CBS > NBIO Common Options > IOMMU (AM5: Advanced > North Bridge > IOMMU)` | Enabled | no |
| Above 4G Decoding | usually under `Advanced > North Bridge Configuration > Above 4G Decoding (Intel: Advanced > Chipset Configuration > Above 4G Decoding)` | Enabled | no |
| CSM (legacy boot support) | usually under `Boot > CSM > CSM` | Disabled | no |
| Primary display | usually under `Advanced > North Bridge (or Chipset) Configuration > Primary Graphics Adapter` | Onboard | no |
| Resizable BAR (optional) | usually under `Advanced > North Bridge (or Chipset) Configuration > Re-Size BAR Support` | Disabled if the VM will not start | no |

## Official manual

The vendor's manuals and support pages: [https://www.asrock.com/support](https://www.asrock.com/support).

## Back to the walkthrough

After you save and exit (usually F10), run `osx-next-cli gpu-setup` again. It re-checks every item and shows a tick for the ones that are now on. See the [full GPU passthrough guide](../../gpu-passthrough.md).
