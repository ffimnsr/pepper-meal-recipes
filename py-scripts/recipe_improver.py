#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import tempfile
import time
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import requests

try:
    from jsonschema import Draft202012Validator
    from jsonschema import ValidationError
except ImportError:  # pragma: no cover - dependency is expected in dev setup
    Draft202012Validator = None
    ValidationError = Exception


REPO_ROOT = Path(__file__).resolve().parents[1]
RECIPE_DIR = REPO_ROOT / "recipes" / "v1" / "recipes" / "by-id"
SCHEMA_PATH = REPO_ROOT / "recipes" / "v1" / "schemas" / "recipe.schema.json"
DEFAULT_STATE_FILE = REPO_ROOT / ".recipe-improver-state.json"
OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
DEFAULT_MODEL = "moonshotai/kimi-k2.5"
PREPARATION_HINTS = {
    "beaten",
    "boiled",
    "butterflied",
    "butterfly",
    "chopped",
    "cleaned",
    "cracked",
    "crushed",
    "cubed",
    "cut",
    "deboned",
    "deveined",
    "diced",
    "drained",
    "filleted",
    "grated",
    "ground",
    "gutted",
    "halved",
    "knotted",
    "mashed",
    "minced",
    "peeled",
    "pitted",
    "quartered",
    "removed",
    "scaled",
    "scales",
    "seeded",
    "shelled",
    "shredded",
    "sliced",
    "soaked",
    "softened",
    "trimmed",
    "wedged",
}
TRAILING_PREPARATION_RE = re.compile(
    r"\b(cut into|cut in|cut to|chopped|cleaned|cubed|diced|gutted|julienned|minced|peeled|pitted|quartered|scaled|seeded|shelled|shredded|sliced|trimmed|wedged)\b.*$",
    re.IGNORECASE,
)
SINGULAR_UNIT_HINTS = (
    ("garlic", "clove"),
    ("fennel bulb", "bulb"),
    ("bulb", "bulb"),
    ("head", "head"),
    ("cabbage", "head"),
    ("lettuce", "head"),
    ("stalk", "stalk"),
    ("lemongrass", "stalk"),
    ("celery", "stalk"),
    ("bay leaf", "piece"),
    ("star anise", "piece"),
)


@dataclass
class ResumeState:
    reviewed_ids: list[str]
    written_ids: list[str]


def load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def dump_json(path: Path, payload: Any) -> None:
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=True) + "\n", encoding="utf-8")


def load_validator() -> Draft202012Validator | None:
    if Draft202012Validator is None:
        return None
    schema = load_json(SCHEMA_PATH)
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema)


def validate_recipe(recipe: dict[str, Any], validator: Draft202012Validator | None) -> None:
    if validator is None:
        return
    validator.validate(recipe)


def list_recipe_files() -> list[Path]:
    if not RECIPE_DIR.exists():
        return []
    return sorted(RECIPE_DIR.glob("*.json"))


def load_state(path: Path) -> ResumeState:
    if not path.exists():
        return ResumeState(reviewed_ids=[], written_ids=[])
    payload = load_json(path)
    reviewed_ids = payload.get("reviewed_ids", [])
    written_ids = payload.get("written_ids", [])
    if not isinstance(reviewed_ids, list) or not isinstance(written_ids, list):
        raise SystemExit(f"invalid state file: {path}")
    return ResumeState(reviewed_ids=[str(recipe_id) for recipe_id in reviewed_ids], written_ids=[str(recipe_id) for recipe_id in written_ids])


def save_state(path: Path, state: ResumeState) -> None:
    dump_json(
        path,
        {
            "reviewed_ids": list(dict.fromkeys(state.reviewed_ids)),
            "written_ids": list(dict.fromkeys(state.written_ids)),
        },
    )


def recipe_id_from_path(path: Path) -> str:
    return path.stem


def next_recipe_path(state: ResumeState) -> Path | None:
    reviewed = set(state.reviewed_ids)
    for recipe_path in list_recipe_files():
        if recipe_id_from_path(recipe_path) in reviewed:
            continue
        return recipe_path
    return None


