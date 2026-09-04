#!/usr/bin/env python3

"""Interactively review recipe ingredients and merge duplicate identities."""

from __future__ import annotations

import json
import subprocess

try:
    import readline
except ImportError:  # pragma: no cover - readline is platform-dependent
    readline = None
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import importlib.util
import sys


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

CHECKPOINT_FILE_NAME = ".recipe-editor-state.json"

JQ_REFERENCES_FILTER = """
{
  recipe_id: .id,
  recipe_name: .name,
  ingredients: [
    .ingredients[]
    | select(.ingredient_id == $ingredient_id)
    | {
        position,
        ingredient_id,
        name,
        normalized_name,
        quantity,
        unit,
        preparation
      }
  ]
}
| select(.ingredients | length > 0)
"""

Input = Callable[[str], str]
Output = Callable[[str], None]
ACCEPTED_NAME_CHANGES: set[str] = set()


@dataclass
class EditStats:
    recipe_files_updated: int = 0
    ingredient_rows_updated: int = 0


@dataclass
class ReviewSnapshot:
    index: int
    ingredient_id: str
    recipe_contents: dict[Path, str]
    accepted_names: set[str]
    stats: EditStats


class PreviousIngredientRequested(Exception):
    pass


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


def checkpoint_path() -> Path:
    return REPO_ROOT / CHECKPOINT_FILE_NAME


def load_review_checkpoint() -> set[str]:
    path = checkpoint_path()
    if not path.exists():
        return set()

    payload = load_json(path)
    checked_ingredient_ids = payload.get("checked_ingredient_ids")
    accepted_names = payload.get("accepted_names", [])
    if not isinstance(checked_ingredient_ids, list) or not all(isinstance(item, str) for item in checked_ingredient_ids):
        raise SystemExit(f"invalid ingredient review checkpoint: {path}")
    if not isinstance(accepted_names, list) or not all(isinstance(item, str) for item in accepted_names):
        raise SystemExit(f"invalid ingredient review checkpoint: {path}")
    ACCEPTED_NAME_CHANGES.update(accepted_names)
    return set(checked_ingredient_ids)


def save_review_checkpoint(checked_ingredient_ids: set[str]) -> None:
    path = checkpoint_path()
    temporary_path = path.with_name(f"{path.name}.tmp")
    dump_json(
        temporary_path,
        {
            "schema_version": 1,
            "checked_ingredient_ids": sorted(checked_ingredient_ids),
            "accepted_names": sorted(ACCEPTED_NAME_CHANGES, key=str.casefold),
        },
    )
    temporary_path.replace(path)


def clear_review_checkpoint() -> None:
    checkpoint_path().unlink(missing_ok=True)


def list_recipe_files() -> list[Path]:
    if not RECIPES_DIR.exists():
        return []
    return sorted(RECIPES_DIR.glob("*.json"))


def build_recipe_references(recipe_files: list[Path]) -> dict[str, list[Path]]:
    """Map an ingredient id to the recipe files that currently contain it."""
    references: dict[str, list[Path]] = defaultdict(list)
    for recipe_path in recipe_files:
        recipe = load_json(recipe_path)
        for ingredient in recipe.get("ingredients", []):
            ingredient_id = ingredient.get("ingredient_id")
            if isinstance(ingredient_id, str):
                references[ingredient_id].append(recipe_path)
    return references


def display_ingredient_references(
    ingredient_id: str,
    recipe_paths: list[Path],
    *,
    output: Output = print,
) -> None:
    """Use jq to render the ingredient rows being reviewed."""
    if not recipe_paths:
        output("No recipe ingredients currently reference this id.")
        return

    command = [
        "jq",
        "--arg",
        "ingredient_id",
        ingredient_id,
        JQ_REFERENCES_FILTER,
        *(str(path) for path in recipe_paths),
    ]
    try:
        result = subprocess.run(command, capture_output=True, text=True, check=False)
    except FileNotFoundError as error:
        raise SystemExit("jq is required for ingredient review but was not found on PATH") from error

    if result.returncode:
        message = result.stderr.strip() or "unknown jq error"
        raise SystemExit(f"jq could not display ingredient references: {message}")
    if result.stdout.strip():
        output(result.stdout.rstrip())
    else:
        output("No recipe ingredients currently reference this id.")


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


