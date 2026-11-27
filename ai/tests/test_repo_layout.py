"""The repo's top-level split, and the one-way dependency inside ai/.

Top level -- one directory per deployable, plus two local-only scratch areas:

    ai/        the Python AI service. Detection only: reads streams, writes
               alerts to Neon and evidence to R2. Runs alone.
    backend/   the Go API the dashboard talks to.
    frontend/  the web dashboard.
    deploy/    process manager, MediaMTX and reverse-proxy config.
    data/      footage and datasets (gitignored).
    outputs/   local scratch (gitignored).

Inside ai/, the shipped code (src/, config/, sql/, tools/) imports nothing
from its tooling beside it:

    ai/dev/    tooling, probes, closed investigations, the reference repo.
    ai/demo/   rendered clips and the scripts that render them.
    ai/docs/   write-ups and benchmarks.

test_ai_standalone.py proves ai/ needs nobody. This proves the other
direction still works: ai/dev/ and ai/demo/ reach *into* ai/src, and a rename
in ai/src/perception that breaks thirty dev scripts should fail here rather
than the next time somebody reaches for one mid-investigation.

Compile-and-resolve, not execute: these scripts open videos, load 300 MB of
weights and write files. What can be checked cheaply is that they parse and
that every name they import from ai.src actually exists.
"""
from __future__ import annotations

import ast
import py_compile
import sys
import tempfile
from pathlib import Path

AI = Path(__file__).resolve().parents[1]
REPO = AI.parent

ALLOWED_TOP_LEVEL = {"ai", "backend", "frontend", "deploy", "data", "outputs"}


# uae-main is a vendored reference implementation, not code this repo edits or
# is responsible for compiling. It carries its own docker/ and torch2trt trees.
VENDORED = {"__pycache__", "node_modules", "uae-main"}


def _py_files(*rels: str) -> list[Path]:
    out: list[Path] = []
    for rel in rels:
        d = AI / rel
        if d.is_dir():
            out += [p for p in sorted(d.rglob("*.py"))
                    if not VENDORED & set(p.parts)]
    return out


def test_top_level_is_only_the_known_folders():
    found = {p.name for p in REPO.iterdir()
             if p.is_dir() and not p.name.startswith(".")}
    unexpected = found - ALLOWED_TOP_LEVEL
    assert not unexpected, (
        f"top level grew new directories: {sorted(unexpected)}. Everything "
        "belongs to one of " + ", ".join(sorted(ALLOWED_TOP_LEVEL))
    )
    assert "ai" in found, "ai/ is missing"
    # The old layout had dev/ and demo/ beside the code; they live in ai/ now.
    for gone in ("dev", "demo", "docs"):
        assert gone not in found, f"{gone}/ belongs inside ai/ now"


def test_dev_and_demo_scripts_still_parse():
    broken = []
    with tempfile.TemporaryDirectory() as td:
        for f in _py_files("dev", "demo"):
            try:
                py_compile.compile(str(f), cfile=str(Path(td) / "x.pyc"), doraise=True)
            except py_compile.PyCompileError as e:
                broken.append(f"{f.relative_to(REPO)}: {e.msg.strip().splitlines()[-1]}")
    assert not broken, "ai/dev/demo scripts no longer compile:\n  " + "\n  ".join(broken)


def test_every_name_dev_imports_from_ai_exists():
    """`from ai.src.perception.zones import anchor` must still resolve.

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
                    and node.module.startswith("ai.")):
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
                # `from ai.src.perception import warehouse` imports a
                # submodule, which is not an attribute of the package until
                # something imports it. hasattr alone would call every such
                # line broken.
                try:
                    importlib.import_module(f"{node.module}.{alias.name}")
                except ImportError:
                    missing.append(f"{f.relative_to(REPO)}:{node.lineno} "
                                   f"{node.module} has no {alias.name!r}")
    assert not missing, "ai/dev and ai/demo import names that ai.src does not export:\n  " + \
        "\n  ".join(missing)


def test_ai_src_imports_nothing_from_its_tooling():
    """The rule that the whole restructure exists to hold.

    Complements test_ai_standalone: that one removes the siblings and
    watches for failures, this one reads the import statements directly, so a
    dependency added behind a lazy import inside a function is still caught.
    """
    forbidden = ("dev.", "demo.", "scripts.", "ai.dev", "ai.demo", "ai.docs")
    offenders = []
    for f in sorted((AI / "src").rglob("*.py")):
        for node in ast.walk(ast.parse(f.read_text())):
            names = []
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                names = [node.module]
            for n in names:
                if n.startswith(forbidden) or n in {"dev", "demo", "scripts"}:
                    offenders.append(f"{f.relative_to(REPO)}:{node.lineno} imports {n}")
    assert not offenders, "ai/src imports outward:\n  " + "\n  ".join(offenders)
