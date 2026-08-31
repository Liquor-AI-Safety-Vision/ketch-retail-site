"""One logging setup, called by entry points, inherited by every tier process.

Before this, operational messages were print() -- eleven of them across the
runtime, pipelines and perception layers, several prefixed by hand with
"WARNING" or "ERROR" because there was no level to carry it. That is workable
for one process reading its own stdout. It is not workable for the shape this
actually runs in: fourteen spawned processes writing to one terminal, where
the line that says a camera's gallery is empty arrives with no timestamp, no
level, and nothing to say which of the fourteen said it.

What stays print(): the entry points' own reports. `run_split_pipeline` prints
a JSON result to stdout and a human summary to stderr, and the bench scripts
grep both -- `grep -E '=== layer|wall +:'`. Those are the tool's output, not
its log, and routing them through logging would change the text the scripts
match on. The rule is: a library logs, an entry point prints its result.

Process name is in the format because it is the only thing that distinguishes
sixteen cameras' worth of decode workers from each other in a merged stream.
"""
from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

FORMAT = "%(asctime)s %(levelname)-7s %(processName)-14s %(name)s: %(message)s"
DATEFMT = "%H:%M:%S"

#: Set on the root logger the first time configure_logging runs in a process.
_CONFIGURED = "_liquir_logging_configured"


def configure_logging(
    level: str | int | None = None,
    *,
    log_file: str | Path | None = None,
    force: bool = False,
) -> None:
    """Configure the root logger. Safe to call more than once.

    level: LIQUIR_LOG_LEVEL if unset, else INFO.
    log_file: also LIQUIR_LOG_FILE. Opened in append mode -- a restart must not
        erase the run that explains why the restart happened.

    Idempotent because tier processes are spawned, not forked: each one imports
    the entry point module afresh and would otherwise stack a second handler on
    the root logger, doubling every line.
    """
    root = logging.getLogger()
    if getattr(root, _CONFIGURED, False) and not force:
        return

    if level is None:
        level = os.environ.get("LIQUIR_LOG_LEVEL", "INFO")
    if isinstance(level, str):
        level = getattr(logging, level.upper(), logging.INFO)

    for handler in list(root.handlers):
        root.removeHandler(handler)

    formatter = logging.Formatter(FORMAT, datefmt=DATEFMT)

    # stderr, not stdout: stdout carries the machine-readable result that
    # regression.py and the bench scripts parse, and interleaving log lines
    # into it would corrupt the JSON.
    stream = logging.StreamHandler(sys.stderr)
    stream.setFormatter(formatter)
    root.addHandler(stream)

    log_file = log_file or os.environ.get("LIQUIR_LOG_FILE")
    if log_file:
        path = Path(log_file).expanduser()
        path.parent.mkdir(parents=True, exist_ok=True)
        file_handler = logging.FileHandler(path, mode="a", encoding="utf-8")
        file_handler.setFormatter(formatter)
        root.addHandler(file_handler)

    root.setLevel(level)
    setattr(root, _CONFIGURED, True)
