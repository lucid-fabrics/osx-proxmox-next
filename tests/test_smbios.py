import re
import uuid as uuid_mod

from osx_proxmox_next.smbios import (
    APPLE_PLATFORM_DATA,
    BASE34,
    _YEAR_CHARS,
    _encode_year_week,
    _verify_mlb_checksum,
    generate_mlb,
    generate_rom,
    generate_rom_from_mac,
    generate_serial,
    generate_smbios,
    generate_uuid,
    model_for_macos,
)


def test_serial_format() -> None:
    serial = generate_serial()
    assert len(serial) == 12
    assert re.fullmatch(r"[A-Z0-9]{12}", serial)


def test_mlb_format() -> None:
    mlb = generate_mlb()
    assert len(mlb) == 17
    assert re.fullmatch(r"[A-Z0-9]{17}", mlb)


def test_uuid_format() -> None:
    u = generate_uuid()
    parsed = uuid_mod.UUID(u)
    assert parsed.version == 4


def test_rom_format() -> None:
    rom = generate_rom()
    assert len(rom) == 12
    assert re.fullmatch(r"[A-F0-9]{12}", rom)


def test_model_for_known_macos() -> None:
    assert model_for_macos("ventura") == "MacPro7,1"
    assert model_for_macos("sonoma") == "MacPro7,1"
    assert model_for_macos("sequoia") == "MacPro7,1"
    assert model_for_macos("tahoe") == "MacPro7,1"


def test_model_for_unknown_macos() -> None:
    assert model_for_macos("unknown") == "MacPro7,1"


def test_generate_smbios_complete() -> None:
    identity = generate_smbios("sequoia")
    assert len(identity.serial) == 12
    assert len(identity.mlb) == 17
    assert len(identity.rom) == 12
    assert identity.model == "MacPro7,1"
    uuid_mod.UUID(identity.uuid)


def test_generate_rom_from_mac() -> None:
    assert generate_rom_from_mac("02:AB:CD:EF:01:23") == "02ABCDEF0123"
    assert generate_rom_from_mac("aa:bb:cc:dd:ee:ff") == "AABBCCDDEEFF"


def test_generate_smbios_apple_services_derives_rom_from_mac() -> None:
    identity = generate_smbios("sequoia", apple_services=True)
    assert identity.mac != ""
    expected_rom = identity.mac.replace(":", "")[:12].upper()
    assert identity.rom == expected_rom


def test_generate_smbios_no_apple_services_random_rom() -> None:
    identity = generate_smbios("sequoia", apple_services=False)
    assert identity.mac == ""
    assert len(identity.rom) == 12


def test_uniqueness() -> None:
    serials = {generate_serial() for _ in range(100)}
    assert len(serials) == 100

    uuids = {generate_uuid() for _ in range(100)}
    assert len(uuids) == 100

    mlbs = {generate_mlb() for _ in range(100)}
    assert len(mlbs) == 100


# ---------------------------------------------------------------------------
# Apple-format serial tests
# ---------------------------------------------------------------------------

BASE34_PATTERN = re.compile(r"^[0-9A-HJ-NP-Z]+$")


def test_apple_serial_format() -> None:
    serial = generate_serial(apple_services=True)
    assert len(serial) == 12
    assert BASE34_PATTERN.fullmatch(serial), f"Non-base34 chars in serial: {serial}"


def test_apple_serial_model_code_suffix() -> None:
    platform = APPLE_PLATFORM_DATA["MacPro7,1"]
    for _ in range(20):
        serial = generate_serial(apple_services=True, model="MacPro7,1")
        suffix = serial[8:]
        assert suffix in platform["model_codes"], f"Bad model code: {suffix}"


def test_apple_serial_country_prefix() -> None:
    platform = APPLE_PLATFORM_DATA["MacPro7,1"]
    for _ in range(20):
        serial = generate_serial(apple_services=True, model="MacPro7,1")
        prefix = serial[:3]
        assert prefix in platform["country_codes"], f"Bad country: {prefix}"


def test_apple_mlb_format() -> None:
    serial = generate_serial(apple_services=True)
    mlb = generate_mlb(apple_services=True, serial=serial)
    assert len(mlb) == 17
    assert BASE34_PATTERN.fullmatch(mlb), f"Non-base34 chars in MLB: {mlb}"


def test_apple_mlb_checksum() -> None:
    for _ in range(50):
        serial = generate_serial(apple_services=True)
        mlb = generate_mlb(apple_services=True, serial=serial)
        assert _verify_mlb_checksum(mlb), f"MLB checksum failed: {mlb}"


