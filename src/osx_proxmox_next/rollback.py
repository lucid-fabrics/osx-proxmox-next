from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from .infrastructure import ProxmoxAdapter


@dataclass
class RollbackSnapshot:
    vmid: int
    path: Path


def create_snapshot(vmid: int, adapter: ProxmoxAdapter | None = None) -> RollbackSnapshot:
    out_dir = Path.cwd() / "generated" / "snapshots"
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = out_dir / f"vm-{vmid}-{ts}.conf"

    if adapter is None:
        from .services.proxmox_service import get_proxmox_adapter
        adapter = get_proxmox_adapter()
    runtime = adapter
    result = runtime.run(["qm", "config", str(vmid)])
    content = result.output if result.ok else "# No existing VM config captured\n"
    path.write_text(content, encoding="utf-8")
    return RollbackSnapshot(vmid=vmid, path=path)


def rollback_hints(snapshot: RollbackSnapshot) -> list[str]:
    return [
        f"Review snapshot: {snapshot.path}",
        f"If needed: qm destroy {snapshot.vmid} --purge",
        "Re-apply previous known-good config from snapshot content.",
    ]
