#!/usr/bin/env python3
"""Quick scan of the recipe catalog for malformed ingredient rows.

Signals considered "broken":
- name/normalized_name containing stray parentheses or unit remnants ("ounce)", "(0.25")
- name containing digits or leading with a known unit remnant
- quantity containing "(" or letters
- unit == "pieces" while name is not a countable food item (heuristic)
- name identical to a known-bad token soup

Prints aggregate stats and writes a detailed report to /tmp/bad_ingredients.txt
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

RECIPES_DIR = Path(__file__).resolve().parent.parent / "recipes/v1/recipes/by-id"

KNOWN_UNITS = {
    "can", "cans", "clove", "cloves", "cup", "cups", "gram", "grams", "g", "kg",
    "kilogram", "kilograms", "lb", "lbs", "ounce", "ounces", "oz", "package",
    "packages", "piece", "pieces", "pinch", "pinches", "pound", "pounds",
    "sprig", "sprigs", "tablespoon", "tablespoons", "tbsp", "teaspoon",
    "teaspoons", "tsp",
}

# Corruption signals, separate from the design choice of defaulting
# quantity-less rows to unit="pieces".
BAD_NAME_RE = re.compile(r"[\(\)]|\d")
BAD_QUANTITY_RE = re.compile(r"[()a-zA-Z]")
# A unit that still carries a stray parenthesis, e.g. "ounce)" or "(packages".
UNIT_REMNANT_RE = re.compile(r"[\(\)]")
NAME_HAS_UNIT_RE = re.compile(rf"^(?:{ '|'.join(sorted(KNOWN_UNITS, key=len, reverse=True)) })\)?\b")


def main() -> None:
    rows: list[dict] = []
    total_ingredients = 0
    total_recipes = 0
    for path in sorted(RECIPES_DIR.glob("*.json")):
        try:
            recipe = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        total_recipes += 1
        for idx, row in enumerate(recipe.get("ingredients", [])):
            total_ingredients += 1
            name = row.get("name") or ""
            norm = row.get("normalized_name") or ""
            quantity = row.get("quantity")
            unit = row.get("unit")
            issues: list[str] = []

            if not name.strip():
                issues.append("empty-name")
            if BAD_NAME_RE.search(name):
                issues.append(f"name-junk({name!r})")
            if norm and BAD_NAME_RE.search(norm):
                issues.append(f"norm-junk({norm!r})")
            if quantity and BAD_QUANTITY_RE.search(str(quantity)):
                issues.append(f"quantity-junk({quantity!r})")
            if unit and UNIT_REMNANT_RE.search(str(unit)):
                issues.append(f"unit-remnant({unit!r})")
            if NAME_HAS_UNIT_RE.search(name):
                issues.append(f"name-starts-with-unit({name!r})")

            if issues:
                rows.append(
                    {
                        "recipe_id": recipe.get("id"),
                        "recipe_slug": recipe.get("slug"),
                        "position": row.get("position"),
                        "name": name,
                        "quantity": quantity,
                        "unit": unit,
                        "issues": issues,
                    }
                )

    recipes_with_issues = {r["recipe_id"] for r in rows}
    print(f"recipes: {total_recipes}, ingredients: {total_ingredients}")
    print(f"broken rows: {len(rows)} in {len(recipes_with_issues)} recipes")

    # Group by distinct broken name so we see how many distinct fixes are needed.
    by_name: dict[str, list] = {}
    for row in rows:
        by_name.setdefault(row["name"], []).append(row)
    print(f"distinct broken names: {len(by_name)}")

    report = Path("/tmp/bad_ingredients.txt")
    with report.open("w", encoding="utf-8") as fh:
        for name in sorted(by_name):
            occurrences = by_name[name]
            fh.write(f"{len(occurrences):>4}  {name!r}\n")
            for row in occurrences[:2]:
                fh.write(
                    f"        qty={row['quantity']!r} unit={row['unit']!r} "
                    f"slug={row['recipe_slug']} pos={row['position']}\n"
                )
    print(f"detail report: {report}")


if __name__ == "__main__":
    sys.exit(main())