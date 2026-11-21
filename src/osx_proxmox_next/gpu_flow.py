"""The graphics card flow without a screen: what a choice would need, fingerprints and the approval checks.

The screens (screens/gpu*.py) draw this and call it; nothing here imports Textual, so every rule of the
approval can be tested without a pilot. One approval covers the whole setup: the host plan before the restart
and the attach after it, each pinned by a fingerprint of what it would write."""

from __future__ import annotations

import hashlib
import json
import re
import time
from dataclasses import dataclass, field
from typing import Any

from . import gpu, host_setup
from .services import fetch_vm_info
from .gpu_setup import INTEL_DISPLAY_NOTE, NVIDIA_DISPLAY_NOTE, CardChanged, GpuCard, Session
from .nullmoth import NOTICE_BODY, NOTICE_TITLE

__all__ = ["LATER", "Choice", "same_plan", "REBOOT_SECONDS", "Before", "Preview", "approval_record", "attach_differs", "build_before",
           "build_preview", "changes_text", "needs_typed", "plan_fingerprint", "plan_for_approval", "plan_lines",
           "preview_vm_plan", "replan_attach", "card_facts", "stop_mac", "need_rows", "part_steps", "short_card_name", "vm_fingerprint", "card_verdict"]

LATER = "later"
REBOOT_SECONDS = "about 3 min"

_VERDICTS = {"native": "Works in macOS without extra drivers", "nullmoth": "Works with the NullMoth driver (beta)"}


# The iGPU attach script names the SHA-256 of the cached zips. That hash only appears once the zip is downloaded,
# and the patcher checks it against the verified download right before it unpacks, so it stays out of the digest.
_SHA_ENV = re.compile(r"(IGPU_(?:WEX|AMFI)_SHA256=)[0-9a-fA-F]*")


def plan_fingerprint(plan: dict) -> str:
    """A stable digest of what a host plan would write and run."""
    body = {"changes": [(c["path"], c["new"]) for c in plan["changes"]],
            "commands": [c["argv"] for c in plan["commands"]]}
    return hashlib.sha1(json.dumps(body, sort_keys=True).encode()).hexdigest()


def vm_fingerprint(bdf: str, vmid: int | None, display: str, steps: list) -> str:
    """A stable digest of the VM settings an attach would write. Stopping a running Mac is not part of
    it: whether the Mac is running changes between the approval and the attach."""
    body = {"bdf": bdf, "vm": vmid, "display": display,
            "steps": [[_SHA_ENV.sub(r"\1", a) for a in s.argv] for s in steps if not s.title.lower().startswith("stop")]}
    return hashlib.sha1(json.dumps(body, sort_keys=True).encode()).hexdigest()


def short_card_name(name: str) -> str:
    """`GeForce RTX 3060` out of `NVIDIA Corporation GA106 [GeForce RTX 3060]`: the marketing name, when there is one."""
    inner = [part.split("]")[0] for part in name.split("[")[1:]]
    for part in reversed(inner):
        if part and not part.startswith("AMD/ATI"):
            return part
    return name


def card_verdict(card: GpuCard) -> str:
    """The one-sentence verdict under the card name."""
    return _VERDICTS.get(card.kind) or card.headline


def needs_typed(plan: dict) -> bool:
    return bool(plan["hard_warnings"]) or any("only display adapter" in r for r in plan["refusals"])


def plan_lines(plan: dict) -> list[str]:
    """The plan's own words, as the host step has always shown them."""
    lines = []
    for key, prefix in (("refusals", "Refused: "), ("hard_warnings", "HARD WARNING: "), ("warnings", "Warning: "),
                        ("notes", "Note: "), ("manual", "Manual step: "), ("skipped", "OK: ")):
        # Typing the phrase overrides the only-display-adapter stop, so that one is "Careful", not "Refused".
        lines += [("Careful: " if key == "refusals" and "only display adapter" in t else prefix) + t
                  for t in plan[key]]
    return lines


