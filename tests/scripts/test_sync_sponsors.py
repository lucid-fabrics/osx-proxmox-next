"""Tests for scripts/sync_sponsors.py.

Covers deterministic functions only (render_block, update_readme, load_manual).
fetch_github_sponsors() requires an authenticated `gh` CLI - skip in CI.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

SCRIPT_PATH = Path(__file__).resolve().parent.parent.parent / "scripts" / "sync_sponsors.py"


@pytest.fixture
def sync_module():
    spec = importlib.util.spec_from_file_location("sync_sponsors", SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_render_block_with_github_sponsors(sync_module):
    github = [
        {"login": "alice", "amount": 10, "tier": "Backer"},
        {"login": "bob", "amount": 5, "tier": "Supporter"},
    ]
    block = sync_module.render_block(github, [])
    assert "**Sponsors:**" in block
    assert "[alice](https://github.com/alice)" in block
    assert "Backer ($10/mo)" in block
    assert "[bob](https://github.com/bob)" in block
    assert "Supporter ($5/mo)" in block
    # Higher amount first
    assert block.index("alice") < block.index("bob")


def test_render_block_sorts_by_amount_desc(sync_module):
    github = [
        {"login": "low", "amount": 1, "tier": "Tier"},
        {"login": "high", "amount": 50, "tier": "Tier"},
        {"login": "mid", "amount": 5, "tier": "Tier"},
    ]
    block = sync_module.render_block(github, [])
    assert block.index("high") < block.index("mid") < block.index("low")


def test_render_block_empty_github_shows_placeholder(sync_module):
    block = sync_module.render_block([], [])
    assert "No GitHub Sponsors yet" in block
    assert "github.com/sponsors/lucid-fabrics" in block


def test_render_block_with_manual_entries(sync_module):
    manual = ["Charlie", "[Dora](https://example.com/dora)"]
    block = sync_module.render_block([], manual)
    # Manual entries render directly under the main Sponsors header.
    assert "**Past supporters" not in block
    assert "❤️ Charlie" in block
    assert "❤️ [Dora](https://example.com/dora)" in block
    # Manual-only path shows the recurring-sponsor CTA.
    assert "Sponsor on GitHub" in block


def test_render_block_combined(sync_module):
    github = [{"login": "alice", "amount": 10, "tier": "Backer"}]
    manual = ["Bob"]
    block = sync_module.render_block(github, manual)
    assert "[alice]" in block
    # When GitHub sponsors exist, the CTA is suppressed.
    assert "Sponsor on GitHub" not in block
    assert "❤️ Bob" in block


def test_render_block_no_split_subsection(sync_module):
    """No 'Past supporters' subsection - all entries share the main header."""
    block = sync_module.render_block(
        [{"login": "alice", "amount": 5, "tier": "T"}],
        ["Bob"],
    )
    assert "**Sponsors:**" in block
    assert "**Past supporters" not in block
    assert block.count("❤️") == 2


def test_update_readme_replaces_block(tmp_path, sync_module):
    readme = tmp_path / "README.md"
    readme.write_text(
        "Header\n\n"
        "**Sponsors:**\n"
        "- ❤️ [OldOne](https://github.com/old)\n"
        "\n"
        "---\n"
        "Footer\n",
        encoding="utf-8",
    )
    sync_module.README = readme
    new_block = "**Sponsors:**\n\n- ❤️ [NewOne](https://github.com/new)\n"
    assert sync_module.update_readme(new_block) is True
    content = readme.read_text(encoding="utf-8")
    assert "[NewOne]" in content
    assert "[OldOne]" not in content
    # Footer preserved
    assert "Footer" in content
    # Separator preserved
    assert content.count("---") == 1


def test_update_readme_idempotent(tmp_path, sync_module):
    readme = tmp_path / "README.md"
    block = "**Sponsors:**\n\n- ❤️ [X](https://github.com/x)\n"
    readme.write_text(f"Header\n\n{block}\n---\nFooter\n", encoding="utf-8")
    sync_module.README = readme
    assert sync_module.update_readme(block) is False


def test_load_manual_skips_non_bullet_lines(sync_module, tmp_path):
    manual = tmp_path / "SPONSORS.md"
    manual.write_text(
        "# Header line\n"
        "Some prose\n"
        "- [Alice](https://github.com/alice)\n"
        "- [Bob]\n"
        "   - indented included after strip\n"
        "\n",
        encoding="utf-8",
    )
    sync_module.MANUAL_FILE = manual
    result = sync_module.load_manual()
    joined = "\n".join(result)
    assert "[Alice]" in joined
    assert "[Bob]" in joined
    assert "Header line" not in joined
    assert "Some prose" not in joined


def test_load_manual_missing_file(sync_module, tmp_path):
    sync_module.MANUAL_FILE = tmp_path / "missing.md"
    assert sync_module.load_manual() == []


def test_update_readme_normalizes_crlf(tmp_path, sync_module):
    """CRLF line endings in README must not break the Sponsors regex."""
    readme = tmp_path / "README.md"
    readme.write_text(
        "Header\r\n\r\n**Sponsors:**\r\n- stale\r\n\r\n---\r\nFooter\r\n",
        encoding="utf-8",
    )
    sync_module.README = readme
    new_block = "**Sponsors:**\n\n- ❤️ [new](https://github.com/new)\n"
    assert sync_module.update_readme(new_block) is True
    content = readme.read_text(encoding="utf-8")
    assert "[new]" in content
    assert "stale" not in content
    # CRLF must be normalized to LF on write so future regex matches hold.
    assert "\r\n" not in content


def test_fetch_paginates_until_end(monkeypatch, sync_module):
    """Pagination loop must follow pageInfo.hasNextPage and stop when false.

    Also asserts that the cursor returned from page 1 is forwarded to the
    page 2 request via `-F after=...`.
    """
    import json as _json
    page1 = {
        "data": {"organization": {"sponsorshipsAsMaintainer": {
            "pageInfo": {"hasNextPage": True, "endCursor": "C1"},
            "nodes": [{"sponsorEntity": {"login": "alice"}, "tier": {"monthlyPriceInDollars": 10, "name": "T"}}],
        }}},
    }
    page2 = {
        "data": {"organization": {"sponsorshipsAsMaintainer": {
            "pageInfo": {"hasNextPage": False, "endCursor": None},
            "nodes": [{"sponsorEntity": {"login": "bob"}, "tier": {"monthlyPriceInDollars": 5, "name": "T"}}],
        }}},
    }
    responses = [page1, page2]
    calls: list[list[str]] = []

    def fake_run(cmd, **kwargs):
        calls.append(list(cmd))
        from unittest.mock import MagicMock
        out = _json.dumps(responses.pop(0))
        m = MagicMock()
        m.stdout = out
        m.returncode = 0
        return m

    monkeypatch.setattr(sync_module.shutil, "which", lambda _: "/usr/bin/gh")
    monkeypatch.setattr(sync_module.subprocess, "run", fake_run)

    sponsors = sync_module.fetch_github_sponsors()
    assert [s["login"] for s in sponsors] == ["alice", "bob"]
    assert len(responses) == 0  # both pages consumed
    # First call has no `after` flag; second call carries the cursor.
    assert "after=C1" in calls[1]
    assert all("after=" not in c for c in calls[:1])


def test_fetch_caps_at_ten_pages(monkeypatch, sync_module):
    """Pagination must stop at 10 pages even if hasNextPage stays true."""
    import json as _json

    def make_page(cursor):
        return {
            "data": {"organization": {"sponsorshipsAsMaintainer": {
                "pageInfo": {"hasNextPage": True, "endCursor": cursor},
                "nodes": [],
            }}},
        }

    responses = [make_page(f"C{i}") for i in range(20)]

    def fake_run(cmd, **kwargs):
        from unittest.mock import MagicMock
        out = _json.dumps(responses.pop(0))
        m = MagicMock()
        m.stdout = out
        m.returncode = 0
        return m

    monkeypatch.setattr(sync_module.shutil, "which", lambda _: "/usr/bin/gh")
    monkeypatch.setattr(sync_module.subprocess, "run", fake_run)

    sponsors = sync_module.fetch_github_sponsors()
    assert sponsors == []
    # 10 pages consumed, 10 left in responses (never touched).
    assert len(responses) == 10


def test_fetch_deduplicates_sponsors(monkeypatch, sync_module):
    """Same login across pages must only appear once."""
    import json as _json
    page1 = {
        "data": {"organization": {"sponsorshipsAsMaintainer": {
            "pageInfo": {"hasNextPage": True, "endCursor": "C1"},
            "nodes": [{"sponsorEntity": {"login": "alice"}, "tier": {"monthlyPriceInDollars": 10, "name": "T"}}],
        }}},
    }
    page2 = {
        "data": {"organization": {"sponsorshipsAsMaintainer": {
            "pageInfo": {"hasNextPage": False, "endCursor": None},
            # Duplicate alice + new bob
            "nodes": [
                {"sponsorEntity": {"login": "alice"}, "tier": {"monthlyPriceInDollars": 10, "name": "T"}},
                {"sponsorEntity": {"login": "bob"}, "tier": {"monthlyPriceInDollars": 5, "name": "T"}},
            ],
        }}},
    }
    responses = [page1, page2]

    def fake_run(cmd, **kwargs):
        from unittest.mock import MagicMock
        out = _json.dumps(responses.pop(0))
        m = MagicMock()
        m.stdout = out
        m.returncode = 0
        return m

    monkeypatch.setattr(sync_module.shutil, "which", lambda _: "/usr/bin/gh")
    monkeypatch.setattr(sync_module.subprocess, "run", fake_run)

    sponsors = sync_module.fetch_github_sponsors()
    logins = [s["login"] for s in sponsors]
    assert logins == ["alice", "bob"]
    assert len(logins) == 2