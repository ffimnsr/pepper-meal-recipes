#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
import os
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

    return normalized, repairs


def build_payload(recipe: dict[str, Any], model: str) -> dict[str, Any]:
    return {
        "model": model,
        "messages": [
            {
                "role": "system",
                "content": (
                    "You improve recipe JSON for Pepper Meal Planner. Return valid JSON only. "
                    "Return an object with recipe and summary. recipe must be the full updated recipe object. "
                    "Preserve schema compatibility and do not change id, slug, schema_version, updated_at, or revision. "
                    "Only change fields when necessary and copy unchanged fields exactly. "
                    "Do not convert structured arrays into plain strings. "
                    "categories and tags must remain arrays of objects with id, slug, and name. "
                    "dietary_labels, allergens, equipment, recipe_notes, and storage_notes must remain arrays of strings. "
                    "instructions must remain an array of objects with position and text. "
                    "ingredients must remain an array of objects with ingredient_id, name, normalized_name, and position, plus any existing optional fields. "
                    "If you cannot improve categories or tags while preserving their full object shape, leave them unchanged."
                ),
            },
            {
                "role": "user",
                "content": json.dumps(recipe, indent=2, ensure_ascii=False),
            },
        ],
        "temperature": 0.2,
        "response_format": {"type": "json_object"},
        "plugins": [{"id": "response-healing"}],
    }


def call_openrouter(recipe: dict[str, Any], args: argparse.Namespace) -> dict[str, Any]:
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
    status(f"Sending request to OpenRouter with {model}...")
    request_started_at = time.perf_counter()
    response = requests.post(OPENROUTER_URL, headers=headers, json=build_payload(recipe, model), timeout=args.timeout)
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
        raise SystemExit("openrouter response did not include any choices")
    message = choices[0].get("message") or {}
    content = message.get("content")
    if not isinstance(content, str) or not content.strip():
        raise SystemExit("openrouter response content was empty")
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


def validate_recipe_with_manual_fix(
    original_recipe: dict[str, Any],
    improved_recipe: dict[str, Any],
    validator: Draft202012Validator | None,
) -> dict[str, Any]:
    current_recipe = improved_recipe
    while True:
        try:
            validate_recipe(current_recipe, validator)
            return current_recipe
        except ValidationError as error:
            print(f"Schema validation failed: {error}")
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
    improved_recipe = validate_recipe_with_manual_fix(original_recipe, improved_recipe, validator)

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
        except Exception as error:
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
