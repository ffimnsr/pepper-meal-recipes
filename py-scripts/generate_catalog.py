#!/usr/bin/env python3

from __future__ import annotations

import argparse
import hashlib
import html
import json
import re
import uuid
from collections import Counter
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

try:
    from jsonschema import Draft202012Validator
except ImportError:
    Draft202012Validator = None


SCHEMA_VERSION = 1
CATALOG_VERSION = "v1"
MINIMUM_SUPPORTED_CLIENT_VERSION = "1.0.0"
REPO_ROOT = Path(__file__).resolve().parents[1]
CATALOG_ROOT = REPO_ROOT / "recipes" / CATALOG_VERSION
RECIPES_DIR = CATALOG_ROOT / "recipes" / "by-id"
INDEXES_DIR = CATALOG_ROOT / "indexes"
MANIFESTS_DIR = CATALOG_ROOT / "manifests"
RELEASE_FILE = CATALOG_ROOT / "release.json"
SCHEMAS_DIR = CATALOG_ROOT / "schemas"
INGREDIENT_REVIEW_FILE = INDEXES_DIR / "ingredients.review.json"

CATALOG_NAMESPACE = uuid.UUID("8d7c8f42-d53a-4d2d-9d67-935eeea8d7c4")
RECIPE_NAMESPACE = uuid.uuid5(CATALOG_NAMESPACE, "recipe")
CATEGORY_NAMESPACE = uuid.uuid5(CATALOG_NAMESPACE, "category")
TAG_NAMESPACE = uuid.uuid5(CATALOG_NAMESPACE, "tag")
INGREDIENT_NAMESPACE = uuid.uuid5(CATALOG_NAMESPACE, "ingredient")
AMOUNT_TOKEN = re.compile(r"^(?:\d+(?:[./]\d+)?|\d+\s+\d+/\d+|[¼½¾⅐⅑⅒⅓⅔⅕⅖⅗⅘⅙⅚⅛⅜⅝⅞]|to)$")
FRACTION_REPLACEMENTS = {
    "¼": "1/4",
    "½": "1/2",
    "¾": "3/4",
    "⅐": "1/7",
    "⅑": "1/9",
    "⅒": "1/10",
    "⅓": "1/3",
    "⅔": "2/3",
    "⅕": "1/5",
    "⅖": "2/5",
    "⅗": "3/5",
    "⅘": "4/5",
    "⅙": "1/6",
    "⅚": "5/6",
    "⅛": "1/8",
    "⅜": "3/8",
    "⅝": "5/8",
    "⅞": "7/8",
}
KNOWN_UNITS = {
    "can",
    "cans",
    "clove",
    "cloves",
    "cup",
    "cups",
    "gram",
    "grams",
    "g",
    "kg",
    "kilogram",
    "kilograms",
    "lb",
    "lbs",
    "ounce",
    "ounces",
    "oz",
    "package",
    "packages",
    "pack",
    "packs",
    "piece",
    "pieces",
    "pinch",
    "pinches",
    "pound",
    "pounds",
    "sprig",
    "sprigs",
    "tablespoon",
    "tablespoons",
    "tbsp",
    "teaspoon",
    "teaspoons",
    "tsp",
}
LEADING_AMOUNT_PHRASES = (
    "a bit of ",
    "a bunch of ",
    "a dash of ",
    "a few dashes of ",
    "a few drops of ",
    "a hint of ",
    "a pack of ",
    "a pinch of ",
    "an assortment of ",
    "a slice of ",
    "a can of ",
    "can of ",
    "pack of ",
    "bunch of ",
    "head of ",
)
LEADING_DESCRIPTORS = {
    "additional",
    "another",
    "bit",
    "bunch",
    "dash",
    "few",
    "fresh",
    "head",
    "heads",
    "hint",
    "large",
    "medium",
    "piece",
    "pieces",
    "pack",
    "raw",
    "small",
    "thumb",
    "thumbs",
    "whole",
}
NON_IDENTITY_WORDS = {
    "also",
    "around",
    "best",
    "length",
    "optional",
}
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
    "head",
    "heads",
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
SAFE_COMPOUND_SPLITS = {
    "fish sauce and crushed peppercorn": ["fish sauce", "crushed peppercorn"],
    "fish sauce and ground black pepper": ["fish sauce", "ground black pepper"],
    "fish sauce and ground white pepper": ["fish sauce", "ground white pepper"],
    "salt and ground black pepper": ["salt", "ground black pepper"],
    "salt and pepper": ["salt", "pepper"],
}
PACKAGING_PREFIXES = {
    "can": "canned",
}
EXCLUDED_INGREDIENT_PATTERNS = (
    re.compile(r"^aluminum foil$", re.IGNORECASE),
    re.compile(r"^a double boiler$", re.IGNORECASE),
    re.compile(r"^bamboo skewers(?: .*)?$", re.IGNORECASE),
    re.compile(r"^water for boiling\b", re.IGNORECASE),
    re.compile(r"^additional water for boiling\b", re.IGNORECASE),
)
AMBIGUOUS_CONNECTOR_RE = re.compile(r"\b(or|and/or)\b", re.IGNORECASE)
FOR_BOILING_RE = re.compile(r"\bfor boiling\b.*$", re.IGNORECASE)
TO_TASTE_RE = re.compile(r"\bto taste\b.*$", re.IGNORECASE)
CUT_PREPARATION_RE = re.compile(
    r"\s+(cut into|cut in|cut to|chopped|cleaned|cubed|diced|gutted|julienned|knotted|minced|peeled|pitted|quartered|scaled|seeded|shelled|shredded|sliced|trimmed|wedged)\b.*$",
    re.IGNORECASE,
)
PAREN_CONTENT_RE = re.compile(r"\(([^()]*)\)")
INGREDIENT_REVIEW_SCHEMA = "ingredients-review.schema.json"
VALIDATE_SCHEMAS = True


