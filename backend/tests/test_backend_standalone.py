"""backend/ must import and resolve its own files with nothing else present.

This is the test the old layout would have failed, and the reason it existed
unnoticed for so long: backend/src reached the perception stack by inserting
the repo's top-level scripts/ directory onto sys.path at import time. Inside a
checkout that worked perfectly. Copied to a GPU box on its own, nine modules
raised ModuleNotFoundError -- every pipeline, the camera runtime, the inference
registry and the supervisor among them.

Nothing about that was visible from inside the repo, so no amount of care
would have caught it. Only a test that takes backend away from its siblings
can, which is what this does: it copies backend/ into a scratch directory,
runs a fresh interpreter whose sys.path contains only that directory, and
imports every module under backend/src.

Deliberately a subprocess and a copy, not an import here. Reaching for
importlib in this process would find the modules already loaded and the repo
already on sys.path -- it would test nothing and pass always.
"""
from __future__ import annotations

import ast
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
REPO = BACKEND.parent

# Model weights are 364 MB and irrelevant to importing; data/ is 12 GB. The
# question here is whether the CODE stands alone, so the copy skips both.
SKIP = shutil.ignore_patterns("__pycache__", "*.pyc", "models", "data", ".git")

CHILD = r"""
import importlib, json, pathlib, sys, traceback
root = pathlib.Path(sys.argv[1]).resolve()
sys.path.insert(0, str(root))
mods = sorted(
    p.relative_to(root).with_suffix("").as_posix().replace("/", ".")
    for p in (root / "backend" / "src").rglob("*.py")
    if p.name != "__init__.py"
)
failures = []
for m in mods:
    try:
        importlib.import_module(m)
    except Exception as e:
        failures.append((m, f"{type(e).__name__}: {e}"))
print(json.dumps({"total": len(mods), "failures": failures}))
"""


def _isolated_copy(tmp: Path) -> Path:
    """backend/ alone in an empty directory, exactly as it would be shipped."""
    dest = tmp / "shipped"
    dest.mkdir()
    shutil.copytree(BACKEND, dest / "backend", ignore=SKIP)
    return dest


def test_every_backend_module_imports_without_the_repo():
    with tempfile.TemporaryDirectory() as td:
        root = _isolated_copy(Path(td))
        proc = subprocess.run(
            [sys.executable, "-c", CHILD, str(root)],
            capture_output=True, text=True, timeout=600,
        )
        assert proc.returncode == 0, f"child crashed:\n{proc.stderr}"
        report = json.loads(proc.stdout.strip().splitlines()[-1])

    assert report["total"] > 30, f"only found {report['total']} modules -- copy is wrong"
    if report["failures"]:
        lines = "\n".join(f"  {m}\n      {e}" for m, e in report["failures"])
        raise AssertionError(
            f"{len(report['failures'])} of {report['total']} backend modules cannot "
            f"import without the rest of the repo:\n{lines}\n\n"
            "Whatever they reach for belongs inside backend/, or the code that "
            "reaches for it belongs in dev/."
        )


def test_no_module_puts_a_named_directory_on_sys_path():
    """The exact mechanism that hid the old coupling, banned by shape.

    `sys.path.insert(0, str(ROOT))` is allowed: an entry point invoked as
    `python backend/src/app/run_split_pipeline.py` has to put the directory
    above backend on the path before `import backend...` can work, and that
    directory is derived from __file__, so it is wherever backend actually is.

    `sys.path.insert(0, str(ROOT / "scripts"))` is banned. Naming a sibling
    directory is what made the import graph depend on backend having neighbours
    -- and it is invisible to the import test above, because re-adding it would
    make the imports succeed inside a checkout again. The tell is the string
    literal: a path built from a name, rather than located from this file.
    """
    offenders = []
    for path in sorted((BACKEND / "src").rglob("*.py")):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            f = getattr(node, "func", None)
            if not (isinstance(node, ast.Call) and isinstance(f, ast.Attribute)
                    and f.attr in {"insert", "append", "extend"}):
                continue
            tgt = f.value
            if not (isinstance(tgt, ast.Attribute) and tgt.attr == "path"
                    and isinstance(tgt.value, ast.Name) and tgt.value.id == "sys"):
                continue
            named = [n.value for a in node.args for n in ast.walk(a)
                     if isinstance(n, ast.Constant) and isinstance(n.value, str)]
            if named:
                offenders.append(
                    f"{path.relative_to(REPO)}:{node.lineno} -> {named}")
    assert not offenders, (
        "backend/src may only put its own parent on sys.path. Naming another "
        "directory is how the scripts/ coupling stayed invisible:\n  "
        + "\n  ".join(offenders)
    )


