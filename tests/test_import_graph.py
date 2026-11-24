"""Structure tests for the package import graph.

Dependencies point one way: no import cycles, and no function-level import of a
package module unless it is listed in LAZY_IMPORTS below with the reason it has to
be lazy. A cycle workaround (importing late to dodge a circular import) is never
a valid reason: move the shared code below both sides instead.
"""
from __future__ import annotations

import ast
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import osx_proxmox_next

PKG = "osx_proxmox_next"
ROOT = Path(osx_proxmox_next.__file__).resolve().parent

# (module file relative to the package, imported module relative to the package) -> reason.
# Every entry must still exist in the source: a stale entry fails the test.
_TEXTUAL_SCREEN = "Textual screen loaded on demand: keeps start-up fast and the screen modules out of CLI runs"
_SCREEN_TO_SCREEN = "screens open each other on user action; a top-level import would couple their load order"
# None of the entries below is a cycle workaround (hoisting them creates no cycle, the cycle test proves it) and none
# has a runtime reason such as an optional dependency or start-up cost. Each stack is a front end that a later change
# restructures, and hoisting its imports now would mean retargeting the tests' patches twice. Do not add an entry to
# dodge a cycle.
_LATER = "left lazy until the {} stack is restructured in a later change: hoisting now means retargeting test patches twice"
_GPU = _LATER.format("gpu setup")
_IGPU = _LATER.format("igpu")
_NULLMOTH = _LATER.format("nullmoth")
_FINISH = _LATER.format("igpu and nullmoth finish")
_DRIVER = _LATER.format("driver update")
LAZY_IMPORTS: dict[tuple[str, str], str] = {
    ("app.py", "screens.home"): _TEXTUAL_SCREEN,
    ("app.py", "screens.new_mac"): _TEXTUAL_SCREEN,
    ("app.py", "screens.host_check"): _TEXTUAL_SCREEN,
    ("app.py", "screens.gpu"): _TEXTUAL_SCREEN,
    ("app.py", "screens.vm_view"): _TEXTUAL_SCREEN,
    ("screens/new_mac.py", "screens.build"): _SCREEN_TO_SCREEN,
    ("screens/vm_actions.py", "screens.driver"): _SCREEN_TO_SCREEN,
    ("screens/vm_view.py", "screens.build"): _SCREEN_TO_SCREEN,
    ("screens/vm_view.py", "screens.gpu"): _SCREEN_TO_SCREEN,
    ("cli.py", "gpu_setup"): _GPU,
    ("cli.py", "unattended"): _IGPU,
    ("driver_intel.py", "downloader"): _DRIVER,
    ("driver_nvidia.py", "downloader"): _DRIVER,
    ("driver_nvidia.py", "igpu_finish"): _DRIVER,
    ("driver_oc.py", "driver_engine"): _DRIVER,
    ("finish_common.py", "downloader"): _FINISH,
    ("gpu_setup.py", "defaults"): _GPU,
    ("gpu_setup.py", "executor"): _GPU,
    ("gpu_setup.py", "oc_builder"): _GPU,
    ("gpu_setup_cli.py", "executor"): _GPU,
    ("gpu_setup_cli.py", "rollback"): _GPU,
    ("igpu_cli.py", "executor"): _IGPU,
    ("igpu_cli.py", "planner"): _IGPU,
    ("igpu_cli.py", "rollback"): _IGPU,
    ("igpu_cli.py", "unattended"): _IGPU,
    ("nullmoth.py", "oc_builder"): _NULLMOTH,
    ("nullmoth_cli.py", "oc_builder"): _NULLMOTH,
    ("screens/gpu_attach_step.py", "executor"): _GPU,
    ("screens/gpu_attach_step.py", "rollback"): _GPU,
}


def _module_name(path: Path) -> str:
    rel = path.relative_to(ROOT).with_suffix("")
    parts = [PKG, *rel.parts]
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def _all_modules() -> dict[str, Path]:
    return {_module_name(p): p for p in sorted(ROOT.rglob("*.py"))}


def _resolve(node: ast.AST, current: str, is_pkg: bool, known: set[str]) -> set[str]:
    """Package modules an import statement refers to."""
    out: set[str] = set()
    if isinstance(node, ast.Import):
        for alias in node.names:
            if alias.name in known:
                out.add(alias.name)
        return out
    assert isinstance(node, ast.ImportFrom)
    if node.level:
        base = current.split(".")
        if not is_pkg:
            base = base[:-1]
        base = base[: len(base) - (node.level - 1)]
        prefix = ".".join(base)
        target = f"{prefix}.{node.module}" if node.module else prefix
    else:
        target = node.module or ""
    if target not in known or target == PKG:
        if target == PKG:  # `from . import module`
            out.update(f"{PKG}.{a.name}" for a in node.names if f"{PKG}.{a.name}" in known)
        return out
    out.add(target)
    for alias in node.names:  # `from pkg import submodule`
        if f"{target}.{alias.name}" in known:
            out.add(f"{target}.{alias.name}")
    return out


def _is_type_checking(node: ast.AST) -> bool:
    if not isinstance(node, ast.If):
        return False
    test = node.test
    return (isinstance(test, ast.Name) and test.id == "TYPE_CHECKING") or (
        isinstance(test, ast.Attribute) and test.attr == "TYPE_CHECKING"
    )