def load_recipe(path: Path) -> dict[str, Any]:
    payload = load_json(path)
    if not isinstance(payload, dict):
        raise SystemExit(f"recipe payload must be a JSON object: {path}")
    return payload


def parse_model_response(content: str) -> dict[str, Any]:
    text = content.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        text = "\n".join(line.rstrip() for line in lines).strip()

    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        try:
            payload, _ = json.JSONDecoder().raw_decode(text)
        except json.JSONDecodeError as error:
            raise SystemExit(f"model output was not valid JSON: {error}") from error

    if not isinstance(payload, dict):
        raise SystemExit("model output must be a JSON object")
    if "recipe" not in payload or not isinstance(payload["recipe"], dict):
        raise SystemExit('model output must include a "recipe" object')
    if "summary" in payload and not isinstance(payload["summary"], str):
        raise SystemExit('model output "summary" must be a string if present')
    return payload


def extract_choice_content(choice: dict[str, Any]) -> str:
    message = choice.get("message") or {}
    content = message.get("content")
    if isinstance(content, str) and content.strip():
        return content

    collected_parts: list[str] = []
    if isinstance(content, list):
        for part in content:
            if isinstance(part, str) and part.strip():
                collected_parts.append(part.strip())
                continue
            if not isinstance(part, dict):
                continue
            text = part.get("text")
            if isinstance(text, str) and text.strip():
                collected_parts.append(text.strip())
                continue
            if isinstance(text, dict):
                nested_text = text.get("value")
                if isinstance(nested_text, str) and nested_text.strip():
                    collected_parts.append(nested_text.strip())
    if collected_parts:
        return "\n".join(collected_parts)

    fallback_text = choice.get("text")
    if isinstance(fallback_text, str) and fallback_text.strip():
        return fallback_text

    return ""


def is_named_object_list(value: Any) -> bool:
    if not isinstance(value, list):
        return False
    for item in value:
        if not isinstance(item, dict):
            return False
        if not isinstance(item.get("id"), str) or not item.get("id"):
            return False
        if not isinstance(item.get("slug"), str) or not item.get("slug"):
            return False
        if not isinstance(item.get("name"), str) or not item.get("name"):
            return False
    return True


def clean_text(value: Any) -> str:
    if value is None:
        return ""
    return re.sub(r"\s+", " ", str(value).strip())


def is_blank(value: Any) -> bool:
    return not clean_text(value)


def singularize_unit(unit: str) -> str:
    lowered = unit.lower()
    if lowered.endswith("ves"):
        return lowered[:-3] + "f"
    if lowered.endswith("ies"):
        return lowered[:-3] + "y"
    if lowered.endswith("s") and not lowered.endswith("ss"):
        return lowered[:-1]
    return lowered


def pluralize_unit(unit: str) -> str:
    lowered = unit.lower()
    if lowered.endswith("f"):
        return lowered[:-1] + "ves"
    if lowered.endswith("y") and len(lowered) > 1 and lowered[-2] not in "aeiou":
        return lowered[:-1] + "ies"
    if lowered.endswith("s"):
        return lowered
    return lowered + "s"


def quantity_is_plural(quantity: Any) -> bool:
    text = clean_text(quantity).lower()
    if not text:
        return False
    if text in {"a", "an", "one"}:
        return False
    if re.fullmatch(r"\d+\s+\d+/\d+", text):
        whole, fraction = text.split(maxsplit=1)
        numerator, denominator = fraction.split("/", maxsplit=1)
        try:
            value = int(whole) + (int(numerator) / int(denominator))
        except ValueError:
            return False
        return value > 1
    if re.fullmatch(r"\d+/\d+", text):
        numerator, denominator = text.split("/", maxsplit=1)
        try:
            value = int(numerator) / int(denominator)
        except ValueError:
            return False
        return value > 1
    match = re.search(r"\d+(?:\.\d+)?", text)
    if not match:
        return False
    try:
        return float(match.group(0)) > 1
    except ValueError:
        return False


