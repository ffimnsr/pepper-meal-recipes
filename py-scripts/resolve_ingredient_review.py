#!/usr/bin/env python3

"""Interactively resolve ingredients.review.json entries by editing the underlying recipe rows."""

from __future__ import annotations

import importlib.util
import json
import re
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

try:
    import readline
except ImportError:  # pragma: no cover - readline is platform-dependent
    readline = None

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
AMBIGUOUS_CONNECTOR_RE = generate_catalog.AMBIGUOUS_CONNECTOR_RE
EXCLUDED_INGREDIENT_PATTERNS = generate_catalog.EXCLUDED_INGREDIENT_PATTERNS
SAFE_COMPOUND_SPLITS = generate_catalog.SAFE_COMPOUND_SPLITS
dump_json = generate_catalog.dump_json
clean_text = generate_catalog.clean_text
normalize_name = generate_catalog.normalize_name
stable_uuid = generate_catalog.stable_uuid

CHECKPOINT_FILE_NAME = ".ingredient-review-state.json"

Input = Callable[[str], str]
Output = Callable[[str], None]


@dataclass
class ResolveStats:
    recipe_files_updated: int = 0
    ingredient_rows_updated: int = 0
    entries_resolved: int = 0


@dataclass
class ReviewSnapshot:
    index: int
    entry_key: tuple[str, int | None, str]
    recipe_contents: dict[Path, str]
    stats: ResolveStats


class PreviousEntryRequested(Exception):
    pass