def _walk(body: list[ast.stmt], top: bool, out: list[tuple[ast.AST, bool]]) -> None:
    """Collect (import node, is_top_level). Imports under `if TYPE_CHECKING` never run, so they are skipped."""
    for stmt in body:
        if isinstance(stmt, (ast.Import, ast.ImportFrom)):
            out.append((stmt, top))
        elif _is_type_checking(stmt):
            continue
        elif isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            _walk(stmt.body, False, out)
        else:
            for field in ("body", "orelse", "finalbody"):
                _walk(getattr(stmt, field, []) or [], top, out)
            for handler in getattr(stmt, "handlers", []) or []:
                _walk(handler.body, top, out)


def _imports() -> list[tuple[str, str, bool]]:
    """(importer module, imported module, is_top_level) for every package-internal import."""
    modules = _all_modules()
    known = set(modules)
    rows: list[tuple[str, str, bool]] = []
    for name, path in modules.items():
        found: list[tuple[ast.AST, bool]] = []
        _walk(ast.parse(path.read_text()).body, True, found)
        for node, top in found:
            for target in _resolve(node, name, path.name == "__init__.py", known):
                rows.append((name, target, top))
    return rows


def _ancestors(name: str) -> list[str]:
    parts = name.split(".")
    return [".".join(parts[:i]) for i in range(2, len(parts))]  # packages below the root, excluding itself


def _graph(rows: list[tuple[str, str, bool]]) -> dict[str, set[str]]:
    """Top-level import edges. Importing a.b.c also runs a.b.__init__, so that is an edge too unless the
    importer already lives inside a.b (its __init__ is then already running)."""
    graph: dict[str, set[str]] = {name: set() for name in _all_modules()}
    for src, dst, top in rows:
        if not top:
            continue
        graph[src].add(dst)
        for parent in _ancestors(dst):
            if not (src == parent or src.startswith(parent + ".")):
                graph[src].add(parent)
    return graph


def _cycles(graph: dict[str, set[str]]) -> list[list[str]]:
    """Strongly connected components with more than one module (Tarjan)."""
    index: dict[str, int] = {}
    low: dict[str, int] = {}
    stack: list[str] = []
    on: set[str] = set()
    out: list[list[str]] = []
    counter = [0]

    def visit(v: str) -> None:
        index[v] = low[v] = counter[0]
        counter[0] += 1
        stack.append(v)
        on.add(v)
        for w in graph[v]:
            if w not in index:
                visit(w)
                low[v] = min(low[v], low[w])
            elif w in on:
                low[v] = min(low[v], index[w])
        if low[v] == index[v]:
            comp = []
            while True:
                w = stack.pop()
                on.discard(w)
                comp.append(w)
                if w == v:
                    break
            if len(comp) > 1:
                out.append(sorted(comp))

    sys.setrecursionlimit(max(sys.getrecursionlimit(), 5000))
    for v in graph:
        if v not in index:
            visit(v)
    return out


def _rel(name: str) -> str:
    return name[len(PKG) + 1:]


def test_no_import_cycles_at_top_level() -> None:
    cycles = _cycles(_graph(_imports()))
    assert not cycles, "import cycle(s): " + "; ".join(" <-> ".join(_rel(m) for m in c) for c in cycles)


def test_function_level_imports_are_all_allowlisted() -> None:
    modules = _all_modules()
    seen: set[tuple[str, str]] = set()
    unlisted: list[str] = []
    for src, dst, top in _imports():
        if top:
            continue
        key = (modules[src].relative_to(ROOT).as_posix(), _rel(dst))
        seen.add(key)
        if key not in LAZY_IMPORTS:
            unlisted.append(f"{key[0]} imports {key[1]} inside a function")
    assert not unlisted, (
        "function-level imports of package modules (move the shared code down and import at the top, or add "
        "a reason to LAZY_IMPORTS): " + "; ".join(sorted(set(unlisted)))
    )
    stale = sorted(set(LAZY_IMPORTS) - seen)
    assert not stale, f"LAZY_IMPORTS entries that no longer exist: {stale}"


def test_the_cycle_detector_sees_a_cycle() -> None:
    assert _cycles({"a": {"b"}, "b": {"a"}, "c": set()}) == [["a", "b"]]
    assert _cycles({"a": {"b"}, "b": set()}) == []


def test_the_walker_skips_type_checking_and_flags_function_imports() -> None:
    src = (
        "from . import a\n"
        "if TYPE_CHECKING:\n    from . import b\n"
        "def f():\n    from . import c\n"
        "try:\n    from . import d\nexcept ImportError:\n    pass\n"
    )
    found: list[tuple[ast.AST, bool]] = []
    _walk(ast.parse(src).body, True, found)
    got = {(n.names[0].name, top) for n, top in found}  # type: ignore[attr-defined]
    assert got == {("a", True), ("c", False), ("d", True)}


def _import_each_in_fresh_interpreter(names: list[str]) -> list[str]:
    failures = []
    for name in names:
        proc = subprocess.run(
            [sys.executable, "-c", f"import {name}"], capture_output=True, text=True, timeout=120
        )
        if proc.returncode != 0:
            failures.append(f"{name}: {proc.stderr.strip().splitlines()[-1] if proc.stderr.strip() else proc.returncode}")
    return failures


def test_every_non_screen_module_imports_on_its_own() -> None:
    """A module must import first in a fresh interpreter, so a cycle cannot hide behind the order in which
    pytest (or the CLI) happens to import things. Screens are covered by the Textual tests."""
    names = [
        n for n in _all_modules()
        if n != PKG and ".screens" not in n and not n.endswith(".__main__")
    ]
    batches = [names[i::8] for i in range(8)]  # eight interpreters at a time, one interpreter per module
    with ThreadPoolExecutor(max_workers=8) as pool:
        failures = [f for part in pool.map(_import_each_in_fresh_interpreter, batches) for f in part]
    assert not failures, "\n".join(failures)