@dataclass
class Preview:
    """What choosing a card for a Mac would need, worked out without writing anything."""
    card: GpuCard | None = None
    vmid: int | None = None
    display: str = "console"
    items: list = field(default_factory=list)
    plan: dict = field(default_factory=dict)
    vm_plan: Any = None
    error: str = ""
    typed_needed: bool = False    # a hard warning or the only-display-adapter stop wants the typed phrase
    host_pending: list[str] = field(default_factory=list)   # attach findings the host setup will fix
    optional: bool = False        # the optional early binding is written too
    typed_extra: list[str] = field(default_factory=list)   # warnings only the typed plan has (not on SSH ...)

    @property
    def bios_failing(self) -> list:
        return [i for i in self.items if not i["ok"]]

    @property
    def host_work(self) -> bool:
        return bool(self.plan) and Session.plan_needs_work(self.plan) and not self.plan["optional"]

    @property
    def refusals(self) -> list[str]:
        """Reasons that cannot be typed away (the only-display-adapter stop can). Host work to do is not one."""
        out = [r for r in self.plan.get("refusals", []) if "only display adapter" not in r]
        if self.vm_plan is not None:
            out += list(self.vm_plan.issues)
        return out


def preview_vm_plan(s: Session, card: GpuCard, vmid: int, display: str) -> tuple[Any, list[str]]:
    """(attach plan, host findings). The plan is judged as if the host setup had run: a card still on its host
    driver is work to do, not a refusal, and the steps are the ones the attach builds after the restart."""
    raw = s.preview_vm(card, vmid, display, True)
    pending = [i for i in raw.issues if gpu.is_host_readiness_issue(i)]
    if not pending:
        return raw, []
    with gpu.assuming_host_ready():
        return s.preview_vm(card, vmid, display, True), pending


@dataclass
class Choice:
    """The card, the Mac and the settings one preview is worked out for."""
    card: GpuCard
    vmid: int | None = None
    display: str = "console"
    optional: bool = False


def _extra_lines(plain: dict, typed: dict) -> list[str]:
    """What the plan with the phrase typed says that the plain plan does not (an SSH warning, for one)."""
    seen = set(plan_lines(plain))
    return [t for t in plan_lines(typed) if t not in seen and not t.startswith("OK: ")]


def build_preview(s: Session, choice: Choice, typed: bool = False) -> Preview:
    """The BIOS items, host plan and attach plan of one choice. *typed*: the user typed "I understand"."""
    card, vmid, display, optional = choice.card, choice.vmid, choice.display, choice.optional
    extra: list[str] = []
    try:
        prev = s.preview(card, allow_no_console=False, write_optional=optional)
        wants = needs_typed(prev["plan"])
        if wants:
            with_phrase = s.preview(card, allow_no_console=True, write_optional=optional)
            if typed:  # the phrase is typed: show the plan as it will run
                prev = with_phrase
            else:      # the dialog must show every warning of the plan that will run after the phrase
                extra = _extra_lines(prev["plan"], with_phrase["plan"])
    except CardChanged as exc:
        return Preview(card, vmid, display, error=str(exc))
    vm_plan, pending = preview_vm_plan(s, card, vmid, display) if vmid is not None else (None, [])
    return Preview(card, vmid, display, prev["items"], prev["plan"], vm_plan, typed_needed=wants,
                   host_pending=pending, optional=optional, typed_extra=extra)


def _spoken(prev: Preview) -> set[str]:
    """The warnings a preview shows, without the Careful line that only the phrase-less plan carries."""
    return {w for w in build_before(prev).warnings if not w.startswith("Careful: ")}


