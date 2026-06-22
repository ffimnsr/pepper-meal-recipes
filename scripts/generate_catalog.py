from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SOURCE_PATH = REPO_ROOT / "py-scripts" / "generate_catalog.py"
SPEC = importlib.util.spec_from_file_location("generate_catalog_impl", SOURCE_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)

for name in dir(MODULE):
    if name.startswith("_"):
        continue
    globals()[name] = getattr(MODULE, name)
