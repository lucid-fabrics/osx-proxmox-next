"""Counting what the tests leave in pytest's base temp dir (used by the guard in conftest.py)."""
from __future__ import annotations

import os


# --- Temp-size guard -------------------------------------------------------------------------------------------
# A test that builds or copies a real image (a recovery image is ~0.9 GB) fills the disk: one local run once left
# 19 GB behind. pytest keeps the temp dirs of the last three runs, so it multiplies. This check runs once, at the
# end of the session, and fails the run when the base temp dir is far bigger than the suite really needs.
# Usage is counted in allocated blocks (st_blocks * 512), not apparent size, so a sparse file costs what it uses.

TOTAL_LIMIT_MB = int(os.environ.get("PYTEST_TMP_TOTAL_LIMIT_MB", "500"))
DIR_LIMIT_MB = int(os.environ.get("PYTEST_TMP_DIR_LIMIT_MB", "50"))
_MB = 1024 * 1024


def allocated_bytes(path: str, seen: set | None = None) -> int:
    """Bytes allocated on disk under *path* (symlinks not followed, hard links counted once)."""
    seen = set() if seen is None else seen
    total = 0
    stack = [path]
    while stack:
        try:
            with os.scandir(stack.pop()) as it:
                entries = list(it)
        except OSError:
            continue
        for entry in entries:
            try:
                st = entry.stat(follow_symlinks=False)
            except OSError:
                continue
            if entry.is_dir(follow_symlinks=False):
                stack.append(entry.path)
            elif st.st_nlink > 1 and (st.st_dev, st.st_ino) in seen:
                continue
            else:
                seen.add((st.st_dev, st.st_ino))
            total += st.st_blocks * 512
    return total


def temp_size_report(base: str) -> tuple[int, list[tuple[int, str]]]:
    """(total allocated bytes, [(bytes, test dir)] largest first) for a pytest base temp dir."""
    seen: set = set()
    sizes = []
    total = 0
    try:
        with os.scandir(base) as it:
            children = list(it)
    except OSError:
        return 0, []
    for child in children:
        if child.is_symlink():
            continue
        size = allocated_bytes(child.path, seen) if child.is_dir(follow_symlinks=False) else child.stat(follow_symlinks=False).st_blocks * 512
        sizes.append((size, child.name))
        total += size
    return total, sorted(sizes, reverse=True)


def temp_size_problems(total: int, sizes: list[tuple[int, str]], total_limit_mb: int, dir_limit_mb: int) -> list[str]:
    """The lines to print when the temp usage is over a limit, empty when it is fine."""
    big = [(n, name) for n, name in sizes if n > dir_limit_mb * _MB]
    if total <= total_limit_mb * _MB and not big:
        return []
    lines = [f"Test temp files use {total / _MB:.0f} MB (limit {total_limit_mb} MB; one test dir may use {dir_limit_mb} MB)."]
    lines += [f"  {n / _MB:8.1f} MB  {name}" for n, name in sizes[:10]]
    lines.append("A test is writing a real image or payload. Stub the build, copy or download, or point it at a tiny fixture.")
    return lines
