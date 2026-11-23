---
sidebar_position: 4
title: TUI Design Rules
---

# TUI Design Rules

This page describes how the terminal interface is laid out and why, so that a new screen or dialog looks and behaves like the rest. The interface is built with Textual and every screen is a real `Screen` or `ModalScreen` in `src/osx_proxmox_next/screens/`.

## Colours

All colours come from one Textual theme named `osx-next`. The stylesheet uses theme variables only, never a literal hex value.

| Role | Value |
| --- | --- |
| background | `#0b0f14` |
| surface (top bar, footer bar) | `#121821` |
| raised (selected row or card) | `#1a2330` |
| border | `#2a3645` |
| text | `#e6edf3` |
| muted text | `#8b98a8` |
| accent | `#4da3ff` |
| on-accent (text on an accent background) | `#06121f` |
| success | `#3fb950` |
| warning | `#d29922` |
| error | `#f85149` |

## Visual rules

- Inputs and buttons are one row high. There are no three-row bordered boxes.
- The top bar and the footer bar use the surface colour. A selected row or card uses the raised colour with an accent edge.
- The primary button has an accent background with bold on-accent text. The danger button has an error background with bold text in the background colour.
- Accent is for focus, selection, key letters and the primary action only. Green, amber and red are for status only.
- Colour is never the only signal. Every status has a glyph: `✓` done, `✗` failed, `●` running or to do, `○` stopped, `◐` in progress, `·` separator, `!` warning.
- Wrapped text in numbered steps and in setting rows keeps a hanging indent under the start of the text.
- In compact lists a row is exactly one row high, with the name in the normal text colour. Only the glyph and the status word carry the status colour.

## Keyboard model

The interface is keyboard first, and the mouse works everywhere too.

- Arrows move the selection, and `h j k l` do the same where no text field has focus. `tab` and `shift+tab` cycle the focus.
- `enter` runs the primary action of the screen. `esc` goes back or cancels, and never quits the app. The footer never promises an enter action that the focused button does not do: a question that destroys something (Remove, Put everything back) keeps the focus on Cancel, and its footer says `enter Choose`.
- Single letters run the actions listed in the footer. They never fire while a text field has focus.
- `?` opens a help overlay with every key of the current screen. `f1` does the same and also works inside text fields.
- `q` quits. While a job runs in this process (creating a VM, a host fix, a graphics card change) it asks first.
- `ctrl+p` opens the command palette with every action and every Mac by name.
- Every screen shows its keys in a one-row footer. Keys that a browser tab takes (`ctrl+w`, `ctrl+t`, `ctrl+n`, `f5`, `f11`) are not bound, because many people use the Proxmox web shell.
- Focus is always visible and lands on something sensible when a screen opens.

## Screens

### Home

Home is the list of your Macs. Each Mac is a card with a live thumbnail of its screen, its name, its VM ID and a status line. On a narrow terminal, or after pressing `v`, the cards become one-row list entries. A dashed strip under the cards starts a new Mac with the proposed defaults. A banner reminds you when a graphics card setup is waiting for you.

![Home](/img/screenshots/tui-home.png)

### VM view

Pressing `enter` on a card opens a large live screen of that Mac with its facts from `qm config` and the same action keys as Home. A Mac with an install in progress opens the Build screen instead. When you ask a Mac to shut down and it is still running a minute later, the screen says so and offers Force stop, but it never forces anything by itself. The request is recorded the moment you ask, so the card says shutting down at once, and Force stop works while `qm shutdown` still holds the VM lock.

![VM view](/img/screenshots/tui-vm-view.png)

### New Mac

New Mac is a single screen, not a wizard. Every row is prefilled from what the host reports, and left and right change a choice. The panel on the right shows how much of the host's CPU, memory and disk the Mac would use, and a short summary of what happens when you press `enter`. Errors appear in a fixed two-row area and move the focus to the first row that needs attention.

![New Mac](/img/screenshots/tui-new-mac.png)

### Build

Build is one pipeline: check the host, download macOS, rehearse every step, create the VM, install macOS, and ready. Nothing on the host changes before the rehearsal has passed. The live screen of the VM sits on the right from the moment the VM exists. Once the hands-off install starts, the screen says it is safe to close, and opening the Mac later returns to this screen.

![Build](/img/screenshots/tui-build.png)

### Host check

