from __future__ import annotations

from ..infrastructure import ProxmoxAdapter

__all__ = ["get_proxmox_adapter"]

_pve: ProxmoxAdapter | None = None


def get_proxmox_adapter() -> ProxmoxAdapter:
    """Lazy singleton ProxmoxAdapter — avoids import-time side effects."""
    global _pve  # noqa: PLW0603
    if _pve is None:
        _pve = ProxmoxAdapter()
    return _pve
