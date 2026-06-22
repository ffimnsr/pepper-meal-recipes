#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.generate_catalog import (
    INGREDIENT_NAMESPACE,
    INDEXES_DIR,
    RECIPES_DIR,
    dump_json,
    normalize_name,
    stable_uuid,
)


@dataclass(frozen=True)
class IngredientTarget:
    ingredient_id: str
    name: str
    normalized_name: str


@dataclass
class EditStats:
    recipe_files_updated: int = 0
    ingredient_rows_updated: int = 0


def load_json(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def load_ingredient_index() -> dict[str, dict]:
    index_path = INDEXES_DIR / "ingredients.index.json"
    if not index_path.exists():
        raise SystemExit(f"missing ingredient index: {index_path}")

    payload = load_json(index_path)
    return {entry["id"]: entry for entry in payload.get("ingredients", [])}


def list_recipe_files() -> list[Path]:
    if not RECIPES_DIR.exists():
        return []
    return sorted(RECIPES_DIR.glob("*.json"))


def target_from_id(ingredient_index: dict[str, dict], ingredient_id: str) -> IngredientTarget:
    entry = ingredient_index.get(ingredient_id)
    if entry is None:
        raise SystemExit(f"unknown ingredient id: {ingredient_id}")
    normalized_name = entry.get("normalized_name") or normalize_name(entry["name"])
    return IngredientTarget(
        ingredient_id=entry["id"],
        name=normalized_name,
        normalized_name=normalized_name,
    )


def update_recipe_ingredients(recipe: dict, source_ids: set[str], target: IngredientTarget) -> int:
    updated_rows = 0
    for ingredient in recipe.get("ingredients", []):
        if ingredient.get("ingredient_id") not in source_ids:
            continue
        ingredient["ingredient_id"] = target.ingredient_id
        ingredient["name"] = target.name
        ingredient["normalized_name"] = target.normalized_name
        updated_rows += 1
    return updated_rows


def rewrite_recipes(source_ids: set[str], target: IngredientTarget) -> EditStats:
    stats = EditStats()
    for recipe_path in list_recipe_files():
        recipe = load_json(recipe_path)
        updated_rows = update_recipe_ingredients(recipe, source_ids, target)
        if not updated_rows:
            continue
        dump_json(recipe_path, recipe)
        stats.recipe_files_updated += 1
        stats.ingredient_rows_updated += updated_rows
    return stats


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Update recipe ingredient rows by ingredient id.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    merge_parser = subparsers.add_parser(
        "merge",
        help="Merge one or more ingredient ids into the first id provided.",
    )
    merge_parser.add_argument("ingredient_ids", nargs="+", help="Ingredient ids to merge.")

    rename_parser = subparsers.add_parser("rename", help="Rename an ingredient id to a new ingredient name.")
    rename_parser.add_argument("ingredient_id", help="Ingredient id to rename.")
    rename_parser.add_argument("ingredient_name", nargs="+", help="Replacement ingredient name.")

    return parser.parse_args()


def run_merge(ingredient_ids: list[str]) -> EditStats:
    unique_ids = list(dict.fromkeys(ingredient_ids))
    if len(unique_ids) < 2:
        raise SystemExit("merge requires at least two ingredient ids")

    ingredient_index = load_ingredient_index()
    missing_ids = [ingredient_id for ingredient_id in unique_ids if ingredient_id not in ingredient_index]
    if missing_ids:
        raise SystemExit(f"unknown ingredient id(s): {', '.join(missing_ids)}")

    target = target_from_id(ingredient_index, unique_ids[0])
    source_ids = set(unique_ids)
    return rewrite_recipes(source_ids, target)


def run_rename(ingredient_id: str, ingredient_name: str) -> EditStats:
    ingredient_index = load_ingredient_index()
    if ingredient_id not in ingredient_index:
        raise SystemExit(f"unknown ingredient id: {ingredient_id}")

    normalized_name = normalize_name(ingredient_name)
    if not normalized_name:
        raise SystemExit("ingredient name cannot be empty")

    target = IngredientTarget(
        ingredient_id=stable_uuid(INGREDIENT_NAMESPACE, normalized_name),
        name=normalized_name,
        normalized_name=normalized_name,
    )
    return rewrite_recipes({ingredient_id}, target)


def main() -> None:
    args = parse_args()
    if args.command == "merge":
        stats = run_merge(args.ingredient_ids)
    elif args.command == "rename":
        stats = run_rename(args.ingredient_id, " ".join(args.ingredient_name))
    else:  # pragma: no cover - argparse enforces this
        raise SystemExit(f"unknown command: {args.command}")

    print(
        f"updated {stats.recipe_files_updated} recipe files and {stats.ingredient_rows_updated} ingredient rows"
    )


if __name__ == "__main__":
    main()