def infer_unit_from_ingredient(ingredient: dict[str, Any]) -> str | None:
    quantity = ingredient.get("quantity")
    if is_blank(quantity):
        return None

    haystack = " ".join(
        filter(
            None,
            [
                clean_text(ingredient.get("normalized_name")).lower(),
                clean_text(ingredient.get("name")).lower(),
            ],
        )
    )

    singular = "piece"
    for needle, unit in SINGULAR_UNIT_HINTS:
        if needle in haystack:
            singular = unit
            break

    if quantity_is_plural(quantity):
        return pluralize_unit(singular)
    return singularize_unit(singular)


def looks_like_preparation(text: str) -> bool:
    normalized = clean_text(text).lower()
    if not normalized:
        return False
    return any(token in PREPARATION_HINTS for token in re.findall(r"[a-z]+", normalized))


def infer_preparation_from_name(name: Any) -> str | None:
    text = clean_text(name)
    if not text:
        return None

    parts: list[str] = []
    parenthetical_parts = re.findall(r"\(([^()]*)\)", text)
    for part in parenthetical_parts:
        if looks_like_preparation(part):
            parts.append(clean_text(part).lower())

    base_name = re.sub(r"\([^()]*\)", "", text)
    for separator in [",", " - ", " – "]:
        if separator in base_name:
            _, remainder = base_name.split(separator, 1)
            if looks_like_preparation(remainder):
                parts.append(clean_text(remainder).lower())
            break

    trailing_match = TRAILING_PREPARATION_RE.search(base_name)
    if trailing_match:
        parts.append(clean_text(trailing_match.group(0)).lower())

    if not parts:
        return None

    deduped: list[str] = []
    seen: set[str] = set()
    for part in parts:
        if part not in seen:
            deduped.append(part)
            seen.add(part)
    return ", ".join(deduped)


def normalize_ingredient_metadata(ingredient: Any) -> tuple[Any, list[str]]:
    if not isinstance(ingredient, dict):
        return ingredient, []

    repairs: list[str] = []

    if is_blank(ingredient.get("unit")):
        inferred_unit = infer_unit_from_ingredient(ingredient)
        if inferred_unit:
            ingredient["unit"] = inferred_unit
            repairs.append(f"filled unit for ingredient '{ingredient.get('name', ingredient.get('normalized_name', 'unknown'))}'")

    if is_blank(ingredient.get("preparation")):
        inferred_preparation = infer_preparation_from_name(ingredient.get("name"))
        if inferred_preparation:
            ingredient["preparation"] = inferred_preparation
            repairs.append(
                f"filled preparation for ingredient '{ingredient.get('name', ingredient.get('normalized_name', 'unknown'))}'"
            )

    return ingredient, repairs