def same_plan(a: Preview, b: Preview) -> bool:
    """Both previews would write the same host files and commands and the same VM settings, and warn the same."""
    rec_a, rec_b = approval_record(a, typed=False, release=""), approval_record(b, typed=False, release="")
    return (rec_a["host_fp"] == rec_b["host_fp"] and rec_a["vm_fp"] == rec_b["vm_fp"]
            and _spoken(a) == _spoken(b))   # same files, same words


def approval_record(prev: Preview, *, typed: bool, release: str) -> dict:
    """What the user approved, stored so the steps after the restart run without asking again."""
    steps = prev.vm_plan.steps if prev.vm_plan is not None else []
    return {
        "host_fp": plan_fingerprint(prev.plan) if prev.plan else "",
        "vm_fp": vm_fingerprint(prev.card.bdf, prev.vmid, prev.display, steps) if prev.card else "",
        "host_files": len(prev.plan.get("changes", [])) if prev.plan else 0,
        "host_work": prev.host_work,
        "restart": bool(prev.plan.get("reboot_required")) if prev.plan else False,
        "vm": prev.vmid, "release": release, "typed": typed, "write_optional": prev.optional,
        "allow_no_console": typed and prev.typed_needed,
    }


def plan_for_approval(s: Session, approval: dict | None, typed_now: bool = False) -> tuple[dict, bool]:
    """(host plan, same as approved). A plan that differs from the approval is made again without the earlier
    "I understand": a new plan never inherits it. *typed_now*: the user just typed it for this very plan."""
    approval = approval or {}
    optional = bool(approval.get("write_optional"))
    allow = typed_now or bool(approval.get("allow_no_console"))
    plan = s.host_plan(allow_no_console=allow, write_optional=optional)
    if not typed_now and approval.get("host_fp") == plan_fingerprint(plan) and not plan["refusals"]:
        return plan, True
    if allow and not typed_now:
        plan = s.host_plan(allow_no_console=False, write_optional=optional)
    return plan, False


def attach_differs(approval: dict | None, state: dict, plan: Any) -> bool:
    """The attach plan is not the one the user approved (no approval, a moved VM setting, another card)."""
    fp = vm_fingerprint(state["gpu"]["bdf"], state["vm"], state["display"], plan.steps)
    return (approval or {}).get("vm_fp") != fp


def replan_attach(s: Session, shown_fp: str, stop: bool) -> tuple[Any, bool]:
    """(attach plan now, still what was shown). Called in the worker right before the attach writes anything."""
    plan = s.vm_plan(allow_stop=stop)
    steps = plan.steps if plan.ok else []
    fp = vm_fingerprint(s.state["gpu"]["bdf"], s.state["vm"], s.state["display"], steps)
    return plan, plan.ok and fp == shown_fp


def stop_mac(s: Session, vmid: int, wait: float = 30.0, sleep=time.sleep) -> None:
    """Stop a running Mac and wait until it is down. Raises RuntimeError when it will not stop."""
    ok, out = s.stop_vm(vmid)
    if not ok:
        raise RuntimeError(f"Could not stop VM {vmid}: {out}")
    deadline = time.monotonic() + wait
    while True:
        info = fetch_vm_info(vmid, adapter=s.pve)
        if info is not None and info.status != "running":
            return
        if time.monotonic() >= deadline:
            raise RuntimeError(f"VM {vmid} is still running after {int(wait)} seconds.")
        sleep(1)


@dataclass
class Before:
    """The warnings and the notice of one choice, as the "Before you continue" dialog shows them."""
    warnings: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    notice_title: str = ""
    notice_lead: str = ""
    notice_body: str = ""
    phrase: bool = False       # "I understand" is typed
    nvidia: bool = False       # the NVIDIA acknowledgement is typed

    @property
    def empty(self) -> bool:
        return not (self.warnings or self.notes or self.notice_body)


