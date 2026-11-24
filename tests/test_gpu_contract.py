"""The GPU screen mixins declare what they share through GpuHost, a base that exists for the type checker only.

These tests pin that it changes nothing at run time: GpuHost is plain `object`, GpuScreen keeps the method
resolution order it had before the base existed, and every method that more than one class defines still resolves
to the same class."""
from __future__ import annotations

import ast
import inspect
import sys

from osx_proxmox_next.screens import gpu_contract
from osx_proxmox_next.screens.gpu import GpuScreen
from osx_proxmox_next.screens.gpu_attach_step import AttachMixin
from osx_proxmox_next.screens.gpu_bios import BiosMixin
from osx_proxmox_next.screens.gpu_boot import BootMixin
from osx_proxmox_next.screens.gpu_card_busy import CardBusyMixin
from osx_proxmox_next.screens.gpu_choose import ChooseMixin
from osx_proxmox_next.screens.gpu_restart import RestartMixin
from osx_proxmox_next.screens.gpu_run import RunMixin

PACKAGE = "osx_proxmox_next"

EXPECTED_MRO = [
    "GpuScreen", "Readable", "ChooseMixin", "BiosMixin", "RunMixin", "RestartMixin", "AttachMixin",
    "CardBusyMixin", "BootMixin", "OsxScreen", "Screen", "Generic", "Widget", "DOMNode", "MessagePump", "object",
]

# Names defined by more than one of our own classes on the screen, and the class whose version wins.
EXPECTED_WINNERS = {
    "BINDINGS": "GpuScreen",
    "HELP_EXTRA": "GpuScreen",
    "action_back": "GpuScreen",
    "busy": "GpuScreen",
    "can_focus": "GpuScreen",
    "can_focus_children": "GpuScreen",
    "compose_body": "GpuScreen",
    "crumb": "GpuScreen",
    "footer_items": "GpuScreen",
    "on_resize": "GpuScreen",
    "read_more": "GpuScreen",
    "read_room": "GpuScreen",
    "read_scroll": "GpuScreen",
}


def test_the_contract_is_plain_object_at_run_time() -> None:
    assert gpu_contract.GpuHost is object
    for mixin in (ChooseMixin, BiosMixin, RunMixin, RestartMixin, BootMixin, CardBusyMixin):
        assert mixin.__bases__ == (object,)
    assert AttachMixin.__bases__ == (CardBusyMixin,)


def test_every_mixin_declares_the_contract_in_its_source() -> None:
    # GpuHost is plain object at run time, so issubclass cannot see it: read the class statements instead.
    # AttachMixin reaches the contract through CardBusyMixin.
    for mixin in (ChooseMixin, BiosMixin, RunMixin, RestartMixin, AttachMixin, BootMixin, CardBusyMixin):
        tree = ast.parse(inspect.getsource(sys.modules[mixin.__module__]))
        cls = next(n for n in ast.walk(tree) if isinstance(n, ast.ClassDef) and n.name == mixin.__name__)
        want = "CardBusyMixin" if mixin is AttachMixin else "GpuHost"
        assert [b.id for b in cls.bases if isinstance(b, ast.Name)] == [want], mixin.__name__


def test_the_screen_keeps_its_method_resolution_order() -> None:
    assert [c.__name__ for c in GpuScreen.__mro__] == EXPECTED_MRO


def test_shared_names_resolve_to_the_same_class() -> None:
    own = [c for c in GpuScreen.__mro__ if c.__module__.startswith(PACKAGE)]
    owners: dict[str, list[str]] = {}
    for cls in own:
        for name in vars(cls):
            if not (name.startswith("__") and name.endswith("__")):
                owners.setdefault(name, []).append(cls.__name__)
    winners = {}
    for name, classes in owners.items():
        # Textual's own bookkeeping (_css_type_name, _reactives, ...) is rebuilt per class, so it is not pinned.
        if len(classes) > 1 and not name.startswith("_"):
            winners[name] = next(c.__name__ for c in GpuScreen.__mro__ if name in vars(c))
    assert winners == EXPECTED_WINNERS
