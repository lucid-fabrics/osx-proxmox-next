from __future__ import annotations

import logging
from threading import Thread

from textual.widgets import Button, Input, ProgressBar, Static

from .assets import AssetCheck
from .defaults import (
    DEFAULT_BRIDGE,
    DEFAULT_ISO_DIR,
    DEFAULT_STORAGE,
    default_disk_gb,
    detect_cpu_cores,
    detect_cpu_info,
    detect_memory_mb,
)
from .domain import SUPPORTED_MACOS, VmConfig
from .forms import validate_form_values, build_vm_config_from_values
from .forms.form_handler import FormValues
from .planner import build_plan
from .screens import build_config_summary_text
from .services import run_download_worker
from .smbios import resolve_smbios

log = logging.getLogger(__name__)

__all__ = ["WizardStepsMixin"]


class WizardStepsMixin:
    """Mixin providing wizard step 2-5 logic for NextApp."""

    def _select_os(self, key: str) -> None:
        self.state.selected_os = key  # type: ignore[attr-defined]
        self.state.smbios = resolve_smbios(key, self.state.apple_services)  # type: ignore[attr-defined]
        for os_key in SUPPORTED_MACOS:
            card = self.query_one(f"#os_{os_key}")
            (card.add_class if os_key == key else card.remove_class)("os_selected")
        self.query_one("#next_btn", Button).disabled = False

    def _select_storage(self, target: str) -> None:
        self.state.selected_storage = target  # type: ignore[attr-defined]
        for idx in range(len(self.state.storage_targets)):  # type: ignore[attr-defined]
            btn = self.query_one(f"#storage_{idx}", Button)
            (btn.add_class if self.state.storage_targets[idx] == target else btn.remove_class)("storage_selected")  # type: ignore[attr-defined]

    def _fill_form(self, storage_fallback: str = "") -> None:
        macos = self.state.selected_os or "sequoia"  # type: ignore[attr-defined]
        self._set_input_value("#vmid", str(self._detect_next_vmid()))  # type: ignore[attr-defined]
        self._set_input_value("#name", f"macos-{macos}")  # type: ignore[attr-defined]
        self._set_input_value("#cores", str(detect_cpu_cores()))  # type: ignore[attr-defined]
        self._set_input_value("#memory", str(detect_memory_mb()))  # type: ignore[attr-defined]
        self._set_input_value("#disk", str(default_disk_gb(macos)))  # type: ignore[attr-defined]
        self._set_input_value("#bridge", DEFAULT_BRIDGE)  # type: ignore[attr-defined]
        self._set_input_value(  # type: ignore[attr-defined]
            "#storage_input",
            storage_fallback or self.state.selected_storage or DEFAULT_STORAGE,  # type: ignore[attr-defined]
        )
        self._set_input_value("#iso_dir", self.state.selected_iso_dir)  # type: ignore[attr-defined]
        self._set_input_value("#installer_path", "")  # type: ignore[attr-defined]
        self._update_smbios_preview()  # type: ignore[attr-defined]

    def _apply_host_defaults(self) -> None:
        self._fill_form(storage_fallback=self.state.selected_storage or DEFAULT_STORAGE)  # type: ignore[attr-defined]
        if not self.state.smbios:  # type: ignore[attr-defined]
            macos = self.state.selected_os or "sequoia"  # type: ignore[attr-defined]
            existing_uuid = self.query_one("#existing_uuid", Input).value.strip().upper()
            self.state.smbios = resolve_smbios(macos, self.state.apple_services, existing_uuid)  # type: ignore[attr-defined]
        self._update_smbios_preview()  # type: ignore[attr-defined]

    def _generate_smbios(self) -> None:
        macos = self.state.selected_os or "sequoia"  # type: ignore[attr-defined]
        existing_uuid = self.query_one("#existing_uuid", Input).value.strip().upper()
        self.state.smbios = resolve_smbios(macos, self.state.apple_services, existing_uuid)  # type: ignore[attr-defined]
        self._update_smbios_preview()  # type: ignore[attr-defined]

    def _update_smbios_preview(self) -> None:
        smbios = self.state.smbios  # type: ignore[attr-defined]
        if smbios:
            text = f"SMBIOS: serial={smbios.serial}  uuid={smbios.uuid}  model={smbios.model}"
            if self.state.apple_services:  # type: ignore[attr-defined]
                text += "  [Apple Services]"
        else:
            text = "SMBIOS: not generated yet."
        self.query_one("#smbios_preview", Static).update(text)

    def _validate_form(self, quiet: bool = False) -> bool:
        values = self._read_form_values()  # type: ignore[attr-defined]
        errors = validate_form_values(values)
        for field_id in ("vmid", "name", "memory", "disk", "bridge", "storage_input"):
            widget = self.query_one(f"#{field_id}", Input)
            (widget.add_class if field_id in errors else widget.remove_class)("invalid")
        self.state.form_errors = errors  # type: ignore[attr-defined]
        if errors:
            self.query_one("#form_errors", Static).update(" ".join(errors.values()))
            if not quiet:
                self.notify("Fix form errors before continuing", severity="warning")  # type: ignore[attr-defined]
            return False
        self.query_one("#form_errors", Static).update("")
        return True

    def _show_form_errors(self, issues: list[str]) -> None:
        self.query_one("#form_errors", Static).update(" ".join(issues))
        self.notify("Validation failed", severity="error")  # type: ignore[attr-defined]

    def _read_form_values(self) -> FormValues:
        st = self.state  # type: ignore[attr-defined]
        return FormValues(
            vmid=self.query_one("#vmid", Input).value.strip(),
            name=self.query_one("#name", Input).value.strip(),
            cores=self.query_one("#cores", Input).value.strip(),
            memory=self.query_one("#memory", Input).value.strip(),
            disk=self.query_one("#disk", Input).value.strip(),
            bridge=self.query_one("#bridge", Input).value.strip(),
            storage=self.query_one("#storage_input", Input).value.strip(),
            iso_dir=self.query_one("#iso_dir", Input).value.strip(),
            installer_path=self.query_one("#installer_path", Input).value.strip(),
            existing_uuid=self.query_one("#existing_uuid", Input).value.strip().upper(),
            custom_vmgenid=self.query_one("#custom_vmgenid", Input).value.strip() if st.apple_services else "",
            custom_mac=self.query_one("#custom_mac", Input).value.strip() if st.apple_services else "",
            selected_os=st.selected_os,
            apple_services=st.apple_services,
            use_penryn=st.use_penryn,
            net_model=st.net_model,
            smbios=st.smbios,
        )

    def _read_form(self) -> VmConfig | None:
        return build_vm_config_from_values(self._read_form_values())  # type: ignore[attr-defined]

    def _render_config_summary(self) -> None:
        config = self.state.config  # type: ignore[attr-defined]
        if not config:
            return
        text = build_config_summary_text(config, self.state.plan_steps, detect_cpu_info())  # type: ignore[attr-defined]
        self.query_one("#config_summary", Static).update(text)

    def _rebuild_plan_after_download(self) -> None:
        config = self._read_form()  # type: ignore[attr-defined]
        if config:
            self.state.config = config  # type: ignore[attr-defined]
            try:
                self.state.plan_steps = build_plan(config)  # type: ignore[attr-defined]
            except ValueError:
                log.debug("Failed to rebuild plan after download", exc_info=True)
            self._render_config_summary()  # type: ignore[attr-defined]

    def _download_worker(self, config: VmConfig, missing: list[AssetCheck]) -> None:
        def on_progress(phase: str, pct: int) -> None:
            self.call_from_thread(self._update_download_progress, phase, pct)  # type: ignore[attr-defined]
        errors = run_download_worker(config, missing, on_progress=on_progress)
        self.call_from_thread(self._finish_download, errors)  # type: ignore[attr-defined]

    def _update_download_progress(self, phase: str, pct: int) -> None:
        self.state.download_pct = pct  # type: ignore[attr-defined]
        self.state.download_phase = phase  # type: ignore[attr-defined]
        self.query_one("#download_progress", ProgressBar).update(total=100, progress=pct)
        msg = f"Finalizing {phase}..." if pct >= 100 else f"Downloading {phase}... {pct}%"
        self.query_one("#download_status", Static).update(msg)

    def _finish_download(self, errors: list[str]) -> None:
        self.state.download_running = False  # type: ignore[attr-defined]
        self.query_one("#download_progress").add_class("hidden")
        if errors:
            self.state.download_errors = errors  # type: ignore[attr-defined]
            self.query_one("#download_status", Static).update("Download errors: " + "; ".join(errors))
            self.notify("Some downloads failed", severity="error")  # type: ignore[attr-defined]
        else:
            self.state.downloads_complete = True  # type: ignore[attr-defined]
            self._rebuild_plan_after_download()  # type: ignore[attr-defined]
            self.query_one("#download_status", Static).update("Assets: downloaded and ready")
            self.query_one("#dry_run_btn", Button).disabled = False
            self.notify("Assets downloaded", severity="information")  # type: ignore[attr-defined]
