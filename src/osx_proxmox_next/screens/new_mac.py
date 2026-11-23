"""New Mac: one screen with everything prefilled from host detection."""

from __future__ import annotations

import dataclasses
import logging

from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.widget import Widget
from textual.widgets import Button, Input, Static

from ..defaults import DEFAULT_STORAGE, detect_cpu_cores, detect_memory_mb
from ..domain import SUPPORTED_MACOS
from ..models.wizard_state import (
    NewMacState,
    effective_size,
    validate_arm64_new_mac,
    validate_new_mac,
    validate_stela_new_mac,
)
from ..presets import PRESET_KEYS, build_presets, preset_fits, preset_label
from ..services.host_facts import HostFacts
from ..smbios import SmbiosIdentity, model_for_macos, resolve_smbios
from ..stela_gpus import HostGpu, list_host_gpus
from ..widgets.capacity import CapacityBar
from ..widgets.rows import ActionRow, ChoiceRow, ExpandRow, FieldRow, Option
from .base import OsxScreen
from .new_mac_rows import MORE_ROWS, VERSION_ORDER, custom_rows, more_rows, short_label, version_options

log = logging.getLogger(__name__)

__all__ = ["NewMacScreen", "version_options"]

class NewMacScreen(OsxScreen):
    CRUMB = "New Mac"
    HELP_EXTRA = [("\u2190  \u2192  h  l", "Change the choice on this row"),
                  ("type", "Edit a text field (letters are text there)"),
                  ("enter  space", "Open or close More")]
    BINDINGS = [
        Binding("up", "row(-1)", "Move", show=False),
        Binding("down", "row(1)", "Move", show=False),
        Binding("enter", "create", "Create this Mac"),
        Binding("escape", "back", "Cancel"),
    ]

    def __init__(self, facts: HostFacts | None = None) -> None:
        super().__init__()
        self._facts = facts
        self.facts: HostFacts
        self.state = NewMacState()
        self.errors: dict[str, str] = {}
        self._quiet = False   # True while the screen sets a field itself, so it is not read as an edit
        self.base_cores = detect_cpu_cores()
        self.base_memory_mb = detect_memory_mb()
        self.targets: list[str] = []
        self.identity: SmbiosIdentity | None = None   # the model and serial the Mac gets, when Apple ID is on
        self.gpus: list[HostGpu] = []                  # host GPUs the Stela VM can take (read once, at compose)

    # ── setup ────────────────────────────────────────────────────────

    def _load_facts(self) -> HostFacts:
        return self._facts or self.app.facts()  # type: ignore[attr-defined]

    def compose(self) -> ComposeResult:
        self.facts = self._load_facts()
        st = self.state
        st.vmid = str(self.facts.next_vmid or 900)
        targets = list(self.facts.storage_targets)
        # the usual place for VM disks first, then what pvesm listed
        if DEFAULT_STORAGE in targets:
            targets.remove(DEFAULT_STORAGE)
            targets.insert(0, DEFAULT_STORAGE)
        self.targets = targets or [DEFAULT_STORAGE]
        self.gpus = list_host_gpus()
        st.storage = self.targets[0]
        st.iso_dir = self.facts.iso_dir
        st.use_penryn = self.facts.cpu.needs_penryn
        st.e1000 = self.facts.cpu.is_xeon or self.facts.cpu.needs_penryn
        presets = self._presets()
        balanced = presets["balanced"]
        st.custom_cores, st.custom_memory_gb, st.custom_disk_gb = (
            str(balanced.cores), str(balanced.memory_gb), str(balanced.disk_gb))
        return super().compose()

    def compose_body(self) -> ComposeResult:
        st, facts = self.state, self.facts
        with Vertical(id="new-mac"):
            with Horizontal(id="new-head"):
                yield Static(Text("New Mac"), id="new-title")
                yield Static("picked for this host. Change anything, or just press enter.", id="new-sub")
            with Horizontal(id="new-cols"):
                with VerticalScroll(id="new-form"):
                    yield ChoiceRow("macOS", version_options(), st.macos, id="r-macos", drop_long=True)
                    yield FieldRow("Name", st.name, id="r-name", suffix="", max_width=32)
                    yield ChoiceRow("Size", [Option(k, preset_label(k)) for k in PRESET_KEYS], st.size, id="r-size",
                                    drop_long=True)
                    yield Vertical(*custom_rows(st), id="custom-box")
                    yield ChoiceRow("Lives on", [Option(t, t) for t in self.targets],
                                    st.storage, id="r-storage", hint_lines=0, drop_long=True)
                    yield ChoiceRow("GPU", [Option("none", "None"), *[Option(g.slot, g.label) for g in self.gpus]],
                                    "none", id="r-gpu", hint_lines=0, drop_long=True)
                    yield ChoiceRow("Install", [Option("handsoff", "Hands-off"), Option("manual", "Manual")],
                                    "handsoff", id="r-install", drop_long=True)
                    yield ChoiceRow("Apple ID", [Option("off", "Off"), Option("on", "On")], "off", id="r-apple",
                                    drop_long=True)
                    yield ExpandRow("More", "VLAN · CPU · NIC · installer · UUID",
                                    id="r-more")
                    yield Vertical(*more_rows(st, facts), id="more-box")
                with Vertical(id="new-side"):
                    yield Static("What it takes from this host", classes="side-title", id="side-title")
                    yield CapacityBar("CPU", "cores", id="bar-cpu")
                    yield CapacityBar("Memory", "GB", id="bar-mem")
                    yield CapacityBar("Disk", "GB", id="bar-disk")
                    yield Static("After you press enter", classes="side-title")
                    yield Static("", id="after")
            yield Static("", id="errors")
            with Horizontal(id="new-action"):
                yield Static("You can watch the Mac's screen right here while it installs.", id="new-note")
                yield Button("Create this Mac", id="create", classes="primary")

    def footer_items(self) -> list[tuple]:
        return [("↑↓", "Move", None), ("←→", "Change", None), ("type", "to edit", None),
                ("enter", "Create this Mac", "screen.create"), ("esc", "Cancel", "screen.back"),
                ("?", "Help", "app.help")]

    def on_mount(self) -> None:
        self._sync()
        self.query_one("#r-macos", ChoiceRow).focus()
        self._sync_height()

    def on_resize(self) -> None:
        self._sync_height()

    def _sync_height(self) -> None:
        self.set_class(self.size.height < 30, "short")
        narrow = self.size.width < 90
        if self.query("#new-note"):
            self.query_one("#new-note").display = not narrow     # no room for it: left out, not cut
        if self.query("#r-more"):
            self._sync_collapse()
        if narrow != self.has_class("narrow"):
            self.set_class(narrow, "narrow")
            if self.is_attached and self.query("#r-size"):
                self._sync()

    # ── derived values ───────────────────────────────────────────────

    def _presets(self):
        facts = self.facts
        macos = "tahoe" if self.state.is_beta else self.state.macos   # the arm64 targets are sized like the newest Mac
        return build_presets(macos, self.base_cores, self.base_memory_mb,
                             facts.host_cores, facts.host_memory_mb)

    def _free_disk(self) -> int | None:
        return self.facts.storage_free_gb.get(self.state.storage)

    def _fits(self, key: str) -> bool:
        if key in ("balanced", "custom"):
            return True
        preset = self._presets()[key]
        return preset_fits(preset, self.facts.host_cores, self.facts.vm_memory_limit_mb, self._free_disk())

    # ── sync everything on screen with the state ─────────────────────

    def _sync(self) -> None:
        st, pal = self.state, self.app.get_css_variables()
        presets = self._presets()
        q = self.query_one
        macos = q("#r-macos", ChoiceRow)
        others = [short_label(k) for k in VERSION_ORDER if k != st.macos and k in SUPPORTED_MACOS]
        macos.set_text("", " · ".join(others))
        q("#r-name", FieldRow).set_suffix(f"· VM {st.vmid.strip() or '?'}")
        size = q("#r-size", ChoiceRow)
        size.set_options([Option(k, preset_label(k), self._fits(k)) for k in PRESET_KEYS], st.size)
        st.size = size.value
        cores, mem_mb, disk = effective_size(st, presets)
        mem_gb = f"{int(mem_mb) // 1024}" if mem_mb.isdigit() else "?"
        narrow = self.has_class("narrow")
        q("#side-title", Static).update("What it takes" if narrow else "What it takes from this host")
        disk_word = "" if narrow else " disk"
        size.set_text(f"{cores} cores · {mem_gb} GB · {disk} GB{disk_word}", self._size_hint(pal))
        q("#custom-box").display = st.size == "custom"
        storage = q("#r-storage", ChoiceRow)
        storage.set_text(f"network {st.bridge.strip() or '?'}")
        for rid in ("#r-install", "#r-apple", "#r-more"):
            q(rid).display = not st.is_beta       # these only apply to an OpenCore Mac
        q("#r-gpu").display = st.is_stela         # only the Stela VM can take a GPU
        install = q("#r-install", ChoiceRow)
        install.set_text("BETA" if st.handsoff else "",
                         "Runs by itself up to Setup Assistant" if st.handsoff
                         else "You finish it in the Proxmox console")
        apple = q("#r-apple", ChoiceRow)
        apple.set_text("iMessage, FaceTime and iCloud" if st.apple_services else "no iMessage, FaceTime or iCloud")
        q("#more-box").display = q("#r-more", ExpandRow).open and not st.is_beta
        q("#r-vmgenid").display = st.apple_services
        q("#r-mac").display = st.apple_services
        q("#r-identity").display = st.apple_services
        self._sync_identity()
        self._sync_collapse()
        self._sync_bars(cores, mem_mb, disk)
        self._sync_after()

    def _sync_collapse(self) -> None:
        """While More is open (or the terminal is short) the rows above it shrink to one line each, so the
        fields of More fit; what does not fit is reached by scrolling."""
        tight = self.query_one("#r-more", ExpandRow).open or self.size.height < 30
        self.set_class(self.query_one("#r-more", ExpandRow).open, "more-open")
        for row in self.query("#new-form > ChoiceRow"):
            row.set_collapsed(tight)

    def _sync_identity(self) -> None:
        """The model and serial the Mac gets. It is made when Apple ID is on and kept until enter makes another."""
        st = self.state
        if not st.apple_services:
            self.identity = None
            return
        if self.identity is None or self.identity.model != model_for_macos(st.macos):
            self.identity = resolve_smbios(st.macos, True)
        self.query_one("#r-identity", ActionRow).set_text(f"{self.identity.model} \u00b7 {self.identity.serial}",
                                                           "enter makes a new one")

    def on_action_row_activated(self, event: ActionRow.Activated) -> None:
        if event.row.id == "r-identity":
            self.identity = None
            self._sync_identity()

    def _size_hint(self, pal: dict) -> Text:
        hint = Text()
        for idx, key in enumerate(PRESET_KEYS):
            if idx:
                hint.append(" · ", style=pal["muted"])
            hint.append(preset_label(key), style=pal["muted"] if self._fits(key) else pal["muted"] + " strike")
        unfit = [preset_label(k) for k in PRESET_KEYS if not self._fits(k)]
        if unfit:
            hint.append("  " + ", ".join(unfit) + ": too big", style=pal["muted"])
        return hint

    def _sync_bars(self, cores: str, mem_mb: str, disk: str) -> None:
        facts, q = self.facts, self.query_one
        used_cores = int(cores) if cores.isdigit() else 0
        q("#bar-cpu", CapacityBar).set_values(used_cores, facts.host_cores, "cores" if facts.host_cores else "")
        used_gb = int(mem_mb) / 1024 if mem_mb.isdigit() else 0
        total_gb = facts.host_memory_mb / 1024 if facts.host_memory_mb else 0
        # the host keeps some RAM for itself: a VM that leaves less than that does not fit
        if facts.vm_memory_limit_mb and used_gb * 1024 > facts.vm_memory_limit_mb:
            total_gb = facts.vm_memory_limit_mb / 1024
        q("#bar-mem", CapacityBar).set_values(round(used_gb, 1), round(total_gb), "GB")
        free = self._free_disk()
        used_disk = int(disk) if disk.isdigit() else 0
        q("#bar-disk", CapacityBar).set_values(used_disk, free or 0, "GB free" if free else "")

    def _sync_after(self) -> None:
        st, pal = self.state, self.app.get_css_variables()
        if st.is_stela:
            steps = [("Download Stela and make the boot stick", "about 2 min"),
                     (f"Create VM {st.vmid.strip() or '?'}", "no Mac needed"),
                     ("Install macOS 27, by itself", "about 1 hour, until Setup Assistant")]
        elif st.is_arm64:
            steps = [("Create the runner VM", f"VM {st.vmid.strip() or '?'}, about 2 min"),
                     ("The runner builds the beta", "about 10 min, on its own"),
                     ("Install macOS", "not in this beta build yet")]
        else:
            steps = self._opencore_steps()
        self._paint_after(steps, pal)

    def _opencore_steps(self) -> list[tuple[str, str]]:
        st = self.state
        steps = [("Check and rehearse" if self.has_class("narrow") else "Check, download, rehearse",
                  "nothing changes yet"),
                 (f"Create VM {st.vmid.strip() or '?'}", "about 1 min")]
        if st.handsoff:
            steps.append(("Install macOS, hands-off", "30 to 45 min"))
        else:
            steps.append(("Install macOS yourself", "in the Proxmox console of that VM"))
        return steps

    def _paint_after(self, steps: list[tuple[str, str]], pal: dict) -> None:
        text = Text()
        for idx, (head, tail) in enumerate(steps, start=1):
            if idx > 1:
                text.append("\n")
            text.append(f"{idx} ", style=pal["muted"])
            text.append(head)
            text.append("\n  " + tail, style=pal["muted"])
        self.query_one("#after", Static).update(text)

    # ── reading input ────────────────────────────────────────────────

    def on_choice_row_changed(self, event: ChoiceRow.Changed) -> None:
        st, rid, value = self.state, event.row.id, event.value
        if rid == "r-macos":
            st.set_macos(value)
            self._set_input("#r-name", st.name)
        elif rid == "r-size":
            st.size = value
        elif rid == "r-storage":
            st.storage = value
        elif rid == "r-gpu":
            st.gpu_pci = "" if value == "none" else value
        elif rid == "r-install":
            st.handsoff = value == "handsoff"
        elif rid == "r-apple":
            st.apple_services = value == "on"
        elif rid == "r-penryn":
            st.use_penryn = value == "on"
            balanced = self._presets()["balanced"]
            # Penryn mode runs on 4 cores; switching it off goes back to this host's usual count
            st.custom_cores = "4" if st.use_penryn else str(balanced.cores)
            st.custom_memory_gb = str(balanced.memory_gb)
            st.custom_disk_gb = str(balanced.disk_gb)
            st.size = "custom"
            self._set_input("#r-cores", st.custom_cores)
            self._set_input("#r-memory", st.custom_memory_gb)
            self._set_input("#r-disk", st.custom_disk_gb)
        elif rid == "r-nic":
            st.e1000 = value == "e1000"
        elif rid == "r-force":
            st.force_download = value == "on"
        self._sync()
        self._clear_errors()

    def on_expand_row_toggled(self, event: ExpandRow.Toggled) -> None:
        self._sync()

    def on_input_changed(self, event: Input.Changed) -> None:
        st, iid, value = self.state, event.input.id or "", event.value
        if self._quiet or (iid == "r-name-input" and value == st.name):
            return  # the screen set it itself
        field = {
            "r-name-input": "name", "r-cores-input": "custom_cores", "r-memory-input": "custom_memory_gb",
            "r-disk-input": "custom_disk_gb", "r-vmid-input": "vmid", "r-bridge-input": "bridge",
            "r-vlan-input": "vlan", "r-iso-input": "iso_dir", "r-installer-input": "installer_path",
            "r-uuid-input": "existing_uuid", "r-vmgenid-input": "vmgenid", "r-mac-input": "mac",
        }.get(iid)
        if field is None:
            return
        setattr(st, field, value)
        if field == "name":
            st.name_edited = True
        self._sync()
        self._clear_errors()

    def _set_input(self, selector: str, value: str) -> None:
        self._quiet = True
        try:
            field = self.query_one(selector, FieldRow).input
            field.value = value
            field.cursor_position = len(value)
        finally:
            self._quiet = False

    def on_input_submitted(self, event: Input.Submitted) -> None:
        event.stop()
        self.action_create()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        event.stop()
        self.action_create()

    # ── focus ────────────────────────────────────────────────────────

    def _chain(self) -> list[Widget]:
        out: list[Widget] = []
        for widget in self.query("#new-form ChoiceRow, #new-form ExpandRow, #new-form FieldRow, #new-form ActionRow"):
            if not self._shown(widget):
                continue
            if isinstance(widget, FieldRow):
                out.append(widget.input)
            else:
                out.append(widget)
        return out

    @staticmethod
    def _shown(widget: Widget) -> bool:
        node: Widget | None = widget
        while node is not None and not isinstance(node, VerticalScroll):
            if not node.display:
                return False
            node = node.parent  # type: ignore[assignment]
        return True

    def action_row(self, delta: int) -> None:
        chain = self._chain()
        if not chain:
            return
        focused = self.focused
        idx = chain.index(focused) if focused in chain else -1
        target = chain[max(0, min(len(chain) - 1, idx + delta))] if idx >= 0 else chain[0]
        target.focus()
        target.scroll_visible()

    # ── create ───────────────────────────────────────────────────────

    def _clear_errors(self) -> None:
        if self.errors:
            self.errors = {}
            self.query_one("#errors", Static).update("")
            for row in self.query(FieldRow):
                row.set_invalid(False)

    def _row_widget(self, key: str) -> Widget | None:
        ids = {
            "macos": "#r-macos", "name": "#r-name", "size": "#r-size", "cores": "#r-cores", "memory": "#r-memory",
            "disk": "#r-disk", "storage": "#r-storage", "gpu": "#r-gpu", "install": "#r-install", "apple": "#r-apple",
            "vmid": "#r-vmid", "bridge": "#r-bridge", "vlan": "#r-vlan", "iso_dir": "#r-iso",
            "installer_path": "#r-installer", "existing_uuid": "#r-uuid", "penryn": "#r-penryn", "nic": "#r-nic",
            "vmgenid": "#r-vmgenid", "mac": "#r-mac", "force": "#r-force", "identity": "#r-identity",
        }
        selector = ids.get(key)
        return self.query_one(selector) if selector else None

    def action_create(self) -> None:
        st = self.state
        presets = self._presets()
        arm_config = None
        stela_config = None
        if st.is_stela:
            errors, stela_config = validate_stela_new_mac(st, presets, self.facts.vm_memory_limit_mb)
            config = None
        elif st.is_arm64:
            errors, arm_config = validate_arm64_new_mac(st, presets, self.facts.vm_memory_limit_mb)
            config = None
        else:
            errors, config = validate_new_mac(st, presets, self.facts.vm_memory_limit_mb, self._smbios_for_create())
        self.errors = errors
        error_box = self.query_one("#errors", Static)
        for row in self.query(FieldRow):
            row.set_invalid(False)
        if errors:
            pal = self.app.get_css_variables()
            shown = list(errors.values())[:2]
            text = Text()
            for idx, message in enumerate(shown):
                if idx:
                    text.append("\n")
                text.append("✗ ", style=pal["error"])
                text.append(message, style=pal["error"])
            if len(errors) > 2:
                text.append(f"  (+{len(errors) - 2} more)", style=pal["muted"])
            error_box.update(text)
            self._focus_first_bad(errors)
            return
        error_box.update("")
        if st.is_stela:
            from .stela_build import StelaBuildScreen
            self.app.switch_screen(StelaBuildScreen(stela_config))
            return
        if st.is_arm64:
            from .arm64_build import Arm64BuildScreen
            self.app.switch_screen(Arm64BuildScreen(arm_config))
            return
        assert config is not None
        from .build import BuildScreen
        # the build replaces this screen: Back on the build goes to your Macs, not to a form that is done
        self.app.switch_screen(BuildScreen(config, unattended=st.handsoff, force_download=st.force_download))

    def _smbios_for_create(self) -> SmbiosIdentity:
        """The identity shown in More when Apple ID is on, with an existing UUID kept; else a fresh one."""
        uuid = self.state.existing_uuid.strip().upper()
        if self.identity is None:
            return resolve_smbios(self.state.macos, self.state.apple_services, uuid)
        return dataclasses.replace(self.identity, uuid=uuid) if uuid else self.identity

    def _focus_first_bad(self, errors: dict[str, str]) -> None:
        first = next(iter(errors))
        if first in MORE_ROWS:
            more = self.query_one("#r-more", ExpandRow)
            if not more.open:
                more.set_open(True)
                self._sync()
        if first in ("cores", "memory", "disk") and self.state.size != "custom":
            first = "size"
        for key in errors:
            widget = self._row_widget(key)
            if isinstance(widget, FieldRow):
                widget.set_invalid(True)
        target = self._row_widget(first)
        if isinstance(target, FieldRow):
            target.input.focus()
        elif target is not None:
            target.focus()
        if target is not None:
            target.scroll_visible()
