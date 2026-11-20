"""The checkbox of the app: an accent check mark inside the box when on, an empty box when off."""

from __future__ import annotations

from textual.widgets import Checkbox

__all__ = ["Check"]


class Check(Checkbox):
    """The box holds a check mark when on and a space when off, so a copy of the screen tells the truth too."""

    @property
    def BUTTON_INNER(self) -> str:  # noqa: N802 - Textual's own name for it
        return "✓" if self.value else " "
