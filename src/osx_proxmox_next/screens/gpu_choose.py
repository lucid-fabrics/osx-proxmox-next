"""Moment 1 of the graphics card flow: choose the card and the Mac, see what it takes, approve once."""

from __future__ import annotations

import logging
from typing import Any

from rich.text import Text
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.widget import Widget
from textual.widgets import Button, Static

from .. import nullmoth_cli, nullmoth_finish
from ..gpu_flow import (
    LATER,
    Choice,
    Preview,
    approval_record,
    build_before,
    build_preview,
    card_verdict,
    need_rows,
    part_steps,
    same_plan,
    short_card_name,
)
from ..gpu_setup import DISPLAY_OPTIONS, GpuCard, VmOption
from ..widgets.lines import Line, Lines
from ..widgets.rows import ChoiceRow, Option
from .gpu_before import BeforeDialog

log = logging.getLogger(__name__)

# the Display row words (the command line keeps its own wording in gpu_setup.DISPLAY_OPTIONS)
CHOOSE_LABEL = 10   # the label column of the choose rows (the mockup)
DISPLAY_LABELS = {"gpu": "Monitor on the card", "console": "Keep the Proxmox console"}
DISPLAY_HINTS = {
    "gpu": ["Best performance. Plug a screen into the card.", "Other choice: keep the Proxmox console"],
    "console": ["The card speeds up apps and you keep using the web console.", "Other choice: monitor on the card"],
}
SAFE_LINE = "Every file is backed up first."
NO_CARD = "No card here can run macOS graphics. See the docs for cards that work."


