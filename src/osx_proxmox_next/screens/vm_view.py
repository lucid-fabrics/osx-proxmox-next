"""The VM view: one Mac, its live screen large, and what `qm config` says about it."""

from __future__ import annotations

import logging

from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.widgets import Static

from .. import gpu
from ..services import VmRecord, fetch_vm_info, get_proxmox_adapter
from ..services.unattended_service import read_driver_state
from ..widgets.live_screen import LiveScreen
from .home import status_line
from .vm_actions import VmActionScreen

log = logging.getLogger(__name__)

__all__ = ["VmViewScreen", "open_vm_screen"]

VIEW_REFRESH_SECONDS = 2


def open_vm_screen(app, vmid: int) -> None:
    """Open the VM view, or the Build screen when a hands-off install is running on that Mac."""
    driver = read_driver_state(vmid)
    if driver.installing:
        from .build import BuildScreen
        app.push_screen(BuildScreen.resume(vmid, driver))
        return
    record = next((r for r in app.vm_records if r.vmid == vmid), None)
    if record is None:
        info = fetch_vm_info(vmid, adapter=get_proxmox_adapter())
        if info is None:
            app.notify(f"VM {vmid} was not found.", severity="error")
            return
        record = VmRecord(vmid, info.name, info.status, 0, 0, info.config_raw)
        facts = record.facts
        record.cores, record.memory_mb = facts.cores, facts.memory_mb
    app.push_screen(VmViewScreen(record))


class VmViewScreen(VmActionScreen):
    BINDINGS = [
        Binding("s", "toggle_power", "Start or Stop"),
        Binding("e", "edit", "Edit"),
        Binding("g", "gpu", "Graphics card"),
        Binding("d", "remove", "Remove"),
        Binding("u", "undo_gpu", "Put everything back"),
        Binding("z", "zoom", "Zoom the screen"),
    ]

    def __init__(self, record: VmRecord) -> None:
        super().__init__()
        self.record = record

    def crumb(self) -> str:
        return self.record.name

    def current_record(self) -> VmRecord | None:
        return self.record

    def compose_body(self) -> ComposeResult:
        facts = self.record.facts
        with Vertical(id="vm-view"):
            with Horizontal(id="vm-view-head"):
                yield Static(self.record.name, id="vm-view-title")
                yield Static("", id="vm-view-status")
            with Horizontal(id="vm-view-body"):
                with Vertical(id="vm-view-screen"):
                    yield LiveScreen(self.record.vmid, self.record.status, VIEW_REFRESH_SECONDS,
                                     has_gpu=facts.display_on_gpu, fit=True, id="vm-live")
                    yield Static("", id="vm-live-caption")
                yield Static("", id="vm-facts")

    def on_mount(self) -> None:
        self._paint()
        self.set_interval(4, self._reload)

    def _paint(self) -> None:
        rec, pal = self.record, self.app.get_css_variables()
        facts = rec.facts
        glyph, label = status_line(rec, None)
        tone = {"●": pal["success"], "○": pal["muted"]}[glyph]
        status = Text(glyph + " ", style=tone)
        status.append(label)
        self.query_one("#vm-view-status", Static).update(status)
        caption = f"live from VM {rec.vmid}" + (f" · refreshes every {VIEW_REFRESH_SECONDS} s" if rec.status == "running" else "")
        self.query_one("#vm-live-caption", Static).update(Text(caption, style=pal["muted"]))
        rows = [("VM ID", str(rec.vmid)), ("Cores", str(rec.cores)), ("Memory", f"{rec.memory_gb} GB")]
        for disk in facts.disks:
            if disk.size_gb > 1 and disk.storage != "local":
                rows.append(("Disk", f"{disk.size_gb} GB on {disk.storage}"))
        if facts.bridge:
            net = f"{facts.nic_model} on {facts.bridge}" + (f" (VLAN {facts.vlan})" if facts.vlan else "")
            rows.append(("Network", net))
        rows.append(("Display", "on the graphics card" if facts.display_on_gpu else
                     ("graphics card attached" if facts.has_gpu else "virtual, shown here")))
        text = Text()
        for idx, (key, value) in enumerate(rows):
            if idx:
                text.append("\n")
            text.append(key.ljust(9), style=pal["muted"])
            text.append(value)
        self.query_one("#vm-facts", Static).update(text)
        live = self.query_one("#vm-live", LiveScreen)
        live.set_state(rec.status, facts.display_on_gpu)
        self.refresh_footer()

    def _reload(self) -> None:
        def work() -> None:
            info = fetch_vm_info(self.record.vmid, adapter=get_proxmox_adapter())
            self.app.call_from_thread(self._reloaded, info)

        self.run_worker(work, thread=True, exclusive=True, group="vm-reload", exit_on_error=False)

    def _reloaded(self, info) -> None:
        if info is None:
            return
        rec = self.record
        facts_new = VmRecord(rec.vmid, info.name or rec.name, info.status, 0, 0, info.config_raw)
        facts = facts_new.facts
        facts_new.cores, facts_new.memory_mb = facts.cores, facts.memory_mb
        self.record = facts_new
        self._paint()

    def after_change(self, removed: bool = False) -> None:
        if removed:
            self.app.pop_screen()
        else:
            self._reload()

    def action_zoom(self) -> None:
        from .build import ZoomScreen
        self.app.push_screen(ZoomScreen(self.record.vmid, self.record.status))

    def action_undo_gpu(self) -> None:
        if gpu.load_record(self.record.vmid) is not None:
            from .gpu import GpuScreen
            self.app.push_screen(GpuScreen(vmid=self.record.vmid, undo=True))

    def footer_items(self) -> list[tuple]:
        power = "Stop" if self.record.status == "running" else "Start"
        items = [("s", power, "screen.toggle_power"), ("e", "Edit", "screen.edit"),
                 ("g", "GPU", "screen.gpu"), ("d", "Remove", "screen.remove"), ("z", "Zoom", "screen.zoom")]
        if gpu.load_record(self.record.vmid) is not None:
            items.append(("u", "Put everything back", "screen.undo_gpu"))
        items += [("esc", "Back", "screen.back"), ("?", "Help", "app.help")]
        return items