def normalize_improved_recipe(original: dict[str, Any], improved: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    normalized = deepcopy(improved)
    repairs: list[str] = []

    for key in ("id", "slug", "schema_version", "updated_at", "revision"):
        if normalized.get(key) != original.get(key):
            normalized[key] = deepcopy(original.get(key))
            repairs.append(f"restored immutable field {key}")

    if not is_named_object_list(normalized.get("categories")):
        normalized["categories"] = deepcopy(original.get("categories", []))
        repairs.append("restored categories because model returned an invalid shape")

    if not is_named_object_list(normalized.get("tags")):
        normalized["tags"] = deepcopy(original.get("tags", []))
        repairs.append("restored tags because model returned an invalid shape")

    ingredients = normalized.get("ingredients")
    if isinstance(ingredients, list):
        normalized_ingredients = []
        for ingredient in ingredients:
            normalized_ingredient, ingredient_repairs = normalize_ingredient_metadata(ingredient)
            normalized_ingredients.append(normalized_ingredient)
            repairs.extend(ingredient_repairs)
        normalized["ingredients"] = normalized_ingredients

    return normalized, repairs


def build_payload(
    recipe: dict[str, Any],
    model: str,
    repair_feedback: str | None = None,
) -> dict[str, Any]:
    system_content = (
        "You improve recipe JSON for Pepper Meal Planner. Return valid JSON only. "
        "Return an object with recipe and summary. recipe must be the full updated recipe object. "
        "Preserve schema compatibility and do not change id, slug, schema_version, updated_at, or revision. "
        "Only change fields when necessary and copy unchanged fields exactly. "
        "Do not convert structured arrays into plain strings. "
        "categories and tags must remain arrays of objects with id, slug, and name. "
        "dietary_labels, allergens, equipment, recipe_notes, and storage_notes must remain arrays of strings. "
        "instructions must remain an array of objects with position and text. "
        "ingredients must remain an array of objects with ingredient_id, name, normalized_name, and position, plus any existing optional fields. "
        "For ingredients, fill in missing unit values using the usual measure for that ingredient; if there is no better fit, use piece/pieces. "
        "If preparation is present in the ingredient name, move it into preparation. If no preparation is evident, leaving preparation null is acceptable. "
        "If you cannot improve categories or tags while preserving their full object shape, leave them unchanged."
    )
    if repair_feedback:
        system_content += (
            " The previous candidate failed schema validation. Fix the validation issues in the recipe while preserving all valid data. "
            "Prioritize the explicit validation error details provided by the user message."
        )

    user_content = json.dumps(recipe, indent=2, ensure_ascii=False)
    if repair_feedback:
        user_content = (
            "Repair this recipe so it passes schema validation.\n\n"
            f"Validation error:\n{repair_feedback}\n\n"
            "Current recipe candidate:\n"
            f"{user_content}"
        )

    return {
        "model": model,
        "messages": [
            {
                "role": "system",
                "content": system_content,
            },
            {
                "role": "user",
                "content": user_content,
            },
        ],
        "temperature": 0.2,
        "response_format": {"type": "json_object"},
        "plugins": [{"id": "response-healing"}],
    }


def call_openrouter(
    recipe: dict[str, Any],
    args: argparse.Namespace,
    repair_feedback: str | None = None,
) -> dict[str, Any]:
    api_key = args.api_key or os.environ.get("OPENROUTER_API_KEY")
    if not api_key:
        raise SystemExit("OPENROUTER_API_KEY is required")

    model = args.model or os.environ.get("OPENROUTER_MODEL") or DEFAULT_MODEL
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "HTTP-Referer": args.site_url or os.environ.get("OPENROUTER_SITE_URL") or "https://github.com/ffimnsr/pepper-meal-recipes",
        "X-OpenRouter-Title": args.app_name or os.environ.get("OPENROUTER_APP_NAME") or "Pepper Meal Recipe Improver",
        "X-OpenRouter-Metadata": "enabled",
    }
    max_attempts = 2
    last_error: str | None = None
    for attempt_index in range(max_attempts):
        status(f"Sending request to OpenRouter with {model}...")
        request_started_at = time.perf_counter()
        response = requests.post(
            OPENROUTER_URL,
            headers=headers,
            json=build_payload(recipe, model, repair_feedback=repair_feedback),
            timeout=args.timeout,
        )
        response.raise_for_status()
        response_received_at = time.perf_counter()
        status(f"Response received in {response_received_at - request_started_at:.2f}s; parsing model output...")
        payload = response.json()
        generation_id = response.headers.get("X-Generation-Id")
        if generation_id:
            status(f"OpenRouter generation id: {generation_id}")

        usage = payload.get("usage")
        if isinstance(usage, dict):
            prompt_tokens = usage.get("prompt_tokens")
            completion_tokens = usage.get("completion_tokens")
            total_tokens = usage.get("total_tokens")
            status(
                "OpenRouter usage: "
                f"prompt_tokens={prompt_tokens}, "
                f"completion_tokens={completion_tokens}, "
                f"total_tokens={total_tokens}"
            )

        metadata = payload.get("openrouter_metadata")
        if isinstance(metadata, dict):
            summary = metadata.get("summary")
            attempt = metadata.get("attempt")
            strategy = metadata.get("strategy")
            status(f"OpenRouter routing: strategy={strategy}, attempt={attempt}, summary={summary}")
            attempts = metadata.get("attempts")
            if isinstance(attempts, list) and attempts:
                attempt_summaries = []
                for item in attempts:
                    if not isinstance(item, dict):
                        continue
                    provider = item.get("provider")
                    model_name = item.get("model")
                    status_code = item.get("status")
                    attempt_summaries.append(f"{provider}:{model_name}:{status_code}")
                if attempt_summaries:
                    status("OpenRouter provider attempts: " + " | ".join(attempt_summaries))

            pipeline = metadata.get("pipeline")
            if isinstance(pipeline, list) and pipeline:
                pipeline_summaries = []
                for item in pipeline:
                    if not isinstance(item, dict):
                        continue
                    pipeline_summaries.append(f"{item.get('type')}:{item.get('name')}")
                if pipeline_summaries:
                    status("OpenRouter pipeline: " + " | ".join(pipeline_summaries))

        choices = payload.get("choices") or []
        if not choices:
            raise RuntimeError("openrouter response did not include any choices")

        choice = choices[0]
        content = extract_choice_content(choice)
        if not content:
            finish_reason = choice.get("finish_reason")
            last_error = f"openrouter response content was empty (finish_reason={finish_reason})"
            if attempt_index + 1 < max_attempts:
                status(f"{last_error}; retrying once...")
                continue
            raise RuntimeError(last_error)

        parse_started_at = time.perf_counter()
        result = parse_model_response(content)
        parse_finished_at = time.perf_counter()
        status(
            "Model profiling: "
            f"request={response_received_at - request_started_at:.2f}s, "
            f"parse={parse_finished_at - parse_started_at:.2f}s, "
            f"total={parse_finished_at - request_started_at:.2f}s"
        )
        return result

    raise RuntimeError(last_error or "openrouter request failed")


