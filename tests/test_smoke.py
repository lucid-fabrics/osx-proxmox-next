import re

from osx_proxmox_next import __version__


def test_version() -> None:
    assert re.fullmatch(r"\d+\.\d+\.\d+", __version__)