def test_apple_mlb_board_code() -> None:
    platform = APPLE_PLATFORM_DATA["MacPro7,1"]
    for _ in range(20):
        serial = generate_serial(apple_services=True)
        mlb = generate_mlb(apple_services=True, serial=serial)
        board = mlb[11:15]
        assert board in platform["board_codes"], f"Bad board code: {board}"


def test_apple_serial_mlb_country_consistency() -> None:
    for _ in range(20):
        serial = generate_serial(apple_services=True)
        mlb = generate_mlb(apple_services=True, serial=serial)
        assert serial[:3] == mlb[:3], (
            f"Country mismatch: serial={serial[:3]} mlb={mlb[:3]}"
        )


def test_apple_mlb_checksum_rejects_corruption() -> None:
    serial = generate_serial(apple_services=True)
    mlb = generate_mlb(apple_services=True, serial=serial)
    assert _verify_mlb_checksum(mlb)
    # Corrupt one character — single-char change always invalidates checksum
    # because gcd(weight, 34) = 1 for both weights (1 and 3).
    chars = list(mlb)
    orig = chars[8]
    for c in BASE34:
        if c != orig:
            chars[8] = c
            break
    corrupted = "".join(chars)
    assert not _verify_mlb_checksum(corrupted)


def test_apple_serial_year_char_valid() -> None:
    """Serial position 3 must be a valid _YEAR_CHARS entry for the model's year range."""
    valid_year_chars: set[str] = set()
    for year in range(*APPLE_PLATFORM_DATA["MacPro7,1"]["year_range"], 1):
        for week in (1, 27):
            yc, _ = _encode_year_week(year, week)
            valid_year_chars.add(yc)
    # Include the final year
    for week in (1, 27):
        yc, _ = _encode_year_week(APPLE_PLATFORM_DATA["MacPro7,1"]["year_range"][1], week)
        valid_year_chars.add(yc)

    for _ in range(50):
        serial = generate_serial(apple_services=True)
        assert serial[3] in valid_year_chars, (
            f"Bad year char '{serial[3]}' in serial={serial}, expected one of {valid_year_chars}"
        )


def test_apple_serial_week_char_valid() -> None:
    """Serial position 4 must encode a week index 1-26 (never 0 or >26)."""
    for _ in range(50):
        serial = generate_serial(apple_services=True)
        week_index = BASE34.index(serial[4])
        assert 1 <= week_index <= 26, (
            f"Bad week char '{serial[4]}' (index={week_index}) in serial={serial}"
        )


def test_apple_serial_uniqueness() -> None:
    serials = {generate_serial(apple_services=True) for _ in range(100)}
    assert len(serials) == 100


def test_backwards_compat_serial() -> None:
    serial = generate_serial(apple_services=False)
    assert len(serial) == 12
    assert re.fullmatch(r"[A-Z0-9]{12}", serial)


def test_backwards_compat_mlb() -> None:
    mlb = generate_mlb(apple_services=False)
    assert len(mlb) == 17
    assert re.fullmatch(r"[A-Z0-9]{17}", mlb)


def test_apple_mlb_year_week_in_valid_range() -> None:
    """MLB year_dec and week_dec must be plausible decimal values."""
    for _ in range(50):
        identity = generate_smbios("sequoia", apple_services=True)
        mlb = identity.mlb
        year_dec = int(mlb[3])
        week_dec = int(mlb[4:6])
        # Year last digit should be 9,0,1,2,3 (for 2019-2023)
        assert year_dec in {9, 0, 1, 2, 3}, f"Bad year_dec={year_dec} in MLB={mlb}"
        # Week should be 1-52
        assert 1 <= week_dec <= 52, f"Bad week_dec={week_dec} in MLB={mlb}"


def test_generate_smbios_apple_services_full() -> None:
    identity = generate_smbios("sequoia", apple_services=True)

    # Serial: 12 chars, base34, valid model code + country
    assert len(identity.serial) == 12
    assert BASE34_PATTERN.fullmatch(identity.serial)
    platform = APPLE_PLATFORM_DATA["MacPro7,1"]
    assert identity.serial[:3] in platform["country_codes"]
    assert identity.serial[8:] in platform["model_codes"]

    # MLB: 17 chars, base34, valid checksum, board code, country match
    assert len(identity.mlb) == 17
    assert BASE34_PATTERN.fullmatch(identity.mlb)
    assert _verify_mlb_checksum(identity.mlb)
    assert identity.mlb[11:15] in platform["board_codes"]
    assert identity.serial[:3] == identity.mlb[:3]

    # ROM derived from MAC
    assert identity.mac != ""
    assert identity.rom == identity.mac.replace(":", "")[:12].upper()
