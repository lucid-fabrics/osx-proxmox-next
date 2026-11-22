"""The Home marker `driver update`: is a newer driver known for a Mac. It reads local files only. The newest releases
are asked from GitHub at most once a day, by a separate worker, and never while no Mac has a driver record."""

from __future__ import annotations

from collections.abc import Callable

from . import driver_engine as engine
from .driver_targets import latest_known, pinned_amd

__all__ = ["has_update", "refresh_if_due"]


def latest_map(base: str | None = None) -> dict[str, str]:
    """The newest versions known: the cached GitHub check plus what this release pins for AMD."""
    latest = dict(engine.read_check(base).get("latest", {}))
    latest.update({name: comp["version"] for name, comp in pinned_amd().items()})
    return latest


def has_update(vmid: int, base: str | None = None) -> bool:
    """True when a component this Mac has installed has a newer known release."""
    record = engine.read_record(vmid, base)
    if not record:
        return False
    latest = latest_map(base)
    for name, version in (record.get("latest") or {}).items():     # what the update that wrote the record knew as newest
        if name not in latest or engine.is_newer(version, latest.get(name)):
            latest[name] = version
    have = engine.installed_versions(record)
    return any(engine.is_newer(latest.get(name), have.get(name)) for name in engine.COMPONENTS[record["kind"]])


def refresh_if_due(vmids: list[int], get: Callable | None = None, now: float | None = None,
                   base: str | None = None) -> bool:
    """Ask GitHub for the newest releases when the last check is a day old and a listed Mac has a driver record.
    Returns True when the cache changed. A failed check changes nothing and is tried again next time."""
    if not any(engine.read_record(v, base) for v in vmids) or not engine.check_due(now, base):
        return False
    try:
        engine.write_check(latest_known(get), now, base)
    except Exception:  # noqa: BLE001 - offline, rate limited or a release that cannot be used: no marker, no error
        return False
    return True
