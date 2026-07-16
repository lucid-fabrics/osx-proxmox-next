"""Tests for scripts/sync_donations.py."""
from __future__ import annotations

import importlib.util
from pathlib import Path
from unittest.mock import MagicMock

import pytest

SCRIPT_PATH = Path(__file__).resolve().parent.parent.parent / "scripts" / "sync_donations.py"


@pytest.fixture
def sync_module():
    spec = importlib.util.spec_from_file_location("sync_donations", SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_extract_donor_kofi(sync_module):
    entry = {"data": {"platform": "kofi", "from_name": "Alice", "amount": "3"}}
    assert sync_module.extract_donor(entry) == "Alice"


def test_extract_donor_bmc(sync_module):
    entry = {"data": {"platform": "buymeacoffee", "name": "Bob", "amount": 5}}
    assert sync_module.extract_donor(entry) == "Bob"


def test_extract_donor_anonymous_returns_none(sync_module):
    assert sync_module.extract_donor({"data": {"platform": "kofi", "from_name": "anonymous"}}) is None
    assert sync_module.extract_donor({"data": {"platform": "kofi", "from_name": ""}}) is None
    assert sync_module.extract_donor({"data": {"platform": "kofi", "from_name": "  "}}) is None
    assert sync_module.extract_donor({"data": {"platform": "kofi"}}) is None


def test_extract_donor_fallback_to_other_field(sync_module):
    # BMC payload missing 'name' but has 'from_name'
    assert sync_module.extract_donor({"data": {"platform": "buymeacoffee", "from_name": "X"}}) == "X"


def test_format_manual_entry_plain(sync_module):
    assert sync_module.format_manual_entry("Alice") == "- 💖 Alice"


def test_format_manual_entry_github_handle(sync_module):
    assert sync_module.format_manual_entry("@alice") == "- [💖 alice](https://github.com/alice)"


def test_existing_names_case_insensitive(tmp_path, sync_module):
    p = tmp_path / "SPONSORS.md"
    p.write_text("# Header\n- Alice\n- BOB\n- [Carol](https://github.com/carol)\n", encoding="utf-8")
    sync_module.MANUAL_FILE = p
    names = sync_module.existing_names()
    # Bare names, normalized to lowercase, with link brackets stripped.
    assert names == {"alice", "bob", "carol"}


def test_existing_names_missing_file(tmp_path, sync_module):
    sync_module.MANUAL_FILE = tmp_path / "missing.md"
    assert sync_module.existing_names() == set()


def test_append_to_manual(tmp_path, sync_module):
    p = tmp_path / "SPONSORS.md"
    p.write_text("# Header\n- Alice\n", encoding="utf-8")
    sync_module.MANUAL_FILE = p
    assert sync_module.append_to_manual(["- 💖 Bob", "- 💖 Carol"]) is True
    content = p.read_text(encoding="utf-8")
    assert "Alice" in content
    assert "Bob" in content
    assert "Carol" in content


def test_append_to_manual_no_new(tmp_path, sync_module):
    p = tmp_path / "SPONSORS.md"
    p.write_text("# Header\n- Alice\n", encoding="utf-8")
    sync_module.MANUAL_FILE = p
    assert sync_module.append_to_manual([]) is False


def test_main_skips_duplicates(tmp_path, sync_module, monkeypatch):
    """Existing donors must not be re-added even if KV still holds their entry."""
    p = tmp_path / "SPONSORS.md"
    p.write_text("- 💖 Alice\n", encoding="utf-8")
    sync_module.MANUAL_FILE = p

    poll_response = {
        "donations": [
            {"key": "kofi_1", "data": {"platform": "kofi", "from_name": "Alice"}},
            {"key": "kofi_2", "data": {"platform": "kofi", "from_name": "Bob"}},
        ],
    }
    deleted_keys: list[str] = []

    monkeypatch.setattr(sync_module, "fetch_donations", lambda token: poll_response["donations"])
    monkeypatch.setattr(sync_module, "delete_entries", lambda keys, token: deleted_keys.extend(keys))
    monkeypatch.setattr(os_module := __import__("os"), "environ", {"POLL_TOKEN": "test"})

    rc = sync_module.main()
    assert rc == 0
    content = p.read_text(encoding="utf-8")
    assert content.count("Alice") == 1
    assert "Bob" in content
    assert "kofi_1" in deleted_keys
    assert "kofi_2" in deleted_keys


def test_main_missing_token_returns_1(tmp_path, sync_module, monkeypatch, capsys):
    sync_module.MANUAL_FILE = tmp_path / "SPONSORS.md"
    monkeypatch.setattr("os.environ", {})
    rc = sync_module.main()
    assert rc == 1
    captured = capsys.readouterr()
    assert "POLL_TOKEN" in captured.err