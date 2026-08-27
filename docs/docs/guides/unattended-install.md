---
sidebar_position: 8
title: Unattended Install (Beta)
---

# Unattended Install (Beta)

The unattended driver finishes the macOS install for you. It waits for the OpenCore boot picker, boots recovery, **erases the VM disk**, runs `startosinstall`, and answers the picker on every reboot until macOS sits at Setup Assistant.

Nothing is installed inside the VM to make this work. The driver watches the console with `qm screendump` and types with `qm sendkey`, so it works on a stock Proxmox host.

:::warning
This is beta, and it **erases the target VM disk** without asking. Point it at a freshly created VM, never at one that holds data. Leave the VM console alone while it runs.
:::

## Entry Points

| Surface | How to start it |
|---------|-----------------|
| TUI wizard | Step 4 (VM Configuration): tick **Unattended install (BETA)** |
| CLI | `osx-next-cli install-unattended --vmid 910` |
| Bash script | Answer **yes** to the `UNATTENDED INSTALL (BETA)` prompt in advanced settings |

The bash prompt only appears when you also chose to start the VM after creation.

### CLI

Run it right after `apply --execute`, while the VM is running and sitting at the boot picker.

```bash
osx-next-cli apply --execute \
  --vmid 910 --name macos-sequoia --macos sequoia \
  --cores 8 --memory 16384 --disk 128 \
  --bridge vmbr0 --storage local-lvm

osx-next-cli install-unattended --vmid 910
```

| Flag | Type | Required | Description |
|------|------|----------|-------------|
| `--vmid` | int | Yes | VM ID to drive |
| `--disk-gb` | int | No | Target disk size in GB (default: read `virtio0` from the VM config) |

The command refuses to start if the VM is not running, or if the disk size cannot be read and `--disk-gb` was not given (exit code `2`).

### TUI

When the checkbox is ticked, the wizard spawns the driver as a detached process after the VM is created, so it keeps running when you close the TUI. Progress goes to a log file:

```bash
tail -f /var/log/osx-next-unattended-910.log
```

## How It Works

| Phase | What the driver does |
|-------|----------------------|
| Wait for picker | Polls the frame size until a 1080p OpenCore picker frame appears |
| Boot recovery | Presses `ret` on the first picker entry, waits for a smaller macOS frame |
| Settle | Sleeps while the recovery utilities window finishes loading |
| Open Terminal | Walks the menu bar with `ctrl-f2` and arrow keys to Utilities, Terminal |
| Erase and install | Types one line that erases the disk and launches the installer |
| Detach recovery | On the installer's first reboot: stops the VM, `qm set --delete ide2`, starts it again |
| Answer reboots | Presses `ret` on every later 1080p frame, counting boots |
| Detect done | Returns once no picker frame has appeared for the quiet period |

### Why recovery gets detached

At the installer's first reboot the picker would otherwise list both the recovery volume and the installer. Walking entries blind with `right`, `right`, `ret` proved flaky: one dropped keystroke boots recovery again and the install stalls with no error. Detaching `ide2` leaves exactly one real entry, so a blind `ret` can never pick the wrong volume.

### The command it types

The target disk is matched by the size `diskutil` prints for it, never by a hardcoded `/dev/disk` node:

```bash
d=$(diskutil list | awk '/\*137.4 GB/{print $NF; exit}') && \
  diskutil eraseDisk APFS MACOS $d && \
  "/Install macOS "*.app/Contents/Resources/startosinstall \
    --agreetolicense --volume /Volumes/MACOS --nointeraction
```

The size in that pattern is derived from `--disk-gb`, since QEMU sizes are binary GiB and `diskutil` prints decimal GB (128 GiB shows as 137.4 GB).

### Frame-size heuristic

The driver tells screens apart by the byte size of the screendump, not by reading pixels:

| Screen | Resolution | Frame size |
|--------|-----------|------------|
| OpenCore picker | 1920x1080 | above 5,000,000 bytes |
| Recovery, installer, Setup Assistant | 1280x800 | below 4,500,000 bytes |

## Timeouts

Every phase is time-bounded. The Python and bash implementations share the same values, and `tests/test_unattended.py` diffs them so they cannot drift apart.

| Python constant | Bash constant | Value | Purpose |
|-----------------|---------------|-------|---------|
| `PICKER_TIMEOUT` | `UNATTENDED_PICKER_TIMEOUT` | 600 s | Wait for the OpenCore picker |
| `RECOVERY_TIMEOUT` | `UNATTENDED_RECOVERY_TIMEOUT` | 900 s | Wait for recovery to start |
| `RECOVERY_SETTLE` | `UNATTENDED_RECOVERY_SETTLE` | 240 s | Let the utilities window load |
| `INSTALL_REBOOT_TIMEOUT` | `UNATTENDED_INSTALL_REBOOT_TIMEOUT` | 1800 s | Wait for the installer's first reboot |
| `DONE_QUIET` | `UNATTENDED_DONE_QUIET` | 900 s | No picker for this long means the install is done |
| `TOTAL_BUDGET` | `UNATTENDED_TOTAL_BUDGET` | 10800 s | Hard ceiling on the whole run |
| `POLL_INTERVAL` | `UNATTENDED_POLL` | 6 s | Screendump polling interval |

## Verified Versions

Verified end to end on Ventura 13, Sonoma 14, Sequoia 15 and Tahoe 26, on both Intel and AMD hosts. A run takes 30 to 60 minutes.

## When a Phase Times Out

The driver stops and leaves the VM exactly where it is, so you can take over on the Proxmox console. The CLI prints which phase failed and exits `1`.

| Symptom | Likely cause |
|---------|--------------|
| Picker never appeared | VM was not at the boot picker when the driver started, or boot order is wrong |
| Recovery never started | Recovery image missing or attached to a slot other than `ide2` |
| Installer never rebooted | The erase step failed, usually a `--disk-gb` value that does not match the real disk |
| Budget exhausted | Install is genuinely slower than 3 hours; finish it by hand on the console |

## After It Finishes

Complete Setup Assistant on the VM console, then lock in the boot order:

```bash
osx-next-cli post-install --vmid 910 --execute
```

:::note
The driver already detaches recovery at the first reboot, so `post-install` afterwards sets the OpenCore-first boot order (`ide0;virtio0`) and restores the picker's 15 second auto-boot, which the boot image disables during install.
:::
