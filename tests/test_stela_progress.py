from osx_proxmox_next.stela_progress import StelaTracker, is_guest_dhcp


MAC = "aa:bb:cc:dd:ee:ff"


def _dhcp_frame(
    mac: str = MAC,
    source_port: int = 68,
    destination_port: int = 67,
    vlan: bool = False,
) -> bytes:
    ethernet_header = (
        bytes.fromhex("ffffffffffff")
        + bytes.fromhex(mac.replace(":", ""))
        + (b"\x81\x00\x00\x01\x08\x00" if vlan else b"\x08\x00")
    )
    ipv4_header = (
        bytes([0x45, 0x00, 0x00, 0x1C])
        + bytes([0x00, 0x00, 0x00, 0x00])
        + bytes([0x40, 0x11, 0x00, 0x00])
        + bytes([192, 168, 1, 10])
        + bytes([192, 168, 1, 1])
    )
    udp_header = (
        source_port.to_bytes(2, "big")
        + destination_port.to_bytes(2, "big")
        + (8).to_bytes(2, "big")
        + b"\x00\x00"
    )
    return ethernet_header + ipv4_header + udp_header


def test_menu_is_detected_from_hold_line():
    tracker = StelaTracker()

    messages = tracker.feed("HOLD Press a key to continue")

    assert tracker.menu_ready is True
    assert tracker.phase == "menu"
    assert messages == ["Stela's menu is up"]


def test_install_choice_is_detected():
    tracker = StelaTracker()

    messages = tracker.feed("CHOICE 2 (Install macOS)")

    assert tracker.install_chosen is True
    assert tracker.phase == "install chosen"
    assert messages == ["Install macOS was chosen"]


def test_step_title_with_time_suffix_is_parsed():
    tracker = StelaTracker()

    messages = tracker.feed("STEP Starting the macOS installer t=1.0")

    assert tracker.installer_started is True
    assert tracker.phase == "installing"
    assert messages == [
        "The macOS installer is running (it downloads macOS from Apple, this takes a while)"
    ]


def test_step_lookalike_lines_are_ignored():
    tracker = StelaTracker()

    assert tracker.feed("STEP o something t=1.0") == []
    assert tracker.feed("STEP w something t=2.0") == []
    assert tracker.feed("STEP DETAIL something t=3.0") == []
    assert tracker.installer_started is False
    assert tracker.phase == "starting"


def test_fatal_is_captured():
    tracker = StelaTracker()

    messages = tracker.feed("STELA FATAL disk image failed")

    assert tracker.fatal == "disk image failed"
    assert tracker.phase == "stopped"
    assert messages == []      # the driver reports it, once


def test_stopped_is_captured():
    tracker = StelaTracker()

    messages = tracker.feed("STELA STOPPED waiting for input")

    assert tracker.fatal == "waiting for input"
    assert tracker.phase == "stopped"
    assert messages == []


def test_installer_start_and_completion_via_restart():
    tracker = StelaTracker()

    start_messages = tracker.feed("STEP Starting the macOS installer t=1.0")
    assert tracker.installer_started is True
    assert tracker.installer_done is False
    assert start_messages == [
        "The macOS installer is running (it downloads macOS from Apple, this takes a while)"
    ]

    restart_messages = tracker.feed("STELA RESTART code=0x15")

    assert tracker.installer_done is True
    assert tracker.phase == "installed"
    assert tracker.last_restart_line == 2
    assert restart_messages == ["The installer finished and Stela restarted the PC"]


def test_macos_start_is_counted_only_after_installer_finishes():
    tracker = StelaTracker()

    tracker.feed("STEP Starting the macOS installer t=1.0")
    assert tracker.feed("STEP Starting macOS t=2.0") == []
    assert tracker.macos_started == 0

    tracker.feed("STELA RESTART code=0x15")
    messages = tracker.feed("STEP Starting macOS t=3.0")

    assert tracker.macos_started == 1
    assert tracker.last_macos_start_line == 4
    assert tracker.phase == "macos booting"
    assert messages == ["Starting the installed macOS (boot 1)"]

    tracker.feed("STEP Starting macOS t=4.0")
    assert tracker.macos_started == 2
    assert tracker.last_macos_start_line == 5