Host check lists the failing checks first, each with one sentence on why it matters, and then one line for the checks that passed. When a safe automatic fix exists, `f` shows the exact commands, asks for confirmation, runs them with live output and checks again. Checks without a safe fix only explain the problem.

![Host check](/img/screenshots/tui-host-check.png)

### Graphics card

Passing a graphics card to a Mac is shown as three moments instead of a long walkthrough.

In the first moment you choose the card and the Mac and approve the whole plan once. The right panel lists what it takes on this host, with a glyph for what is already right and what is still to do. A "Before you continue" dialog shows the warnings, the NVIDIA notice and the phrases you have to type. Because this is the only approval, the dialog lists every warning of the plan that will actually run, including the one for other VMs that have the card in their config, and "Set it up" there is the single approval: nothing else opens between the choose screen, that dialog and the run screen. A VM that has the card and starts with the host (Start at boot) gets its own warning, because after the restart it takes the card before the Mac can. Devices the tool does not offer are one muted "Not offered" line at the end of the Card block, never a note under the chosen card. `p` shows every change in plain lines (one sentence per VM step); `x` switches to the exact commands.

![Graphics card](/img/screenshots/tui-gpu.png)

If a BIOS setting has to change, a screen titled "One thing only you can do" lists the settings with their menu path and a QR code, offers to restart into the BIOS and checks again. A host whose BIOS is already right never sees it.

![BIOS screen](/img/screenshots/tui-gpu-bios.png)

In the second moment the pipeline runs: back up the files, set up the host, restart, check the card is free, attach it to the Mac and start it. The run screen is up the moment you approve, the step that works shows `◐` and a running time, and keys keep working while it runs. A step gets `✓` only when its check passed; a refused step shows `✗` with the reason in a few words and the steps after it stay `·`. The backup step counts only files that existed ("Nothing to back up (2 new files)"). The restart box lists every running VM that the restart shuts down, by name and ID, and each VM that starts with the host and will take the card. It is the confirmation: the focus starts on "Restart now", enter restarts (ignored for the first second, so keys typed ahead cannot restart the host) and no second question follows. After the restart the flow continues without asking again, unless the plan no longer matches what was approved. When another VM is running with the card, the box names it ("VM 103 is using the card"), offers Leave and Check again, and the tool never starts, stops or edits any VM but the Mac it is setting up. On Home the banner says "Restart done" once the host has restarted.

In the third moment you confirm the first boot. AMD cards ask whether it worked and offer troubleshooting if it did not. NVIDIA cards finish with Done. "Put everything back" is available throughout. It asks first and lists what it will do, with the same words as its result: "Remove" for a file the setup created, "Restore" for one it changed, "Take the card off" a Mac, and whether the host needs a restart afterwards.

### Driver