def test_nothing_locates_its_own_files_by_going_up_and_back_down():
    """`parents[3] / "backend/sql/..."` is banned; ask backend_root() instead.

    Four call sites located the SQL schema that way -- walking up to what they
    assumed was a checkout, then back down through a directory they assumed was
    named `backend`. Both assumptions hold in this repo and neither is checked
    anywhere, so a renamed deploy directory breaks them all at once. Two of the
    four then swallowed the miss with `if schema_path.exists()`, creating a
    database with no tables and surfacing it later as a foreign-key error in
    start_run().

    A path derived from __file__ and used to reach *upward* past backend is the
    tell. Reaching sideways into a sibling is already covered by the sys.path
    test; this covers reaching up and back in.
    """
    offenders = []
    for path in sorted((BACKEND / "src").rglob("*.py")):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if not (isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div)):
                continue
            right = node.right
            if not (isinstance(right, ast.Constant) and isinstance(right.value, str)):
                continue
            if right.value.startswith("backend/") or right.value == "backend":
                offenders.append(f"{path.relative_to(REPO)}:{node.lineno} "
                                 f"-> {right.value!r}")
    assert not offenders, (
        "backend reaches up out of itself and back in by name. Use "
        "backend_root() / schema_path() / model_path():\n  " + "\n  ".join(offenders)
    )


def test_a_missing_schema_says_so_instead_of_carrying_on():
    sys.path.insert(0, str(REPO))
    from backend.src.common.paths import schema_path

    assert schema_path("local_dev_schema.sql").exists()
    try:
        schema_path("no_such_schema.sql")
    except FileNotFoundError as e:
        assert "packaging" in str(e)
    else:
        raise AssertionError("a missing schema was accepted silently")


def test_config_and_models_resolve_inside_backend():
    """Zones and weights are inputs the pipeline reads at runtime, so they ship."""
    sys.path.insert(0, str(REPO))
    from backend.src.common.paths import backend_root, model_path, zones_dir

    assert backend_root() == BACKEND
    assert zones_dir().is_dir(), "backend/config/zones is missing"

    # top_side_view_zones.json used to live in outputs/, the dev artefact
    # directory, and was the counter pipeline's default. A backend unpacked
    # anywhere else came up with no zones and failed at camera bootstrap.
    for name in ("top_side_view_zones.json", "storage_zones.json"):
        p = zones_dir() / name
        assert p.exists(), f"zone config not shipped: {p}"
        json.loads(p.read_text())

    for name in ("yolo26m-pose.onnx", "osnet_x0_25_msmt17.onnx",
                 "till_seg_v4_640.onnx", "yolov6s_face.onnx",
                 "ms1mv3_arcface_r18.onnx"):
        resolved = Path(model_path(name))
        assert resolved.parent == BACKEND / "models", f"{name} resolves outside backend"


def test_manifests_use_placeholders_not_repo_layout():
    """Config may not assume the folder above backend is a checkout."""
    sys.path.insert(0, str(REPO))
    from backend.src.common.paths import resolve_repo_path

    bad = []
    for manifest in sorted((BACKEND / "config").rglob("*.json")):
        text = manifest.read_text()
        if "${REPO_ROOT}" in text:
            bad.append(f"{manifest.name}: still uses ${{REPO_ROOT}}")
        # ${DATA_ROOT} is footage. Anything under backend/ is backend's own and
        # must say so: the bulk rewrite from ${REPO_ROOT} turned the staff-seed
        # db_path into ${DATA_ROOT}/backend/data/..., which resolves to a
        # directory that does not exist and would have failed at seed time.
        if "${DATA_ROOT}/backend" in text:
            bad.append(f"{manifest.name}: ${{DATA_ROOT}}/backend -- "
                       "use ${BACKEND_ROOT}/ for backend's own files")
        for cam in json.loads(text).get("cameras", []):
            uri = cam.get("stream", {}).get("uri", "")
            if uri.startswith("${DATA_ROOT}"):
                assert Path(resolve_repo_path(uri)).is_absolute()
            elif uri and not uri.startswith(("rtsp://", "${")):
                bad.append(f"{manifest.name}/{cam.get('camera_id')}: bare path {uri}")
    assert not bad, "manifest paths not portable:\n  " + "\n  ".join(bad)
