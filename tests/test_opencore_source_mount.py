"""Execute both OpenCore source-mount paths without root or loop devices."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from osx_proxmox_next.script_renderer import _mount_source_oc_script


REPO = Path(__file__).resolve().parent.parent


def _source_mount_script(implementation: str) -> str:
    if implementation == "standalone":
        text = (REPO / "scripts/bash/osx-proxmox-next.sh").read_text()
        start = text.index("  # Mount source ISO")
        end = text.index("  # Copy OpenCore files", start)
        return (
            "mount_source() {\nlocal source_iso=unused.iso\n"
            + text[start:end]
            + "}\nmount_source\n"
        )
    # Device setup is unchanged; exercise the real probe, mount and validation.
    text = _mount_source_oc_script(Path("unused.iso"))
    return text[text.index("SRC_PART="):].removesuffix(" && ")


@pytest.mark.parametrize("implementation", ["standalone", "rendered"])
@pytest.mark.parametrize(
    "layout,selected",
    [
        ({"": "", "p1": "vfat"}, "p1"),
        ({"": "iso9660", "p1": "vfat"}, "p1"),
        ({"": "", "p1": "hfsplus", "p2": "vfat"}, "p2"),
        ({"": "vfat"}, ""),
        ({"": ""}, ""),
        ({"": "iso9660", "p1": "hfsplus"}, ""),
        ({"": "", "p1": "vfat", "p2": "vfat"}, "p1"),
    ],
    ids=[
        "gpt-with-fat-esp", "iso-with-fat-esp", "skip-non-fat-partition",
        "raw-fat-image", "unrecognized-image-fallback", "non-fat-fallback",
        "first-fat-partition",
    ],
)
@pytest.mark.parametrize("mount_exit", [0, 1], ids=["mount-ok", "mount-fails"])
@pytest.mark.parametrize("spaced_path", [False, True], ids=["plain-path", "spaced-path"])
def test_opencore_source_mount(
    tmp_path: Path, implementation: str, layout: dict[str, str],
    selected: str, mount_exit: int, spaced_path: bool,
) -> None:
    loop_dir = tmp_path / ("loop devices" if spaced_path else "loops")
    loop_dir.mkdir()
    loop = loop_dir / "loop0"
    for suffix, fs_type in layout.items():
        Path(str(loop) + suffix).write_text(fs_type)

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    blkid = bin_dir / "blkid"
    blkid.write_text(
        f"#!{sys.executable}\n"
        "import sys\nfrom pathlib import Path\n"
        "if sys.argv[1:3] == ['-o', 'device']:\n"
        "    for name in sys.argv[3:]:\n"
        "        if Path(name).is_file():\n"
        "            sys.stdout.write(name + '\\n')\n"
        "else:\n"
        "    assert sys.argv[1:5] == ['-s', 'TYPE', '-o', 'value']\n"
        "    path = Path(sys.argv[5])\n"
        "    if not path.is_file() or not path.read_text():\n"
        "        sys.exit(2)\n"
        "    sys.stdout.write(path.read_text() + '\\n')\n"
    )
    blkid.chmod(0o755)
    mount_log = tmp_path / "mounted-source"
    script = (
        "set -euo pipefail\n"
        'YW=""; CL=""\n'
        'SRC_LOOP="$TEST_SRC_LOOP"; OC_SRC="$TMPDIR"\n'
        'setup_loop() { printf -v "$1" "%s" "$TEST_SRC_LOOP"; }\n'
        'mount() { printf "%s" "$1" > "$MOUNT_LOG"; return "$MOUNT_EXIT"; }\n'
        'safe_mount() { mount "$@"; }\n'
        "mountpoint() { return 0; }\n"
        + _source_mount_script(implementation)
    )
    result = subprocess.run(
        ["bash", "-c", script], capture_output=True, text=True, timeout=10,
        env={
            **os.environ,
            "PATH": str(bin_dir) + os.pathsep + os.environ["PATH"],
            "TMPDIR": str(tmp_path),
            "TEST_SRC_LOOP": str(loop),
            "MOUNT_LOG": str(mount_log),
            "MOUNT_EXIT": str(mount_exit),
        },
    )
    assert result.returncode == mount_exit, result.stdout + result.stderr
    assert mount_log.read_text() == str(loop) + selected
    assert ("trying raw mount" in result.stdout) == ("vfat" not in layout.values())
