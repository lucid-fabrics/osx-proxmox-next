"""The checkbox of the app: a check mark in the accent colour inside the box when on, an empty box when off."""

from __future__ import annotations

from textual.widgets import Checkbox

__all__ = ["Check"]


class Check(Checkbox):
    BUTTON_INNER = "✓"
