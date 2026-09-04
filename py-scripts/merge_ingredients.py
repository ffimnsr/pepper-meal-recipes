#!/usr/bin/env python3

"""Interactively merge multiple ingredient identities into a single canonical ingredient."""

from __future__ import annotations

import importlib.util
import json
import re
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
GENERATE_CATALOG_PATH = Path(__file__).with_name("generate_catalog.py")
GENERATE_CATALOG_SPEC = importlib.util.spec_from_file_location("generate_catalog", GENERATE_CATALOG_PATH)
assert GENERATE_CATALOG_SPEC is not None
assert GENERATE_CATALOG_SPEC.loader is not None
generate_catalog = importlib.util.module_from_spec(GENERATE_CATALOG_SPEC)
sys.modules[GENERATE_CATALOG_SPEC.name] = generate_catalog
GENERATE_CATALOG_SPEC.loader.exec_module(generate_catalog)

INGREDIENT_NAMESPACE = generate_catalog.INGREDIENT_NAMESPACE
INDEXES_DIR = generate_catalog.INDEXES_DIR
RECIPES_DIR = generate_catalog.RECIPES_DIR
dump_json = generate_catalog.dump_json
normalize_name = generate_catalog.normalize_name
stable_uuid = generate_catalog.stable_uuid

MAX_SEARCH_MATCHES = 300

Input = Callable[[str], str]
Output = Callable[[str], None]


@dataclass(frozen=True)
class IngredientIdentity:
    id: str
    name: str
    normalized_name: str


@dataclass
class MergeStats:
    recipe_files_updated: int = 0
    ingredient_rows_updated: int = 0


