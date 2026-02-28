from __future__ import annotations

import json
from dataclasses import asdict, fields
from pathlib import Path

from .domain import VmConfig

_VALID_FIELDS = {f.name for f in fields(VmConfig)}


def _profiles_path() -> Path:
    path = Path.home() / ".config" / "osx-proxmox-next" / "profiles.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.write_text("{}", encoding="utf-8")
    return path


def load_profiles() -> dict[str, VmConfig]:
    raw = json.loads(_profiles_path().read_text(encoding="utf-8"))
    return {
        name: VmConfig(**{k: v for k, v in value.items() if k in _VALID_FIELDS})
        for name, value in raw.items()
    }


def save_profile(name: str, config: VmConfig) -> Path:
    profiles = load_profiles()
    profiles[name] = config
    serialized = {key: asdict(value) for key, value in profiles.items()}
    path = _profiles_path()
    path.write_text(json.dumps(serialized, indent=2, sort_keys=True), encoding="utf-8")
    return path


def get_profile(name: str) -> VmConfig | None:
    return load_profiles().get(name)