def ask_review_confirmation(
    prompt: str,
    *,
    input_fn: Input = input,
    output: Output = print,
) -> bool:
    while True:
        answer = input_fn(f"{prompt} [Y/n/r]: ").strip().lower()
        if not answer or answer in {"y", "yes"}:
            return True
        if answer in {"n", "no"}:
            return False
        if answer == "r":
            raise PreviousIngredientRequested
        output("Please answer y, n, or r to return to the previous ingredient.")


def prompt_value(
    field_name: str,
    current_value: str | None,
    *,
    input_fn: Input = input,
) -> str | None:
    """Keep blank answers; use '-' to explicitly clear nullable fields."""
    current = current_value if current_value is not None else "(none)"
    answer = input_fn(f"{field_name} [{current}] (Enter to keep, - to clear): ").strip()
    if not answer:
        return current_value
    if answer == "-":
        return None
    return answer


def name_completion_matches(prefix: str) -> list[str]:
    normalized_prefix = prefix.casefold()
    return sorted(
        (name for name in ACCEPTED_NAME_CHANGES if name.casefold().startswith(normalized_prefix)),
        key=str.casefold,
    )


def configure_name_completion() -> None:
    """Enable Tab completion for names accepted during this editor session."""
    if readline is None:
        return

    def complete(prefix: str, state: int) -> str | None:
        matches = name_completion_matches(prefix)
        return matches[state] if state < len(matches) else None

    readline.set_completer(complete)
    readline.set_completer_delims("")
    backend = getattr(readline, "backend", "")
    uses_libedit = backend == "editline" or "libedit" in (readline.__doc__ or "").lower()
    readline.parse_and_bind("bind ^I rl_complete" if uses_libedit else "tab: complete")


def prompt_name_or_other_fields(*, input_fn: Input = input, output: Output = print) -> str | None:
    """Return a corrected name, or None when detailed per-row editing is requested."""
    while True:
        answer = input_fn("Enter corrected name, or [o]ther fields: ").strip()
        if answer.lower() == "o":
            return None
        if answer:
            return answer
        output("Enter a corrected name or o for other fields.")


def update_ingredient_name(ingredient: dict, name: str) -> bool:
    normalized_name = normalize_name(name)
    if not normalized_name:
        raise SystemExit("ingredient name must contain letters or numbers")

    ingredient_id = stable_uuid(INGREDIENT_NAMESPACE, normalized_name)
    if (
        ingredient.get("name") == name
        and ingredient.get("normalized_name") == normalized_name
        and ingredient.get("ingredient_id") == ingredient_id
    ):
        return False
    ingredient["name"] = name
    ingredient["normalized_name"] = normalized_name
    ingredient["ingredient_id"] = ingredient_id
    ACCEPTED_NAME_CHANGES.add(name)
    return True


def apply_row_correction(
    ingredient: dict,
    *,
    copy_name_from: str | None = None,
    input_fn: Input = input,
) -> bool:
    """Prompt for a single row and return whether it changed."""
    if copy_name_from:
        answer = input_fn(f'Use the first ingredient name "{copy_name_from}"? [Y/name]: ').strip()
        name = copy_name_from if not answer or answer.lower() in {"y", "yes"} else answer
    else:
        name = prompt_value("Name", ingredient.get("name"), input_fn=input_fn)
    if not name:
        raise SystemExit("ingredient name cannot be empty")
    quantity = prompt_value("Quantity", ingredient.get("quantity"), input_fn=input_fn)
    unit = prompt_value("Unit", ingredient.get("unit"), input_fn=input_fn)
    preparation = prompt_value("Preparation", ingredient.get("preparation"), input_fn=input_fn)

    changed = False
    if name != ingredient.get("name") and update_ingredient_name(ingredient, name):
        changed = True
    if quantity != ingredient.get("quantity"):
        ingredient["quantity"] = quantity
        changed = True
    if unit != ingredient.get("unit"):
        ingredient["unit"] = unit
        changed = True
    if preparation != ingredient.get("preparation"):
        ingredient["preparation"] = preparation
        changed = True
    ACCEPTED_NAME_CHANGES.add(name)
    return changed


