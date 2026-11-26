"""Run a test module without pytest installed.

pytest is not in this environment, and the tests that matter most right now
are about process failure handling -- exactly the thing that cannot be
verified by reading. This is a deliberately tiny substitute: it provides the
one pytest feature those tests use (pytest.raises) and calls every test_*
function.

It has to be a real file rather than a heredoc: the tests spawn child
processes, and multiprocessing's spawn re-imports the parent's __main__ by
path in the child. A heredoc has no path, so every child dies during startup
and every test fails for a reason that has nothing to do with the code.

    python ai/tools/run_tests_nopytest.py ai/tests/test_tier_status.py
"""
from __future__ import annotations

import contextlib
import importlib
import sys
import traceback
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


_STUB_SOURCE = '''
"""Minimal stand-in for pytest, enough for these tests to import and run."""
import contextlib


class _Info:
    def __init__(self):
        self.value = None


@contextlib.contextmanager
def raises(exc, **_kwargs):
    info = _Info()
    try:
        yield info
    except exc as caught:
        info.value = caught
        return
    raise AssertionError(f"expected {exc.__name__} to be raised, nothing was")


def _identity_decorator(*_a, **_k):
    return lambda fn: fn


fixture = _identity_decorator


class mark:
    skipif = staticmethod(_identity_decorator)
    parametrize = staticmethod(_identity_decorator)
'''


def _install_pytest_stub() -> None:
    """Put the stub on disk, not just in sys.modules.

    A module injected into the parent's sys.modules does not exist in a
    spawned child, and these tests spawn children that re-import the test
    module -- which imports pytest at its top. So the stub has to be a real
    importable file. Appended to sys.path rather than prepended, so a genuine
    pytest installation always wins.
    """
    try:
        import pytest  # noqa: F401

        return
    except ImportError:
        pass

    import tempfile

    stub_dir = Path(tempfile.mkdtemp(prefix="pytest-stub-"))
    (stub_dir / "pytest.py").write_text(_STUB_SOURCE)
    sys.path.append(str(stub_dir))
    print(f"pytest not installed; using a minimal stub at {stub_dir}", file=sys.stderr)


def main(argv: list[str]) -> int:
    if not argv:
        print(__doc__.strip(), file=sys.stderr)
        return 2

    _install_pytest_stub()
    failures = 0
    total = 0
    for path in argv:
        test_path = Path(path).resolve()
        # Imported by real module name, not loaded from a path under an
        # invented one: a spawned child re-imports the module that defines
        # its target function, and it can only do that if the name resolves.
        sys.path.insert(0, str(test_path.parent))
        sys.path.insert(0, str(ROOT))
        module = importlib.import_module(test_path.stem)

        names = sorted(n for n in dir(module) if n.startswith("test_"))
        print(f"\n{test_path.name}  ({len(names)} tests)", file=sys.stderr)
        for name in names:
            total += 1
            try:
                fn = getattr(module, name)
                fn(**({"tmp_path": _tmp_path(name)} if "tmp_path" in fn.__code__.co_varnames else {}))
                print(f"  PASS  {name}", file=sys.stderr)
            except BaseException as exc:
                failures += 1
                print(f"  FAIL  {name}: {exc}", file=sys.stderr)
                traceback.print_exc()

    print(f"\n{total - failures}/{total} passed", file=sys.stderr)
    return 1 if failures else 0


def _tmp_path(name: str) -> Path:
    import tempfile

    return Path(tempfile.mkdtemp(prefix=f"{name}-"))


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