def load_json(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def load_ingredient_index() -> list[dict]:
    index_path = INDEXES_DIR / "ingredients.index.json"
    if not index_path.exists():
        raise SystemExit(f"missing ingredient index: {index_path}")

    payload = load_json(index_path)
    ingredients = payload.get("ingredients", [])
    if not isinstance(ingredients, list):
        raise SystemExit(f"invalid ingredient index: {index_path}")
    return ingredients


def list_recipe_files() -> list[Path]:
    if not RECIPES_DIR.exists():
        return []
    return sorted(RECIPES_DIR.glob("*.json"))


def parse_selection_ranges(answer: str) -> list[tuple[int, int]] | None:
    """Parse '1,3,5-7' into 1-based index ranges, or None for a search term."""
    if not re.fullmatch(r"[\d,\s-]+", answer):
        return None
    ranges: list[tuple[int, int]] = []
    for token in re.split(r"[\s,]+", answer.strip()):
        if not token:
            continue
        try:
            if "-" in token:
                start_text, end_text = token.split("-", 1)
                start, end = int(start_text), int(end_text)
            else:
                start = end = int(token)
        except ValueError:
            return None
        if start < 1 or end < start:
            return None
        ranges.append((start, end))
    return ranges


def filter_ingredient_entries(ingredients: list[dict], query: str) -> list[dict]:
    normalized_query = query.casefold()
    return [
        entry
        for entry in ingredients
        if normalized_query in str(entry.get("name", "")).casefold()
        or normalized_query in str(entry.get("normalized_name", "")).casefold()
    ]


def display_ingredient_entries(entries: list[dict], *, output: Output = print) -> None:
    for index, entry in enumerate(entries, start=1):
        output(f"{index:>4}. {entry.get('name')} ({entry.get('id')})")


def selected_entries(ingredients: list[dict], selected_ids: set[str]) -> list[dict]:
    return [entry for entry in ingredients if entry.get("id") in selected_ids]


def select_ingredient_entries(
    ingredients: list[dict],
    *,
    input_fn: Input = input,
    output: Output = print,
) -> list[dict]:
    """Interactively multi-select index entries; return them in index order."""
    selected_ids: set[str] = set()
    view: list[dict] = ingredients
    output("All indexed ingredients:")
    display_ingredient_entries(view, output=output)

    while True:
        if selected_ids:
            names = ", ".join(entry["name"] for entry in selected_entries(ingredients, selected_ids))
            output(f"\nSelected ({len(selected_ids)}): {names}")
        else:
            output("\nNo ingredients selected yet.")
        answer = input_fn(
            "Select: numbers (e.g. 1,3,5-7) toggle listed items; search text filters; "
            "[l]ist, [r]eset, [c]lear, [d]one, [q]uit: "
        ).strip()

        if not answer or answer.lower() in {"d", "done"}:
            return selected_entries(ingredients, selected_ids)
        if answer.lower() in {"q", "quit"}:
            output("Merge cancelled. No files were changed.")
            raise SystemExit(0)
        if answer.lower() in {"l", "list"}:
            display_ingredient_entries(view, output=output)
            continue
        if answer.lower() in {"r", "reset"}:
            view = ingredients
            display_ingredient_entries(view, output=output)
            continue
        if answer.lower() in {"c", "clear"}:
            selected_ids.clear()
            output("Selection cleared.")
            continue

        ranges = parse_selection_ranges(answer)
        if ranges is not None:
            toggled: list[str] = []
            for start, end in ranges:
                if end > len(view):
                    output(f"Ignoring {start}-{end}: only {len(view)} items are listed.")
                    continue
                for index in range(start, end + 1):
                    entry = view[index - 1]
                    ingredient_id = entry.get("id")
                    if ingredient_id in selected_ids:
                        selected_ids.discard(ingredient_id)
                        toggled.append(f"-{entry.get('name')}")
                    else:
                        selected_ids.add(ingredient_id)
                        toggled.append(f"+{entry.get('name')}")
            output("  " + ", ".join(toggled) if toggled else "  No changes.")
            continue

        matches = filter_ingredient_entries(ingredients, answer)
        if not matches:
            output(f"No ingredients match {answer!r}.")
        elif len(matches) > MAX_SEARCH_MATCHES:
            output(f"{len(matches)} ingredients match {answer!r}; refine the search.")
        else:
            view = matches
            output(f"Matching {len(matches)} ingredient(s):")
            display_ingredient_entries(view, output=output)


def choose_merged_name(
    selected: list[dict],
    *,
    input_fn: Input = input,
    output: Output = print,
) -> str:
    """Return the ingredient name to keep, picked from the selection or typed fresh."""
    while True:
        output("\nChoose the name for the merged ingredient:")
        for index, entry in enumerate(selected, start=1):
            output(f"  {index}. {entry.get('name')} ({entry.get('id')})")
        answer = input_fn("Enter a number above to use that name, or type a new name: ").strip()
        if answer.isdigit():
            number = int(answer)
            if 1 <= number <= len(selected):
                return str(selected[number - 1].get("name"))
            output(f"Number must be between 1 and {len(selected)}.")
            continue
        if answer:
            if not normalize_name(answer):
                output("Name must contain letters or numbers.")
                continue
            return answer
        output("Enter a number or a name.")


def ask_yes_no(
    prompt: str,
    *,
    default: bool | None = None,
    input_fn: Input = input,
    output: Output = print,
) -> bool:
    if default is True:
        choices = "[Y/n]"
    elif default is False:
        choices = "[y/N]"
    else:
        choices = "[y/n]"

    while True:
        answer = input_fn(f"{prompt} {choices}: ").strip().lower()
        if not answer and default is not None:
            return default
        if answer in {"y", "yes"}:
            return True
        if answer in {"n", "no"}:
            return False
        output("Please answer y or n.")


def count_referencing_rows(recipe_paths: list[Path], source_ids: set[str]) -> tuple[int, int]:
    """Return (ingredient rows, recipe files) that reference any source id."""
    rows = 0
    files = 0
    for recipe_path in recipe_paths:
        recipe = load_json(recipe_path)
        matching = [
            ingredient
            for ingredient in recipe.get("ingredients", [])
            if ingredient.get("ingredient_id") in source_ids
        ]
        if matching:
            files += 1
            rows += len(matching)
    return rows, files


def merge_ingredient_identities(
    recipe_paths: list[Path],
    source_ids: set[str],
    target: IngredientIdentity,
) -> MergeStats:
    """Rewrite every recipe row referencing a source id to the target identity."""
    stats = MergeStats()
    for recipe_path in recipe_paths:
        recipe = load_json(recipe_path)
        recipe_changed = False
        for ingredient in recipe.get("ingredients", []):
            if ingredient.get("ingredient_id") not in source_ids:
                continue
            if (
                ingredient.get("ingredient_id") == target.id
                and ingredient.get("name") == target.name
                and ingredient.get("normalized_name") == target.normalized_name
            ):
                continue
            ingredient["ingredient_id"] = target.id
            ingredient["name"] = target.name
            ingredient["normalized_name"] = target.normalized_name
            recipe_changed = True
            stats.ingredient_rows_updated += 1
        if recipe_changed:
            dump_json(recipe_path, recipe)
            stats.recipe_files_updated += 1
    return stats


def run_merge(*, input_fn: Input = input, output: Output = print) -> MergeStats:
    ingredients = load_ingredient_index()
    recipe_files = list_recipe_files()
    output(f"Loaded {len(ingredients)} ingredients from {INDEXES_DIR / 'ingredients.index.json'}.")

    selected = select_ingredient_entries(ingredients, input_fn=input_fn, output=output)
    while len(selected) == 1:
        output("\nSelect at least two ingredients to merge.")
        selected = select_ingredient_entries(ingredients, input_fn=input_fn, output=output)
    if not selected:
        output("No ingredients selected; nothing to do.")
        return MergeStats()

    selected_ids = {entry["id"] for entry in selected}
    merged_name = choose_merged_name(selected, input_fn=input_fn, output=output)
    normalized_name = normalize_name(merged_name)
    target = IngredientIdentity(
        id=stable_uuid(INGREDIENT_NAMESPACE, normalized_name),
        name=merged_name,
        normalized_name=normalized_name,
    )

    row_count, file_count = count_referencing_rows(recipe_files, selected_ids)
    collision = next(
        (entry for entry in ingredients if entry.get("id") == target.id and entry.get("id") not in selected_ids),
        None,
    )

    output("\nMerge preview")
    output(f"  Target: {target.name}")
    output(f"  Normalized name: {target.normalized_name}")
    output(f"  Target UUID: {target.id}")
    output("  Sources:")
    for entry in selected:
        recipe_count = entry.get("recipe_count")
        count_text = f"{recipe_count} recipe(s)" if isinstance(recipe_count, int) else "?"
        output(f"    - {entry.get('name')} ({entry.get('id')}) — {count_text}")
    if collision is not None:
        output(
            f"  WARNING: {collision.get('name')} already uses the target UUID; "
            "its references will also merge into the target."
        )
    output(f"  Affected: {row_count} ingredient row(s) in {file_count} recipe file(s)")

    if not ask_yes_no("Proceed with the merge?", default=False, input_fn=input_fn, output=output):
        output("Merge cancelled. No files were changed.")
        return MergeStats()

    stats = merge_ingredient_identities(recipe_files, selected_ids, target)
    if stats.recipe_files_updated == 0:
        output("No recipe files needed changes.")
    else:
        output(
            f"\nMerged {len(selected)} ingredient identities into "
            f'"{target.name}" ({target.id}): updated {stats.recipe_files_updated} recipe files '
            f"and {stats.ingredient_rows_updated} ingredient rows."
        )
    return stats


def main() -> None:
    try:
        stats = run_merge()
    except KeyboardInterrupt:
        print("\nMerge interrupted. No further files were changed.")
        return

    print(f"updated {stats.recipe_files_updated} recipe files and {stats.ingredient_rows_updated} ingredient rows")
    print("Run python3 py-scripts/generate_catalog.py to rebuild the ingredient index and catalog metadata.")


if __name__ == "__main__":
    main()