@dataclass
class RecipeSummary:
    recipe_id: str
    slug: str
    name: str
    recipe_type: str | None
    brief_description: str | None
    cuisine: str | None
    image_url: str | None
    difficulty: str | None
    servings: int | None
    cooking_time_minutes: int | None
    preparation_time_minutes: int | None
    rest_time_minutes: int | None
    total_time_minutes: int | None
    category_slugs: list[str]
    tag_slugs: list[str]
    dietary_labels: list[str]
    allergens: list[str]
    ingredient_names: list[str]
    instruction_step_count: int
    updated_at: int
    revision: int
    recipe_path: str
    file_sha256: str


@dataclass
class CanonicalRecipe:
    source_path: Path
    output_path: Path
    payload: dict
    review_entries: list[dict]


@dataclass
class CanonicalIngredientResult:
    ingredients: list[dict]
    review_entries: list[dict]


def utc_timestamp() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def timestamp_from_unix_seconds(value: int) -> str:
    return datetime.fromtimestamp(value, tz=timezone.utc).replace(microsecond=0).isoformat().replace(
        "+00:00", "Z"
    )


def stable_uuid(namespace: uuid.UUID, key: str) -> str:
    return str(uuid.uuid5(namespace, key))


def clean_text(value: object) -> str:
    if not value:
        return ""
    if isinstance(value, list):
        value = " ".join(clean_text(item) for item in value if clean_text(item))
    elif not isinstance(value, str):
        value = str(value)
    value = html.unescape(value)
    value = value.replace("\xa0", " ")
    value = re.sub(r"\s+", " ", value)
    return value.strip(" \t\r\n•▢")


def normalize_name(value: str) -> str:
    value = clean_text(value).lower()
    value = re.sub(r"\([^)]*\)", "", value)
    value = re.sub(r"[^a-z0-9\s/-]", "", value)
    value = re.sub(r"\s+", " ", value)
    return value.strip(" -/")


def split_parenthetical_chunks(value: str) -> tuple[str, list[str]]:
    text = clean_text(value)
    parts: list[str] = []
    previous = None
    while previous != text:
        previous = text
        for match in PAREN_CONTENT_RE.findall(text):
            cleaned = clean_text(match)
            if cleaned:
                parts.append(cleaned)
        text = PAREN_CONTENT_RE.sub(" ", text)
        text = clean_text(text)
    return text, parts


def classify_parenthetical_part(value: str) -> str:
    normalized = normalize_name(value)
    if not normalized:
        return "ignore"
    if normalized == "optional" or normalized.startswith("optional "):
        return "optional"
    if AMBIGUOUS_CONNECTOR_RE.search(normalized):
        return "ambiguous"
    if any(token in PREPARATION_HINTS for token in normalized.split()):
        return "preparation"
    return "alias"


def singularize_token(token: str) -> str:
    irregular = {
        "batches": "batch",
        "berries": "berry",
        "chilies": "chili",
        "eggs": "egg",
        "leaves": "leaf",
        "loaves": "loaf",
        "potatoes": "potato",
        "tomatoes": "tomato",
    }
    if token in irregular:
        return irregular[token]
    if len(token) <= 3 or token.endswith(("ss", "us", "is")):
        return token
    if token.endswith("ies") and len(token) > 4:
        return f"{token[:-3]}y"
    if token.endswith("oes") and len(token) > 4:
        return token[:-2]
    if token.endswith("ses") and len(token) > 4:
        return token[:-2]
    if token.endswith("s") and not token.endswith("ds"):
        return token[:-1]
    return token


def singularize_phrase(value: str) -> str:
    tokens = value.split()
    if not tokens:
        return value
    tokens[-1] = singularize_token(tokens[-1])
    return " ".join(token for token in tokens if token)


def normalize_packaging_prefix(value: str) -> str:
    for prefix, replacement in PACKAGING_PREFIXES.items():
        if value.startswith(f"{prefix} of "):
            return clean_text(f"{replacement} {value[len(prefix) + 4:]}")
    return value


def strip_leading_descriptors(value: str) -> str:
    tokens = value.split()
    while tokens and tokens[0] in LEADING_DESCRIPTORS:
        tokens.pop(0)
    return " ".join(tokens)


def strip_non_identity_words(value: str) -> str:
    tokens = [token for token in value.split() if token not in NON_IDENTITY_WORDS]
    return " ".join(tokens)


