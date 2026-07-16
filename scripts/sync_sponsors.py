#!/usr/bin/env python3
"""Sync GitHub Sponsors + manual SPONSORS.md list into README.

Requires `gh` CLI authenticated with read:org scope (default for repo forks).
Run locally: python scripts/sync_sponsors.py
Run in CI:   uses GITHUB_TOKEN automatically.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
README = REPO_ROOT / "README.md"
MANUAL_FILE = REPO_ROOT / "SPONSORS.md"
ORG = "lucid-fabrics"

# Pagination bounds: 100 sponsors per page, capped at 10 pages = 1000 sponsors max.
PAGE_SIZE = 100
MAX_PAGES = 10
GH_TIMEOUT_SECS = 30

# Matches the entire Sponsors block (between "**Sponsors:**" and the next "\n---").
BLOCK_RE = re.compile(
    r"\*\*Sponsors:\*\*\n.*?(?=\n---)",
    re.DOTALL,
)


def fetch_github_sponsors() -> list[dict]:
    """Query GitHub Sponsors GraphQL API via gh CLI.

    Paginates with cursor. Caps at 10 pages (1000 sponsors) to bound runtime.
    """
    if not shutil.which("gh"):
        print("gh CLI not found, skipping GitHub Sponsors fetch", file=sys.stderr)
        return []

    sponsors: list[dict] = []
    seen_logins: set[str] = set()
    cursor: str | None = None
    page = 0
    while page < MAX_PAGES:
        page += 1
        query = (
            "query($login:String!,$first:Int!,$after:String){"
            f"organization(login:$login){{"
            "sponsorshipsAsMaintainer(first:$first,after:$after,includePrivate:false){"
            "pageInfo{hasNextPage endCursor}"
            "nodes{"
            "sponsorEntity{"
            "... on User{login}"
            "... on Organization{login}"
            "}"
            "tier{monthlyPriceInDollars name}"
            "}"
            "}"
            "}"
            "}"
        )
        cmd = [
            "gh", "api", "graphql",
            "-f", f"query={query}",
            "-F", f"login={ORG}",
            "-F", f"first={PAGE_SIZE}",
        ]
        if cursor:
            cmd.extend(["-F", f"after={cursor}"])
        try:
            result = subprocess.run(
                cmd,
                capture_output=True, text=True, check=True, timeout=GH_TIMEOUT_SECS,
            )
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
            print(f"gh API call failed: {exc}", file=sys.stderr)
            return sponsors

        try:
            payload = json.loads(result.stdout)
        except json.JSONDecodeError as exc:
            print(f"Failed to parse gh response: {exc}", file=sys.stderr)
            return sponsors

        if payload.get("errors"):
            print(f"GraphQL errors: {payload['errors']}", file=sys.stderr)
            return sponsors

        conn = (
            payload.get("data", {})
            .get("organization", {})
            .get("sponsorshipsAsMaintainer")
        )
        if not conn:
            break

        for node in conn.get("nodes", []) or []:
            entity = node.get("sponsorEntity") or {}
            tier = node.get("tier") or {}
            login = entity.get("login")
            if not login or login in seen_logins:
                continue
            seen_logins.add(login)
            sponsors.append({
                "login": login,
                "amount": tier.get("monthlyPriceInDollars", 0),
                "tier": tier.get("name", "Sponsor"),
            })

        page_info = conn.get("pageInfo") or {}
        if not page_info.get("hasNextPage"):
            break
        cursor = page_info.get("endCursor")
        if not cursor:
            break

    return sponsors


def load_manual() -> list[str]:
    """Load manually maintained sponsor names from SPONSORS.md."""
    if not MANUAL_FILE.exists():
        return []
    lines = []
    for raw in MANUAL_FILE.read_text(encoding="utf-8").splitlines():
        stripped = raw.strip()
        if stripped.startswith("- "):
            lines.append(stripped[2:].strip())
    return lines


def render_block(github: list[dict], manual: list[str]) -> str:
    """Build the markdown block that replaces the Sponsors section.

    Layout:
      **Sponsors:**
      - github sponsors (with tier + monthly amount)
      - manual supporters (from SPONSORS.md, no tier info)
      If no sponsors at all, show a CTA placeholder.
    """
    github = sorted(github, key=lambda s: (-s["amount"], s["login"].lower()))
    out = ["**Sponsors:**", ""]

    if not github and not manual:
        out.append("- _No GitHub Sponsors yet. [Be the first!](https://github.com/sponsors/lucid-fabrics)_")
        return "\n".join(out)

    for s in github:
        out.append(
            f"- ❤️ [{s['login']}](https://github.com/{s['login']}) "
            f"| {s['tier']} (${s['amount']}/mo)"
        )

    for name in manual:
        out.append(f"- ❤️ {name}")

    if not github and manual:
        # Manual-only: hint that GitHub Sponsors is the recurring option.
        out += ["", "_Want to join them? [Sponsor on GitHub](https://github.com/sponsors/lucid-fabrics)_"]

    return "\n".join(out)


def update_readme(block: str) -> bool:
    """Replace the Sponsors section in README. Returns True if changed."""
    readme = README.read_text(encoding="utf-8")
    # Normalize CRLF -> LF so the Sponsors-block regex matches on Windows checkouts.
    readme = readme.replace("\r\n", "\n")
    new = BLOCK_RE.sub(block, readme, count=1)
    if new == readme:
        return False
    README.write_text(new, encoding="utf-8")
    return True


def main() -> int:
    import time
    started = time.monotonic()
    github = fetch_github_sponsors()
    manual = load_manual()
    block = render_block(github, manual)
    changed = update_readme(block)
    elapsed = time.monotonic() - started
    print(f"GitHub sponsors: {len(github)} | manual: {len(manual)} | changed: {changed} | elapsed: {elapsed:.1f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())