def test_restarted_since_latest_macos_start():
    tracker = StelaTracker()

    assert tracker.restarted_since_macos_start is False

    tracker.feed("STEP Starting the macOS installer t=1.0")
    tracker.feed("STELA RESTART code=0x15")
    assert tracker.restarted_since_macos_start is False

    tracker.feed("STEP Starting macOS t=2.0")
    assert tracker.restarted_since_macos_start is False

    tracker.feed("STELA RESTART code=0x15")
    assert tracker.restarted_since_macos_start is True

    tracker.feed("STEP Starting macOS t=3.0")
    assert tracker.restarted_since_macos_start is False


def test_guest_dhcp_frame_is_detected():
    assert is_guest_dhcp(_dhcp_frame(), MAC) is True


def test_guest_dhcp_frame_with_wrong_mac_is_rejected():
    frame = _dhcp_frame(mac="11:22:33:44:55:66")

    assert is_guest_dhcp(frame, MAC) is False


def test_guest_dhcp_frame_with_wrong_port_is_rejected():
    frame = _dhcp_frame(source_port=67, destination_port=68)

    assert is_guest_dhcp(frame, MAC) is False


def test_vlan_tagged_guest_dhcp_frame_is_detected():
    assert is_guest_dhcp(_dhcp_frame(vlan=True), MAC) is True


def test_short_frame_is_rejected():
    frame = _dhcp_frame()[:-1]

    assert is_guest_dhcp(frame, MAC) is False


# ── what the first live run's serial log really says ─────────────────────────────────────────────────

REAL_FATAL = "STELA FATAL Installing macOS needs a network for Apple's signing server. No supported network adapter."


def test_a_real_boot_to_the_first_install_step_is_followed_line_by_line():
    tracker = StelaTracker()
    said = []
    for line in [
        "STELA UEFI MOUNT version=0.5.0",
        "STEP Opening the boot log on the USB t=5.238",
        "STEP o Opening the boot log on the USB (\\EFI\\stela\\boot.log) ms=5",
        "HOLD No disk is chosen for macOS yet: choose Install macOS first.",
        "CHOICE 1 (macOS) left out: its SSD is not prepared",
        "CHOICE 2 (Install macOS)",
        "STEP Getting a network address t=176.737",
        "STEP w Getting a network address (No supported network adapter) ms=3",
        "STEP Starting the macOS installer t=178.629",
    ]:
        said += tracker.feed(line)
    assert tracker.menu_ready and tracker.install_chosen and tracker.installer_started
    assert said[0] == "Stela's menu is up" and said[1] == "Install macOS was chosen"
    assert tracker.fatal == "" and tracker.phase == "installing"


def test_installer_stages_are_reported_once_each_while_the_installer_runs():
    tracker = StelaTracker()
    tracker.feed("STEP Starting the macOS installer t=1.0")
    out = []
    for line in [
        "STEP DETAIL installer: format media | 291362803 blocks executed, 3153808/s ms=1003",
        "STEP DETAIL installer: format media | 296321248 blocks executed, 1277231/s ms=1000",
        "STEP DETAIL installer: update iBoot ms=668",
        "STEP DETAIL macOS kernel | 1 blocks executed ms=1",
    ]:
        out += tracker.feed(line)
    assert out == ["Installer: format media", "Installer: update iBoot"]


def test_installer_stages_are_not_reported_after_the_installer_is_done():
    tracker = StelaTracker()
    tracker.feed("STEP Starting the macOS installer t=1.0")
    tracker.feed("STELA RESTART code=0x15")
    assert tracker.feed("STEP DETAIL installer: format media | 1 blocks ms=1") == []


def test_the_real_fatal_line_keeps_its_reason_and_only_the_first_one_counts():
    tracker = StelaTracker()
    assert tracker.feed(REAL_FATAL) == []
    assert tracker.feed("STELA STOPPED code=0x11; power off to continue") == []
    assert tracker.fatal == "Installing macOS needs a network for Apple's signing server. No supported network adapter."


def test_a_fatal_line_with_no_reason_keeps_the_line_itself():
    tracker = StelaTracker()
    tracker.feed("STELA STOPPED")
    assert tracker.fatal == "STELA STOPPED"


def test_a_restart_before_the_installer_started_is_not_the_end_of_the_install():
    tracker = StelaTracker()
    assert tracker.feed("STELA RESTART code=0x15") == []
    assert not tracker.installer_done
