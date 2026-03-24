---
sidebar_position: 2
title: Installation
---

# Installation

Two ways to install OSX Proxmox Next on your Proxmox host. All commands run as root on the Proxmox node.

## Method 1: One-Liner Script (Recommended)

The fastest way to get started. Clones the repo, sets up a Python virtual environment, and launches the TUI wizard:

```bash
bash -c "$(curl -fsSL https://raw.githubusercontent.com/lucid-fabrics/osx-proxmox-next/main/install.sh)"
```

This handles all dependencies automatically.

## Method 2: Minimal Bash Script (No Python)

A standalone bash script with the same VM creation logic, using whiptail menus instead of the Python TUI:

```bash
bash -c "$(curl -fsSL https://raw.githubusercontent.com/lucid-fabrics/osx-proxmox-next/main/scripts/bash/osx-proxmox-next.sh)"
```

No Python or virtual environment needed.

## Verifying Installation

After installing via Method 1, confirm the tool is available:

```bash
# Check version
osx-next-cli --version

# Run preflight checks (validates host readiness)
osx-next-cli preflight
```

The preflight command checks:

- CPU vendor detection (Intel/AMD)
- Virtualization extensions (VT-x/AMD-V)
- Available storage pools
- Required dependencies

:::warning
All installation methods require root access on the Proxmox host. The tool creates and manages VMs via `qm` commands, which require root privileges.
:::
