---
sidebar_position: 1
title: The TUI
---

# The TUI

`osx-next` opens a terminal interface built around your Macs. It starts on a gallery of the macOS VMs on the host, and every other screen is one key away. Launch it by running the install script on your Proxmox host:

```bash
bash -c "$(curl -fsSL https://raw.githubusercontent.com/lucid-fabrics/osx-proxmox-next/main/install.sh)"
```

After the first install you can open it again with `osx-next`.

## Keys

The keyboard is the main way in, and the mouse works too.

| Key | What it does |
|-----|--------------|
| Arrows, or `h` `j` `k` `l` | Move the selection (the letters only when no text field has focus) |
| `tab`, `shift+tab` | Cycle the focus |
| `enter` | Run the main action of the screen |
| `esc` | Go back or cancel. It never quits the app |
| Letters in the footer | Run the action shown there |
| `?` or `f1` | Show every key of the current screen |
| `ctrl+p` | Command palette with every action and every Mac by name |
| `q` | Quit. It asks first while a job runs in this process |

## Home

![Home: three Macs shown as cards with a live screen, and a New Mac strip below them](/img/tui/home.png)

Home lists your Macs as cards. Each card shows a live picture of the VM's screen, its name, its VM ID and a status line. The screens refresh every 5 seconds. A dashed strip below the cards starts a new Mac with the proposed defaults.

| Key | Action |
|-----|--------|
| `enter` | Open the selected Mac (a Mac that is still installing opens its Build screen) |
| `s` | Start the Mac, or shut it down |
| `e` | Edit |
| `g` | Graphics card |
| `d` | Remove |
| `n` | New Mac |
| `h` | Host check |
| `r` | Refresh |
| `v` | Switch between cards and a one-row-per-Mac list |

### Start, shut down, force stop

`s` on a running Mac asks what you want. **Shut down** asks macOS to close everything and power off. **Force stop** cuts the power at once, so anything open is lost. If a Mac is still running a minute after you asked it to shut down, the screen says so and offers Force stop. It never forces anything by itself.

### Edit

`e` opens a form for the name, cores, memory, disk, network bridge and similar settings. The VM is stopped before the change is applied. The existing MAC address and NIC model are kept when you change the bridge, and the disk can only grow.

### Remove

`d` asks you to type the VM ID before the Remove button works, and lets you choose whether the disks go with it. The focus starts on Cancel, so a stray `enter` removes nothing.

## New Mac

![New Mac: a prefilled form on the left, capacity bars for CPU, memory and disk on the right](/img/tui/new_mac.png)

New Mac is a single screen. Every row is prefilled from what the host reports, left and right change a choice and up and down move between rows. The panel on the right shows how much of the host's CPU, memory and disk the Mac would use. Press `enter` to start.

| Row | What it sets |
|-----|--------------|
| macOS | Monterey 12, Ventura 13, Sonoma 14, Sequoia 15 or Tahoe 26 |
| Name | The VM name, `macos-<version>` until you change it |
| Size | Light, Balanced, Heavy or Custom. Custom reveals cores, memory and disk. A size the host cannot fit is marked |
| Lives on | A storage target from the auto-detected pools, including NAS mounts under `/mnt/pve/` |
| Install | **Hands-off** (beta) finishes the install by itself, see [Unattended Install](./unattended-install.md). **Manual** leaves it to you in the Proxmox console |
| Apple ID | **On** adds `vmgenid`, a static MAC and PlatformInfo patching, see [Apple Services](./apple-services.md) |
| More | VM ID, bridge, VLAN tag, ISO storage, installer path, existing UUID, CPU model, NIC model, `vmgenid`, MAC, a new SMBIOS identity, and forcing a fresh download |

:::warning
macOS needs a power-of-2 core count. The presets use one. If you pick Custom, stay with 2, 4, 8 or 16: values such as 6 or 12 can hang the kernel at the Apple logo.
:::

:::note
Xeon and pre-Skylake Intel CPUs are detected automatically. Xeon keeps `-cpu host`, and older consumer Intel gets Penryn mode with an `e1000` NIC. The **CPU model** row under **More** shows what was detected.
:::

Errors appear in a fixed area on the screen and move the focus to the first row that needs attention.

## Build

![Build: a phase timeline on the left and the live screen of the VM on the right](/img/tui/build.png)

`enter` on New Mac starts one pipeline with five phases:

1. **Check this host.** The same checks as the Host check screen.
2. **Download macOS.** Fetches OpenCore parts and the recovery image if they are missing. Images are cached, so a second Mac with the same macOS version skips this.
3. **Rehearse.** Every `qm` command runs as a dry run first. Nothing on the host changes before the rehearsal has passed.
4. **Create VM.** Runs `qm create`, builds the boot disk, imports the disks, sets the boot order and starts the VM.
5. **Install macOS.** Drives the installer when Install is on Hands-off. Otherwise the VM is ready for you to continue in the console.

The live screen of the VM sits on the right from the moment the VM exists. `l` shows the full log, `z` zooms the screen, `r` retries a phase that failed and `h` opens Host check. Once the hands-off install is running, the screen says it is safe to close: reopen osx-next later and the Mac opens on this screen again.

## Post-Install

Run this as soon as the installer reboots the VM for the first time, not only once macOS is fully installed:

```bash
osx-next-cli post-install --vmid <id> --execute
```

It detaches recovery, sets boot order `ide0;virtio0`, and restores the picker's 15 second auto-boot. Until you run it, every reboot stops at the picker with recovery listed first, so the install only resumes if you pick the installer by hand.

---

## Host check

![Host check: failing checks first with a sentence each, then one line for the checks that passed](/img/tui/host_check.png)

Press `h` on Home to see the state of the host. Failing checks come first, each with one sentence on why it matters, followed by one line for the checks that passed.

| Check | What It Verifies |
|-------|-----------------|
| CPU vendor | Intel or AMD detected, applies correct config |
| Virtualization | VT-x / AMD-V enabled |
| Root access | Running as root on Proxmox |
| Dependencies | Required packages installed |
| TSC flags | `constant_tsc` and `nonstop_tsc` for clock stability |
| `ignore_msrs` | Set in `/etc/modprobe.d/kvm.conf` and live in sysfs |

Where a safe fix exists, `f` shows the exact commands, asks for confirmation, runs them with live output and checks again. Checks without a safe fix only explain the problem. `r` checks again.

## Graphics card

![Graphics card: choose the card and the Mac, with the plan for this host listed on the right](/img/tui/gpu_choose.png)

Press `g` on a Mac to attach a graphics card. You choose the card and the Mac once and approve the whole plan with one `enter`. `p` lists every change in plain words, `x` inside that list shows the exact commands.

![A restart box listing the VMs the restart shuts down and the VMs that would take the card](/img/tui/gpu_restart_onboot.png)

The host restarts once. The box lists every running VM that the restart shuts down. After the restart the setup finishes by itself the next time you open osx-next, and Home shows a banner until then. `u` puts everything back. See [GPU passthrough](./gpu-passthrough.md) for the details, the BIOS screen and the supported cards.

## Command palette and help

`ctrl+p` lists every screen and action, plus every Mac by name, so you can jump straight to one. `?` shows the keys of the screen you are on.

:::note
The standalone bash script `osx-proxmox-next.sh` keeps its whiptail menus. This page describes the Python TUI.
:::
