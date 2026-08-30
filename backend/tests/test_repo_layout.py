"""The five-folder split, and the one-way dependency that makes it mean something.

    backend/   the shipping unit. Runs alone. Imports nothing outside itself.
    data/      footage and datasets, not code.
    dev/       tooling, probes, closed investigations, the reference repo.
    demo/      rendered clips and the scripts that render them.
    docs/      write-ups.

test_backend_standalone.py proves backend needs nobody. This proves the other
direction still works: dev/ and demo/ reach *into* backend, and a rename in
backend/src/perception that breaks thirty dev scripts should fail here rather
than the next time somebody reaches for one mid-investigation.

Compile-and-resolve, not execute: these scripts open videos, load 300 MB of
weights and write files. What can be checked cheaply is that they parse and
that every name they import from backend actually exists.
"""
from __future__ import annotations

import ast
import py_compile
import sys
import tempfile
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
REPO = BACKEND.parent

EXPECTED_DIRS = {"backend", "data", "dev", "demo", "docs"}


# uae-main is a vendored reference implementation, not code this repo edits or
# is responsible for compiling. It carries its own docker/ and torch2trt trees.
VENDORED = {"__pycache__", "node_modules", "uae-main"}


def _py_files(*rels: str) -> list[Path]:
    out: list[Path] = []
    for rel in rels:
        d = REPO / rel
        if d.is_dir():
            out += [p for p in sorted(d.rglob("*.py"))
                    if not VENDORED & set(p.parts)]
    return out


def test_top_level_is_only_the_five_folders():
    found = {p.name for p in REPO.iterdir()
             if p.is_dir() and not p.name.startswith(".")}
    unexpected = found - EXPECTED_DIRS
    assert not unexpected, (
        f"top level grew new directories: {sorted(unexpected)}. Everything "
        "belongs to one of " + ", ".join(sorted(EXPECTED_DIRS))
    )
    assert EXPECTED_DIRS - found == set(), f"missing: {sorted(EXPECTED_DIRS - found)}"


def test_dev_and_demo_scripts_still_parse():
    broken = []
    with tempfile.TemporaryDirectory() as td:
        for f in _py_files("dev", "demo"):
            try:
                py_compile.compile(str(f), cfile=str(Path(td) / "x.pyc"), doraise=True)
            except py_compile.PyCompileError as e:
                broken.append(f"{f.relative_to(REPO)}: {e.msg.strip().splitlines()[-1]}")
    assert not broken, "dev/demo scripts no longer compile:\n  " + "\n  ".join(broken)


def test_every_name_dev_imports_from_backend_exists():
    """`from backend.src.perception.zones import anchor` must still resolve.

    The perception modules moved wholesale out of scripts/, and thirty-odd dev
    and demo scripts were rewritten to follow them. A typo in that rewrite is
    invisible until somebody runs the script months later.
    """
    sys.path.insert(0, str(REPO))
    import importlib

    missing = []
    for f in _py_files("dev", "demo"):
        for node in ast.walk(ast.parse(f.read_text())):
            if not (isinstance(node, ast.ImportFrom) and node.module
                    and node.module.startswith("backend.")):
                continue
            try:
                mod = importlib.import_module(node.module)
            except Exception as e:
                missing.append(f"{f.relative_to(REPO)}:{node.lineno} "
                               f"{node.module} -> {type(e).__name__}: {e}")
                continue
            for alias in node.names:
                if alias.name == "*" or hasattr(mod, alias.name):
                    continue
                # `from backend.src.perception import warehouse` imports a
                # submodule, which is not an attribute of the package until
                # something imports it. hasattr alone would call every such
                # line broken.
                try:
                    importlib.import_module(f"{node.module}.{alias.name}")
                except ImportError:
                    missing.append(f"{f.relative_to(REPO)}:{node.lineno} "
                                   f"{node.module} has no {alias.name!r}")
    assert not missing, "dev/demo import names that backend does not export:\n  " + \
        "\n  ".join(missing)


def test_nothing_outside_backend_is_imported_by_backend():
    """The rule that the whole restructure exists to hold.

    Complements test_backend_standalone: that one removes the siblings and
    watches for failures, this one reads the import statements directly, so a
    dependency added behind a lazy import inside a function is still caught.
    """
    forbidden = ("dev.", "demo.", "scripts.")
    offenders = []
    for f in sorted((BACKEND / "src").rglob("*.py")):
        for node in ast.walk(ast.parse(f.read_text())):
            names = []
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                names = [node.module]
            for n in names:
                if n.startswith(forbidden) or n in {"dev", "demo", "scripts"}:
                    offenders.append(f"{f.relative_to(REPO)}:{node.lineno} imports {n}")
    assert not offenders, "backend imports outward:\n  " + "\n  ".join(offenders)