def normalize_core_ingredient_name(value: str) -> str:
    normalized = normalize_name(value)
    if not normalized:
        return ""
    if normalized.startswith("a can of "):
        normalized = f"canned {normalized[len('a can of '):]}"
    elif normalized.startswith("can of "):
        normalized = f"canned {normalized[len('can of '):]}"
    for phrase in LEADING_AMOUNT_PHRASES:
        if normalized.startswith(phrase):
            normalized = normalized[len(phrase) :]
            break
    normalized = FOR_BOILING_RE.sub("", normalized).strip()
    normalized = TO_TASTE_RE.sub("", normalized).strip()
    normalized = strip_leading_descriptors(normalized)
    normalized = CUT_PREPARATION_RE.sub("", normalized).strip()
    normalized = strip_non_identity_words(normalized)
    normalized = re.sub(r"\bof\b$", "", normalized).strip()
    normalized = re.sub(r"\s+", " ", normalized).strip(" -/")
    normalized = singularize_phrase(normalized)
    synonym_map = {
        "green onions": "green onion",
        "scallions": "green onion",
        "spring onions": "green onion",
        "yellow onions": "yellow onion",
        "red onions": "red onion",
        "white onions": "white onion",
        "canned tunas": "canned tuna",
    }
    return synonym_map.get(normalized, normalized)


def looks_like_preparation(value: str) -> bool:
    normalized = normalize_name(value)
    return bool(normalized) and any(token in PREPARATION_HINTS for token in normalized.split())


def clean_preparation_text(value: str | None) -> str | None:
    text = normalize_name(value or "")
    if not text:
        return None
    text = re.sub(r"\boptional\b", "", text).strip()
    text = re.sub(r"\s+", " ", text).strip(" -/")
    return text or None


def is_excluded_ingredient_name(value: str) -> bool:
    text = clean_text(value)
    if not text:
        return True
    return any(pattern.search(text) for pattern in EXCLUDED_INGREDIENT_PATTERNS)


def build_review_entry(
    recipe: dict,
    ingredient: dict,
    issue_types: list[str],
    resolution: str,
    original_text: str,
    cleaned_name: str | None = None,
    replacements: list[str] | None = None,
) -> dict:
    return {
        "recipe_id": recipe["id"],
        "recipe_slug": recipe["slug"],
        "recipe_name": recipe["name"],
        "position": ingredient.get("position"),
        "original_text": original_text,
        "quantity": ingredient.get("quantity"),
        "unit": ingredient.get("unit"),
        "cleaned_name": cleaned_name,
        "replacements": replacements or [],
        "issue_types": sorted(dict.fromkeys(issue_types)),
        "resolution": resolution,
    }


def canonicalize_ingredient_entry(recipe: dict, ingredient: dict) -> CanonicalIngredientResult:
    original_text = clean_text(" ".join(filter(None, [ingredient.get("quantity"), ingredient.get("unit"), ingredient.get("name")])))
    if is_section_heading(ingredient.get("name") or ""):
        return CanonicalIngredientResult([], [])

    base_name, parenthetical_parts = split_parenthetical_chunks(ingredient.get("name") or "")
    issue_types: list[str] = []
    preparation_parts: list[str] = []
    if ingredient.get("preparation"):
        preparation_parts.append(ingredient["preparation"])

    for part in parenthetical_parts:
        kind = classify_parenthetical_part(part)
        if kind == "preparation":
            preparation_parts.append(part)
        elif kind == "optional":
            issue_types.append("optional")
        elif kind == "ambiguous":
            issue_types.append("ambiguous_parenthetical")
        elif kind == "alias":
            issue_types.append("alias_parenthetical")

    canonical_name = normalize_core_ingredient_name(base_name)
    trailing_match = CUT_PREPARATION_RE.search(base_name)
    if trailing_match:
        preparation_parts.append(trailing_match.group(0))

    preparation = clean_preparation_text(" ".join(preparation_parts))
    if is_excluded_ingredient_name(base_name) or is_excluded_ingredient_name(canonical_name):
        review = build_review_entry(
            recipe,
            ingredient,
            issue_types + ["excluded_non_ingredient"],
            "excluded",
            original_text,
            cleaned_name=canonical_name or None,
        )
        return CanonicalIngredientResult([], [review])

    if not canonical_name:
        review = build_review_entry(recipe, ingredient, issue_types + ["empty_canonical_name"], "excluded", original_text)
        return CanonicalIngredientResult([], [review])

    if canonical_name in SAFE_COMPOUND_SPLITS:
        replacements = SAFE_COMPOUND_SPLITS[canonical_name]
        split_ingredients = [
            {
                "ingredient_id": stable_uuid(INGREDIENT_NAMESPACE, normalize_name(name)),
                "name": name,
                "normalized_name": normalize_name(name),
                "quantity": None,
                "unit": None,
                "preparation": preparation,
                "position": ingredient["position"],
            }
            for name in replacements
        ]
        review = build_review_entry(
            recipe,
            ingredient,
            issue_types + ["compound_split"],
            "split",
            original_text,
            cleaned_name=canonical_name,
            replacements=replacements,
        )
        return CanonicalIngredientResult(split_ingredients, [review])

    if AMBIGUOUS_CONNECTOR_RE.search(canonical_name):
        review = build_review_entry(
            recipe,
            ingredient,
            issue_types + ["ambiguous_connector"],
            "review",
            original_text,
            cleaned_name=canonical_name,
        )
        return CanonicalIngredientResult([], [review])

    canonical = {
        "ingredient_id": stable_uuid(INGREDIENT_NAMESPACE, normalize_name(canonical_name)),
        "name": canonical_name,
        "normalized_name": normalize_name(canonical_name),
        "quantity": ingredient.get("quantity"),
        "unit": ingredient.get("unit"),
        "preparation": preparation,
        "position": ingredient["position"],
    }
    review_entries: list[dict] = []
    if issue_types:
        review_entries.append(
            build_review_entry(
                recipe,
                ingredient,
                issue_types,
                "canonicalized",
                original_text,
                cleaned_name=canonical_name,
            )
        )
    return CanonicalIngredientResult([canonical], review_entries)


