"""Donation/support messaging shared by CLI and TUI so copy never drifts."""

KOFI_URL = "https://ko-fi.com/lucidfabrics"

SUPPORT_LINE = f"c[_] If this saved you time: {KOFI_URL}"

SUPPORT_LINES_POST_INSTALL = (
    r"     ) )",
    r"    ( (",
    r"  ._______.",
    r"  |       |)   Manual OpenCore setup takes hours.",
    r"  |       |    This took minutes.",
    r"   \_____/     Worth a coffee?",
    f"               {KOFI_URL}",
)
