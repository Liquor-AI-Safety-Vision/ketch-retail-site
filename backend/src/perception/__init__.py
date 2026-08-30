"""Perception stack: detect, track, zone state, face align and identity.

These modules used to live in the repo's top-level scripts/ directory, and
backend/src reached them by inserting that directory onto sys.path at import
time. That worked in the repo and nowhere else: copying backend/ to a GPU box
left nine of its modules unimportable, including every pipeline. They live
here now so that backend/ is the whole shipping unit.

The dependency direction is one-way by design -- dev/ and demo/ import from
backend, backend imports nothing outside itself. tests/test_backend_standalone.py
enforces that, because the previous arrangement was invisible from inside the
repo and only failed at deploy.
"""