def normalize_ingredient_text(value: str) -> str:
    text = clean_text(value)
    text = text.replace("\u2044", "/")
    for char, replacement in FRACTION_REPLACEMENTS.items():
        text = re.sub(rf"(\d){re.escape(char)}", rf"\1 {replacement}", text)
        text = text.replace(char, replacement)
    text = re.sub(r"(^|\s)/(?=\d)", r"\g<1>1/", text)
    text = re.sub(r"(?<=\d)\s*/\s+(?=\d+/\d+\b)", " ", text)
    text = re.sub(r"(^|\s)[~≈](?=\s*\d)", r"\1", text)
    text = re.sub(r"[–—]", " - ", text)
    text = re.sub(r"(?<=\d)(?=[A-Za-z])", " ", text)
    text = re.sub(r"(?<=\d/\d)(?=[A-Za-z])", " ", text)
    text = re.sub(r"(?<=\d)\s*-\s*(?=[A-Za-z])", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def normalize_amount_token(token: str) -> str:
    return token.strip("()[]{}.,:;").lower()


def is_amount_token(token: str) -> bool:
    normalized = normalize_amount_token(token)
    if not normalized:
        return False
    normalized = normalized.strip("-")
    if AMOUNT_TOKEN.match(normalized):
        return True
    range_parts = [part for part in normalized.split("-") if part]
    return len(range_parts) == 2 and all(AMOUNT_TOKEN.match(part) for part in range_parts)


def strip_outer_parentheses(value: str) -> str:
    text = clean_text(value)
    while text.startswith("(") and text.endswith(")"):
        depth = 0
        balanced = True
        for index, char in enumerate(text):
            if char == "(":
                depth += 1
            elif char == ")":
                depth -= 1
                if depth == 0 and index != len(text) - 1:
                    balanced = False
                    break
        if not balanced or depth != 0:
            break
        text = clean_text(text[1:-1])
    return text


def strip_leading_measurement(value: str) -> str:
    tokens = normalize_ingredient_text(value).split()
    while tokens and (tokens[0] == "-" or is_amount_token(tokens[0])):
        tokens.pop(0)
    if tokens and tokens[0].lower().rstrip(".") in KNOWN_UNITS:
        tokens.pop(0)
    if tokens and tokens[0].lower() == "of":
        tokens.pop(0)
    return clean_text(" ".join(tokens))


def clean_ingredient_name(value: str) -> str:
    name = strip_note_references(clean_text(value))
    if not name:
        return ""
    stripped = strip_outer_parentheses(name)
    without_measurement = strip_leading_measurement(stripped)
    if without_measurement:
        return without_measurement
    return stripped or name


def is_section_heading(value: str) -> bool:
    text = clean_text(value)
    if not text or not text.startswith(">>"):
        return False
    content = text.lstrip(">").strip()
    return bool(content) and content.endswith(":")


def strip_note_references(value: str | None) -> str:
    text = clean_text(value)
    if not text:
        return ""

    original = text
    pattern = re.compile(r"\([^)]*see notes?[^)]*\)\d*\)?", re.IGNORECASE)
    previous = None
    while text != previous:
        previous = text
        text = pattern.sub("", text)
        text = re.sub(r"\s+", " ", text).strip()

    if text != original:
        text = re.sub(r"\s+\d+\)$", ")", text)
        text = re.sub(r"\s+\d+$", "", text)
        text = re.sub(r"(?<=\b[A-Za-z])\s+\d+(?=\)|$)", "", text)
        text = re.sub(r"\(\s*\)", "", text)
        text = re.sub(r"\s+", " ", text).strip()

    return text


def rebalance_parenthetical_parts(name: str, preparation: str | None) -> tuple[str, str | None]:
    cleaned_name = clean_text(name)
    cleaned_preparation = clean_text(preparation) or None
    missing_closers = cleaned_name.count("(") - cleaned_name.count(")")

    while missing_closers > 0 and cleaned_preparation and cleaned_preparation.endswith(")"):
        cleaned_name = clean_text(f"{cleaned_name})")
        cleaned_preparation = clean_text(cleaned_preparation[:-1]) or None
        missing_closers -= 1

    if missing_closers > 0:
        cleaned_name = clean_text(f"{cleaned_name}{')' * missing_closers}")

    return cleaned_name, cleaned_preparation


def strip_unmatched_closing_parentheses(value: str | None) -> str | None:
    text = clean_text(value) or None
    if not text:
        return None

    excess_closers = text.count(")") - text.count("(")
    while excess_closers > 0 and text.endswith(")"):
        text = clean_text(text[:-1]) or None
        excess_closers -= 1

    return text


def strip_trailing_reference_digits(value: str | None) -> str | None:
    text = clean_text(value) or None
    if not text:
        return None

    text = re.sub(r"\s+\d+(?=\)|$)", "", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text or None


def split_ingredient_text(text: str) -> tuple[str | None, str | None, str, str | None]:
    original = clean_text(text)
    if not original:
        return None, None, "", None

    preparation = None
    base = original
    for separator in [",", " - ", " – "]:
        if separator in base:
            base, remainder = base.split(separator, 1)
            preparation = clean_text(remainder)
            break

    base = strip_outer_parentheses(base)
    tokens = normalize_ingredient_text(base).split()
    quantity_tokens: list[str] = []
    while tokens and (tokens[0] == "-" or is_amount_token(tokens[0])):
        token = tokens.pop(0)
        if token != "-":
            quantity_tokens.append(token)

    quantity = " ".join(quantity_tokens) or None
    unit = None
    if tokens and tokens[0].lower().rstrip(".") in KNOWN_UNITS:
        unit = tokens.pop(0).rstrip(".")

    if tokens and tokens[0].lower() == "of":
        tokens.pop(0)

    name = clean_ingredient_name(" ".join(tokens)) or clean_ingredient_name(original) or original
    name, preparation = rebalance_parenthetical_parts(name, preparation)
    return quantity, unit, name, preparation


def normalize_ingredient_record(ingredient: dict) -> dict:
    raw_parts = [ingredient.get("quantity"), ingredient.get("unit"), ingredient.get("name")]
    raw_text = clean_text(" ".join(part for part in raw_parts if clean_text(part)))
    quantity, unit, name, preparation = split_ingredient_text(raw_text or ingredient.get("name") or "")
    fallback_name = clean_ingredient_name(ingredient.get("normalized_name") or "")
    source_name = clean_text(ingredient.get("name") or "")
    source_preparation = clean_text(ingredient.get("preparation") or "") or None

    ingredient["name"] = name or clean_ingredient_name(ingredient.get("name") or "") or ingredient["name"]
    if source_name.startswith("(") and source_name.endswith(")") and fallback_name:
        ingredient["name"] = fallback_name
    ingredient["name"] = strip_unmatched_closing_parentheses(ingredient.get("name")) or ingredient["name"]
    ingredient["name"] = strip_trailing_reference_digits(ingredient.get("name")) or ingredient["name"]
    ingredient["quantity"] = quantity if quantity is not None else ingredient.get("quantity")
    ingredient["unit"] = unit if unit is not None else ingredient.get("unit")
    ingredient["preparation"] = preparation if preparation is not None else source_preparation
    ingredient["preparation"] = strip_note_references(ingredient.get("preparation")) or None
    ingredient["preparation"] = strip_unmatched_closing_parentheses(ingredient.get("preparation"))
    ingredient["preparation"] = strip_trailing_reference_digits(ingredient.get("preparation"))
    ingredient["name"], ingredient["preparation"] = rebalance_parenthetical_parts(
        ingredient["name"], ingredient.get("preparation")
    )
    ingredient["normalized_name"] = normalize_name(ingredient["name"]) or normalize_name(raw_text)
    ingredient["ingredient_id"] = stable_uuid(INGREDIENT_NAMESPACE, ingredient["normalized_name"])
    return ingredient


def load_json(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def dump_json(path: Path, payload: dict) -> None:
    text = json.dumps(payload, indent=2, ensure_ascii=True) + "\n"
    path.write_text(text, encoding="utf-8")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def recipe_relative_path(recipe_id: str) -> str:
    return f"recipes/by-id/{recipe_id}.json"


def manifest_file_name(sequence: int) -> str:
    return f"{sequence:010d}.json"


def catalog_generated_at(recipe_payloads: list[dict], fallback: str) -> str:
    updated_values = [payload.get("updated_at") for payload in recipe_payloads if payload.get("updated_at") is not None]
    if not updated_values:
        return fallback
    return timestamp_from_unix_seconds(max(updated_values))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Rebuild Pepper Meal Planner catalog metadata.")
    parser.add_argument(
        "--bump-sequence",
        action="store_true",
        help="Increment repo_sequence and publish a new manifest file.",
    )
    parser.add_argument(
        "--skip-validation",
        action="store_true",
        help="Skip JSON Schema validation when local validation dependencies are unavailable.",
    )
    return parser.parse_args()


def load_schema(schema_name: str) -> dict:
    if not VALIDATE_SCHEMAS:
        return {}
    if Draft202012Validator is None:
        raise SystemExit("missing dependency: install with 'python3 -m pip install -r requirements-dev.txt'")
    schema = load_json(SCHEMAS_DIR / schema_name)
    Draft202012Validator.check_schema(schema)
    return schema


def format_validation_error(error) -> str:
    location = ".".join(str(part) for part in error.absolute_path)
    if not location:
        location = "<root>"
    return f"{location}: {error.message}"


def validate_payload(payload: dict, schema_name: str, source_name: str) -> None:
    if not VALIDATE_SCHEMAS:
        return
    validator = Draft202012Validator(load_schema(schema_name))
    errors = sorted(validator.iter_errors(payload), key=lambda item: list(item.absolute_path))
    if not errors:
        return

    details = "\n".join(format_validation_error(error) for error in errors[:5])
    raise SystemExit(f"schema validation failed for {source_name}:\n{details}")


def canonicalize_recipe(recipe: dict) -> tuple[dict, list[dict]]:
    canonical = deepcopy(recipe)
    slug = canonical["slug"]
    canonical["$schema"] = "../../schemas/recipe.schema.json"
    canonical["schema_version"] = SCHEMA_VERSION
    canonical["id"] = stable_uuid(RECIPE_NAMESPACE, slug)

    for category in canonical.get("categories", []):
        category["id"] = stable_uuid(CATEGORY_NAMESPACE, category["slug"])

    for tag in canonical.get("tags", []):
        tag["id"] = stable_uuid(TAG_NAMESPACE, tag["slug"])

    normalized_ingredients: list[dict] = []
    review_entries: list[dict] = []
    for ingredient in canonical.get("ingredients", []):
        normalize_ingredient_record(ingredient)
        result = canonicalize_ingredient_entry(canonical, ingredient)
        for normalized in result.ingredients:
            normalized["position"] = len(normalized_ingredients) + 1
            normalized_ingredients.append(normalized)
        review_entries.extend(result.review_entries)

    canonical["ingredients"] = normalized_ingredients

    return canonical, review_entries


def sort_recipe_files() -> list[Path]:
    return sorted(RECIPES_DIR.glob("*.json"))


def collect_canonical_recipes() -> list[CanonicalRecipe]:
    canonical_recipes: list[CanonicalRecipe] = []

    for recipe_path in sort_recipe_files():
        canonical, review_entries = canonicalize_recipe(load_json(recipe_path))
        canonical_path = RECIPES_DIR / f"{canonical['id']}.json"
        validate_payload(canonical, "recipe.schema.json", str(recipe_path.relative_to(REPO_ROOT)))
        canonical_recipes.append(
            CanonicalRecipe(
                source_path=recipe_path,
                output_path=canonical_path,
                payload=canonical,
                review_entries=review_entries,
            )
        )

    return canonical_recipes


def rewrite_recipe_files(canonical_recipes: list[CanonicalRecipe]) -> list[Path]:
    rewritten_paths: list[Path] = []

    for recipe in canonical_recipes:
        dump_json(recipe.output_path, recipe.payload)
        rewritten_paths.append(recipe.output_path)
        if recipe.output_path != recipe.source_path and recipe.source_path.exists():
            recipe.source_path.unlink()

    return sorted(rewritten_paths)


def build_indexes(recipe_paths: list[Path], generated_at: str, repo_sequence: int) -> tuple[list[RecipeSummary], dict, dict, dict, dict]:
    recipe_summaries: list[RecipeSummary] = []
    category_records: dict[str, dict] = {}
    tag_records: dict[str, dict] = {}
    ingredient_records: dict[str, dict] = {}
    category_counts: Counter[str] = Counter()
    tag_counts: Counter[str] = Counter()
    ingredient_counts: Counter[str] = Counter()

    for recipe_path in recipe_paths:
        recipe = load_json(recipe_path)
        file_hash = sha256_file(recipe_path)
        category_slugs = [category["slug"] for category in recipe.get("categories", [])]
        tag_slugs = [tag["slug"] for tag in recipe.get("tags", [])]
        dietary_labels = recipe.get("dietary_labels", [])
        allergens = recipe.get("allergens", [])
        ingredient_names = [ingredient["normalized_name"] for ingredient in recipe.get("ingredients", [])]
        total_time_minutes = sum(
            value or 0
            for value in [
                recipe.get("preparation_time_minutes"),
                recipe.get("cooking_time_minutes"),
                recipe.get("rest_time_minutes"),
            ]
        ) or None

        recipe_summaries.append(
            RecipeSummary(
                recipe_id=recipe["id"],
                slug=recipe["slug"],
                name=recipe["name"],
                recipe_type=recipe.get("recipe_type"),
                brief_description=recipe.get("brief_description"),
                cuisine=recipe.get("cuisine"),
                image_url=recipe.get("image_url"),
                difficulty=recipe.get("difficulty"),
                servings=recipe.get("servings"),
                cooking_time_minutes=recipe.get("cooking_time_minutes"),
                preparation_time_minutes=recipe.get("preparation_time_minutes"),
                rest_time_minutes=recipe.get("rest_time_minutes"),
                total_time_minutes=total_time_minutes,
                category_slugs=category_slugs,
                tag_slugs=tag_slugs,
                dietary_labels=dietary_labels,
                allergens=allergens,
                ingredient_names=ingredient_names,
                instruction_step_count=len(recipe.get("instructions", [])),
                updated_at=recipe["updated_at"],
                revision=recipe["revision"],
                recipe_path=recipe_relative_path(recipe["id"]),
                file_sha256=file_hash,
            )
        )

        for category in recipe.get("categories", []):
            category_counts[category["slug"]] += 1
            category_records[category["slug"]] = {
                "id": category["id"],
                "slug": category["slug"],
                "name": category["name"],
                "description": category.get("description"),
            }

        for tag in recipe.get("tags", []):
            tag_counts[tag["slug"]] += 1
            tag_records[tag["slug"]] = {
                "id": tag["id"],
                "slug": tag["slug"],
                "name": tag["name"],
                "color": tag.get("color"),
            }

        for ingredient in recipe.get("ingredients", []):
            ingredient_counts[ingredient["normalized_name"]] += 1
            ingredient_records[ingredient["normalized_name"]] = {
                "id": ingredient["ingredient_id"],
                "name": ingredient["name"],
                "normalized_name": ingredient["normalized_name"],
            }

    recipes_index = {
        "$schema": "../schemas/recipes-index.schema.json",
        "schema_version": SCHEMA_VERSION,
        "generated_at": generated_at,
        "repo_sequence": repo_sequence,
        "recipes": [
            {
                "id": summary.recipe_id,
                "slug": summary.slug,
                "name": summary.name,
                "recipe_type": summary.recipe_type,
                "brief_description": summary.brief_description,
                "cuisine": summary.cuisine,
                "image_url": summary.image_url,
                "difficulty": summary.difficulty,
                "servings": summary.servings,
                "cooking_time_minutes": summary.cooking_time_minutes,
                "preparation_time_minutes": summary.preparation_time_minutes,
                "rest_time_minutes": summary.rest_time_minutes,
                "total_time_minutes": summary.total_time_minutes,
                "category_slugs": summary.category_slugs,
                "tag_slugs": summary.tag_slugs,
                "dietary_labels": summary.dietary_labels,
                "allergens": summary.allergens,
                "ingredient_names": summary.ingredient_names,
                "instruction_step_count": summary.instruction_step_count,
                "updated_at": summary.updated_at,
                "revision": summary.revision,
            }
            for summary in sorted(recipe_summaries, key=lambda item: (item.name.lower(), item.slug))
        ],
    }

    categories_index = {
        "$schema": "../schemas/categories-index.schema.json",
        "schema_version": SCHEMA_VERSION,
        "generated_at": generated_at,
        "repo_sequence": repo_sequence,
        "categories": [
            {
                **category_records[slug],
                "recipe_count": category_counts[slug],
            }
            for slug in sorted(category_records)
        ],
    }

    tags_index = {
        "$schema": "../schemas/tags-index.schema.json",
        "schema_version": SCHEMA_VERSION,
        "generated_at": generated_at,
        "repo_sequence": repo_sequence,
        "tags": [
            {
                **tag_records[slug],
                "recipe_count": tag_counts[slug],
            }
            for slug in sorted(tag_records)
        ],
    }

    ingredients_index = {
        "$schema": "../schemas/ingredients-index.schema.json",
        "schema_version": SCHEMA_VERSION,
        "generated_at": generated_at,
        "repo_sequence": repo_sequence,
        "ingredients": [
            {
                **ingredient_records[name],
                "recipe_count": ingredient_counts[name],
            }
            for name in sorted(ingredient_records)
        ],
    }

    validate_payload(recipes_index, "recipes-index.schema.json", "recipes/v1/indexes/recipes.index.json")
    validate_payload(
        categories_index,
        "categories-index.schema.json",
        "recipes/v1/indexes/categories.index.json",
    )
    validate_payload(tags_index, "tags-index.schema.json", "recipes/v1/indexes/tags.index.json")
    validate_payload(
        ingredients_index,
        "ingredients-index.schema.json",
        "recipes/v1/indexes/ingredients.index.json",
    )

    return recipe_summaries, recipes_index, categories_index, tags_index, ingredients_index


def load_release() -> dict:
    if RELEASE_FILE.exists():
        return load_json(RELEASE_FILE)
    return {
        "$schema": "./schemas/release.schema.json",
        "schema_version": SCHEMA_VERSION,
        "catalog_version": CATALOG_VERSION,
        "repo_sequence": 1,
        "generated_at": utc_timestamp(),
        "minimum_supported_client_version": MINIMUM_SUPPORTED_CLIENT_VERSION,
        "indexes": {},
        "latest_manifest": {},
    }


def write_index_files(recipes_index: dict, categories_index: dict, tags_index: dict, ingredients_index: dict) -> None:
    dump_json(INDEXES_DIR / "recipes.index.json", recipes_index)
    dump_json(INDEXES_DIR / "categories.index.json", categories_index)
    dump_json(INDEXES_DIR / "tags.index.json", tags_index)
    dump_json(INDEXES_DIR / "ingredients.index.json", ingredients_index)


def build_ingredient_review_index(canonical_recipes: list[CanonicalRecipe], generated_at: str, repo_sequence: int) -> dict:
    entries: list[dict] = []
    for recipe in canonical_recipes:
        entries.extend(recipe.review_entries)
    review_index = {
        "$schema": f"../schemas/{INGREDIENT_REVIEW_SCHEMA}",
        "schema_version": SCHEMA_VERSION,
        "generated_at": generated_at,
        "repo_sequence": repo_sequence,
        "entries": sorted(
            entries,
            key=lambda item: (
                item["recipe_slug"],
                int(item["position"] or 0),
                item["original_text"].lower(),
                item["resolution"],
            ),
        ),
    }
    validate_payload(review_index, INGREDIENT_REVIEW_SCHEMA, "recipes/v1/indexes/ingredients.review.json")
    return review_index


def write_ingredient_review_file(review_index: dict) -> None:
    dump_json(INGREDIENT_REVIEW_FILE, review_index)


def rebuild_manifest(recipe_summaries: list[RecipeSummary], generated_at: str, repo_sequence: int) -> Path:
    manifest_path = MANIFESTS_DIR / manifest_file_name(repo_sequence)
    manifest = {
        "$schema": "../schemas/manifest.schema.json",
        "schema_version": SCHEMA_VERSION,
        "from_sequence": max(repo_sequence - 1, 0),
        "to_sequence": repo_sequence,
        "generated_at": generated_at,
        "upserts": [
            {
                "id": summary.recipe_id,
                "slug": summary.slug,
                "updated_at": summary.updated_at,
                "revision": summary.revision,
                "file_sha256": summary.file_sha256,
                "recipe_path": summary.recipe_path,
            }
            for summary in sorted(recipe_summaries, key=lambda item: item.slug)
        ],
        "removals": [],
    }
    validate_payload(manifest, "manifest.schema.json", str(manifest_path.relative_to(REPO_ROOT)))
    dump_json(manifest_path, manifest)
    return manifest_path


def rebuild_release(generated_at: str, repo_sequence: int, manifest_path: Path) -> None:
    release = load_release()
    release["$schema"] = "./schemas/release.schema.json"
    release["schema_version"] = SCHEMA_VERSION
    release["catalog_version"] = CATALOG_VERSION
    release["repo_sequence"] = repo_sequence
    release["generated_at"] = generated_at
    release.setdefault("minimum_supported_client_version", MINIMUM_SUPPORTED_CLIENT_VERSION)
    release["indexes"] = {
        "recipes": {
            "path": "indexes/recipes.index.json",
            "sha256": sha256_file(INDEXES_DIR / "recipes.index.json"),
        },
        "categories": {
            "path": "indexes/categories.index.json",
            "sha256": sha256_file(INDEXES_DIR / "categories.index.json"),
        },
        "tags": {
            "path": "indexes/tags.index.json",
            "sha256": sha256_file(INDEXES_DIR / "tags.index.json"),
        },
        "ingredients": {
            "path": "indexes/ingredients.index.json",
            "sha256": sha256_file(INDEXES_DIR / "ingredients.index.json"),
        },
    }
    release["latest_manifest"] = {
        "sequence": repo_sequence,
        "path": f"manifests/{manifest_path.name}",
        "sha256": sha256_file(manifest_path),
    }
    validate_payload(release, "release.schema.json", str(RELEASE_FILE.relative_to(REPO_ROOT)))
    dump_json(RELEASE_FILE, release)


def main() -> None:
    args = parse_args()
    global VALIDATE_SCHEMAS
    VALIDATE_SCHEMAS = not args.skip_validation
    release = load_release()
    repo_sequence = int(release.get("repo_sequence", 1))
    if args.bump_sequence:
        repo_sequence += 1
    fallback_generated_at = release.get("generated_at") or utc_timestamp()

    RECIPES_DIR.mkdir(parents=True, exist_ok=True)
    INDEXES_DIR.mkdir(parents=True, exist_ok=True)
    MANIFESTS_DIR.mkdir(parents=True, exist_ok=True)

    canonical_recipes = collect_canonical_recipes()
    recipe_paths = rewrite_recipe_files(canonical_recipes)
    if not recipe_paths:
        raise SystemExit("no recipe payloads found under recipes/v1/recipes/by-id")

    generated_at = catalog_generated_at(
        [canonical_recipe.payload for canonical_recipe in canonical_recipes],
        fallback_generated_at,
    )

    recipe_summaries, recipes_index, categories_index, tags_index, ingredients_index = build_indexes(
        recipe_paths,
        generated_at,
        repo_sequence,
    )
    write_index_files(recipes_index, categories_index, tags_index, ingredients_index)
    ingredient_review_index = build_ingredient_review_index(canonical_recipes, generated_at, repo_sequence)
    write_ingredient_review_file(ingredient_review_index)

    manifest_path = rebuild_manifest(recipe_summaries, generated_at, repo_sequence)
    rebuild_release(generated_at, repo_sequence, manifest_path)


if __name__ == "__main__":
    main()
