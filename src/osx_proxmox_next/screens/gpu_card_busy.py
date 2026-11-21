"""The card is in use by another VM: what to say and what to offer. The tool never starts, stops or edits that VM."""

from __future__ import annotations

from ..gpu import Holder
from .gpu_run import Guidance

BUSY_FAIL = "Card is in use by VM {ids}"


def busy_ids(holders: list[Holder]) -> str:
    return ", ".join(str(h.vmid) for h in holders)


def busy_title(holders: list[Holder]) -> str:
    return f"VM {holders[0].vmid} is using the card" if len(holders) == 1 else f"VMs {busy_ids(holders)} are using the card"


def busy_lines(holders: list[Holder], warnings: list[str]) -> tuple[list[str], tuple[int, ...]]:
    """(lines, indexes of the quiet ones): who runs with the card, whether it starts with the host, other warnings."""
    named = [f"{h.name or 'VM ' + str(h.vmid)} (VM {h.vmid}) is running with this card." for h in holders]
    lines = [named[0] + " Shut it down, then press enter."] if len(holders) == 1 else \
        named + ["Shut them down, then press enter."]
    muted: tuple[int, ...] = ()
    starting = [h for h in holders if h.onboot]
    if starting:
        who = "It starts" if len(holders) == 1 else (
            f"VM {starting[0].vmid} starts" if len(starting) == 1 else f"VMs {busy_ids(starting)} start")
        its = "its" if len(starting) == 1 else "their"
        it = "it" if len(starting) == 1 else "them"
        muted = (len(lines),)
        lines.append(f"{who} with this host. Turn off {its} Start at boot option to stop {it} taking the card "
                     "after every restart.")
    return lines + warnings, muted


class CardBusyMixin:
    """The box shown while another VM holds the card, in the check step and again in the attach step."""

    def card_busy(self, holders: list[Holder], warnings: list[str]) -> None:
        lines, muted = busy_lines(holders, warnings)
        ids = busy_ids(holders)
        reason = BUSY_FAIL.format(ids=ids) if len(holders) == 1 else f"Card is in use by VMs {ids}"
        self._guidance(Guidance(busy_title(holders), lines, "Check again", self.step_verify,
                                ("Leave", self.action_back), fail=("check", reason), muted=muted))
