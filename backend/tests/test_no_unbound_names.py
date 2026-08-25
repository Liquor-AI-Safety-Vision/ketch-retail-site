"""No function may reference a name that does not exist.

This catches the specific damage a refactor does: splitting one long function
into a caller and a helper, and leaving the helper referring to a name that
was a local of the original. Python does not complain until that line runs,
and in this codebase the lines most likely to be refactored are also the ones
that only run on a failure path -- so the traceback arrives during an
incident, months later, in a tier process, replacing the real error.

compileall does not catch it: `foo` is valid syntax whether or not foo
exists. It was caught once here by running symtable by hand after a refactor,
which is exactly the kind of check that gets skipped the next time. So it
lives here instead.

The rule is deliberately narrow -- a global reference that is neither a
module-level name nor a builtin. That is a real error every time, so there
are no allowances to tune and nothing to argue about when it fires.

    python backend/tools/run_tests_nopytest.py backend/tests/test_no_unbound_names.py
"""
from __future__ import annotations

import builtins
import symtable
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# The split architecture and its tools: the code that gets restructured most,
# and whose failure paths are hardest to reach in a normal run.
CHECKED = [
    "backend/src/app/run_split_pipeline.py",
    "backend/src/runtime/business_tier.py",
    "backend/src/runtime/decode_tier.py",
    "backend/src/runtime/frame_ring.py",
    "backend/src/runtime/inference_server.py",
    "backend/src/runtime/perf.py",
    "backend/src/runtime/tier_status.py",
    "backend/tools/diag_split_tiers.py",
    "backend/tools/diag_infer_modules.py",
]

_BUILTINS = set(dir(builtins))


def _module_level_names(table: symtable.SymbolTable) -> set[str]:
    return {s.get_name() for s in table.get_symbols()}


def _unbound_in(table: symtable.SymbolTable, module_names: set[str],
                path: str, trail: str = "") -> list[str]:
    problems = []
    for child in table.get_children():
        name = f"{trail}{child.get_name()}"
        if child.get_type() == "function":
            for sym in child.get_symbols():
                if not sym.is_global():
                    continue
                ident = sym.get_name()
                if ident in module_names or ident in _BUILTINS:
                    continue
                problems.append(f"{path}: {name}() references undefined name {ident!r}")
        # Nested functions and comprehensions see the enclosing module too.
        problems.extend(_unbound_in(child, module_names, path, trail=f"{name}."))
    return problems


def test_no_function_references_an_undefined_name():
    problems: list[str] = []
    missing: list[str] = []
    for rel in CHECKED:
        path = ROOT / rel
        if not path.exists():
            missing.append(rel)
            continue
        table = symtable.symtable(path.read_text(), rel, "exec")
        problems.extend(_unbound_in(table, _module_level_names(table), rel))

    assert not missing, f"checked files that no longer exist: {missing}"
    assert not problems, "\n".join(["undefined names found:"] + problems)
