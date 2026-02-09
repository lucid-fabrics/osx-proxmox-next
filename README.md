<h1 align="center">
  <br>
  🍏 OSX Proxmox Next
  <br>
</h1>

<p align="center">
  <strong>One-command macOS VM setup for Proxmox 9.</strong><br>
  No manual <code>qm</code> commands. No config file editing. Just a guided wizard.
</p>

<p align="center">
  <a href="https://github.com/wmehanna/osx-proxmox-next">
    <img alt="Proxmox" src="https://img.shields.io/badge/Proxmox-9%20Ready-E57000?logo=proxmox&logoColor=white">
  </a>
  <img alt="macOS" src="https://img.shields.io/badge/macOS-Sonoma%2014%20%7C%20Sequoia%2015%20%7C%20Tahoe%2026-111111?logo=apple&logoColor=white">
  <a href="https://ko-fi.com/lucidfabrics">
    <img alt="Support on Ko-fi" src="https://img.shields.io/badge/Support-Ko--fi-FF5E5B?logo=ko-fi&logoColor=white">
  </a>
</p>

---

## 🧰 What It Does

This tool automates macOS virtual machine creation on Proxmox VE 9. It handles VMID selection, CPU/RAM detection, OpenCore bootloader setup, and the full `qm` command sequence — so you don't have to.

**You get:**
- 🧙 A step-by-step TUI wizard: **Preflight > Configure > Review > Dry Run > Live Apply**
- 🔍 Auto-detected hardware defaults (CPU cores, RAM, storage targets)
- 💿 Automatic OpenCore and recovery/installer ISO detection
- 🛡️ Safe dry-run mode to preview every command before execution
- 🚫 Validation that blocks live apply when required assets are missing

![Wizard Screenshot](docs/images/wizard-step2.png)

![macOS Desktop via VNC](docs/images/macos-vnc-desktop.png)

> **Note:** Dynamic wallpapers are known to not display correctly without GPU passthrough on VNC. Use a static wallpaper instead.

---

## 🚀 Quick Start

Run this on your Proxmox 9 host as root:

```bash
bash -c "$(curl -fsSL https://raw.githubusercontent.com/wmehanna/osx-proxmox-next/main/install.sh)"
```

This clones the repo, sets up a Python venv, and launches the TUI wizard.

### 🪄 Wizard Walkthrough

| Step | What Happens |
|------|-------------|
| **1️⃣ Preflight** | Checks for `qm`, `pvesm`, `/dev/kvm`, and root access |
| **2️⃣ Configure** | Pick macOS version, storage target, and review auto-detected CPU/RAM/VMID |
| **3️⃣ Review** | Validates config and checks that OpenCore + installer ISOs exist |
| **4️⃣ Dry Run** | Shows every `qm` command that will run — nothing is executed yet |
| **5️⃣ Live Apply** | Creates the VM for real |

**Most users:** click **Use Recommended** in step 2, pick your macOS version, pick your storage, then click through to **Live Apply**.

---

## 📋 Requirements

### 🖥️ Hardware

| Component | Minimum | Recommended |
|-----------|---------|-------------|
| 🧠 CPU | 4 cores, VT-x/AMD-V | 8+ cores |
| 💾 RAM | 8 GB host (4 GB to VM) | 16+ GB host |
| 💽 Storage | 64 GB free | 128+ GB SSD/NVMe |
| 🎮 GPU | Integrated | Discrete (for passthrough) |

### 🏠 Host

- Proxmox VE 9 with root shell access
- Internet access (for bootstrap + dependencies)
- ISO storage available (e.g. `/var/lib/vz/template/iso`)

### ⏱️ TSC Check (Recommended)

Stable TSC flags reduce clock drift and VM lag. Check with:

```bash
lscpu | grep -E 'Model name|Flags'
```

Look for `constant_tsc` and `nonstop_tsc` in the output.

---

## 🍎 Supported macOS Versions

| macOS | Channel | Notes |
|-------|---------|-------|
| **Sonoma 14** | ✅ Stable | Best tested, most reliable |
| **Sequoia 15** | ✅ Stable | Fully supported |
| **Tahoe 26** | 🧪 Preview | Requires full installer ISO (not just recovery) |

---

## ⌨️ CLI Usage

For scripting or headless use, the CLI bypasses the TUI entirely:

```bash
# Check host readiness
osx-next-cli preflight

# Preview commands (dry run)
osx-next-cli apply \
  --vmid 910 --name macos-sequoia --macos sequoia \
  --cores 8 --memory 16384 --disk 128 \
  --bridge vmbr0 --storage local-lvm

# Execute for real
osx-next-cli apply --execute \
  --vmid 910 --name macos-sequoia --macos sequoia \
  --cores 8 --memory 16384 --disk 128 \
  --bridge vmbr0 --storage local-lvm
```

---

## 🔧 Troubleshooting

<details>
<summary>💽 <strong>macOS installer doesn't show my disk</strong></summary>

In the macOS installer:
1. Open **Disk Utility**
2. Click **View > Show All Devices**
3. Select **QEMU HARDDISK Media**
4. Erase with format **APFS** and scheme **GUID Partition Map**
5. Close Disk Utility and continue installation
</details>

<details>
<summary>🚫 <strong>Live apply is blocked — missing assets</strong></summary>

The tool requires OpenCore and recovery/installer ISOs in your Proxmox ISO storage. It scans `/var/lib/vz/template/iso` and `/mnt/pve/*/template/iso` for:
- `opencore-v21.iso` or `opencore-{version}.iso`
- `{version}-recovery.iso`
- For Tahoe: a full installer ISO matching `*tahoe*full*.iso` or `*InstallAssistant*.iso`
</details>

<details>
<summary>🐚 <strong>I see UEFI Shell instead of macOS boot</strong></summary>

Boot media path or order mismatch. Ensure OpenCore is on `ide2` and recovery/installer on `ide3`, with boot order set to `ide2;ide3;sata0`.
</details>

<details>
<summary>🖥️ <strong>"Guest has not initialized the display"</strong></summary>

Boot/display profile mismatch during early boot. Use `vga: std` for stable noVNC during installation.
</details>

---

## 🎮 GPU Passthrough

Host-side setup is manual and required before the VM can use a discrete GPU.

1. Enable **VT-d / IOMMU** in BIOS/UEFI
2. Add to kernel cmdline:
   - Intel: `intel_iommu=on iommu=pt`
   - AMD: `amd_iommu=on iommu=pt`
3. Bind GPU + GPU audio to `vfio-pci`
4. Reboot host
5. Attach both PCI functions to VM (`hostpci0`, `hostpci1`)

