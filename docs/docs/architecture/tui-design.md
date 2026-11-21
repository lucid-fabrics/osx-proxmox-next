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
- `enter` runs the primary action of the screen. `esc` goes back or cancels, and never quits the app.
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

Pressing `enter` on a card opens a large live screen of that Mac with its facts from `qm config` and the same action keys as Home. A Mac with an install in progress opens the Build screen instead. When you ask a Mac to shut down and it is still running a minute later, the screen says so and offers Force stop, but it never forces anything by itself.

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

In the first moment you choose the card and the Mac and approve the whole plan once. The right panel lists what it takes on this host, with a glyph for what is already right and what is still to do. A "Before you continue" dialog shows the warnings, the NVIDIA notice and the phrases you have to type. Because this is the only approval, the dialog lists every warning of the plan that will actually run.

![Graphics card](/img/screenshots/tui-gpu.png)

If a BIOS setting has to change, a screen titled "One thing only you can do" lists the settings with their menu path and a QR code, offers to restart into the BIOS and checks again. A host whose BIOS is already right never sees it.

![BIOS screen](/img/screenshots/tui-gpu-bios.png)

In the second moment the pipeline runs: back up the files, set up the host, restart, check the card is free, attach it to the Mac and start it. The restart step shows what a restart affects right now, including any Mac with an install in progress. After the restart the flow continues without asking again, unless the plan no longer matches what was approved.

In the third moment you confirm the first boot. AMD cards ask whether it worked and offer troubleshooting if it did not. NVIDIA cards finish with Done. "Put everything back" is available throughout and asks before it does anything.
