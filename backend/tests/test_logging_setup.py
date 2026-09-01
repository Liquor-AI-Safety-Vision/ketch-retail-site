"""Operational messages go through logging; tool output stays on stdout.

Eleven messages across runtime, pipelines and perception were print(), several
carrying "WARNING" or "ERROR" as literal text because there was no level to
carry it. One process reading its own stdout can live with that. Fourteen
spawned processes writing to one terminal cannot: the line saying a camera's
gallery is empty arrived with no timestamp, no level, and nothing to say which
of the fourteen said it.

The line this file defends is the other half: entry points still print their
result. run_split_pipeline writes JSON to stdout and a summary to stderr, and
the bench scripts grep both. Routing those through logging would change the
text they match on and silently break every bench runner.
"""
from __future__ import annotations

import ast
import logging
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
REPO = BACKEND.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from backend.src.common.logging_setup import configure_logging  # noqa: E402

#: Layers that are libraries: imported, never the thing being run.
LIBRARY_DIRS = ("runtime", "pipelines", "infra")

#: Allowed to print because the message must survive an unconfigured logger.
PRINT_EXEMPT = {
    # tier_guard reports a tier that died, possibly before logging was set up
    # in that process, and its whole job is to make sure the reason escapes.
    "runtime/tier_status.py",
}


def _prints_in(path: Path) -> list[int]:
    lines = []
    for node in ast.walk(ast.parse(path.read_text())):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                and node.func.id == "print"):
            lines.append(node.lineno)
    return lines


def test_library_layers_log_instead_of_printing():
    offenders = []
    for sub in LIBRARY_DIRS:
        for path in sorted((BACKEND / "src" / sub).rglob("*.py")):
            rel = path.relative_to(BACKEND / "src").as_posix()
            if rel in PRINT_EXEMPT:
                continue
            for lineno in _prints_in(path):
                offenders.append(f"{rel}:{lineno}")
    assert not offenders, (
        "library code must log, not print -- a print has no level, no "
        "timestamp and no process name, and sixteen cameras share one "
        "terminal:\n  " + "\n  ".join(offenders)
    )


def test_no_message_carries_its_level_as_text():
    """`print("WARNING ...")` is a level that no handler can filter on."""
    offenders = []
    for sub in LIBRARY_DIRS:
        for path in sorted((BACKEND / "src" / sub).rglob("*.py")):
            for i, line in enumerate(path.read_text().split("\n"), 1):
                stripped = line.strip()
                if stripped.startswith("#") or "logger." not in stripped:
                    continue
                for level in ("WARNING", "ERROR", "CRITICAL"):
                    if f'"{level} ' in stripped or f"'{level} " in stripped:
                        rel = path.relative_to(BACKEND / "src").as_posix()
                        offenders.append(f"{rel}:{i} {level!r} in the message text")
    assert not offenders, "\n  ".join(offenders)


def test_entry_points_still_print_their_result():
    """The bench scripts grep this output; it is not a log."""
    path = BACKEND / "src" / "app" / "run_split_pipeline.py"
    assert _prints_in(path), (
        "run_split_pipeline stopped printing its result. bench/run_n3_16cam.sh "
        "greps 'wall .* FPS' out of it and would silently report nothing."
    )


def test_configure_logging_is_idempotent():
    """Tier processes are spawned, so each re-imports the entry point module.

    Without the guard each one stacks another handler on the root logger and
    every line is emitted twice, then three times.
    """
    root = logging.getLogger()
    saved = list(root.handlers)
    try:
        configure_logging("INFO", force=True)
        after_first = len(root.handlers)
        configure_logging("INFO")
        configure_logging("INFO")
        assert len(root.handlers) == after_first
    finally:
        for h in list(root.handlers):
            root.removeHandler(h)
        for h in saved:
            root.addHandler(h)


def test_logs_go_to_stderr_so_stdout_stays_machine_readable():
    root = logging.getLogger()
    saved = list(root.handlers)
    try:
        configure_logging("INFO", force=True)
        streams = [h.stream for h in root.handlers if isinstance(h, logging.StreamHandler)]
        assert streams, "no stream handler configured"
        assert all(s is sys.stderr for s in streams), (
            "a log handler writes to stdout, which also carries the JSON result "
            "the bench scripts parse"
        )
    finally:
        for h in list(root.handlers):
            root.removeHandler(h)
        for h in saved:
            root.addHandler(h)


def test_a_log_file_can_be_requested(tmp_path):
    root = logging.getLogger()
    saved = list(root.handlers)
    target = Path(tmp_path) / "nested" / "run.log"
    try:
        configure_logging("INFO", log_file=target, force=True)
        logging.getLogger("backend.test").warning("hello from the test")
        for h in root.handlers:
            h.flush()
        assert target.exists(), "log file was not created"
        assert "hello from the test" in target.read_text()
    finally:
        for h in list(root.handlers):
            h.close()
            root.removeHandler(h)
        for h in saved:
            root.addHandler(h)