def load_json(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def load_ingredient_review() -> list[dict]:
    review_path = INDEXES_DIR / "ingredients.review.json"
    if not review_path.exists():
        raise SystemExit(f"missing ingredient review index: {review_path}")

    payload = load_json(review_path)
    entries = payload.get("entries", [])
    if not isinstance(entries, list):
        raise SystemExit(f"invalid ingredient review index: {review_path}")
    return entries


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


def load_review_checkpoint() -> set[tuple[str, int | None, str]]:
    path = checkpoint_path()
    if not path.exists():
        return set()

    payload = load_json(path)
    resolved = payload.get("resolved", [])
    if not isinstance(resolved, list):
        raise SystemExit(f"invalid ingredient review checkpoint: {path}")
    keys: set[tuple[str, int | None, str]] = set()
    for item in resolved:
        if not isinstance(item, dict):
            raise SystemExit(f"invalid ingredient review checkpoint: {path}")
        recipe_id = item.get("recipe_id")
        position = item.get("position")
        original_text = item.get("original_text")
        if (
            not isinstance(recipe_id, str)
            or not (isinstance(position, int) or position is None)
            or not isinstance(original_text, str)
        ):
            raise SystemExit(f"invalid ingredient review checkpoint: {path}")
        keys.add((recipe_id, position, original_text))
    return keys


def save_review_checkpoint(keys: set[tuple[str, int | None, str]]) -> None:
    path = checkpoint_path()
    temporary_path = path.with_name(f"{path.name}.tmp")
    dump_json(
        temporary_path,
        {
            "schema_version": 1,
            "resolved": sorted(
                ({"recipe_id": key[0], "position": key[1], "original_text": key[2]} for key in keys),
                key=lambda item: (item["recipe_id"], item["position"] or 0, item["original_text"]),
            ),
        },
    )
    temporary_path.replace(path)


def clear_review_checkpoint() -> None:
    checkpoint_path().unlink(missing_ok=True)


def review_entry_key(entry: dict) -> tuple[str, int | None, str] | None:
    recipe_id = entry.get("recipe_id")
    position = entry.get("position")
    original_text = entry.get("original_text")
    if not isinstance(recipe_id, str) or not isinstance(original_text, str):
        return None
    if not (isinstance(position, int) or position is None):
        return None
    return recipe_id, position, original_text


def recipe_path_for(recipe_id: str) -> Path | None:
    path = RECIPES_DIR / f"{recipe_id}.json"
    return path if path.exists() else None


def rendered_ingredient_text(ingredient: dict) -> str:
    return clean_text(
        " ".join(filter(None, [ingredient.get("quantity"), ingredient.get("unit"), ingredient.get("name")]))
    )


def find_ingredient_row(recipe: dict, entry: dict) -> tuple[int | None, dict | None]:
    """Locate the ingredient row referenced by a review entry, tolerating shifted positions."""
    rows = recipe.get("ingredients", [])
    if not isinstance(rows, list):
        return None, None
    position = entry.get("position")
    original_text = entry.get("original_text")
    cleaned_name = entry.get("cleaned_name")

    def rendered_match(row: dict) -> bool:
        if isinstance(original_text, str) and rendered_ingredient_text(row).casefold() == original_text.casefold():
            return True
        if isinstance(cleaned_name, str):
            row_name = row.get("name")
            if isinstance(row_name, str) and row_name.casefold() == cleaned_name.casefold():
                return True
        return False

    if isinstance(position, int):
        for index, row in enumerate(rows):
            if row.get("position") == position and rendered_match(row):
                return index, row
        for index, row in enumerate(rows):
            if rendered_match(row):
                return index, row
        for index, row in enumerate(rows):
            if row.get("position") == position:
                return index, row
    else:
        for index, row in enumerate(rows):
            if rendered_match(row):
                return index, row
    return None, None


def renumber_ingredient_positions(recipe: dict) -> None:
    for index, ingredient in enumerate(recipe.get("ingredients", []), start=1):
        ingredient["position"] = index


def update_ingredient_name(ingredient: dict, name: str) -> bool:
    normalized_name = normalize_name(name)
    if not normalized_name:
        return False
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
    return True


def split_name_suggestion(entry: dict, row: dict | None = None) -> list[str]:
    replacements = entry.get("replacements")
    if isinstance(replacements, list) and replacements:
        return [str(item) for item in replacements if str(item).strip()]

    cleaned_name = entry.get("cleaned_name")
    if isinstance(cleaned_name, str) and cleaned_name:
        parts = [
            part.strip()
            for part in re.split(r"\s+(?:or|and/or)\s+", cleaned_name, flags=re.IGNORECASE)
            if part.strip()
        ]
        if len(parts) > 1:
            return parts

    row_name = row.get("name") if isinstance(row, dict) else None
    if isinstance(row_name, str):
        parts = [
            part.strip()
            for part in re.split(r"\s+(?:or|and/or)\s+", row_name, flags=re.IGNORECASE)
            if part.strip()
        ]
        if len(parts) > 1 and all(not part.startswith("(") and not part.endswith(")") for part in parts):
            return parts
    return []


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


COMPLETION_NAMES: list[str] = []


def configure_name_completion() -> None:
    """Enable Tab completion of indexed ingredient names for rename prompts."""
    if readline is None:
        return

    def complete(prefix: str, state: int) -> str | None:
        matches = [
            name
            for name in COMPLETION_NAMES
            if name.casefold().startswith(prefix.casefold())
        ]
        return matches[state] if state < len(matches) else None

    readline.set_completer(complete)
    readline.set_completer_delims("")
    backend = getattr(readline, "backend", "")
    uses_libedit = backend == "editline" or "libedit" in (readline.__doc__ or "").lower()
    readline.parse_and_bind("bind ^I rl_complete" if uses_libedit else "tab: complete")


def describe_index_status(entry: dict) -> str:
    resolution = entry.get("resolution")
    if resolution == "review":
        return "not indexed (flagged for review)"
    if resolution == "excluded":
        return "not indexed (excluded as non-ingredient)"
    if resolution == "split":
        replacements = entry.get("replacements") or []
        return f"indexed via generator auto-split into {', '.join(str(item) for item in replacements)}"
    cleaned_name = entry.get("cleaned_name")
    if isinstance(cleaned_name, str) and cleaned_name:
        return f"indexed as cleaned name {cleaned_name!r}"
    return "indexed"


def display_entry(
    entry: dict,
    row: dict | None,
    number: int,
    total: int,
    *,
    output: Output = print,
) -> None:
    recipe_name = entry.get("recipe_name") or entry.get("recipe_slug") or entry.get("recipe_id")
    output(f"\n[{number}/{total}] {recipe_name}")
    output(f"  Position {entry.get('position')} — {entry.get('original_text')}")
    if row is None:
        output("  Row: not found in the recipe file.")
    else:
        row_text = ", ".join(
            f"{key}={value!r}"
            for key, value in (
                ("name", row.get("name")),
                ("quantity", row.get("quantity")),
                ("unit", row.get("unit")),
                ("preparation", row.get("preparation")),
            )
        )
        output(f"  Row: {row_text}")
    output(f"  Issues: {', '.join(str(item) for item in entry.get('issue_types', []))} (resolution: {entry.get('resolution')})")
    output(f"  Index: {describe_index_status(entry)}")


def render_rename_hints(
    name: str,
    index_by_id: dict,
    *,
    output: Output = print,
) -> list[str]:
    """Print what the name would become; return warnings that require confirming."""
    normalized_name = normalize_name(name)
    if not normalized_name:
        return []
    canonical_id = stable_uuid(INGREDIENT_NAMESPACE, normalized_name)
    existing = index_by_id.get(canonical_id)
    if existing is not None:
        recipe_count = existing.get("recipe_count")
        count_text = f"{recipe_count} recipe(s)" if isinstance(recipe_count, int) else "recipes"
        output(f"  -> merges into existing ingredient {existing.get('name')!r} ({count_text}).")
    else:
        output(f"  -> will appear in the index as a new ingredient ({name!r} -> {normalized_name!r}).")

    warnings: list[str] = []
    if AMBIGUOUS_CONNECTOR_RE.search(normalized_name):
        warnings.append("the generator still flags 'or'/'and/or' names as ambiguous")
    if any(pattern.search(normalized_name) for pattern in EXCLUDED_INGREDIENT_PATTERNS):
        warnings.append("the generator excludes this name as a non-ingredient")
    if normalized_name in SAFE_COMPOUND_SPLITS:
        output(
            f"  Note: the generator auto-splits this name into {', '.join(SAFE_COMPOUND_SPLITS[normalized_name])}."
        )
    if warnings:
        output(f"  WARNING: {'; '.join(warnings)}.")
    return warnings


def split_ingredient_row(
    recipe: dict,
    row_index: int,
    entry: dict,
    *,
    input_fn: Input = input,
    output: Output = print,
) -> bool:
    """Replace one ingredient row with multiple rows; return whether the recipe changed."""
    rows = recipe.get("ingredients", [])
    row = rows[row_index]
    suggestion = split_name_suggestion(entry, row)

    while True:
        default_text = "|".join(suggestion) if suggestion else ""
        answer = input_fn(f"Split into names separated by | [{'|'.join(suggestion)}]: ").strip()
        if not answer and not default_text:
            output("Enter at least one split name.")
            continue
        answer = answer or default_text
        parts = [part.strip() for part in re.split(r"\s*\|\s*", answer) if part.strip()]
        if not parts:
            output("Enter at least one split name.")
            continue
        invalid = [part for part in parts if not normalize_name(part)]
        if invalid:
            output(f"Names contain no letters or numbers: {', '.join(invalid)}.")
            continue
        break

    if len(parts) == 1:
        return rename_ingredient_row(recipe, row_index, parts[0], input_fn=input_fn, output=output) == "renamed"

    quantity = row.get("quantity")
    unit = row.get("unit")
    preparation = row.get("preparation")
    new_rows: list[dict] = []
    for index, name in enumerate(parts, start=1):
        output(f"\nSplit part {index}/{len(parts)}: {name}")
        part_quantity = prompt_value("Quantity", quantity, input_fn=input_fn)
        part_unit = prompt_value("Unit", unit, input_fn=input_fn)
        part_preparation = prompt_value("Preparation", preparation, input_fn=input_fn)
        new_rows.append(
            {
                "ingredient_id": stable_uuid(INGREDIENT_NAMESPACE, normalize_name(name)),
                "name": name,
                "normalized_name": normalize_name(name),
                "quantity": part_quantity,
                "unit": part_unit,
                "preparation": part_preparation,
            }
        )
    rows[row_index : row_index + 1] = new_rows
    renumber_ingredient_positions(recipe)
    return True


def rename_ingredient_row(
    recipe: dict,
    row_index: int,
    name: str,
    *,
    index_by_id: dict | None = None,
    input_fn: Input = input,
    output: Output = print,
) -> str:
    """Rename a row into a canonical identity; return renamed, unchanged, invalid, or aborted."""
    if not normalize_name(name):
        output("Name must contain letters or numbers.")
        return "invalid"
    row = recipe.get("ingredients", [])[row_index]
    if row.get("name") == name and normalize_name(name) == row.get("normalized_name"):
        output(f"Row already uses the name {name!r}.")
        return "unchanged"

    if index_by_id is not None:
        warnings = render_rename_hints(name, index_by_id, output=output)
        if warnings and not ask_yes_no("Continue with this name?", default=False, input_fn=input_fn, output=output):
            return "aborted"

    if update_ingredient_name(row, name):
        COMPLETION_NAMES.append(name)
        return "renamed"
    return "unchanged"


def edit_other_fields(
    row: dict,
    *,
    input_fn: Input = input,
) -> bool:
    quantity = prompt_value("Quantity", row.get("quantity"), input_fn=input_fn)
    unit = prompt_value("Unit", row.get("unit"), input_fn=input_fn)
    preparation = prompt_value("Preparation", row.get("preparation"), input_fn=input_fn)
    changed = False
    if quantity != row.get("quantity"):
        row["quantity"] = quantity
        changed = True
    if unit != row.get("unit"):
        row["unit"] = unit
        changed = True
    if preparation != row.get("preparation"):
        row["preparation"] = preparation
        changed = True
    return changed


def resolve_entry(
    entry: dict,
    recipe_path: Path,
    recipe: dict,
    row_index: int | None,
    row: dict | None,
    *,
    index_by_id: dict,
    number: int,
    total: int,
    input_fn: Input = input,
    output: Output = print,
) -> tuple[bool, ResolveStats]:
    """Resolve one review entry; return (changed, stats)."""
    stats = ResolveStats()
    while True:
        display_entry(entry, row, number, total, output=output)
        answer = input_fn(
            "Enter a new name to rename/merge, [s]plit, [e]xclude, [o]ther fields, [k]eep, [p]revious, [q]uit: "
        ).strip()
        command = answer.lower()
        if not answer or command in {"k", "keep"}:
            return False, stats
        if command in {"q", "quit"}:
            output("\nQuit. Resume later with the same command.")
            raise SystemExit(0)
        if command in {"p", "previous"}:
            raise PreviousEntryRequested
        if row is None or row_index is None:
            output("No matching row is available for this entry; choose [k]eep or [q]uit.")
            continue

        if command == "s":
            if split_ingredient_row(recipe, row_index, entry, input_fn=input_fn, output=output):
                dump_json(recipe_path, recipe)
                stats.recipe_files_updated += 1
                stats.ingredient_rows_updated += 1
                stats.entries_resolved += 1
                return True, stats
            continue
        if command == "e":
            if ask_yes_no(
                "Remove this ingredient row from the recipe?",
                default=False,
                input_fn=input_fn,
                output=output,
            ):
                recipe.get("ingredients", []).pop(row_index)
                renumber_ingredient_positions(recipe)
                dump_json(recipe_path, recipe)
                stats.recipe_files_updated += 1
                stats.ingredient_rows_updated += 1
                stats.entries_resolved += 1
                return True, stats
            continue
        if command == "o":
            if edit_other_fields(row, input_fn=input_fn):
                dump_json(recipe_path, recipe)
                stats.recipe_files_updated += 1
                stats.ingredient_rows_updated += 1
                stats.entries_resolved += 1
            return True, stats

        outcome = rename_ingredient_row(
            recipe,
            row_index,
            answer,
            index_by_id=index_by_id,
            input_fn=input_fn,
            output=output,
        )
        if outcome == "aborted":
            continue
        if outcome == "renamed":
            dump_json(recipe_path, recipe)
            stats.recipe_files_updated += 1
            stats.ingredient_rows_updated += 1
            stats.entries_resolved += 1
            return True, stats
        if outcome == "unchanged":
            return False, stats
        # invalid name: stay in the menu


def order_review_entries(entries: list[dict]) -> list[dict]:
    """Walk each recipe's entries in descending position so earlier edits do not shift later rows."""
    grouped: dict[str, list[dict]] = {}
    for entry in entries:
        recipe_id = entry.get("recipe_id")
        if not isinstance(recipe_id, str):
            continue
        grouped.setdefault(recipe_id, []).append(entry)
    ordered: list[dict] = []
    for recipe_id in grouped:
        ordered.extend(
            sorted(
                grouped[recipe_id],
                key=lambda item: -(int(item.get("position") or 0)),
            )
        )
    return ordered


def run_resolve(*, input_fn: Input = input, output: Output = print) -> ResolveStats:
    entries = load_ingredient_review()
    index_entries = load_ingredient_index()
    index_by_id = {}
    for index_entry in index_entries:
        ingredient_id = index_entry.get("id")
        if isinstance(ingredient_id, str):
            index_by_id[ingredient_id] = index_entry
    global COMPLETION_NAMES
    COMPLETION_NAMES = sorted(
        (str(entry.get("name")) for entry in index_entries if isinstance(entry.get("name"), str)),
        key=str.casefold,
    )
    configure_name_completion()

    resolved_keys = load_review_checkpoint()
    ordered = order_review_entries(entries)
    total = ResolveStats()
    output(f"Resolving {len(ordered)} ingredient review entries.")
    if resolved_keys:
        output(f"Resuming: {len(resolved_keys)} entries already resolved.")

    index = 0
    previous_review: ReviewSnapshot | None = None
    while index < len(ordered):
        entry = ordered[index]
        number = index + 1
        key = review_entry_key(entry)
        recipe_name = entry.get("recipe_name") or entry.get("recipe_slug") or entry.get("recipe_id")
        if key is not None and key in resolved_keys:
            output(f"\n[{number}/{len(ordered)}] {recipe_name} (already resolved; skipping)")
            index += 1
            continue

        recipe_path = recipe_path_for(entry.get("recipe_id")) if isinstance(entry.get("recipe_id"), str) else None
        if recipe_path is None:
            output(f"\n[{number}/{len(ordered)}] {recipe_name}: recipe file not found; skipping.")
            index += 1
            continue

        recipe = load_json(recipe_path)
        row_index, row = find_ingredient_row(recipe, entry)
        recipe_contents = {recipe_path: recipe_path.read_text(encoding="utf-8")}
        try:
            changed, stats = resolve_entry(
                entry,
                recipe_path,
                recipe,
                row_index,
                row,
                index_by_id=index_by_id,
                number=number,
                total=len(ordered),
                input_fn=input_fn,
                output=output,
            )
        except PreviousEntryRequested:
            if previous_review is None:
                output("No previous entry is available in this session.")
                continue
            for path, contents in previous_review.recipe_contents.items():
                path.write_text(contents, encoding="utf-8")
            if previous_review.entry_key in resolved_keys:
                resolved_keys.discard(previous_review.entry_key)
                save_review_checkpoint(resolved_keys)
            total.recipe_files_updated -= previous_review.stats.recipe_files_updated
            total.ingredient_rows_updated -= previous_review.stats.ingredient_rows_updated
            total.entries_resolved -= previous_review.stats.entries_resolved
            index = previous_review.index
            output("Returning to the previous entry.")
            previous_review = None
            continue

        total.recipe_files_updated += stats.recipe_files_updated
        total.ingredient_rows_updated += stats.ingredient_rows_updated
        total.entries_resolved += stats.entries_resolved
        if key is not None:
            if changed and key not in resolved_keys:
                resolved_keys.add(key)
                save_review_checkpoint(resolved_keys)
            previous_review = ReviewSnapshot(
                index=index,
                entry_key=key,
                recipe_contents=recipe_contents,
                stats=stats,
            )
        index += 1

    clear_review_checkpoint()
    return total


def main() -> None:
    try:
        stats = run_resolve()
    except KeyboardInterrupt:
        print(f"\nReview interrupted. Resume later; progress is saved in {checkpoint_path()}.")
        return

    print(
        f"updated {stats.recipe_files_updated} recipe files, {stats.ingredient_rows_updated} ingredient rows, "
        f"and resolved {stats.entries_resolved} review entries"
    )
    print("Run python3 py-scripts/generate_catalog.py to rebuild the ingredient index and review queue.")


if __name__ == "__main__":
    main()