def preview_changes(before: dict[str, Any], after: dict[str, Any]) -> str:
    keys = sorted(set(before) | set(after))
    ignored = {"schema_version", "id", "slug", "updated_at", "revision"}
    changed = [key for key in keys if key not in ignored and before.get(key) != after.get(key)]
    return ", ".join(changed) if changed else "no visible changes"


def prompt_yes_no(question: str, default_no: bool = True) -> bool:
    suffix = " [y/N]: " if default_no else " [Y/n]: "
    answer = input(question + suffix).strip().lower()
    if not answer:
        return not default_no
    return answer in {"y", "yes"}


def status(message: str) -> None:
    print(message, flush=True)


def choose_recipe_path(args: argparse.Namespace, state: ResumeState) -> Path | None:
    if args.recipe_id:
        recipe_path = RECIPE_DIR / f"{args.recipe_id}.json"
        if not recipe_path.exists():
            raise SystemExit(f"recipe not found: {recipe_path}")
        return recipe_path
    return next_recipe_path(state)


def write_improved_recipe(recipe_path: Path, improved_recipe: dict[str, Any]) -> None:
    dump_json(recipe_path, improved_recipe)


def edit_recipe_in_vim(recipe: dict[str, Any]) -> dict[str, Any]:
    with tempfile.NamedTemporaryFile("w+", suffix=".json", delete=False, encoding="utf-8") as handle:
        temp_path = Path(handle.name)
        handle.write(json.dumps(recipe, indent=2, ensure_ascii=True) + "\n")

    try:
        subprocess.run(["vim", str(temp_path)], check=True)
        edited = load_json(temp_path)
    finally:
        temp_path.unlink(missing_ok=True)

    if not isinstance(edited, dict):
        raise SystemExit("edited recipe must be a JSON object")
    return edited


def re_improve_recipe_with_validation_feedback(
    original_recipe: dict[str, Any],
    current_recipe: dict[str, Any],
    validation_error: ValidationError,
    args: argparse.Namespace,
) -> tuple[dict[str, Any], str]:
    status("Retrying with the validation error fed back to the model...")
    improvement = call_openrouter(current_recipe, args, repair_feedback=str(validation_error))
    retried_recipe, repairs = normalize_improved_recipe(original_recipe, improvement["recipe"])
    if repairs:
        status("Normalization repairs after retry: " + "; ".join(repairs))
    summary = clean_text(improvement.get("summary"))
    if summary:
        status(f"Retry summary: {summary}")
    return retried_recipe, summary


