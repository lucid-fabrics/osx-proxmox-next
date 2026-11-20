"""Lines with a value on the right edge: the "What it takes on this host" panel and the BIOS settings."""

from __future__ import annotations

from dataclasses import dataclass

from rich.text import Text
from textual.widget import Widget

from .rows import wrap_text

__all__ = ["Line", "Lines"]


@dataclass
class Line:
    """One row: *left*, and *right* flush with the right edge. A long left text runs onto more rows (*wrap*)."""
    left: Text
    right: Text | None = None
    indent: int = 0
    wrap: bool = True


class Lines(Widget):
    """A list of Line rows that grows to the height it needs; nothing is cut off."""

    DEFAULT_CSS = "Lines { height: auto; }"

    def __init__(self, *, id: str | None = None, classes: str | None = None) -> None:
        super().__init__(id=id, classes=classes)
        self.rows: list[Line] = []

    def set_lines(self, rows: list[Line]) -> None:
        self.rows = rows
        self.refresh(layout=True)

    def _layout(self, width: int) -> list[Text]:
        console = self.app.console
        out: list[Text] = []
        for row in self.rows:
            pad = " " * row.indent
            inner = max(8, width - row.indent)
            pieces = wrap_text(row.left, inner, console) if row.wrap else [row.left]
            for idx, piece in enumerate(pieces):
                out.append(Text(pad).append_text(piece))
            if row.right is None:
                continue
            last = out[-1]
            room = width - last.cell_len - row.right.cell_len
            if room >= 1:
                last.append(" " * room).append_text(row.right)
            else:
                out.append(Text("  " + pad).append_text(row.right))
        return out

    def get_content_height(self, container, viewport, width: int) -> int:
        return len(self._layout(width or 40))

    def render(self) -> Text:
        out = Text()
        for idx, line in enumerate(self._layout(self.size.width or 40)):
            out.append("\n" if idx else "")
            out.append_text(line)
        return out

    def render_text(self) -> str:
        """The rows as plain text, a value two spaces after its label (what tests and screen readers read)."""
        return "\n".join(
            " " * r.indent + r.left.plain + (("  " + r.right.plain) if r.right is not None else "")
            for r in self.rows)