def rename_recipe_ingredients(recipe_paths: list[Path], ingredient_id: str, name: str) -> EditStats:
    """Apply one corrected ingredient name to every row with the indexed id."""
    stats = EditStats()
    for recipe_path in recipe_paths:
        recipe = load_json(recipe_path)
        recipe_changed = False
        for ingredient in recipe.get("ingredients", []):
            if ingredient.get("ingredient_id") == ingredient_id and update_ingredient_name(ingredient, name):
                recipe_changed = True
                stats.ingredient_rows_updated += 1
        if recipe_changed:
            dump_json(recipe_path, recipe)
            stats.recipe_files_updated += 1
    return stats


def review_ingredient(
    index_entry: dict,
    recipe_paths: list[Path],
    *,
    next_names: list[str] | None = None,
    input_fn: Input = input,
    output: Output = print,
) -> EditStats:
    ingredient_id = index_entry.get("id")
    name = index_entry.get("name")
    if not isinstance(ingredient_id, str) or not isinstance(name, str):
        raise SystemExit("invalid ingredient entry in ingredients.index.json")

    display_ingredient_references(ingredient_id, recipe_paths, output=output)
    queue_text = f"Queue (next items: {', '.join(f'\"{next_name}\"' for next_name in next_names)}). " if next_names else ""
    if not recipe_paths:
        return EditStats()
    if ask_review_confirmation(
        f'{queue_text}Are all rows for "{name}" correct?',
        input_fn=input_fn,
        output=output,
    ):
        ACCEPTED_NAME_CHANGES.add(name)
        return EditStats()

    corrected_name = prompt_name_or_other_fields(input_fn=input_fn, output=output)
    if corrected_name is not None:
        ACCEPTED_NAME_CHANGES.add(corrected_name)
        return rename_recipe_ingredients(recipe_paths, ingredient_id, corrected_name)

    stats = EditStats()
    first_ingredient_name: str | None = None
    for recipe_path in recipe_paths:
        recipe = load_json(recipe_path)
        recipe_changed = False
        for ingredient in recipe.get("ingredients", []):
            if ingredient.get("ingredient_id") != ingredient_id:
                continue
            output(f"\n{recipe_path.name}, ingredient position {ingredient.get('position', '?')}")
            output(json.dumps(ingredient, indent=2, ensure_ascii=True))
            if apply_row_correction(
                ingredient,
                copy_name_from=first_ingredient_name,
                input_fn=input_fn,
            ):
                recipe_changed = True
                stats.ingredient_rows_updated += 1
            if first_ingredient_name is None and isinstance(ingredient.get("name"), str):
                first_ingredient_name = ingredient["name"]
        if recipe_changed:
            dump_json(recipe_path, recipe)
            stats.recipe_files_updated += 1
    return stats


def merge_same_name_ingredients(recipe_files: list[Path]) -> EditStats:
    """Give all ingredients with the same normalized name the same stable UUID."""
    stats = EditStats()
    for recipe_path in recipe_files:
        recipe = load_json(recipe_path)
        recipe_changed = False
        for ingredient in recipe.get("ingredients", []):
            name = ingredient.get("name")
            if not isinstance(name, str):
                continue
            normalized_name = normalize_name(name)
            if not normalized_name:
                continue
            canonical_id = stable_uuid(INGREDIENT_NAMESPACE, normalized_name)
            if (
                ingredient.get("ingredient_id") == canonical_id
                and ingredient.get("normalized_name") == normalized_name
            ):
                continue
            ingredient["ingredient_id"] = canonical_id
            ingredient["normalized_name"] = normalized_name
            recipe_changed = True
            stats.ingredient_rows_updated += 1
        if recipe_changed:
            dump_json(recipe_path, recipe)
            stats.recipe_files_updated += 1
    return stats


def next_unchecked_ingredient_names(
    ingredients: list[dict],
    start_index: int,
    current_ingredient_id: object,
    checked_ingredient_ids: set[str],
) -> list[str]:
    next_names: list[str] = []
    for candidate in ingredients[start_index:]:
        candidate_id = candidate.get("id")
        candidate_name = candidate.get("name")
        if (
            not isinstance(candidate_id, str)
            or candidate_id == current_ingredient_id
            or candidate_id in checked_ingredient_ids
            or not isinstance(candidate_name, str)
        ):
            continue
        next_names.append(candidate_name)
        if len(next_names) == 3:
            break
    return next_names


