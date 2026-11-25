"""The release workflow and the gitleaks allowlist: a dispatch must not bump main, and no input is spliced into a shell line."""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "release.yml"


def workflow() -> str:
    return WORKFLOW.read_text(encoding="utf-8")


def run_blocks(text: str) -> list[str]:
    """Every `run: |` body, so the assertions see the shell lines and not the env: mappings."""
    lines = text.splitlines()
    blocks: list[str] = []
    i = 0
    while i < len(lines):
        header = re.match(r"^(\s*)run: \|$", lines[i])
        if header is None:
            i += 1
            continue
        indent = len(header.group(1))
        i += 1
        body = []
        while i < len(lines) and (not lines[i].strip() or len(lines[i]) - len(lines[i].lstrip()) > indent):
            body.append(lines[i])
            i += 1
        blocks.append("\n".join(body))
    return blocks


def test_the_release_job_only_runs_on_a_push():
    assert "  release:\n    if: github.event_name == 'push'\n" in workflow()


def test_the_dispatch_inputs_reach_the_shell_through_env_only():
    text = workflow()
    for block in run_blocks(text):
        assert "${{ inputs." not in block, block
    assert "TAG: ${{ inputs.tag }}" in text
    assert "RENDERER_URL: ${{ inputs.renderer_asset_url }}" in text
    assert "RENDERER_SHA256: ${{ inputs.renderer_sha256 }}" in text


def test_an_empty_renderer_sha_falls_back_to_the_pin():
    text = workflow()
    assert '. arm64/pins.env && printf %s "$RENDERER_SHA256"' in text
    assert "arm64/pins.env" in text and "exit 1" in text


def test_the_gitleaks_allowlist_does_not_blanket_the_install_tree():
    text = (ROOT / ".gitleaks.toml").read_text(encoding="utf-8")
    assert "paths = ['''arm64/lib/install/''']" not in text
    assert "hackkey\\.pem" in text
    assert 'key="ApImg4Ticket"' in text
