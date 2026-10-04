#!/usr/bin/env python3
"""In-memory dry-run of generate_catalog: what WOULD the regenerated
ingredient index and review queue look like, without writing anything.

Usage:
    python3 py-scripts/dryrun_generate_catalog.py
    python3 py-scripts/dryrun_generate_catalog.py --find "10 flour tortilla|85 organic"
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import generate_catalog as gc  # noqa: E402

INDEX_PATH = gc.REPO_ROOT / "recipes/v1/indexes/ingredients.index.json"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--find", default="", help="regex; print raw rows whose canonical name matches")
    args = parser.parse_args()
    find_re = re.compile(args.find) if args.find else None

    payload = json.loads(INDEX_PATH.read_text(encoding="utf-8"))
    current = payload.get("ingredients", []) if isinstance(payload, dict) else payload
    current_by_name = {e["normalized_name"]: e.get("recipe_count", 0) for e in current}

    ingredient_counts: Counter[str] = Counter()
    review_names: list[str] = []
    review_total = 0
    matches: list[tuple[str, str, int, dict, str]] = []  # canonical, slug, pos, raw_row, recipe_id
    for recipe_path in gc.sort_recipe_files():
        recipe = gc.load_json(recipe_path)
        canonical, review_entries = gc.canonicalize_recipe(recipe)
        review_total += len(review_entries)
        for entry in review_entries:
            cleaned = entry.get("cleaned_name") or entry.get("original_text") or ""
            if cleaned:
                review_names.append(gc.normalize_name(cleaned))
        if find_re:
            for ingredient in canonical["ingredients"]:
                if find_re.search(ingredient["normalized_name"]):
                    raw = next(
                        (r for r in recipe.get("ingredients", []) if r.get("position") == ingredient.get("position")),
                        {},
                    )
                    matches.append(
                        (ingredient["normalized_name"], recipe["slug"],
                         ingredient.get("position") or 0, raw, recipe.get("id", ""))
                    )
        for ingredient in canonical["ingredients"]:
            ingredient_counts[ingredient["normalized_name"]] += 1

    if find_re:
        by_canonical: dict[str, list] = {}
        for canonical, slug, pos, raw, recipe_id in matches:
            by_canonical.setdefault(canonical, []).append((slug, pos, raw, recipe_id))
        print(f"rows matching {args.find!r}: {len(matches)}")
        for canonical in sorted(by_canonical):
            print(f"\n  {canonical!r}: {len(by_canonical[canonical])} rows")
            for slug, pos, raw, recipe_id in by_canonical[canonical][:6]:
                print(
                    f"      {recipe_id[:8]} {slug} pos={pos} "
                    f"name={raw.get('name')!r} qty={raw.get('quantity')!r} "
                    f"unit={raw.get('unit')!r} prep={raw.get('preparation')!r}"
                )
        return

    dropped = [name for name in current_by_name if name not in ingredient_counts]
    added = [name for name in ingredient_counts if name not in current_by_name]
    changed = [
        name
        for name in current_by_name
        if name in ingredient_counts and ingredient_counts[name] != current_by_name[name]
    ]

    print(f"current index entries:   {len(current_by_name)}")
    print(f"would-be index entries:  {len(ingredient_counts)}")
    print(f"dropped entries:         {len(dropped)}")
    print(f"added entries:           {len(added)}")
    print(f"count changed:           {len(changed)}")

    print("\n── top dropped (would disappear) ──")
    for name, count in sorted(((n, current_by_name[n]) for n in dropped), key=lambda x: -x[1])[:60]:
        print(f"    {count:>4}x  {name!r}")
    print("\n── top added ──")
    for name in sorted(added, key=lambda x: -ingredient_counts[x])[:40]:
        print(f"    {ingredient_counts[name]:>4}x  {name!r}")

    print(f"\nwould-be review queue:   {review_total} entries")
    review_counts = Counter(review_names)
    for name, count in review_counts.most_common(40):
        print(f"    {count:>4}  {name!r}")


if __name__ == "__main__":
    sys.exit(main())