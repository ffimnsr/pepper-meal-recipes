#!/usr/bin/env python3
"""Scan the ingredient index + recipe rows for residual junk classes.

Categorizes suspicious ingredient names (unit-in-name, embedded "about"
amounts, temperature water, scoop-leads, tablespoon typos, percent
products, etc.) and prints counts with sample rows so the repair rules
can be extended.

Usage: python3 py-scripts/scan_ingredient_index.py
"""
from __future__ import annotations

import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
RECIPES_DIR = REPO_ROOT / "recipes/v1/recipes/by-id"
INDEX_PATH = REPO_ROOT / "recipes/v1/indexes/ingredients.index.json"

CLASSES: dict[str, re.Pattern[str]] = {
    "fluid_ounce_lead": re.compile(r"^(?:fluid\s+)?ounces?|^fluid ounces?\b", re.IGNORECASE),
    "about_lead": re.compile(r"^about\s*\d", re.IGNORECASE),
    "water_temp": re.compile(r"^water\s+\d", re.IGNORECASE),
    "scoop_lead": re.compile(r"^scoops?\s+", re.IGNORECASE),
    "tablespoon_typo": re.compile(
        r"^(?:tablepoons?|tablepsoons?|tablespooons?|tablspoons?|table spoon)\b", re.IGNORECASE
    ),
    "unit_merged": re.compile(
        r"^(?:tablespoons?|tbsps?|teaspoons?|tsps?|cups?|ounces?|ozs?|pounds?|lbs?|"
        r"cloves?|bunches?|containers?|packages?|cans?)[a-z]",
        re.IGNORECASE,
    ),
    "digit_lead": re.compile(r"^\d+"),
    "dash_junk": re.compile(r"^\d+(?:\.\d+)?-\s"),
    "unit_word_lead": re.compile(
        r"^(?:ounces?|ozs?|pounds?|lbs?|cups?|tablespoons?|tbsps?|teaspoons?|tsps?|"
        r"pints?|quarts?|gallons?|grams?|kilograms?|milliliters?|liters?|cloves?)\b",
        re.IGNORECASE,
    ),
    "prep_artifact": re.compile(
        r"^(?:slivered|lightly packed|roughly chopped|finely chopped|coarsely chopped|"
        r"cut into|broken into pieces|crushed)$",
        re.IGNORECASE,
    ),
    "cloves_garlic_clove": re.compile(r"^cloves? garlic clove\b", re.IGNORECASE),
    "trailing_junk": re.compile(r"\s(?:as needed|for serving|for garnish|to taste)$", re.IGNORECASE),
}

FOODISH = re.compile(
    r"\b(?:milk|cream|yogurt|beef|turkey|chicken|chocolate|cacao|cocoa|pumpkin|lime|onion|"
    r"tortilla|roll|charcoal|stick|ginger|yogurt|cheese|oil|sugar|flour)\b",
    re.IGNORECASE,
)