`g` on a Mac that already has a card (a graphics card: PCI class 0x03, not a USB controller or a NIC) opens the same Graphics card screen in its attached state, with a Driver block instead of the choice of a card. The block never wraps at 100x30 or 80x24: a `Driver` row holds the installed version (`NullMoth v2.8.1`, one row per component for Intel and AMD, `installed version unknown` in muted, with a line that says why when a manual rollback made the record untrue; NullMoth leaves its version on the Mac's OpenCore disk, so a rolled back NVIDIA Mac still shows it) and its own `Status` row holds the short form, `v2.9 available` in accent or `✓ up to date` in success. A Version row (`‹ v2.9 (latest) ›`, the newest ten releases, latest first) is shown for NVIDIA; Intel has two rows, `Version` (WhateverXe) and `Package` (the graphics package has its own release), and `↑ ↓` moves between them; AMD shows the versions this release pins. The right panel lists the facts of the approval with a glyph each, and the last line says which older driver snapshot is removed after a good update. The footer gains `enter Update driver` (`Reinstall` when the Mac is up to date and the Version row was moved, `Finish` when an earlier update stopped), `←→ Version`, `p See every change`, and `r Undo driver update` (only while a driver snapshot of this Mac exists). `u` is not on this screen: it undoes the card setup for every Mac that has the card, so that undo stays in the Graphics card flow and the Mac's own view, where it keeps its key and label. An up to date Mac shows no button: the action row says `Nothing to update.` and the footer keeps `p`, `r` and `esc`. The snapshot line reads `✓ A snapshot is saved first, so the update can be undone.` before any update ran and `✓ Driver snapshot of 2026-11-21 10:30. r undoes the driver update.` after. Enter opens "Before you continue" without the "asked once, never again" tag (a driver update is approved every time): how long the Mac stops, that the card comes off and goes back (NVIDIA and Intel), the snapshot name, the NullMoth notice for NVIDIA and the typed phrase `no snapshot` when the storage cannot snapshot. `r` asks "Undo the driver update?": the Mac stops, its disks go back to the snapshot of that date, and everything done in macOS since is lost, with Cancel focused.

An update that stopped keeps its place: the attached screen says `! The driver update stopped before it finished. The card is off this Mac. enter finishes it, r undoes the driver update.` (the card sentence only while the card is off), hides the version picker and shows `Finish`. It shows only that state: the Status row says `update stopped` (never `update available`), the right panel is titled `What Finish does` and says that no new snapshot is saved (never `A snapshot called ... is saved first`), and the snapshot line says which snapshot undoes it or that the update has none. A Mac that runs shows its Intel or AMD versions from the record under a muted `(from the last update)` line, because the OpenCore disk is only read while the Mac is stopped. `g` opens this screen for such a Mac even when it has no card right now.

The run screen reuses the card setup's step list: `◐` with a running time for the step at work, `✓` with its time when done, `✗` for the step that stopped and `·` for the rest. The steps are `Stopped the Mac` (only when the Mac was running: the plan and the run list the same steps), `Saved a snapshot`, `Took the card off` (NVIDIA and Intel), `Staged NullMoth v2.9` (the stage line names the driver), `Put the card back`, `Started the Mac` and `Driver installed` (shown only once the driver is verified); AMD runs `Stopped the Mac`, `Saved a snapshot`, `Replaced the kexts` and `Started the Mac`. The update runs in a detached process and the screen follows its state file every second, so a reopened terminal shows the same steps. After `Driver installed` the screen asks "Do you see the macOS desktop?" with `y` and `n` (n opens the short no-signal help) and, after a yes, offers "Remove the driver snapshot?" with Keep as the default. The line under the box says `Not happy with it? r undoes the driver update.` The state text follows the state: a run that stopped says `The update stopped. Nothing is running.` instead of `Safe to leave: the update keeps running.`, and a Finish that has not written its first state yet says `Starting...` instead of the failure the last run left and draws a fresh step list (what the run before finished stays `✓` without a time, the step that failed is `·` again). On a failed run enter finishes it, as the box says, and the footer lists `enter Finish`; `p` then shows what Finish does. `?` lists only the keys that work in the state on screen: `y` and `n` only at the first boot question, `r` only while a snapshot exists. The title names the card (`Updating the NVIDIA driver of macos-sequoia`).

`r` shows its own progress screen from the first second, with the steps `Stopped the Mac`, `Released the disks`, `Put the snapshot back` and `Started the Mac` drawn like the update (`◐ ✓ ✗ ·`). A failed undo shows an error screen in the same box (`The undo did not finish`, with the exact command when a device map still holds a disk) and enter goes back to the driver screen. After a TUI-launched update, a mode change never leaves the keyboard focus on a widget the screen removed, so `r`, `y`, `n`, `p` and `esc` keep working. The first boot question is only asked after an update that this screen started: a command line update clears its state when it is done.

Home shows `● driver update` in accent in the status line of a Mac with a newer driver (the card view replaces the cores and memory with it: `○ stopped · ● driver update` is 27 cells, the whole width of a card's text; the list shows it after the cores and memory). The marker also shows before the first daily check ran, when the Mac's record says a newer release was known (the update that wrote it stored what it knew as newest, so the marker survives an undo). A Mac whose update stopped shows `● driver update stopped` in the warning colour instead of its state. The marker reads local files only; the newest releases are asked from GitHub by a background worker at most once a day and never while no Mac has a driver record.

## Recording the demo

The README demo (`docs/static/img/demo-tui.gif`) is a recording of the real interface running against a fake host, so it needs no Proxmox server. `scripts/demo/run_demo.py` starts `NextApp` with the test fakes answering every host lookup (a Radeon RX 6800 at 03:00, two Macs, a build that advances one phase about every 1.5 s), and it refuses to start any process. `scripts/demo/tui.tape` types the walk through Home, New Mac, Build, the graphics card screens and the restart box. To record it again, install [VHS](https://github.com/charmbracelet/vhs) and run this from the repository root:

```bash
scripts/record-demo.sh
```
