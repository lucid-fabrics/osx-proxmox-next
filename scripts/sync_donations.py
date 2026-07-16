#!/usr/bin/env python3
"""Pull Ko-fi + BuyMeACoffee donations from the Cloudflare Worker and merge
into SPONSORS.md. Opens a PR (via the calling workflow) if new donors appear.

Worker contract:
  GET  /webhooks/poll     - returns {donations: [{key, data: {...}}, ...]}
  POST /webhooks/delete   - body {"key": "..."} deletes one entry from KV

Ko-fi payload field: from_name
BMC payload field:    name

Requires POLL_TOKEN env var (Cloudflare Worker secret).
"""
from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
MANUAL_FILE = REPO_ROOT / "SPONSORS.md"
WORKER_URL = "https://donation-webhooks.wassimmehanna.workers.dev"
POLL_ENDPOINT = f"{WORKER_URL}/webhooks/poll"
DELETE_ENDPOINT = f"{WORKER_URL}/webhooks/delete"


def http_post_json(url: str, payload: dict, token: str) -> dict:
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=30) as resp:  # noqa: S310
        return json.loads(resp.read().decode("utf-8") or "{}")


def http_get_json(url: str, token: str) -> dict:
    req = urllib.request.Request(
        url,
        headers={"Authorization": f"Bearer {token}"},
        method="GET",
    )
    with urllib.request.urlopen(req, timeout=30) as resp:  # noqa: S310
        return json.loads(resp.read().decode("utf-8") or "{}")


def fetch_donations(token: str) -> list[dict]:
    """Return list of {key, data} entries currently in KV."""
    body = http_get_json(POLL_ENDPOINT, token)
    return body.get("donations", [])


def extract_donor(entry: dict) -> str | None:
    """Pull a display name from a Ko-fi or BMC donation entry."""
    data = entry.get("data") or {}
    platform = data.get("platform")
    if platform == "kofi":
        name = data.get("from_name") or data.get("name")
    else:
        name = data.get("name") or data.get("from_name")
    name = (name or "").strip()
    if not name or name.lower() == "anonymous":
        return None
    return name


def format_manual_entry(name: str) -> str:
    """Render a donor name as a SPONSORS.md bullet.

    If it looks like a GitHub login, wrap as a link.
    """
    if name.startswith("@"):
        handle = name[1:]
        return f"- [💖 {handle}](https://github.com/{handle})"
    return f"- 💖 {name}"


def existing_names() -> set[str]:
    """Read existing donor names from SPONSORS.md (case-insensitive).

    Strips leading `- `, leading `💖 ` or `❤️ `, and surrounding link
    brackets so dedup compares the bare donor name across format variants.
    """
    if not MANUAL_FILE.exists():
        return set()
    out: set[str] = set()
    for raw in MANUAL_FILE.read_text(encoding="utf-8").splitlines():
        stripped = raw.strip()
        if not stripped.startswith("- "):
            continue
        body = stripped[2:].strip()
        body = body.removeprefix("💖 ").removeprefix("❤️ ")
        if body.startswith("[") and "](" in body:
            body = body[1: body.index("](")]
        out.add(body.lower().strip())
    return out


def append_to_manual(new_entries: list[str]) -> bool:
    """Append new donors to SPONSORS.md. Returns True if file changed."""
    if not new_entries:
        return False
    existing = MANUAL_FILE.read_text(encoding="utf-8") if MANUAL_FILE.exists() else ""
    if not existing.endswith("\n"):
        existing += "\n"
    existing += "\n".join(new_entries) + "\n"
    MANUAL_FILE.write_text(existing, encoding="utf-8")
    return True


def delete_entries(keys: list[str], token: str) -> None:
    """Remove processed entries from worker KV."""
    for key in keys:
        try:
            http_post_json(DELETE_ENDPOINT, {"key": key}, token)
        except (urllib.error.URLError, urllib.error.HTTPError) as exc:
            print(f"Failed to delete {key}: {exc}", file=sys.stderr)


def main() -> int:
    token = os.environ.get("POLL_TOKEN", "").strip()
    if not token:
        print("POLL_TOKEN env var not set, skipping donation sync", file=sys.stderr)
        return 1

    try:
        entries = fetch_donations(token)
    except urllib.error.HTTPError as exc:
        print(f"Poll failed: HTTP {exc.code}", file=sys.stderr)
        return 1
    except urllib.error.URLError as exc:
        print(f"Poll failed: {exc.reason}", file=sys.stderr)
        return 1

    existing = existing_names()
    new_lines: list[str] = []
    new_keys: list[str] = []
    seen_in_run: set[str] = set()
    for entry in entries:
        donor = extract_donor(entry)
        if not donor:
            # Still delete unknown-shape entries to keep KV clean.
            new_keys.append(entry["key"])
            continue
        key_normalized = donor.lower()
        if key_normalized in existing or key_normalized in seen_in_run:
            new_keys.append(entry["key"])
            continue
        seen_in_run.add(key_normalized)
        new_lines.append(format_manual_entry(donor))
        new_keys.append(entry["key"])

    changed = append_to_manual(new_lines)
    if new_keys:
        delete_entries(new_keys, token)

    print(f"Fetched: {len(entries)} | new donors: {len(new_lines)} | changed: {changed}")
    return 0


if __name__ == "__main__":
    sys.exit(main())