def main() -> None:
    payload = json.loads(INDEX_PATH.read_text(encoding="utf-8"))
    ingredients = payload.get("ingredients", []) if isinstance(payload, dict) else payload

    # rows per name (recipe_id, position, quantity, unit) for samples
    rows_by_name: dict[str, list[dict]] = defaultdict(list)
    for path in RECIPES_DIR.glob("*.json"):
        recipe = json.loads(path.read_text(encoding="utf-8"))
        for row in recipe.get("ingredients", []):
            name = row.get("name")
            if isinstance(name, str):
                rows_by_name[name].append(
                    {
                        "recipe_id": recipe.get("id"),
                        "slug": recipe.get("slug"),
                        "position": row.get("position"),
                        "quantity": row.get("quantity"),
                        "unit": row.get("unit"),
                        "preparation": row.get("preparation"),
                    }
                )

    buckets: defaultdict[str, list[dict]] = defaultdict(list)
    for entry in ingredients:
        name = entry.get("name") or ""
        normalized = entry.get("normalized_name") or ""
        for label, pattern in CLASSES.items():
            if pattern.search(name):
                buckets[label].append(entry)
                break

    print(f"index entries: {len(ingredients)}\n")
    for label in sorted(buckets):
        entries = buckets[label]
        if label in {"digit_lead", "unit_word_lead"}:
            continue  # printed below with sub-class breakdown
        print(f"── {label}: {len(entries)}")
        for entry in sorted(entries, key=lambda e: -(e.get("recipe_count") or 0))[:8]:
            rows = rows_by_name.get(entry["name"], [])
            row = rows[0] if rows else {}
            print(
                f"    {entry['recipe_count']:>4}x  {entry['name']!r}"
                + (f"  qty={row.get('quantity')!r} unit={row.get('unit')!r}" if row else "")
            )
        print()

    # digit-led names: distinguish percent products (intended) from junk
    digit_entries = buckets["digit_lead"]
    percentish: list[dict] = []
    junkish: list[dict] = []
    for entry in digit_entries:
        name = entry["name"]
        tail = re.sub(r"^\d+(?:\.\d+)?(?:\s+to\s+\d+(?:\.\d+)?)?(?:\s*-\s*)?\s*", "", name)
        if not tail:
            junkish.append(entry)
        elif FOODISH.search(tail) and not re.match(r"^\d+(/|-)", tail):
            percentish.append(entry)
        else:
            junkish.append(entry)
    print(f"── digit_lead percent-ish (intended, kept): {len(percentish)}")
    for entry in sorted(percentish, key=lambda e: -(e.get("recipe_count") or 0))[:30]:
        print(f"    {entry['recipe_count']:>4}x  {entry['name']!r}")
    print(f"\n── digit_lead non-percent (candidates for rules): {len(junkish)}")
    for entry in sorted(junkish, key=lambda e: -(e.get("recipe_count") or 0))[:60]:
        print(f"    {entry['recipe_count']:>4}x  {entry['name']!r}")

    # unit-word-led names (incl. clove-family) — show all
    print(f"\n── unit_word_lead: {len(buckets['unit_word_lead'])}")
    for entry in sorted(buckets["unit_word_lead"], key=lambda e: -(e.get("recipe_count") or 0))[:60]:
        print(f"    {entry['recipe_count']:>4}x  {entry['name']!r}")

    # detailed rows for specific names of interest
    DETAIL_NAMES = [
        "1 cinnamon stick", "1 fat milk", "1 knob of ginger", "10 flour tortilla",
        "10 in flour tortilla", "100 natural hardwood lump charcoal", "100 smoked beef stick",
        "12 count kings hawaiian sweet roll", "2 evaporated milk", "2 lime", "2 low-fat milk",
        "2 plain greek yogurt", "2 reduced fat milk", "3- lobster tail",
        "4 milkfat small curd cottage cheese", "56 cacao semisweet chocolate bar",
        "60 cacao bittersweet chocolate bar", "60 cacao dark chocolate chip",
        "7 bag crunchy corn snacks such as bugle", "70 cocoa dark chocolate", "70 dark chocolate",
        "70 to 80 dark chocolate", "8 fat lean ground chicken", "85 lean ground beef chuc",
        "85 organic", "93-lean ground turkey", "99-lean ground turkey",
        "quarts water", "pint cherry tomato", "pint canning jar", "clove garlic", "cloves garlic",
        "cloves garlic clove", "canned", "canola oil", "cannellini bean",
        "fluid ounces water", "fluid ounce lime juice", "vodka 1 fluid ounce coffee liqueur such as kahla",
        "tablespoon plus 1 teaspoon kosher salt divided", "tablespoons plus 1 kosher salt",
        "tablespoon plus 1/ kosher salt", "table spoon shoaxing cooking wine",
        "about 30 pepperoni slices such as hormel from 1 pkg", "about 4 red onion sliver",
        "slivered", "lightly packed", "crushed", "water as needed", "water at room temperature",
        "lightly packed", "all-purposeflour", "tablea", "7up",
    ]
    print(f"\n── detailed rows for {len(DETAIL_NAMES)} names ──")
    for name in DETAIL_NAMES:
        rows = rows_by_name.get(name)
        if not rows:
            print(f"  {name!r}: NO ROWS (index-only?)")
            continue
        print(f"  {name!r}: {len(rows)} rows")
        for row in rows[:4]:
            print(
                f"      {row['slug']} pos={row['position']} qty={row['quantity']!r} "
                f"unit={row['unit']!r} prep={row['preparation']!r}"
            )
    print(f"\n── index staleness (name in index, no row with that name) ──")
    stale = [e for e in ingredients if e.get("name") not in rows_by_name]
    print(f"stale entries: {len(stale)} / {len(ingredients)}")
    for entry in sorted(stale, key=lambda e: -(e.get("recipe_count") or 0))[:80]:
        print(f"    {entry['recipe_count']:>4}x  {entry['name']!r}")


if __name__ == "__main__":
    sys.exit(main())