def validate_recipe_with_manual_fix(
    original_recipe: dict[str, Any],
    improved_recipe: dict[str, Any],
    validator: Draft202012Validator | None,
    args: argparse.Namespace | None = None,
) -> dict[str, Any]:
    current_recipe = improved_recipe
    attempted_model_retry = False
    while True:
        try:
            validate_recipe(current_recipe, validator)
            return current_recipe
        except ValidationError as error:
            print(f"Schema validation failed: {error}")
            if args is not None and not attempted_model_retry:
                attempted_model_retry = True
                current_recipe, _ = re_improve_recipe_with_validation_feedback(
                    original_recipe,
                    current_recipe,
                    error,
                    args,
                )
                status("Re-validating retried recipe...")
                continue

            if not prompt_yes_no("Open the improved recipe in vim to fix validation errors?", default_no=True):
                raise

            status("Opening vim for manual recipe edits...")
            current_recipe = edit_recipe_in_vim(current_recipe)
            current_recipe, repairs = normalize_improved_recipe(original_recipe, current_recipe)
            if repairs:
                status("Normalization repairs after edit: " + "; ".join(repairs))
            status("Re-validating edited recipe...")


def run_recipe(recipe_path: Path, args: argparse.Namespace, validator: Draft202012Validator | None) -> tuple[bool, dict[str, Any]]:
    original_recipe = load_recipe(recipe_path)
    status(f"\nProcessing {recipe_path.name} ({original_recipe.get('name', recipe_path.stem)})")
    improvement = call_openrouter(original_recipe, args)
    improved_recipe, repairs = normalize_improved_recipe(original_recipe, improvement["recipe"])
    if repairs:
        status("Normalization repairs: " + "; ".join(repairs))
    status("Validating improved recipe against the schema...")
    improved_recipe = validate_recipe_with_manual_fix(original_recipe, improved_recipe, validator, args=args)

    print(f"Recipe: {original_recipe.get('name', recipe_path.stem)}")
    print(f"Summary: {improvement.get('summary', '').strip()}")
    print(f"Changed fields: {preview_changes(original_recipe, improved_recipe)}")

    wrote = False
    if args.yes or prompt_yes_no("Overwrite this recipe with the improved version?", default_no=True):
        write_improved_recipe(recipe_path, improved_recipe)
        wrote = True
        print(f"Wrote {recipe_path}")
    else:
        print("Skipped write")

    return wrote, improved_recipe


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Interactively improve recipe JSON using OpenRouter.")
    parser.add_argument("recipe_id", nargs="?", help="Optional recipe id to process directly.")
    parser.add_argument("--api-key", help="OpenRouter API key.")
    parser.add_argument("--model", help="OpenRouter model id.")
    parser.add_argument("--site-url", help="HTTP-Referer header to send to OpenRouter.")
    parser.add_argument("--app-name", help="X-Title header to send to OpenRouter.")
    parser.add_argument("--timeout", type=int, default=120, help="Request timeout in seconds.")
    parser.add_argument("-y", "--yes", action="store_true", help="Automatically answer yes to overwrite and continue prompts.")
    parser.add_argument("--state-file", type=Path, default=DEFAULT_STATE_FILE, help="Resume state file path.")
    parser.add_argument("--resume", action="store_true", help="Skip recipes already reviewed in the state file.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    validator = load_validator()
    state = load_state(args.state_file)

    while True:
        status("Selecting the next recipe...")
        recipe_path = choose_recipe_path(args, state)
        if recipe_path is None:
            print("No recipes left to process.")
            save_state(args.state_file, state)
            return

        recipe_id = recipe_id_from_path(recipe_path)
        try:
            wrote, _ = run_recipe(recipe_path, args, validator)
        except (Exception, SystemExit) as error:
            print(f"Failed to improve {recipe_path.name}: {error}")
            if not prompt_yes_no("Try the next recipe instead?", default_no=True):
                save_state(args.state_file, state)
                return
            state.reviewed_ids.append(recipe_id)
            state.reviewed_ids = list(dict.fromkeys(state.reviewed_ids))
            continue

        state.reviewed_ids.append(recipe_id)
        if wrote:
            state.written_ids.append(recipe_id)
        state.reviewed_ids = list(dict.fromkeys(state.reviewed_ids))
        state.written_ids = list(dict.fromkeys(state.written_ids))
        save_state(args.state_file, state)

        if not args.yes and not prompt_yes_no("Process another recipe?", default_no=True):
            return

        if args.recipe_id:
            args.recipe_id = None


if __name__ == "__main__":
    main()
