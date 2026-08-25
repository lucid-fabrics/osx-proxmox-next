# OpenCore image data files

These files are baked into the locally assembled OpenCore boot image
(see `oc_builder.py` and the bash installer's `assemble_opencore_iso`).

| File | Origin | License |
|------|--------|---------|
| `config.plist` | This project's OpenCore configuration template for macOS VMs on Proxmox, validated against OpenCore 1.0.7 with `ocvalidate` | Apache-2.0 (this repo) |
| `ACPI/SSDT-DTGP.aml`, `SSDT-EC.aml`, `SSDT-EHCI.aml`, `SSDT-PLUG.aml` | Prebuilt community ACPI tables commonly used for macOS virtualization (fixed EC, CPU power management, EHCI, DTGP method) | Freely redistributed community tables |
| `MCEReporterDisabler.kext/Contents/Info.plist` | RehabMan's plist-only kext that stops AppleIntelMCEReporter from crashing multi-socket-presenting VMs | GPLv2, (c) 2017 RehabMan |

Everything else in the image (OpenCore itself, its drivers and picker
resources, and the Lilu/VirtualSMC/WhateverGreen/AppleALC/CryptexFixup/
RestrictEvents kexts) is downloaded at assembly time from the upstream
Acidanthera releases (BSD-3-Clause) at the versions pinned in
`oc_builder.py`, each verified against a pinned SHA-256.

Do not edit `config.plist` by hand without re-running `ocvalidate` from the
pinned OpenCore release against it, and keep the copy embedded in
`scripts/bash/osx-proxmox-next.sh` in sync (`tests/test_oc_builder.py`
fails if the two drift).