class ChooseMixin:
    """The choose view of GpuScreen: rows on the left, "What it takes on this host" on the right."""

    async def _show_choose(self, notice: str = "") -> None:
        s = self.s
        self.preview = None
        self.working = True
        await self.set_mode("choose", [Static("Looking for graphics cards...", id="g-loading")])

        def work() -> Any:
            cards = s.scan()
            vendors = {c.vendor for c in cards if c.selectable}
            return s.summary_fields(), cards, {v: s.vm_options(v) for v in vendors}

        self.job(work, lambda result: self.run_worker(self._choose_ready(result, notice), exclusive=True,
                                                      group="gpu-mode"))

    def _choose_title(self, usable: list[GpuCard]) -> Text:
        pal = self.app.get_css_variables()
        name = self._vm_name(self.vmid) if self.vmid is not None else ""
        title = f"Give {name} a graphics card" if name else "Choose a graphics card"
        sub = ("one card here works with macOS" if len(usable) == 1 else
               ("choose the card to hand over" if usable else "no card here can run macOS graphics"))
        return Text.assemble((title, "bold"), ("  " + sub, pal["muted"]))

    def _choose_form(self, usable: list[GpuCard]) -> list[Widget]:
        options = [Option(c.bdf, short_card_name(c.name), c.selectable) for c in self.cards]
        first = usable[0].bdf if usable else (self.cards[0].bdf if self.cards else "")
        return [
            ChoiceRow("Card", options, first, label_width=CHOOSE_LABEL, id="g-card"),
            ChoiceRow("Mac", [Option(LATER, "Decide later")], LATER, label_width=CHOOSE_LABEL, id="g-mac",
                      hint_lines=0),
            ChoiceRow("Display", [Option(v, DISPLAY_LABELS[v]) for v, _l, _d in DISPLAY_OPTIONS], "gpu",
                      label_width=CHOOSE_LABEL, id="g-display"),
            ChoiceRow("Driver", [Option("latest", "Latest")], "latest", label_width=CHOOSE_LABEL, id="g-driver",
                      hint_lines=0),
            ChoiceRow("Early bind", [Option("off", "Skip"), Option("on", "Write it")], "off",
                      label_width=CHOOSE_LABEL, id="g-optional"),
            Static("", id="g-reason"),
        ]

    async def _choose_ready(self, result: Any, notice: str) -> None:
        fields, cards, pool = result
        self.host_fields = fields
        self.cards = cards
        self.vm_pool = {"amd": [], "nvidia": [], "intel": [], **pool}
        usable = [c for c in cards if c.selectable]
        pal = self.app.get_css_variables()
        widgets: list[Widget] = []
        if notice:
            widgets.append(Static(Text(notice, style=pal["error"]), classes="g-notice"))
        side = Vertical(
            Static(Text("What it takes on this host", style=pal["muted"]), classes="side-title"),
            Lines(id="g-need"),
            Static(Text("Your part", style=pal["muted"]), classes="side-title", id="g-part-title"),
            Static("", id="g-part"), id="g-side")
        safe = Text.assemble(("✓ ", pal["success"]), SAFE_LINE + " ", ("u", "bold " + pal["accent"]),
                             " puts everything back.")
        form = VerticalScroll(*self._choose_form(usable), Static(safe, id="g-safe"), id="g-form")
        widgets += [Static(self._choose_title(usable), id="g-title"), Horizontal(form, side, id="g-cols"),
                    Static("", id="g-result"),
                    Horizontal(Static(Text("Nothing changes until you press enter.", style=pal["muted"]),
                                      id="g-note"),
                               Button("Set it up", id="g-primary", classes="primary", disabled=True),
                               id="g-action")]
        await self.set_mode("choose", widgets)
        self.working = False
        self._fill_mac_row()
        for hidden in ("#g-display", "#g-driver", "#g-optional"):
            self.query_one(hidden).display = False
        if not usable:
            self.query_one("#g-need", Lines).set_lines([Line(Text(NO_CARD, style=pal["error"]), wrap=True)])
        self._card_changed_ui()
        self.query_one("#g-card", ChoiceRow).focus()

    # ── rows ─────────────────────────────────────────────────────────

    def _selected_card(self) -> GpuCard | None:
        try:
            value = self.query_one("#g-card", ChoiceRow).value
        except Exception:  # noqa: BLE001
            return None
        return next((c for c in self.cards if c.bdf == value and c.selectable), None)

    def _fill_mac_row(self) -> None:
        row = self.query_one("#g-mac", ChoiceRow)
        card = self._selected_card()
        pool = self.vm_pool.get(card.vendor, []) if card else []
        if self.fixed_vm:
            opt = next((o for o in pool if o.vmid == self.vmid), None)
            label = opt.name if opt and opt.name else f"VM {self.vmid}"
            row.set_options([Option(str(self.vmid), label)], str(self.vmid))
            row.set_text(f"VM {self.vmid}" + (f" · {opt.status}" if opt else ""),
                         "" if opt is None or opt.eligible or opt.status == "running" else opt.why)
            return
        options = [Option(str(o.vmid), o.name or f"VM {o.vmid}", o.eligible or o.status == "running") for o in pool]
        options.append(Option(LATER, "Decide later"))
        keep = row.value if self._mac_touched and any(o.value == row.value for o in options) else ""
        first = next((o.value for o in options if o.enabled and o.value != LATER), LATER)
        row.set_options(options, keep or first)
        self._mac_detail()

    def _mac_detail(self) -> None:
        row = self.query_one("#g-mac", ChoiceRow)
        if row.value == LATER:
            row.set_text("", "Set up the host now, pick a Mac later")
            return
        opt = self._mac_option(row.value)
        if opt:
            row.set_text(f"VM {opt.vmid}" + (f" · macOS {opt.macos}" if opt.macos else "") + f" · {opt.status}",
                         "" if opt.eligible or opt.status == "running" else opt.why)

    def _mac_option(self, value: str) -> VmOption | None:
        pool = [o for p in self.vm_pool.values() for o in p]
        return next((o for o in pool if str(o.vmid) == value), None)

    def _card_hint(self, card: GpuCard | None, prev: Preview | None) -> Text:
        """Under the card: the verdict, the one amber line when something needs a confirmation, other cards."""
        pal = self.app.get_css_variables()
        hint = Text()
        if card is not None:
            hint.append(card_verdict(card), style=pal["muted"])
            if card.vendor == "intel":
                hint.append("\n" + card.detected_line, style=pal["muted"])
            if prev is not None and not build_before(prev).empty:
                lead = "Third-party driver. " if card.vendor == "nvidia" else "Read the warnings first. "
                hint.append("\n! " + lead + "You confirm before anything changes.", style=pal["warning"])
            for note in card.notes:
                hint.append("\n" + note, style=pal["warning"])
        for other in self.cards:   # devices that are not offered: one muted line each, never under the chosen card's notes
            if not other.selectable:
                hint.append(f"\nNot offered: {short_card_name(other.name)} ({other.short_why_not})",
                            style=pal["muted"])
        return hint

    def _card_changed_ui(self) -> None:
        """The card row changed: refresh its hint, the rows that depend on the vendor, and the preview."""
        card = self._selected_card()
        row = self.query_one("#g-card", ChoiceRow)
        row.set_text("", self._card_hint(card, None))
        vendor = card.vendor if card else ""
        self.query_one("#g-display").display = vendor == "amd"
        self.query_one("#g-driver").display = vendor == "nvidia"
        if vendor == "nvidia":
            self._fill_releases()
        self._fill_mac_row()
        self._show_display_hint()
        self.recompute()

    def _show_display_hint(self) -> None:
        row = self.query_one("#g-display", ChoiceRow)
        row.set_text("", Text("\n".join(DISPLAY_HINTS[row.value]), style=self.app.get_css_variables()["muted"]))

    def _fill_releases(self) -> None:
        row = self.query_one("#g-driver", ChoiceRow)
        if self.releases:
            return

        def fill(found: list[dict] | None) -> None:
            found = found or []
            lines = nullmoth_cli.version_lines(found)
            options = [Option(r["tag"], line) for line, r in zip(lines, found)] or [Option("latest", "Latest")]
            self.releases = found or [{"tag": "latest", "prerelease": False}]
            row.set_options(options, next((r["tag"] for r in found if not r["prerelease"]), options[0].value))
            row.set_text("newest stable release is preselected", "")

        def runner() -> None:
            try:
                found = nullmoth_finish.versions()
            except Exception:  # noqa: BLE001 - no network: the install resolves "latest" itself and reports the error
                found = None
            try:
                self.app.call_from_thread(fill, found)
            except Exception:  # noqa: BLE001
                pass

        self.run_worker(runner, thread=True, exit_on_error=False, group="gpu-releases")

    def on_choice_row_changed(self, event: ChoiceRow.Changed) -> None:
        rid = event.row.id
        if rid == "g-card":
            self._card_changed_ui()
        elif rid == "g-mac":
            self._mac_touched = True
            self._mac_detail()
            self.recompute()
        elif rid == "g-display":
            self._show_display_hint()
            self.recompute()
        elif rid == "g-optional":
            self.recompute()
        elif rid == "g-pick":
            self._pick_changed()

    def choice_values(self) -> tuple[GpuCard | None, int | None, str]:
        card = self._selected_card()
        mac = self.query_one("#g-mac", ChoiceRow).value
        vmid = int(mac) if mac.isdigit() else None
        if card is None:
            return None, vmid, "console"
        display = {"amd": self.query_one("#g-display", ChoiceRow).value, "intel": "gpu"}.get(card.vendor, "console")
        return card, vmid, display

    # ── the preview ──────────────────────────────────────────────────

    def recompute(self, typed: bool = False, then: Any = None) -> None:
        """Work out the preview of the current choices off the UI thread; a newer call wins."""
        card, vmid, display = self.choice_values()
        self._token += 1
        token = self._token
        if card is None:
            self.preview = None
            self.paint_preview()
            return
        s = self.s
        optional = self.query_one("#g-optional", ChoiceRow).value == "on"

        def runner() -> None:
            try:
                result = build_preview(s, Choice(card, vmid, display, optional), typed)
            except Exception as exc:  # noqa: BLE001
                log.exception("GPU preview failed")
                result = Preview(card, vmid, display, error=f"Could not work this out: {exc}")
            try:
                self.app.call_from_thread(self._preview_ready, token, result, then)
            except Exception:  # noqa: BLE001
                pass

        self.preview = None
        self.refresh_buttons()
        self.run_worker(runner, thread=True, exit_on_error=False, group="gpu-preview")

    def _preview_ready(self, token: int, result: Preview, then: Any = None) -> None:
        if token != self._token or self.mode != "choose":
            return
        self.preview = result
        self.paint_preview()
        if then is not None:
            then(result)

    def paint_preview(self) -> None:
        """The right panel, the amber line under the card, the one-line reason and the primary button."""
        pal = self.app.get_css_variables()
        prev = self.preview
        reason = ""
        rows: list[Line] = []
        part = Text()
        if prev is not None and not prev.error:
            for glyph, name, tail in need_rows(prev):
                done = glyph == "✓"
                rows.append(Line(Text.assemble((glyph + " ", pal["success"] if done else pal["accent"]),
                                               (name, pal["muted"] if done else "")), Text(tail, style=pal["muted"])))
            part = self._part_text(prev)
            reason = ("Refused: " + prev.refusals[0]) if prev.refusals else ""
        elif prev is not None:
            reason = prev.error
        elif not any(c.selectable for c in self.cards):
            rows = [Line(Text(NO_CARD, style=pal["error"]), wrap=True)]
        try:
            need = self.query_one("#g-need", Lines)
            need.set_stack(self.size.width < 90)
            need.set_lines(rows)
            self.query_one("#g-part", Static).update(part)
            for widget in (self.query_one("#g-part", Static), self.query_one("#g-part-title", Static)):
                widget.display = bool(part.plain)
            self.query_one("#g-reason", Static).update(Text(reason, style=pal["error"]))
            self.query_one("#g-optional").display = bool(prev and (prev.plan.get("optional") or prev.optional))
            if prev is not None and not prev.error:
                self.query_one("#g-card", ChoiceRow).set_text("", self._card_hint(prev.card, prev))
        except Exception:  # noqa: BLE001 - mode changed under us
            return
        self.refresh_buttons()

    def _part_text(self, prev: Preview) -> Text:
        pal = self.app.get_css_variables()
        out = Text()
        for idx, (head, tail) in enumerate(part_steps(prev), start=1):
            out.append(f"{idx} ", style=pal["muted"])
            out.append(head + "\n")
            if tail:
                out.append("  " + tail + "\n", style=pal["muted"])
        return out

    def refresh_buttons(self) -> None:
        if self.mode != "choose":
            return
        try:
            self.query_one("#g-primary", Button).disabled = not self.can_approve()
        except Exception:  # noqa: BLE001
            return

    def can_approve(self) -> bool:
        prev = self.preview
        return not (prev is None or prev.error or prev.card is None or prev.refusals)

    # ── approving ────────────────────────────────────────────────────

    def approve(self) -> None:
        """enter: the dialog Before you continue when there is something to confirm, else straight on."""
        if not self.can_approve() or self.working:
            return
        prev = self.preview
        assert prev is not None and prev.card is not None
        before = build_before(prev)
        if before.empty:
            self._approved(prev, typed=False, ack=False)
            return

        def answer(result: dict | None) -> None:
            if result is not None:
                self._approved(prev, typed=result["typed"], ack=result["ack"])

        self.app.push_screen(BeforeDialog(before), answer)

    def _approved(self, prev: Preview, typed: bool, ack: bool) -> None:
        """The warnings are accepted. A typed "I understand" changes the host plan (the only-display-adapter
        override), so the plan that will run is worked out again with it before anything is stored."""
        if typed and prev.typed_needed:
            assert prev.card is not None
            choice = Choice(prev.card, prev.vmid, prev.display, prev.optional)
            self.working = True
            self.job(lambda: (build_preview(self.s, choice, typed=True), build_preview(self.s, choice)),
                     lambda fresh: self._typed_planned(prev, fresh[0], fresh[1], typed, ack))
            return
        self._confirmed(prev, typed, ack)

    def _typed_planned(self, shown: Preview, fresh: Preview, plain: Preview, typed: bool, ack: bool) -> None:
        """The plan worked out with the phrase typed must be the one the dialog showed; when the host or the
        Mac changed in between, the dialog is opened again with what would run now (*plain*: the plan without
        the phrase, so the dialog says again why the phrase is asked)."""
        if not fresh.error and not same_plan(shown, fresh):
            self.preview = plain
            self.paint_preview()
            self.say("The plan changed while you were reading it. Check it again.", fail=True)
            self.approve()
            return
        self._confirmed(fresh, typed, ack)

    def _confirmed(self, prev: Preview, typed: bool, ack: bool) -> None:
        if prev.error or prev.refusals:
            self.say(prev.error or "Refused: " + prev.refusals[0], fail=True)
            return
        self._choose(prev, typed, ack)

    def _choose(self, prev: Preview, typed: bool, ack: bool) -> None:
        """Store the choice. Confirming "Before you continue" is also the OK for a card other VMs list (their
        warning is on that dialog), so the sharing acknowledgement is recorded here, once."""
        s, card = self.s, prev.card
        assert card is not None
        shared = bool(card.stopped_users)
        release = self.query_one("#g-driver", ChoiceRow).value if card.vendor == "nvidia" else ""
        record = approval_record(prev, typed=typed, release=release)

        def work() -> list[str]:
            return s.choose(card.bdf, prev.vmid, prev.display, ack, shared, confirmed=card.stopped_users)

        def done(issues: list[str]) -> None:
            if issues:
                self.say("\n".join("Refused: " + i for i in issues), fail=True)
                self.recompute()
                return
            s.approve(record)
            self.durations.clear()
            self.drive()

        self.job(work, done)


__all__ = ["ChooseMixin"]