📖 Reference: [Proxmox PCI(e) Passthrough Wiki](https://pve.proxmox.com/wiki/PCI(e)_Passthrough)

---

## ⚡ Performance Tips

- 💿 Use **SSD/NVMe-backed storage** for VM disks
- 🧠 Don't overcommit host CPU or RAM
- 🔧 Keep the main macOS disk on `sata0`, OpenCore on `ide2`, recovery on `ide3`
- 🖥️ Use `vga: std` during installation (switch after)
- 📏 Change one setting at a time and measure the impact

---

## 🎛️ Guest Performance Profiles (Optional)

These are **optional shell scripts that run inside the macOS guest** to tune responsiveness. They are not part of this project and are not required — use them only if you understand what they change.

### 🔥 Blazing Profile

Optimized for **maximum UI speed** in the VM. Best for general use where you want the snappiest experience.

| What It Changes | Setting |
|----------------|---------|
| 🎞️ UI animations | Disabled (window resize, Mission Control, Dock) |
| 🪟 Transparency effects | Disabled (reduces compositing overhead) |
| 🔎 Spotlight indexing | **Disabled** (`mdutil -a -i off`) — frees CPU/IO |
| 😴 Sleep on AC power | Disabled (sleep, display sleep, disk sleep, Power Nap all off) |
| 🔄 Dock/Finder/SystemUIServer | Restarted to apply changes |

⚠️ **Trade-off:** No Spotlight search (Finder search, Siri suggestions, and in-app search won't index new files).

### 🛠️ Xcode Profile

Optimized for **development workflows** (Xcode, SourceKit, code search). Similar UI optimizations as Blazing, but keeps Spotlight alive.

| What It Changes | Setting |
|----------------|---------|
| 🎞️ UI animations | Disabled (same as Blazing) |
| 🪟 Transparency effects | Disabled (same as Blazing) |
| 🔎 Spotlight indexing | **Kept ON** — required for Xcode code completion and search |
| 😴 System sleep | Disabled, but display sleep is allowed (longer coding sessions) |
| 🔄 Dock/Finder/SystemUIServer | Restarted to apply changes |

⚠️ **Trade-off:** Slightly more background CPU/IO from Spotlight, but Xcode features work fully.

### 🤔 Which Profile Should I Use?

| Use Case | Profile |
|----------|---------|
| 🌐 General browsing, testing apps | **Blazing** |
| 💻 Xcode / SwiftUI / iOS development | **Xcode** |
| 🤷 Don't know / want defaults | **Neither** — skip this section |

### ▶️ Usage

```bash
# Apply blazing profile
bash scripts/profiles/apply_blazing_profile.sh

# Revert to macOS defaults
bash scripts/profiles/revert_blazing_profile.sh

# Apply xcode profile
bash scripts/profiles/apply_xcode_profile.sh

# Revert to macOS defaults
bash scripts/profiles/revert_xcode_profile.sh
```

### 🛡️ Safety Notes

- **Snapshot your VM before applying** any profile
- Apply only one profile at a time
- Always keep the matching `revert_*` script ready
- These scripts accept an optional sudo password argument — avoid storing passwords in plain text

---

## ☁️ Enable Apple Services (iCloud, iMessage, FaceTime)

Apple services require a clean, unique SMBIOS identity and stable network/time configuration.

### 📝 Setup

1. **Generate unique SMBIOS values** for this VM (do not copy from another VM):
   - `SystemSerialNumber`
   - `MLB` (Board Serial)
   - `SystemUUID`
   - `ROM` (stable 6-byte value, usually derived from NIC MAC without separators)
2. **Set all values** in your OpenCore config
3. **Verify** NVRAM is writable and persists across reboots
4. **Boot macOS** and confirm date/time are correct and network/DNS works
5. **Sign in order:** Apple ID (System Settings) first, then Messages, then FaceTime
6. **Reboot** once after login to confirm session persistence

### ✅ Checklist

- [ ] SMBIOS values are unique to this VM
- [ ] MAC address is stable (not regenerated each boot)
- [ ] Same OpenCore EFI is always used
- [ ] NVRAM reset is not triggered on every boot

### 🩺 Common Issues

| Problem | Fix |
|---------|-----|
| "This Mac cannot connect to iCloud" | Recheck serial/MLB/UUID/ROM uniqueness. Sign out, reboot, sign in again. |
| "iMessage activation failed" | Verify ROM format and stable MAC mapping. Check date/time sync. |
| Works once then breaks | VM config is regenerating SMBIOS or NIC MAC between boots. |

> **Important:** Never share SMBIOS values publicly or reuse them across VMs. Apple controls service activation and it can still fail even with correct setup.

---

## 📂 Project Layout

```
src/osx_proxmox_next/
  app.py          # TUI wizard (Textual)
  cli.py          # Non-interactive CLI
  domain.py       # VM config model + validation
  planner.py      # qm command generation
  executor.py     # Dry-run and live execution engine
  assets.py       # OpenCore/installer ISO detection
  defaults.py     # Host-aware hardware defaults
  preflight.py    # Host capability checks
  rollback.py     # VM snapshot/rollback hints
  diagnostics.py  # Log bundling + recovery guidance
  profiles.py     # VM config profile management
  infrastructure.py # Proxmox command adapter
```

---

## 🪝 Git Hooks

```bash
bash scripts/setup-hooks.sh
```

Enables pre-commit, commit-msg, and pre-push hooks for:
- **Commit message validation** — enforces [conventional commits](https://www.conventionalcommits.org/) format
- **Secret detection** — blocks hardcoded passwords, API keys, tokens
- **Code quality warnings** — flags TODO/FIXME and debug `print()` statements

---

## ⚖️ Disclaimer

This project is for **testing, lab use, and learning**. Respect Apple licensing and intellectual property. You are responsible for legal and compliance use in your region.

---

<p align="center">
  If this tool saved you time, consider supporting development:<br><br>
  <a href="https://ko-fi.com/lucidfabrics">
    <img src="https://ko-fi.com/img/githubbutton_sm.svg" alt="Support me on Ko-fi">
  </a>
  <br><br>
  ⭐ <a href="https://github.com/wmehanna/osx-proxmox-next">Star this repo</a> to help others find it.
</p>