def build_before(prev: Preview) -> Before:
    """Today's warnings, notes and the NVIDIA notice, in today's words. Refusals are not here: they stop the button."""
    out = Before(phrase=prev.typed_needed)
    for line in plan_lines(prev.plan) if prev.plan else []:
        if line.startswith(("OK: ", "Refused: ")):
            continue
        (out.notes if line.startswith(("Note: ", "Manual step: ")) else out.warnings).append(line)
    out.warnings += prev.typed_extra
    if prev.vm_plan is not None:
        out.notes += list(prev.vm_plan.hints)
        out.warnings += ["Warning: " + w for w in prev.vm_plan.warnings]
    card = prev.card
    if card is not None and card.vendor == "nvidia":
        out.nvidia = True
        out.notice_lead, out.notice_title, out.notice_body = NVIDIA_DISPLAY_NOTE, NOTICE_TITLE, NOTICE_BODY
    if card is not None and card.vendor == "intel":
        out.notes.append(INTEL_DISPLAY_NOTE)
    return out


def card_facts(card: GpuCard, fields: list[str]) -> str:
    """The card and the host in plain lines, for the top of "See every change"."""
    lines = [f"Card: {card.name}", f"Address: {card.bdf}"]
    lines += [t for t in (card.headline, card.detail) if t]
    if fields:
        lines += ["", "This host: " + "   ".join(fields)]
    return "\n".join(lines) + "\n"


def changes_text(plan: dict | None, vm_plan: Any, vmid: int | None, head: str = "") -> str:
    """Every change in one text: the card and the host facts, the files and commands, the VM settings, the backups."""
    lines: list[str] = [head] if head else []
    if plan is not None:
        lines.append("Host files and commands (each file is backed up first, in " f"{host_setup.BACKUP_ROOT}):")
        preview = host_setup.render_preview(plan)
        lines.append(preview if preview else "  nothing to change on the host")
    if vm_plan is not None:
        lines += ["", f"VM {vmid} settings (a config snapshot is saved first):"]
        lines += [f"  {i:02d}. {st.title}\n      {st.command}" for i, st in enumerate(vm_plan.steps, start=1)]
        lines += ["Refused: " + i for i in vm_plan.issues] + vm_plan.hints + ["Warning: " + w for w in vm_plan.warnings]
    elif vmid is None:
        lines += ["", "No Mac is chosen yet: the VM settings are shown when you pick one."]
    return "\n".join(lines)


def need_rows(prev: Preview) -> list[tuple[str, str, str]]:
    """(glyph, what, tail) of "What it takes on this host": ✓ already right, ● still to do. Only lines that apply."""
    rows: list[tuple[str, str, str]] = []
    if prev.items:
        failing = prev.bios_failing
        rows.append(("\u2713" if not failing else "\u25cf", "BIOS settings",
                     "already right" if not failing else f"{len(failing)} to change"))
    files = len(prev.plan.get("changes", []))
    if prev.plan and not prev.plan["refusals"]:
        if prev.host_work:
            tail = f"{files} file{'s' if files != 1 else ''} change" if files else "a few commands"
            rows.append(("\u25cf", "Host setup", tail))
        elif prev.host_pending:
            rows.append(("\u25cf", "Host setup", "the card is handed over"))
        else:
            rows.append(("\u2713", "Host setup", "already right"))
    if prev.plan.get("reboot_required"):
        rows.append(("\u25cf", "One restart", REBOOT_SECONDS))
    if prev.vmid is not None:
        rows.append(("\u25cf", "Attach to the Mac", "automatic"))
    return rows


def part_steps(prev: Preview) -> list[tuple[str, str]]:
    """(step, muted note) of "Your part": at most three."""
    steps: list[tuple[str, str]] = []
    if prev.bios_failing:
        steps.append(("Change the BIOS settings", "we show exactly how"))
    steps.append(("Press enter", ""))
    if prev.plan.get("reboot_required"):
        steps += [("Restart when asked", ""), ("Open osx-next again", "it finishes by itself")]
    elif prev.vmid is None:
        steps.append(("Pick a Mac afterwards", ""))
    return steps[:3]
