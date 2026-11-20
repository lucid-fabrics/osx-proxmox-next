"""GPU passthrough dialogs must fit the terminal (the Proxmox web shell is often 24x80)."""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

SCRIPT = (Path(__file__).resolve().parents[1] / "scripts/bash/osx-proxmox-next.sh").read_text()
FN = re.search(r"^function gpu_ui_size\(\) \{.*?^\}", SCRIPT, re.M | re.S).group(0)
LONG = "\n".join(f"Step {i}: set something in the BIOS" for i in range(1, 51))


def size(text: str, rows: int, cols: int) -> tuple[int, int, int]:
    out = subprocess.run(["bash", "-c", f'set -euo pipefail\n{FN}\ngpu_ui_size "$1" 100 "$2" "$3"', "_", text, str(rows), str(cols)],
                         capture_output=True, text=True, check=True).stdout.split()
    h, w, over = (int(x) for x in out)
    return h, w, over


@pytest.mark.parametrize("rows,cols", [(24, 80), (15, 50), (40, 120), (60, 200)])
def test_long_text_box_fits_the_terminal(rows, cols):
    h, w, over = size(LONG, rows, cols)
    assert h <= rows - 2 and w <= min(100, cols - 4)
    assert over == (h < 50 + 7)


def test_short_question_gets_a_small_box():
    assert size("Reboot now?", 24, 80) == (8, 76, 0)


def test_tall_terminal_shows_everything_without_scrolling():
    h, _, over = size(LONG, 80, 200)
    assert over == 0 and h == 57


def test_no_gpu_dialog_uses_a_fixed_size():
    assert not re.search(r'whiptail --backtitle "OSX Proxmox Next - GPU passthrough".* \d+ \d+$', SCRIPT, re.M)
