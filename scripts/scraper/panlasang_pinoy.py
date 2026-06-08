#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
import mimetypes
import re
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup, Tag


REPO_ROOT = Path(__file__).resolve().parents[2]
CATALOG_NAMESPACE = uuid.UUID("8d7c8f42-d53a-4d2d-9d67-935eeea8d7c4")
RECIPE_NAMESPACE = uuid.uuid5(CATALOG_NAMESPACE, "recipe")
CATEGORY_NAMESPACE = uuid.uuid5(CATALOG_NAMESPACE, "category")
TAG_NAMESPACE = uuid.uuid5(CATALOG_NAMESPACE, "tag")
INGREDIENT_NAMESPACE = uuid.uuid5(CATALOG_NAMESPACE, "ingredient")
DEFAULT_CATALOG_RECIPES_DIR = REPO_ROOT / "recipes" / "v1" / "recipes" / "by-id"
DEFAULT_CATALOG_ASSETS_DIR = REPO_ROOT / "recipes" / "v1" / "assets" / "by-id"
DEFAULT_PUBLIC_REPO_URL = "https://github.com/ffimnsr/pepper-meal-recipes"
DEFAULT_PUBLIC_REPO_BRANCH = "master"
DEFAULT_SELECTOR = 'div[id^="wprm-recipe-container-"] div.oc-recipe-container'
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/137.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Cache-Control": "no-cache",
    "Pragma": "no-cache",
}
AMOUNT_TOKEN = re.compile(r"^(?:\d+(?:[./]\d+)?|\d+\s+\d+/\d+|[¼½¾⅐⅑⅒⅓⅔⅕⅖⅗⅘⅙⅚⅛⅜⅝⅞]|to)$")
TIME_LABELS = {
    "prep": "preparation_time_minutes",
    "preparation": "preparation_time_minutes",
    "cook": "cooking_time_minutes",
    "cooking": "cooking_time_minutes",
    "rest": "rest_time_minutes",
    "resting": "rest_time_minutes",
    "marinating": "rest_time_minutes",
    "marinade": "rest_time_minutes",
    "total": None,
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


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Scrape Panlasang Pinoy recipe pages into PMP recipe JSON.")
    parser.add_argument("urls", nargs="+", help="Recipe URL(s) to scrape.")
    parser.add_argument(
        "--output-dir",
        type=Path,
        help="Optional directory to write one <slug>.json file per scraped URL.",
    )
    parser.add_argument(
        "--selector",
        default=DEFAULT_SELECTOR,
        help="CSS selector for the recipe container fallback extraction.",
    )
    parser.add_argument(
        "--catalog-recipes-dir",
        type=Path,
        help=(
            "Optional catalog by-id directory to write catalog-ready files named by recipe ID. "
            f"Defaults to {DEFAULT_CATALOG_RECIPES_DIR} when --write-catalog is used."
        ),
    )
    parser.add_argument(
        "--write-catalog",
        action="store_true",
        help="Write catalog-ready files into the repo's recipes/v1/recipes/by-id flow.",
    )
    parser.add_argument(
        "--assets-dir",
        type=Path,
        help=(
            "Optional asset directory to store downloaded recipe images under <recipe-id>/cover.<ext>. "
            f"Defaults to {DEFAULT_CATALOG_ASSETS_DIR} when --write-catalog is used."
        ),
    )
    parser.add_argument(
        "--public-repo-url",
        default=DEFAULT_PUBLIC_REPO_URL,
        help="Base GitHub repository URL used to publish downloaded catalog assets.",
    )
    parser.add_argument(
        "--public-repo-branch",
        default=DEFAULT_PUBLIC_REPO_BRANCH,
        help="Git branch used when building published GitHub asset URLs.",
    )
    return parser.parse_args()


def stable_uuid(namespace: uuid.UUID, key: str) -> str:
    return str(uuid.uuid5(namespace, key))


def slug_from_url(url: str) -> str:
    path = urlparse(url).path.strip("/")
    if not path:
        raise ValueError(f"cannot derive slug from URL: {url}")
    return path.split("/")[-1]


def fetch_html(url: str) -> str:
    response = requests.get(url, headers=HEADERS, timeout=30)
    response.raise_for_status()
    return response.text


def fetch_bytes(url: str) -> tuple[bytes, str | None]:
    response = requests.get(url, headers=HEADERS, timeout=30)
    response.raise_for_status()
    return response.content, response.headers.get("Content-Type")


def clean_text(value: Any) -> str:
    if not value:
        return ""
    if isinstance(value, list):
        value = " ".join(clean_text(item) for item in value if clean_text(item))
    elif not isinstance(value, str):
        value = str(value)
    value = value.replace("\xa0", " ")
    value = re.sub(r"\s+", " ", value)
    return value.strip(" \t\r\n•▢")


def slugify(value: str) -> str:
    value = clean_text(value).lower()
    value = re.sub(r"[^a-z0-9]+", "-", value)
    return value.strip("-")


def normalize_name(value: str) -> str:
    value = clean_text(value).lower()
    value = re.sub(r"\([^)]*\)", "", value)
    value = re.sub(r"[^a-z0-9\s/-]", "", value)
    value = re.sub(r"\s+", " ", value)
    return value.strip(" -/")


def parse_iso_duration_to_minutes(value: str | None) -> int | None:
    if not value:
        return None
    match = re.fullmatch(r"P(?:\d+D)?(?:T(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?)?", value)
    if not match:
        return None
    hours = int(match.group(1) or 0)
    minutes = int(match.group(2) or 0)
    seconds = int(match.group(3) or 0)
    return hours * 60 + minutes + (1 if seconds else 0)


def parse_time_label_minutes(text: str) -> dict[str, int | None]:
    normalized = clean_text(text).lower()
    matches = re.findall(
        r"(prep|preparation|cook|cooking|rest|resting|marinating|marinade|total)"
        r"(?:\s+time)?\s*:?\s*(\d+\s*hours?|\d+\s*minutes?|\d+\s*mins?)",
        normalized,
    )
    parsed: dict[str, int | None] = {
        "preparation_time_minutes": None,
        "cooking_time_minutes": None,
        "rest_time_minutes": None,
    }
    for label, value in matches:
        minutes = 0
        hour_match = re.search(r"(\d+)\s*hours?", value)
        minute_match = re.search(r"(\d+)\s*(?:minutes?|mins?)", value)
        if hour_match:
            minutes += int(hour_match.group(1)) * 60
        if minute_match:
            minutes += int(minute_match.group(1))
        target = TIME_LABELS[label]
        if target:
            parsed[target] = minutes
    return parsed


def parse_servings(value: Any) -> int | None:
    if value is None:
        return None
    if isinstance(value, int):
        return value
    match = re.search(r"\d+", str(value))
    return int(match.group(0)) if match else None


def parse_iso_datetime_to_unix(value: str | None) -> int | None:
    if not value:
        return None
    try:
        return int(datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp())
    except ValueError:
        return None


def current_unix_time() -> int:
    return int(datetime.now(timezone.utc).timestamp())


def normalize_difficulty(value: Any) -> str | None:
    normalized = clean_text(value).lower()
    if normalized in {"easy", "medium", "hard", "expert"}:
        return normalized
    if normalized in {"beginner", "simple", "quick"}:
        return "easy"
    if normalized in {"intermediate", "moderate"}:
        return "medium"
    if normalized in {"advanced", "challenging"}:
        return "hard"
    if normalized in {"professional", "complex"}:
        return "expert"
    return None


def infer_difficulty(step_count: int, preparation_time_minutes: int | None, cooking_time_minutes: int | None, rest_time_minutes: int | None) -> str:
    active_time_minutes = (preparation_time_minutes or 0) + (cooking_time_minutes or 0)
    effective_total_time_minutes = active_time_minutes + ((rest_time_minutes or 0) + 3) // 4

    if step_count >= 14 or effective_total_time_minutes >= 150 or active_time_minutes >= 120:
        return "expert"
    if step_count >= 10 or effective_total_time_minutes >= 90 or active_time_minutes >= 75:
        return "hard"
    if step_count >= 6 or effective_total_time_minutes >= 40 or active_time_minutes >= 30:
        return "medium"
    return "easy"


def normalize_recipe_type(value: Any) -> str | None:
    normalized = slugify(clean_text(value))
    if not normalized:
        return None

    mapping = {
        "appetizer": "appetizer",
        "starter": "appetizer",
        "main": "main",
        "main-course": "main",
        "entree": "main",
        "side": "side",
        "side-dish": "side",
        "salad": "salad",
        "soup": "soup",
        "dessert": "dessert",
        "breakfast": "breakfast",
        "snack": "snack",
        "drink": "drink",
        "beverage": "drink",
    }
    return mapping.get(normalized)


def infer_recipe_type(title: str, description: str | None, categories: list[dict[str, Any]], tags: list[dict[str, Any]]) -> str | None:
    candidates = [title, description or ""]
    candidates.extend(item["name"] for item in categories)
    candidates.extend(item["name"] for item in tags)
    normalized_values = [slugify(value) for value in candidates if slugify(value)]
    combined = " ".join(normalized_values)

    if any(token in combined for token in ["salad"]):
        return "salad"
    if any(token in combined for token in ["appetizer", "starter"]):
        return "appetizer"
    if any(token in combined for token in ["soup", "broth", "bisque"]):
        return "soup"
    if any(token in combined for token in ["dessert", "cake", "cookie", "brownie", "sweet"]):
        return "dessert"
    if any(token in combined for token in ["breakfast"]):
        return "breakfast"
    if any(token in combined for token in ["snack"]):
        return "snack"
    if any(token in combined for token in ["drink", "beverage", "juice", "shake", "smoothie"]):
        return "drink"
    if any(token in combined for token in ["side-dish", "side"]):
        return "side"
    if any(value in {"main-course", "dinner", "lunch", "entree"} for value in normalized_values):
        return "main"
    return None


def infer_image_extension(image_url: str, content_type: str | None) -> str:
    path_suffix = Path(urlparse(image_url).path).suffix.lower()
    if path_suffix:
        return path_suffix

    if content_type:
        guessed = mimetypes.guess_extension(content_type.split(";", 1)[0].strip())
        if guessed:
            return ".jpg" if guessed == ".jpe" else guessed

    return ".jpg"


def download_recipe_image(assets_dir: Path, recipe: dict[str, Any]) -> Path | None:
    image_url = recipe.get("image_url")
    if not image_url:
        return None

    image_bytes, content_type = fetch_bytes(image_url)
    extension = infer_image_extension(image_url, content_type)
    recipe_assets_dir = assets_dir / recipe["id"]
    recipe_assets_dir.mkdir(parents=True, exist_ok=True)
    asset_path = recipe_assets_dir / f"cover{extension}"
    asset_path.write_bytes(image_bytes)
    return asset_path


def build_public_asset_url(asset_path: Path, repo_root: Path, public_repo_url: str, public_repo_branch: str) -> str:
    relative_path = asset_path.relative_to(repo_root).as_posix()
    repo_url = public_repo_url.rstrip("/")
    branch = public_repo_branch.strip("/")
    return f"{repo_url}/raw/{branch}/{relative_path}"


def first_recipe_object(payload: Any) -> dict[str, Any] | None:
    if isinstance(payload, dict):
        payload_type = payload.get("@type")
        if payload_type == "Recipe" or (isinstance(payload_type, list) and "Recipe" in payload_type):
            return payload
        if "@graph" in payload:
            return first_recipe_object(payload["@graph"])
    if isinstance(payload, list):
        for item in payload:
            found = first_recipe_object(item)
            if found:
                return found
    return None


def load_json_ld_recipe(soup: BeautifulSoup) -> dict[str, Any]:
    for script in soup.find_all("script", attrs={"type": "application/ld+json"}):
        text = script.string or script.get_text(" ", strip=True)
        if not text:
            continue
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            continue
        recipe = first_recipe_object(payload)
        if recipe:
            return recipe
    return {}


def find_recipe_container(soup: BeautifulSoup, selector: str) -> Tag | None:
    container = soup.select_one(selector)
    if container:
        return container
    return soup.select_one('div[id^="wprm-recipe-container-"]')


def extract_list_items(section: Tag | None) -> list[str]:
    if section is None:
        return []
    items: list[str] = []
    for node in section.select("li, p"):
        text = clean_text(node.get_text(" ", strip=True))
        if text:
            items.append(text)
    return dedupe_preserve_order(items)


def find_section_by_heading(container: Tag | None, heading_text: str) -> Tag | None:
    if container is None:
        return None
    target = clean_text(heading_text).lower()
    for heading in container.find_all(re.compile(r"^h[1-6]$")):
        if clean_text(heading.get_text(" ", strip=True)).lower() != target:
            continue
        wrapper = BeautifulSoup("<div></div>", "html.parser").div
        sibling = heading.find_next_sibling()
        while sibling:
            if getattr(sibling, "name", None) and re.fullmatch(r"h[1-6]", sibling.name or ""):
                break
            wrapper.append(sibling)
            sibling = heading.find_next_sibling()
        return wrapper
    return None


def dedupe_preserve_order(items: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        if item in seen:
            continue
        seen.add(item)
        result.append(item)
    return result


def split_text_values(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [clean_text(part) for part in re.split(r",|\|", value) if clean_text(part)]
    if isinstance(value, list):
        items: list[str] = []
        for item in value:
            items.extend(split_text_values(item))
        return items
    return [clean_text(value)] if clean_text(value) else []


def extract_meta_values(soup: BeautifulSoup, *keys: str) -> list[str]:
    values: list[str] = []
    for key in keys:
        tags = soup.find_all("meta", attrs={"property": key}) + soup.find_all("meta", attrs={"name": key})
        for tag in tags:
            values.extend(split_text_values(tag.get("content")))
    return dedupe_preserve_order([value for value in values if value])


def extract_breadcrumb_categories(soup: BeautifulSoup) -> list[str]:
    crumbs = soup.select('.breadcrumb a, nav[aria-label="breadcrumb"] a, .aioseo-breadcrumbs a')
    categories: list[str] = []
    for crumb in crumbs:
        label = clean_text(crumb.get_text(" ", strip=True))
        if not label or label.lower() in {"home", "recipes"}:
            continue
        label = re.sub(r"\s+recipes?$", "", label, flags=re.IGNORECASE)
        if label:
            categories.append(label)
    return dedupe_preserve_order(categories)


def build_category_payloads(recipe_ld: dict[str, Any], soup: BeautifulSoup) -> list[dict[str, Any]]:
    values = split_text_values(recipe_ld.get("recipeCategory"))
    values.extend(extract_meta_values(soup, "article:section", "parsely-section"))
    values.extend(extract_breadcrumb_categories(soup))
    return build_taxonomy_payloads(dedupe_preserve_order(values), CATEGORY_NAMESPACE)


def build_tag_payloads(recipe_ld: dict[str, Any], soup: BeautifulSoup, slug: str, title: str) -> list[dict[str, Any]]:
    values = split_text_values(recipe_ld.get("keywords"))
    values.extend(extract_meta_values(soup, "article:tag", "keywords", "parsely-tags"))

    filtered: list[str] = []
    excluded_slugs = {slugify(slug), slugify(title)}
    for value in dedupe_preserve_order(values):
        value_slug = slugify(value)
        if not value_slug or value_slug in excluded_slugs:
            continue
        filtered.append(value)
    return build_taxonomy_payloads(filtered, TAG_NAMESPACE)


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

    tokens = base.split()
    quantity_tokens: list[str] = []
    while tokens and AMOUNT_TOKEN.match(tokens[0].lower()):
        quantity_tokens.append(tokens.pop(0))

    quantity = " ".join(quantity_tokens) or None
    unit = None
    if tokens and tokens[0].lower().rstrip(".") in KNOWN_UNITS:
        unit = tokens.pop(0).rstrip(".")

    name = clean_text(" ".join(tokens)) or original
    return quantity, unit, name, preparation


def build_ingredient_payloads(items: list[str]) -> list[dict[str, Any]]:
    payloads: list[dict[str, Any]] = []
    for position, item in enumerate(items, start=1):
        quantity, unit, name, preparation = split_ingredient_text(item)
        normalized_name = normalize_name(name) or normalize_name(item)
        payloads.append(
            {
                "ingredient_id": stable_uuid(INGREDIENT_NAMESPACE, normalized_name),
                "name": name,
                "normalized_name": normalized_name,
                "quantity": quantity,
                "unit": unit,
                "preparation": preparation,
                "position": position,
            }
        )
    return payloads


def build_instruction_payloads(items: list[str]) -> list[dict[str, Any]]:
    return [
        {
            "position": position,
            "text": clean_text(item),
        }
        for position, item in enumerate(items, start=1)
        if clean_text(item)
    ]


def build_taxonomy_payloads(values: Any, namespace: uuid.UUID) -> list[dict[str, Any]]:
    items = split_text_values(values)
    payloads: list[dict[str, Any]] = []
    for item in dedupe_preserve_order([clean_text(item) for item in items if clean_text(item)]):
        slug = slugify(item)
        if not slug:
            continue
        payload = {
            "id": stable_uuid(namespace, slug),
            "slug": slug,
            "name": item,
        }
        if namespace == TAG_NAMESPACE:
            payload["color"] = None
        payloads.append(payload)
    return payloads


def extract_meta_content(soup: BeautifulSoup, *keys: str) -> str | None:
    for key in keys:
        tag = soup.find("meta", attrs={"property": key}) or soup.find("meta", attrs={"name": key})
        if tag and tag.get("content"):
            return clean_text(tag["content"])
    return None


def extract_time_block(container: Tag | None) -> dict[str, int | None]:
    if container is None:
        return {
            "preparation_time_minutes": None,
            "cooking_time_minutes": None,
            "rest_time_minutes": None,
        }
    text = clean_text(container.get_text(" ", strip=True))
    return parse_time_label_minutes(text)


def scrape_recipe(url: str, selector: str) -> dict[str, Any]:
    html = fetch_html(url)
    soup = BeautifulSoup(html, "html.parser")
    recipe_ld = load_json_ld_recipe(soup)
    container = find_recipe_container(soup, selector)
    if container is None and not recipe_ld:
        raise RuntimeError(f"recipe container not found for {url}")

    slug = slug_from_url(url)
    title = clean_text(recipe_ld.get("name")) or extract_meta_content(soup, "og:title", "twitter:title") or slug.replace("-", " ").title()
    description = clean_text(recipe_ld.get("description")) or extract_meta_content(soup, "description", "og:description")
    image_value = recipe_ld.get("image")
    if isinstance(image_value, list):
        image_url = next((item for item in image_value if isinstance(item, str) and item.startswith("http")), None)
    elif isinstance(image_value, dict):
        image_url = image_value.get("url")
    else:
        image_url = image_value
    image_url = image_url or extract_meta_content(soup, "og:image", "twitter:image")

    ingredient_items = [clean_text(item) for item in recipe_ld.get("recipeIngredient", []) if clean_text(str(item))]
    if not ingredient_items:
        ingredient_items = extract_list_items(find_section_by_heading(container, "Ingredients"))

    instruction_items: list[str] = []
    for entry in recipe_ld.get("recipeInstructions", []):
        if isinstance(entry, str):
            text = clean_text(entry)
        elif isinstance(entry, dict):
            text = clean_text(entry.get("text") or entry.get("name"))
        else:
            text = ""
        if text:
            instruction_items.append(text)
    if not instruction_items:
        instruction_items = extract_list_items(find_section_by_heading(container, "Instructions"))

    equipment_items = extract_list_items(find_section_by_heading(container, "Equipment"))
    note_items = extract_list_items(find_section_by_heading(container, "Notes"))

    times = {
        "preparation_time_minutes": parse_iso_duration_to_minutes(recipe_ld.get("prepTime")),
        "cooking_time_minutes": parse_iso_duration_to_minutes(recipe_ld.get("cookTime")),
        "rest_time_minutes": None,
    }
    if all(value is None for value in times.values()):
        times = extract_time_block(container)
    else:
        fallback_times = extract_time_block(container)
        for key, value in times.items():
            if value is None:
                times[key] = fallback_times[key]

    updated_at = parse_iso_datetime_to_unix(recipe_ld.get("dateModified") or recipe_ld.get("datePublished"))
    if updated_at is None:
        updated_at = current_unix_time()

    instructions = build_instruction_payloads(instruction_items)
    difficulty = normalize_difficulty(recipe_ld.get("difficulty") or recipe_ld.get("recipeDifficulty"))
    if difficulty is None:
        difficulty = infer_difficulty(
            step_count=len(instructions),
            preparation_time_minutes=times["preparation_time_minutes"],
            cooking_time_minutes=times["cooking_time_minutes"],
            rest_time_minutes=times["rest_time_minutes"],
        )

    categories = build_category_payloads(recipe_ld, soup)
    tags = build_tag_payloads(recipe_ld, soup, slug, title)
    recipe_type = normalize_recipe_type(recipe_ld.get("recipeType"))
    if recipe_type is None:
        recipe_type = infer_recipe_type(title, description, categories, tags)

    recipe = {
        "$schema": "../../schemas/recipe.schema.json",
        "schema_version": 1,
        "id": stable_uuid(RECIPE_NAMESPACE, slug),
        "slug": slug,
        "name": title,
        "recipe_type": recipe_type,
        "brief_description": description or None,
        "cuisine": clean_text(recipe_ld.get("recipeCuisine")) or None,
        "instructions": instructions,
        "servings": parse_servings(recipe_ld.get("recipeYield")),
        "cooking_time_minutes": times["cooking_time_minutes"],
        "preparation_time_minutes": times["preparation_time_minutes"],
        "rest_time_minutes": times["rest_time_minutes"],
        "difficulty": difficulty,
        "image_url": image_url,
        "video_url": None,
        "additional_media": [],
        "nutritional_information": recipe_ld.get("nutrition") if isinstance(recipe_ld.get("nutrition"), dict) else None,
        "dietary_labels": [],
        "allergens": [],
        "equipment": equipment_items,
        "ingredients": build_ingredient_payloads(ingredient_items),
        "categories": categories,
        "tags": tags,
        "related_recipe_ids": [],
        "recipe_notes": note_items,
        "storage_notes": [],
        "source": {
            "name": "Panlasang Pinoy",
            "url": url,
        },
        "public": True,
        "updated_at": updated_at,
        "revision": 1,
    }

    if not recipe["ingredients"]:
        raise RuntimeError(f"no ingredients found for {url}")
    if not recipe["instructions"]:
        raise RuntimeError(f"no instructions found for {url}")

    return recipe


def write_output(output_dir: Path, recipe: dict[str, Any]) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / f"{recipe['slug']}.json"
    output_path.write_text(json.dumps(recipe, indent=2, ensure_ascii=True) + "\n", encoding="utf-8")


def write_catalog_output(recipes_dir: Path, recipe: dict[str, Any]) -> None:
    recipes_dir.mkdir(parents=True, exist_ok=True)
    output_path = recipes_dir / f"{recipe['id']}.json"
    output_path.write_text(json.dumps(recipe, indent=2, ensure_ascii=True) + "\n", encoding="utf-8")


def main() -> int:
    args = parse_args()
    recipes: list[dict[str, Any]] = []
    for url in args.urls:
        recipes.append(scrape_recipe(url, args.selector))

    if args.output_dir:
        for recipe in recipes:
            write_output(args.output_dir, recipe)

    if args.write_catalog or args.catalog_recipes_dir:
        catalog_recipes_dir = args.catalog_recipes_dir or DEFAULT_CATALOG_RECIPES_DIR
        assets_dir = args.assets_dir or DEFAULT_CATALOG_ASSETS_DIR
        for recipe in recipes:
            asset_path = download_recipe_image(assets_dir, recipe)
            if asset_path is not None:
                recipe["image_url"] = build_public_asset_url(
                    asset_path,
                    REPO_ROOT,
                    args.public_repo_url,
                    args.public_repo_branch,
                )
            write_catalog_output(catalog_recipes_dir, recipe)
    elif args.assets_dir:
        for recipe in recipes:
            asset_path = download_recipe_image(args.assets_dir, recipe)
            if asset_path is not None:
                recipe["image_url"] = build_public_asset_url(
                    asset_path,
                    REPO_ROOT,
                    args.public_repo_url,
                    args.public_repo_branch,
                )

    payload: Any = recipes[0] if len(recipes) == 1 else recipes
    json.dump(payload, sys.stdout, indent=2, ensure_ascii=True)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
