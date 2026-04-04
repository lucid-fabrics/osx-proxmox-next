---
sidebar_position: 1
---

# Welcome

Running macOS on Proxmox shouldn't require a weekend of trial and error. No more hunting for the right OpenCore build, hand-crafting QEMU args, or guessing why your VM won't boot.

**OSX Proxmox Next** takes you from a fresh Proxmox node to a running macOS VM in under 10 minutes. One command, a few choices, and you're done.

```bash
bash -c "$(curl -fsSL https://raw.githubusercontent.com/lucid-fabrics/osx-proxmox-next/main/install.sh)"
```

## What it does for you

- **Detects your hardware** and picks the right CPU, NIC, and boot flags automatically
- **Builds OpenCore** with the correct kexts, drivers, and config for your macOS version
- **Generates a valid SMBIOS identity** so iCloud, iMessage, and FaceTime just work
- **Downloads the recovery image** from Apple and caches it for future VMs
- **Shows you every command** before touching your system (mandatory dry-run)

## Pick your style

| | TUI Wizard | CLI |
|--|-----------|-----|
| **Command** | `osx-next` | `osx-next-cli apply ...` |
| **Best for** | First-time setup, exploring options | Scripting, automation, headless servers |
| **Experience** | Guided 6-step flow with live validation | Flags and JSON output |

## Supported macOS versions

| Version | Codename | Apple Services | Best for |
|---------|----------|----------------|----------|
| 13 | Ventura | Full | Older or limited hardware |
| 14 | **Sonoma** | **Full** | **Most users** (best tested, most reliable) |
| 15 | Sequoia | Full (with `--apple-services`) | Latest features |
| 26 | Tahoe | Full (with `--apple-services`) | Bleeding edge |

:::note
Sequoia and Tahoe require `--apple-services` to be set during VM creation. A kernel-level patch is injected automatically that prevents Apple's DeviceCheck from detecting the VM, allowing full Apple ID, iCloud, iMessage, and FaceTime sign-in. See [Apple Services](guides/apple-services) for details.
:::

## Where to start

New here? Follow this path:

1. **[Check requirements](getting-started/requirements)** -- make sure your hardware is ready
2. **[Install](getting-started/installation)** -- one command on your Proxmox node
3. **[Create your first VM](getting-started/quick-start)** -- walk through the wizard

Already comfortable? Jump to the **[CLI Reference](guides/cli-reference)** or explore the **[Architecture](architecture/overview)** to understand how it all fits together.