def run_interactive_review(*, input_fn: Input = input, output: Output = print) -> EditStats:
    ACCEPTED_NAME_CHANGES.clear()
    configure_name_completion()
    ingredients = load_ingredient_index()
    recipe_files = list_recipe_files()
    references = build_recipe_references(recipe_files)
    checked_ingredient_ids = load_review_checkpoint()
    ACCEPTED_NAME_CHANGES.update(
        entry["name"]
        for entry in ingredients
        if isinstance(entry.get("id"), str)
        and entry.get("id") in checked_ingredient_ids
        and isinstance(entry.get("name"), str)
    )
    total = EditStats()

    output(f"Reviewing {len(ingredients)} ingredient index entries.")
    if checked_ingredient_ids:
        output(f"Resuming review: {len(checked_ingredient_ids)} ingredient UUIDs already checked.")

    index = 0
    previous_review: ReviewSnapshot | None = None
    while index < len(ingredients):
        index_entry = ingredients[index]
        number = index + 1
        ingredient_id = index_entry.get("id")
        name = index_entry.get("name", "<unnamed>")
        if isinstance(ingredient_id, str) and ingredient_id in checked_ingredient_ids:
            output(f"\n[{number}/{len(ingredients)}] {name} (already checked; skipping)")
            index += 1
            continue

        next_names = next_unchecked_ingredient_names(
            ingredients,
            number,
            ingredient_id,
            checked_ingredient_ids,
        )
        recipe_paths = references.get(ingredient_id, [])
        recipe_contents = {path: path.read_text(encoding="utf-8") for path in recipe_paths}
        accepted_names = ACCEPTED_NAME_CHANGES.copy()
        output(f"\n[{number}/{len(ingredients)}] {name}")
        try:
            stats = review_ingredient(
                index_entry,
                recipe_paths,
                next_names=next_names,
                input_fn=input_fn,
                output=output,
            )
        except PreviousIngredientRequested:
            if previous_review is None:
                output("No previous ingredient is available in this session.")
                continue

            for path, contents in previous_review.recipe_contents.items():
                path.write_text(contents, encoding="utf-8")
            checked_ingredient_ids.discard(previous_review.ingredient_id)
            ACCEPTED_NAME_CHANGES.clear()
            ACCEPTED_NAME_CHANGES.update(previous_review.accepted_names)
            total.recipe_files_updated -= previous_review.stats.recipe_files_updated
            total.ingredient_rows_updated -= previous_review.stats.ingredient_rows_updated
            save_review_checkpoint(checked_ingredient_ids)
            index = previous_review.index
            output("Returning to the previous ingredient.")
            previous_review = None
            continue

        if isinstance(ingredient_id, str):
            checked_ingredient_ids.add(ingredient_id)
            save_review_checkpoint(checked_ingredient_ids)
            previous_review = ReviewSnapshot(
                index=index,
                ingredient_id=ingredient_id,
                recipe_contents=recipe_contents,
                accepted_names=accepted_names,
                stats=stats,
            )
        total.recipe_files_updated += stats.recipe_files_updated
        total.ingredient_rows_updated += stats.ingredient_rows_updated
        index += 1

    if ask_yes_no(
        "Merge ingredients with the same normalized name into one UUID?",
        default=False,
        input_fn=input_fn,
        output=output,
    ):
        stats = merge_same_name_ingredients(recipe_files)
        total.recipe_files_updated += stats.recipe_files_updated
        total.ingredient_rows_updated += stats.ingredient_rows_updated

    clear_review_checkpoint()
    return total


def main() -> None:
    try:
        stats = run_interactive_review()
    except KeyboardInterrupt:
        print(f"\nReview interrupted. Resume later; progress is saved in {checkpoint_path()}.")
        return

    print(
        f"updated {stats.recipe_files_updated} recipe files and {stats.ingredient_rows_updated} ingredient rows"
    )
    print("Run python3 py-scripts/generate_catalog.py to rebuild the ingredient index and catalog metadata.")


if __name__ == "__main__":
    main()
