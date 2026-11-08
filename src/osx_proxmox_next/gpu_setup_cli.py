"""Command line front end of the GPU walkthrough: ``osx-next-cli gpu-setup``."""

from __future__ import annotations

import argparse
from collections.abc import Callable

from . import bios_guide, gpu, host_setup, igpu_finish, nullmoth
from . import gpu_setup
from .gpu_setup import DISPLAY_OPTIONS, INTEL_DISPLAY_NOTE, NVIDIA_DISPLAY_NOTE, SUPPORTED_DOC, TROUBLESHOOTING, CardChanged, Session, card_text, render_rail

__all__ = ["add_parser", "run"]

EXIT_OK, EXIT_FAIL, EXIT_REFUSED, EXIT_BIOS, EXIT_REBOOT = 0, 1, 2, 3, 4
_ANSI_QR = "\x1b[30;47m{}\x1b[0m"


def add_parser(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser(
        "gpu-setup",
        help="Guided GPU passthrough for AMD, NVIDIA and Intel iGPU: scan, BIOS, host setup, VM, first boot",
    )
    p.add_argument("--status", action="store_true", help="Show where the setup is and what this host needs. Changes nothing")
    p.add_argument("--undo", action="store_true", help="Restore every file and VM setting this setup changed")
    p.add_argument("--yes", action="store_true", help="Answer the confirmations (never the BIOS, reboot or risk notices)")
    p.add_argument("--vm", type=int, default=None, help="VM ID of the macOS VM that gets the card")
    p.add_argument("--macos", default="", choices=("", "ventura", "sonoma", "sequoia", "tahoe"),
                   help="macOS release the card is for (default: the --vm release, else sequoia). Decides which card is recommended")
    p.add_argument("--gpu", default="", help="PCI address of the card, e.g. 06:00 or 0000:06:00.0")
    p.add_argument("--display", choices=("gpu", "console"), default="",
                   help="AMD only: gpu = monitor on the card (vga none), console = keep the Proxmox console")
    p.add_argument("--accept-nullmoth-risk", action="store_true", dest="accept_risk",
                   help="NVIDIA: acknowledge the NullMoth notice")
    p.add_argument("--stop-vm", action="store_true", dest="stop_vm", help="Stop the VM first if it is running")


class _Io:
    def __init__(self, ask: Callable[[str], str], say: Callable[[str], None], interactive: bool) -> None:
        self._ask, self.say, self.interactive = ask, say, interactive

    def ask(self, prompt: str) -> str:
        return self._ask(prompt).strip()

    def confirm(self, prompt: str, yes: bool) -> bool:
        if yes:
            return True
        return self.interactive and self.ask(f"{prompt} [y/N] ").lower() in ("y", "yes")

    def typed(self, prompt: str, phrase: str) -> bool:
        return self.interactive and self.ask(f"{prompt} Type '{phrase}' to continue: ") == phrase


def _with_qr_colours(text: str, qr: list[str]) -> str:
    """The card with each QR row drawn dark-on-light, so a phone can read the code from the terminal."""
    rows = list(qr)
    out = []
    for line in text.split("\n"):
        for q in rows:
            at = line.find(q)
            if at >= 0 and q.strip():
                line = line[:at] + _ANSI_QR.format(q) + line[at + len(q):]
                break
        out.append(line)
    return "\n".join(out)


def _print_qr(io: _Io, lines: list[str]) -> None:
    for ln in lines:
        io.say(_ANSI_QR.format(ln))


def _print_findings(io: _Io, plan_like) -> None:
    for line in getattr(plan_like, "issues", []):
        io.say(f"REFUSED: {line}")
    for line in getattr(plan_like, "hints", []):
        io.say(f"  {line}")
    for line in getattr(plan_like, "warnings", []):
        io.say(f"WARN: {line}")


# ── steps ────────────────────────────────────────────────────────────

def _step_choose(s: Session, io: _Io, args: argparse.Namespace) -> int | None:
    io.say(s.summary_line())
    cards = s.scan(exclude_vm=args.vm if args.vm is not None else -1, macos=s.macos_for(args.vm, args.macos))
    if not cards:
        io.say("No supported graphics card found in this host.")
    for card in cards:
        io.say(card_text(card))
    usable = [c for c in cards if c.selectable]
    if not usable:
        if any(c.kind != "unsupported" for c in cards):
            io.say("No supported card can be used right now. See the 'Can't use' line on each card above, fix "
                   "that, then run the scan again.")
        else:
            io.say(f"Nothing here can run macOS graphics. Cards that work: {SUPPORTED_DOC}")
        return EXIT_REFUSED
    chosen = None
    rec = next((c for c in usable if c.recommended), None)
    if not args.gpu and rec is None:
        io.say(f"No graphics card here works with {gpu_setup.MACOS_NAMES[usable[0].macos]}. Continuing without "
               "passthrough: nothing was changed.")
        return EXIT_OK
    if not args.gpu and (rec.vendor == "intel" or len(usable) > 1):
        io.say(rec.detected_line if rec.vendor == "intel" else f"Recommended: {rec.name} ({rec.bdf}), {rec.status}.")
        if io.confirm(f"Use the {rec.name} ({rec.bdf}) for macOS?", args.yes):
            chosen = rec
        elif len(usable) == 1:
            io.say("Nothing was changed. Run again and confirm (or pass --yes) to use it. Nothing is enabled "
                   "without that confirmation" + (": the Intel iGPU takes the host console." if rec.vendor == "intel" else "."))
            return EXIT_REFUSED
        else:
            usable = [c for c in usable if c is not rec]
    if chosen is not None:
        pass
    elif args.gpu:
        wanted = gpu.normalize_bdf(args.gpu)
        chosen = next((c for c in usable if c.bdf == wanted), None)
        if chosen is None:
            io.say(f"{args.gpu} is not one of the usable cards above.")
            return EXIT_REFUSED
    elif len(usable) == 1 and usable[0].vendor == "intel":
        # switched away from the recommendation to the iGPU: it still takes the host console, so ask
        io.say(usable[0].detected_line)
        if not io.confirm(f"Use the {usable[0].name} ({usable[0].bdf}) for macOS?", args.yes):
            io.say("Nothing was changed.")
            return EXIT_REFUSED
        chosen = usable[0]
    elif len(usable) == 1:
        chosen = usable[0]
    elif io.interactive:
        for idx, c in enumerate(usable, start=1):
            io.say(f"  {idx}) {c.name} ({c.bdf})")
        pick = io.ask("Which card? ")
        chosen = usable[int(pick) - 1] if pick.isdigit() and 0 < int(pick) <= len(usable) else None
    if chosen is None:
        io.say("Pick a card with --gpu <address>.")
        return EXIT_REFUSED
    share_ok = False
    if chosen.stopped_users:
        for note in chosen.notes:
            io.say("WARN: " + note)
        share_ok = io.confirm("Use this card anyway?", args.yes)
        if not share_ok:
            io.say("Nothing was changed. Run again with --yes (or answer y) to use a card that another stopped VM "
                   "also lists.")
            return EXIT_REFUSED
    vmid = args.vm
    if vmid is None and io.interactive:
        options = s.vm_options(chosen.vendor)
        io.say("macOS VMs made by this tool:" if options else "No macOS VM found.")
        for opt in options:
            io.say("  " + opt.label)
        reply = io.ask("VM ID to use ('p' = set up the host now and pick the VM later, 'c' = create one first): ")
        if reply.lower() == "c" and chosen.vendor == "intel":
            io.say("The iGPU needs an i440fx VM, so create it with: osx-next-cli igpu-install (it installs macOS "
                   "and the graphics package too). It sets the host up first if needed.")
            reply = "p"
        if reply.lower() == "c":
            s.state["gpu"] = {"bdf": chosen.bdf, "vendor": chosen.vendor, "device": chosen.device_id,
                              "boot_vga": chosen.boot_vga, "name": chosen.name,
                              "identity": host_setup.identity_of(s.root, chosen.bdf)}
            io.say("Create the VM with 'osx-next' (menu: Create VM), then run: " + s.pause())
            return EXIT_OK
        vmid = int(reply) if reply.isdigit() else None
    display = args.display or "gpu"
    if chosen.vendor == "amd" and not args.display and io.interactive:
        io.say("Display:")
        for idx, (_value, label, desc) in enumerate(DISPLAY_OPTIONS, start=1):
            io.say(f"  {idx}) {label}\n     {desc}")
        display = "console" if io.ask("Choice [1]: ") == "2" else "gpu"
    ack = False
    if chosen.vendor == "intel":
        io.say(INTEL_DISPLAY_NOTE)
    if chosen.vendor == "nvidia":
        io.say(NVIDIA_DISPLAY_NOTE)
        io.say(nullmoth.notice_text())
        ack = args.accept_risk or io.typed(nullmoth.ACK_LABEL, nullmoth.ACK_PHRASE)
    issues = s.choose(chosen.bdf, vmid, display, ack, share_ok, confirmed=chosen.stopped_users if share_ok else ())
    for issue in issues:
        io.say("REFUSED: " + issue)
    return EXIT_REFUSED if issues else None


def _step_bios(s: Session, io: _Io, args: argparse.Namespace) -> int | None:
    facts = s.facts()
    items = s.bios_items(facts)
    data = bios_guide.load_data()
    for item in items:
        label = data["settings"][item["key"]]["label"]
        io.say(f"  [{'ok' if item['ok'] else '!!'}] {label}" + ("" if item["ok"] else " - " + item["detail"]))
    card = s.bios_card(items)
    if card is None:
        io.say("Nothing to change in the BIOS.")
        s.complete(3)
        return None
    if s.state.get("bios_seen"):
        io.say("These are still off. Here is where they usually hide:")
    s.state["bios_seen"] = True
    io.say(_with_qr_colours(card["text"], card["qr"]))
    io.say(f"Saved to {card['path']} (print it or cat it from another machine).")
    running = s.running_vms()
    if io.interactive and not args.yes:
        io.say("1) Reboot into the BIOS now   2) I'll do it later   3) Back")
        pick = io.ask("Choice [2]: ") or "2"
        if pick == "3":
            s.goto(2)
            return EXIT_OK
        if pick == "1" and card["firmware_reboot"]:
            if running:
                io.say("These VMs will be shut down by the reboot: " + ", ".join(f"{i} ({n})" for i, n in running))
            if io.confirm("Reboot into the BIOS now?", False):
                ok, out = s.reboot_to_bios()
                io.say("Rebooting..." if ok else "Could not start the reboot: " + out)
                return EXIT_OK if ok else EXIT_FAIL
        elif pick == "1":
            io.say("This firmware does not allow 'reboot into setup'. Reboot and press the setup key yourself.")
    io.say("When you are back in Proxmox, run: " + s.pause())
    return EXIT_BIOS


def _step_host(s: Session, io: _Io, args: argparse.Namespace) -> int | None:
    plan = s.host_plan()
    no_console = False
    if plan["refusals"] and io.interactive and any("only display adapter" in r for r in plan["refusals"]):
        io.say("Careful: " + plan["refusals"][0])
        if io.typed("This card is the only display the host has.", "I understand"):
            no_console = True
            plan = s.host_plan(allow_no_console=True)
    for line in plan["refusals"]:
        io.say("REFUSED: " + line)
    if plan["refusals"]:
        return EXIT_REFUSED
    for key, prefix in (("notes", "Note: "), ("warnings", "WARN: "), ("manual", "Manual step: "), ("skipped", "OK: ")):
        for line in plan[key]:
            io.say(prefix + line)
    if plan.get("optional") and not s.plan_needs_work(plan) and not s.state.get("verify_retry") \
            and io.interactive and not args.yes \
            and io.confirm("Write the boot-time binding anyway? (optional)", False):
        plan = s.host_plan(allow_no_console=no_console, write_optional=True)
        for key, prefix in (("refusals", "REFUSED: "), ("notes", "Note: "), ("warnings", "WARN: "),
                            ("manual", "Manual step: ")):
            for line in plan[key]:
                io.say(prefix + line)
        if plan["refusals"]:
            return EXIT_REFUSED
    if not s.plan_needs_work(plan) and s.state.get("verify_retry"):
        s.state["verify_retry"] = False
        s.save()
        io.say("The verify step found a problem, but there is nothing left for this tool to change. Fix the item(s) "
               "marked !! in the verify list (see the docs), then run: " + s.pause())
        return EXIT_REFUSED
    if not s.plan_needs_work(plan):
        s.complete(4, skipped=True)
        if 5 not in s.state["done"]:
            s.complete(5, skipped=True)
        return None
    if not plan["changes"] and not plan["commands"]:
        io.say("Nothing to write; follow the manual step above, then run: " + s.pause())
        return EXIT_REFUSED
    io.say("This is exactly what will change:\n" + host_setup.render_preview(plan))
    confirmed = False
    for hard in plan["hard_warnings"]:
        io.say("HARD WARNING: " + hard)
        if not io.typed("", "I understand"):
            io.say("Nothing was changed.")
            return EXIT_REFUSED
        confirmed = True
    if not io.confirm("Apply these changes? Each file is backed up first.", args.yes):
        io.say("Nothing was changed.")
        return EXIT_OK
    res = s.host_apply(plan, confirmed)
    if not res["ok"]:
        io.say(f"FAILED: {res['error']}" + (" The files were put back." if res["rolled_back"] else ""))
        return EXIT_FAIL
    io.say(f"Done. Backups are in {res['backup_dir']}. Undo everything with: gpu-setup --undo")
    return None


def _step_reboot(s: Session, io: _Io, args: argparse.Namespace) -> int | None:
    if not s.state.get("reboot_needed"):
        s.complete(5, skipped=True)
        return None
    if not s.reboot_pending():
        io.say("The host has rebooted since the changes were made.")
        return None
    running = s.running_vms()
    io.say("The host must reboot so the new kernel arguments and the early card binding take effect.")
    if running:
        io.say("These VMs will be shut down: " + ", ".join(f"{i} ({n})" for i, n in running))
    if io.interactive and not args.yes and io.confirm("Reboot now?", False):
        ok, out = s.reboot_host()
        io.say("Rebooting..." if ok else "Could not start the reboot: " + out)
        return EXIT_OK if ok else EXIT_FAIL
    io.say("Reboot when it suits you, then run: " + s.pause())
    return EXIT_REBOOT


def _step_verify(s: Session, io: _Io, args: argparse.Namespace) -> int | None:
    checks = s.verify()
    for c in checks:
        io.say(f"  [{ {'ok': 'ok', 'fail': '!!', 'warn': '??', 'info': '..'}[c['status']] }] {c['label']}" + (f" - {c['detail']}" if c["detail"] else ""))
    fails = [c for c in checks if c["status"] == "fail"]
    if not fails:
        return None
    if any("Reboot the host" in c["detail"] for c in fails) and s.rebooted_since_apply():
        io.say("The host has rebooted but the kernel arguments are still not active. Check that the boot loader picked "
               "them up (GRUB: /boot/grub/grub.cfg, systemd-boot: proxmox-boot-tool refresh), then run: " + s.pause())
        return EXIT_REFUSED
    if any("Reboot the host" in c["detail"] for c in fails):
        s.mark_reboot_needed()
        io.say("The change is saved but not active yet. Reboot, then run: " + s.pause())
        return EXIT_REBOOT
    fix = fails[0]["fix"]
    if fix == "scan":
        raise CardChanged(fails[0]["detail"])
    if fix in ("bios", "host"):
        s.send_back(3 if fix == "bios" else 4)
        return None
    io.say("Fix the item(s) marked !! above and run: " + s.pause())
    return EXIT_REFUSED


def _step_vm(s: Session, io: _Io, args: argparse.Namespace) -> int | None:
    if s.state["vm"] is None and args.vm is not None:
        issues = s.check_vm(args.vm, s.state["gpu"]["vendor"])
        for issue in issues:
            io.say("REFUSED: " + issue)
        if issues:
            return EXIT_REFUSED
        s.state["vm"] = args.vm
        s.save()
    if s.state["vm"] is None and s.state["gpu"]["vendor"] == "intel":
        io.say("The host is ready. Create the VM with: osx-next-cli igpu-install --vmid <id> --name <name> --macos "
               "sequoia --cores 4 --memory 8192 --disk 64 --bridge vmbr0 --storage local-lvm. It installs macOS and "
               "the graphics package hands-off. Or pick an installed i440fx macOS VM: " + s.pause() + " --vm <vmid>")
        return EXIT_OK
    if s.state["vm"] is None:
        io.say("The host is ready. Pick or create the macOS VM, then run: " + s.pause() + " --vm <vmid>")
        return EXIT_OK
    plan = s.vm_plan(allow_stop=args.stop_vm)
    _print_findings(io, plan)
    if not plan.ok:
        return EXIT_REFUSED
    if s.state["gpu"]["vendor"] == "nvidia":
        io.say(nullmoth.GUEST_PREP_TITLE + ":")
        for idx, line in enumerate(nullmoth.GUEST_PREP, start=1):
            io.say(f"  {idx}. {line}")
        if not io.confirm(nullmoth.GUEST_PREP_CONFIRM, args.yes):
            io.say("Nothing was changed. Turn them on in macOS, then run: " + s.pause())
            return EXIT_REFUSED
    io.say("This is what will change on VM %s (a snapshot of its config is saved first):" % s.state["vm"])
    for idx, step in enumerate(plan.steps, start=1):
        io.say(f"  {idx:02d}. {step.title}")
    if s.state["gpu"]["vendor"] == "intel":
        io.say("Before that, the VM boots macOS recovery once to put the graphics package inside macOS, "
               "so the first boot with the iGPU installs it by itself. This takes about 15 minutes.")
    if not io.confirm("Attach the card now?", args.yes):
        io.say("Nothing was changed.")
        return EXIT_OK
    if s.state["gpu"]["vendor"] == "intel":
        return _finish_intel(s, io)
    from .executor import apply_plan
    from .rollback import create_snapshot
    if s.state["gpu"]["vendor"] == "nvidia":
        from pathlib import Path
        from .defaults import DEFAULT_ISO_DIR
        from .oc_builder import ensure_amfipass
        ensure_amfipass(Path(DEFAULT_ISO_DIR))
    create_snapshot(s.state["vm"])
    s.begin_attach()
    result = apply_plan(plan.steps, execute=True)
    if not result.ok:
        failed = next((r for r in result.results if not r.ok), None)
        io.say("FAILED" + (f" at step: {failed.title}" if failed else "") + f". Log: {result.log_path}")
        return EXIT_FAIL
    s.vm_applied()
    io.say("Card attached.")
    return None


def _finish_intel(s: Session, io: _Io) -> int | None:
    """Stage the graphics package, attach the iGPU and run the first boot (igpu_finish does all of it)."""
    s.begin_attach()
    try:
        result = igpu_finish.finish(s.state["vm"], pve=s.pve, on_event=lambda m: io.say("  " + m))
    except igpu_finish.FinishError as exc:
        io.say("FAILED: " + str(exc))
        return EXIT_FAIL
    for note in result.notes:
        io.say("NOTE: " + note)
    if not result.installed:
        io.say("The graphics package is not installed yet (" + (result.result or "no result") + "). The VM is usable "
               "without acceleration and tries again on its next boot.")
    s.vm_applied()
    io.say("Card attached.")
    return None


def _step_first_boot(s: Session, io: _Io, args: argparse.Namespace) -> int | None:
    io.say(s.first_boot_text())
    _print_qr(io, s.first_boot_qr())
    if s.state["gpu"]["vendor"] == "amd" and io.interactive and not args.yes:
        io.say("Start the VM now (qm start %s)." % s.state["vm"])
        while not io.confirm("Do you see the macOS desktop on your monitor?", False):
            io.say("1) No signal  2) Black screen after the Apple logo  3) Works once, then fails after a VM restart  4) Stop")
            pick = io.ask("Which? ")
            key = {"1": "no_signal", "2": "black_after_logo", "3": "once_per_boot"}.get(pick)
            if key is None:
                io.say("Come back with: " + s.pause())
                return EXIT_OK
            io.say(TROUBLESHOOTING[key])
    s.finish()
    io.say(s.summary())
    return None


_HANDLERS = {1: _step_choose, 2: _step_choose, 3: _step_bios, 4: _step_host, 5: _step_reboot,
             6: _step_verify, 7: _step_vm, 8: _step_first_boot}


def _status(s: Session, io: _Io) -> int:
    io.say(s.rail())
    io.say(s.summary_line())
    cards = s.scan()
    for card in cards:
        io.say(card_text(card))
    if not cards:
        io.say("No supported graphics card found.")
    if not s.state["gpu"]:
        io.say("No setup in progress.")
        return EXIT_OK
    io.say(f"Chosen card: {s.state['gpu']['name']} ({s.state['gpu']['bdf']}), VM: {s.state['vm']}")
    try:
        s.target()
    except CardChanged as exc:
        io.say("WARN: " + str(exc))
        return EXIT_REFUSED
    items = s.bios_items()
    data = bios_guide.load_data()
    for item in items:
        io.say(f"  BIOS [{'ok' if item['ok'] else '!!'}] {data['settings'][item['key']]['label']}")
    for c in host_setup.verify_host(s.target(), s.root, s.run):
        io.say(f"  Verify [{c['status']}] {c['label']}" + (f" - {c['detail']}" if c["detail"] else ""))
    plan = s.host_plan()
    io.say("Host plan: " + ("nothing to change" if not s.plan_needs_work(plan) else
                            f"{len(plan['changes'])} file(s) and {len(plan['commands'])} command(s) pending"))
    return EXIT_OK


def run(args: argparse.Namespace, session: Session | None = None, ask: Callable[[str], str] = input,
        say: Callable[[str], None] = print, interactive: bool | None = None) -> int:
    import sys
    s = session or Session()
    io = _Io(ask, say, sys.stdin.isatty() if interactive is None else interactive)
    if args.status:
        return _status(s, io)
    if args.undo:
        ok, messages = s.undo(allow_stop=args.stop_vm)
        for m in messages:
            io.say(m)
        kept = getattr(s, "undo_kept", 0)
        if not ok:
            io.say("Some parts could not be undone; run --undo again after fixing the above.")
        elif kept:
            io.say("Done, except for the files listed as kept above. They stay as you left them, so any "
                   "vfio-pci binding in them still applies.")
        elif not getattr(s, "undo_nothing", False):
            io.say("Everything this setup changed is back as it was.")
        return EXIT_OK if ok else EXIT_FAIL
    if s.state["finished"]:
        if not args.yes and not (io.interactive and io.confirm("Setup is finished. Start a new one?", False)):
            io.say(s.summary())
            return EXIT_OK
        s.restart()
    while True:
        io.say(render_rail(s.state))
        step = s.state["step"]
        before = (step, len(s.state["done"]), len(s.state["skipped"]))
        try:
            code = _HANDLERS[step](s, io, args)
        except CardChanged as exc:
            io.say("REFUSED: " + s.card_changed(exc))
            io.say("Nothing was changed. Run the scan again: " + s.pause())
            return EXIT_REFUSED
        if code is not None:
            return code
        if s.state["finished"]:
            return EXIT_OK
        if (s.state["step"], len(s.state["done"]), len(s.state["skipped"])) == before:
            return EXIT_FAIL  # a step that neither moved nor stopped would loop forever
