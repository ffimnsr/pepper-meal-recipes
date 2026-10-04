#!/usr/bin/env python3
"""Batch-repair corrupted Allrecipes ingredient rows in the catalog.

Rule-based deterministic re-parse: reconstructs the original line from
corrupted rows, normalizes Allrecipes packaging notation, and re-parses
with the shared ingredient splitter.

Usage:
    python3 py-scripts/repair_ingredients.py            # dry run + reports
    python3 py-scripts/repair_ingredients.py --write    # apply fixes
    python3 py-scripts/repair_ingredients.py --limit 50 # first 50 recipes
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

from ingredient_repair import (  # noqa: E402
    CLEANUP_OVERRIDES,
    fix_cleanup_row,
    fix_embedded_row,
    is_cleanup_row,
    is_corrupt_row,
    is_embedded_row,
    reconstruct_original,
    repair_row,
)
from panlasang_pinoy import INGREDIENT_NAMESPACE, normalize_name, stable_uuid  # noqa: E402
from generate_catalog import load_ignored_ingredient_ids, singularize_phrase  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parent.parent
RECIPES_DIR = REPO_ROOT / "recipes/v1/recipes/by-id"
INDEXES_DIR = REPO_ROOT / "recipes/v1/indexes"
RESIDUALS_PATH = Path("/tmp/ingredient_residuals.json")
REPORT_PATH = Path("/tmp/ingredient_repair_report.json")
EXAMPLE_RECIPES = {"0a0b678a-9c2b-5dd4-8453-3b4d302b7290", "0a0aa454-fc8f-5a27-87ef-8f4765824d6f"}

# Rows whose food was lost upstream and cannot be recovered by rules.
# ("3 cloves" is the standard Allrecipes "3 cloves garlic" phrasing; the
# ounce rows pair with their recipes' context.)
UNIT_ONLY_OVERRIDES: dict[tuple[str, int], tuple[str, str | None]] = {
    ("142d2df0-4a7c-59e4-9c09-6bb0bfbf2f0f", 13): ("garlic", None),
    ("ba3cf2b6-a4b9-58b7-a9a4-4c3c8a0aecd2", 11): ("garlic", None),
    ("bc0135b3-2c34-5fde-8aa9-b44ea26752b0", 10): ("garlic", None),
    ("c6a1b776-0751-5f0f-823d-5e2db7c744d2", 2): ("unsalted butter", "melted and divided"),
    ("1d1dc7a3-ef67-56bf-a0d6-603cd3a58dee", 6): ("powdered sugar", "plus 1 tablespoon, divided"),
}

# Trailing cut words whose removal yields the canonical catalog entry
# ("beef tenderloin roasts" -> "beef tenderloin").
CUT_SUFFIX_WORDS = {"roast", "roasts"}


def load_index_lookup() -> dict[str, dict]:
    """normalized_name -> {id, name, recipe_count} from the ingredient index."""
    index_path = INDEXES_DIR / "ingredients.index.json"
    if not index_path.exists():
        return {}
    payload = load_json(index_path)
    ingredients = payload.get("ingredients", []) if isinstance(payload, dict) else payload
    if not isinstance(ingredients, list):
        return {}
    return {entry["normalized_name"]: entry for entry in ingredients if entry.get("normalized_name")}


def canonical_candidates(normalized: str) -> list[str]:
    """Close-name variants worth checking against the index, most exact first."""
    candidates = [normalized]
    singular = singularize_phrase(normalized)
    if singular != normalized:
        candidates.append(singular)
    tokens = normalized.split()
    if tokens and tokens[-1] in CUT_SUFFIX_WORDS:
        base = " ".join(tokens[:-1])
        candidates.append(base)
        singular_base = singularize_phrase(base)
        if singular_base != base:
            candidates.append(singular_base)
    return candidates


def canonicalize_with_index(row: dict[str, Any], lookup: dict[str, dict]) -> dict[str, Any]:
    """Adopt the existing catalog entry (id + name) when a close variant
    already exists, preferring the entry used by the most recipes."""
    best: dict | None = None
    for candidate in canonical_candidates(row["normalized_name"]):
        entry = lookup.get(candidate)
        if entry and (best is None or entry["recipe_count"] > best["recipe_count"]):
            best = entry
    if best:
        row["ingredient_id"] = best["id"]
        row["name"] = best["name"]
        row["normalized_name"] = best["normalized_name"]
    return row


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def row_unchanged(repaired: dict[str, Any], row: dict[str, Any]) -> bool:
    """True when a fixer returned the row without any real change."""
    return all(
        repaired.get(field) == row.get(field)
        for field in ("name", "normalized_name", "quantity", "unit", "preparation")
    )


def dump_json(path: Path, payload: Any) -> None:
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write", action="store_true", help="apply fixes to recipe files")
    parser.add_argument("--limit", type=int, default=0, help="only process the first N recipes")
    parser.add_argument(
        "--embedded",
        action="store_true",
        help="repair rows whose names still carry measurements (\"plus 2 tablespoons \""
        "butter\", \"about 8 oz. flour\", \"a large avocado\"); all sources",
    )
    args = parser.parse_args()

    stats: Counter[str] = Counter()
    changed_files: dict[str, dict] = {}
    residuals: list[dict] = []
    example_diff: list[dict] = []
    embedded_samples: list[dict] = []
    embedded_skipped_names: Counter[str] = Counter()
    index_lookup = load_index_lookup()
    ignored_ids = load_ignored_ingredient_ids()

    paths = sorted(RECIPES_DIR.glob("*.json"))
    if args.limit:
        paths = paths[: args.limit]

    for path in paths:
        recipe = load_json(path)
        source = (recipe.get("source") or {}).get("name", "")
        if not args.embedded and source != "Allrecipes":
            stats["recipes_skipped_non_allrecipes"] += 1
            continue
        stats["recipes_scanned"] += 1

        modified = False
        for row in recipe.get("ingredients", []):
            if args.embedded:
                position = int(row.get("position") or 0)
                recipe_id = recipe.get("id")
                override = CLEANUP_OVERRIDES.get((recipe_id, position))
                if override is None:
                    override = UNIT_ONLY_OVERRIDES.get((recipe_id, position))
                    if isinstance(override, tuple):
                        override = {"name": override[0], "preparation": override[1]}
                if override is not None:
                    name = override["name"]
                    repaired = dict(row)
                    repaired.update(
                        {
                            "ingredient_id": stable_uuid(INGREDIENT_NAMESPACE, normalize_name(name)),
                            "name": name,
                            "normalized_name": normalize_name(name),
                            "quantity": override.get("quantity", row.get("quantity")),
                            "unit": override.get("unit", row.get("unit")),
                            "preparation": override.get("preparation", row.get("preparation")),
                        }
                    )
                    if row_unchanged(repaired, row):
                        continue
                elif stable_uuid(INGREDIENT_NAMESPACE, normalize_name(row.get("name") or "")) in ignored_ids:
                    # User-accepted canonical names are indexed as-is; never
                    # rewrite them (a cleanup would re-open the decision).
                    stats["embedded_skipped_accepted"] += 1
                    continue
                elif not (is_embedded_row(row) or is_cleanup_row(row)):
                    continue
                else:
                    stats["embedded_rows"] += 1
                    repaired = fix_embedded_row(row)
                    if row_unchanged(repaired, row):
                        repaired = fix_cleanup_row(row)
                    else:
                        # Embedding fixes can expose cleanup patterns
                        # ("plus 2 tablespoons finely chopped parsley").
                        cleaned = fix_cleanup_row(repaired)
                        if cleaned is not None:
                            repaired = cleaned
                    if repaired is None or row_unchanged(repaired, row):
                        stats["embedded_skipped"] += 1
                        embedded_skipped_names[row.get("name") or ""] += 1
                        continue
            else:
                if not is_corrupt_row(row):
                    continue
                stats["corrupt_rows"] += 1
                repaired = repair_row(row)
                if repaired is None:
                    stats["residual_rows"] += 1
                    residuals.append(
                        {
                            "recipe_id": recipe.get("id"),
                            "recipe_slug": recipe.get("slug"),
                            "recipe_name": recipe.get("name"),
                            "position": row.get("position"),
                            "original_text": reconstruct_original(row),
                            "old_row": row,
                        }
                    )
                    continue
            stats["repaired_rows"] += 1
            pre_canonical = repaired.get("normalized_name")
            canonicalize_with_index(repaired, index_lookup)
            if repaired.get("normalized_name") != pre_canonical:
                stats["canonical_merges"] += 1
            diff = {
                "recipe_id": recipe.get("id"),
                "position": row.get("position"),
                "old": {
                    "name": row.get("name"),
                    "normalized_name": row.get("normalized_name"),
                    "quantity": row.get("quantity"),
                    "unit": row.get("unit"),
                    "preparation": row.get("preparation"),
                },
                "new": {
                    "ingredient_id": repaired.get("ingredient_id"),
                    "name": repaired.get("name"),
                    "normalized_name": repaired.get("normalized_name"),
                    "quantity": repaired.get("quantity"),
                    "unit": repaired.get("unit"),
                    "preparation": repaired.get("preparation"),
                },
            }
            if args.embedded and len(embedded_samples) < 16:
                embedded_samples.append(diff)
            if recipe.get("id") in EXAMPLE_RECIPES:
                example_diff.append(diff)
            if args.write:
                row.update(repaired)
                modified = True
            else:
                changed_files.setdefault(path.name, 0)
                changed_files[path.name] += 1

        if args.write and modified:
            recipe["updated_at"] = int(datetime.now(timezone.utc).timestamp())
            dump_json(path, recipe)

    # ── reports ───────────────────────────────────────────────────────────────
    residual_names = Counter(r["old_row"]["name"] for r in residuals)
    if args.embedded:
        print(f"recipes scanned:        {stats['recipes_scanned']}")
        print(f"embedded rows:          {stats['embedded_rows']}")
        print(f"rows fixed:             {stats['repaired_rows']}")
        print(f"canonical merges:       {stats['canonical_merges']}")
        print(f"skipped (no food):      {stats['embedded_skipped']}")
        print(f"skipped (user-accepted): {stats['embedded_skipped_accepted']}")
        print("\n── example embedded repairs ──")
        for diff in embedded_samples[:14]:
            old = diff["old"]
            new = diff["new"]
            print(
                f"  pos {diff['position']}: "
                f"qty={old['quantity']!r} unit={old['unit']!r} name={old['name']!r}\n"
                f"       -> qty={new['quantity']!r} unit={new['unit']!r} "
                f"name={new['name']!r} prep={new['preparation']!r}"
            )
        print("\n── skipped (no food to keep) ──")
        for name, count in embedded_skipped_names.most_common(20):
            print(f"  {count:>2}  {name!r}")
    else:
        print(f"recipes skipped:        {stats['recipes_skipped_non_allrecipes']}")
        print(f"corrupt rows:           {stats['corrupt_rows']}")
        print(f"rows repaired by rules: {stats['repaired_rows']}")
        print(f"canonical merges:       {stats['canonical_merges']}")
        print(f"residual rows:          {stats['residual_rows']}")
        print(f"residual distinct names:{len(residual_names)}")

        print("\n── example repairs (your two recipes) ──")
        for diff in example_diff:
            old = diff["old"]
            new = diff["new"]
            print(
                f"  pos {diff['position']}: "
                f"qty={old['quantity']!r} unit={old['unit']!r} name={old['name']!r}\n"
                f"       -> qty={new['quantity']!r} unit={new['unit']!r} "
                f"name={new['name']!r} prep={new['preparation']!r}"
            )

        print("\n── top residual names ──")
        for name, count in residual_names.most_common(25):
            texts = [r["original_text"] for r in residuals if r["old_row"]["name"] == name][:1]
            print(f"  {count:>4}  {name!r}  <- {texts[0]!r}")
        dump_json(RESIDUALS_PATH, residuals)
    dump_json(
        REPORT_PATH,
        {
            "stats": dict(stats),
            "residual_distinct_names": len(residual_names),
            "files_affected": sum(changed_files.values()) if args.limit else len(changed_files),
        },
    )
    print(f"\nresiduals -> {RESIDUALS_PATH}")
    print(f"report    -> {REPORT_PATH}")

    if args.write:
        print("changes written (updated_at bumped per file)")


if __name__ == "__main__":
    sys.exit(main())