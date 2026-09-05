#!/usr/bin/env python3

"""Scrape Allrecipes recipe pages into PMP recipe JSON.

Allrecipes embeds a schema.org Recipe object as JSON-LD on every recipe
page, so the parser trusts that structured data first and falls back to
DOM extraction (breadcrumbs, meta tags) only for taxonomy fields the
structured data does not carry.
"""

from __future__ import annotations

import argparse
import html
import json
import re
import sys
from pathlib import Path
from typing import Any

from bs4 import BeautifulSoup

from panlasang_pinoy import (
    CATEGORY_NAMESPACE,
    DEFAULT_CATALOG_ASSETS_DIR,
    DEFAULT_CATALOG_RECIPES_DIR,
    DEFAULT_PUBLIC_REPO_BRANCH,
    DEFAULT_PUBLIC_REPO_URL,
    RECIPE_NAMESPACE,
    REPO_ROOT,
    TAG_NAMESPACE,
    build_ingredient_payloads,
    build_instruction_payloads,
    build_public_asset_url,
    build_taxonomy_payloads,
    clean_text,
    current_unix_time,
    dedupe_preserve_order,
    download_recipe_image,
    extract_meta_content,
    extract_meta_values,
    extract_time_block,
    fetch_html,
    infer_difficulty,
    infer_recipe_type,
    load_json_ld_recipe,
    load_urls_from_file,
    normalize_difficulty,
    normalize_recipe_type,
    parse_iso_datetime_to_unix,
    parse_iso_duration_to_minutes,
    parse_servings,
    print_progress,
    slug_from_url,
    slugify,
    split_text_values,
    stable_uuid,
    write_catalog_output,
    write_output,
)

BREADCRUMB_SELECTOR = (
    "ul.breadcrumbs a, "
    "ul.mntl-universal-breadcrumbs a, "
    'nav[aria-label="Breadcrumb"] a, '
    'nav[aria-label="breadcrumb"] a'
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Scrape Allrecipes recipe pages into PMP recipe JSON.")
    parser.add_argument("urls", nargs="*", help="Recipe URL(s) to scrape.")
    parser.add_argument(
        "--urls-file",
        type=Path,
        action="append",
        default=[],
        help="Optional text file with one recipe URL per line. Supports comments starting with #.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        help="Optional directory to write one <slug>.json file per scraped URL.",
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
    args = parser.parse_args()
    if not args.urls and not args.urls_file:
        parser.error("provide at least one recipe URL or --urls-file")
    return args


def unescape_ld_text(value: Any) -> str:
    """Clean a JSON-LD value, decoding any HTML entities the page embedded."""
    if not value:
        return ""
    if isinstance(value, list):
        value = " ".join(unescape_ld_text(item) for item in value if unescape_ld_text(item))
    else:
        value = str(value)
    return clean_text(html.unescape(value))


def extract_image_url(recipe_ld: dict[str, Any], soup: BeautifulSoup) -> str | None:
    image_value = recipe_ld.get("image")
    if isinstance(image_value, str):
        return image_value if image_value.startswith("http") else None
    if isinstance(image_value, dict):
        url = image_value.get("url") or image_value.get("contentUrl")
        return url if isinstance(url, str) and url.startswith("http") else None
    if isinstance(image_value, list):
        for item in image_value:
            if isinstance(item, str) and item.startswith("http"):
                return item
            if isinstance(item, dict):
                url = item.get("url") or item.get("contentUrl")
                if isinstance(url, str) and url.startswith("http"):
                    return url
    return extract_meta_content(soup, "og:image", "twitter:image")


def extract_video_url(recipe_ld: dict[str, Any]) -> str | None:
    video_value = recipe_ld.get("video")
    if isinstance(video_value, str):
        return video_value if video_value.startswith("http") else None
    if isinstance(video_value, dict):
        url = video_value.get("contentUrl") or video_value.get("embedUrl") or video_value.get("url")
        return url if isinstance(url, str) and url.startswith("http") else None
    if isinstance(video_value, list):
        for item in video_value:
            if isinstance(item, str) and item.startswith("http"):
                return item
            if isinstance(item, dict):
                url = item.get("contentUrl") or item.get("embedUrl") or item.get("url")
                if isinstance(url, str) and url.startswith("http"):
                    return url
    return None


def extract_breadcrumb_categories(soup: BeautifulSoup) -> list[str]:
    crumbs = soup.select(BREADCRUMB_SELECTOR)
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
    values.extend(extract_breadcrumb_categories(soup))
    return build_taxonomy_payloads(dedupe_preserve_order(values), CATEGORY_NAMESPACE)


def build_tag_payloads(recipe_ld: dict[str, Any], soup: BeautifulSoup, slug: str, title: str) -> list[dict[str, Any]]:
    values = split_text_values(recipe_ld.get("keywords"))
    values.extend(extract_meta_values(soup, "article:tag", "parsely-tags"))

    filtered: list[str] = []
    excluded_slugs = {slugify(slug), slugify(title)}
    for value in dedupe_preserve_order(values):
        value_slug = slugify(value)
        if not value_slug or value_slug in excluded_slugs:
            continue
        filtered.append(value)
    return build_taxonomy_payloads(filtered, TAG_NAMESPACE)


def default_unit_for_quantity(quantity: str | None) -> str:
    """Default count-based unit for ingredients without an explicit unit.

    Singular ``piece`` for a quantity of exactly 1, otherwise ``pieces``
    (uncounted, fractional, and ranged quantities are plural). Matches the
    unit vocabulary used by the existing curated catalog rows.
    """
    if quantity is None:
        return "pieces"
    total = 0.0
    try:
        for token in quantity.replace("-", " ").split():
            if "/" in token:
                numerator, _, denominator = token.partition("/")
                total += float(numerator) / float(denominator)
            else:
                total += float(token)
    except ValueError:
        return "pieces"
    return "piece" if total == 1 else "pieces"


def build_ingredients_with_default_units(items: list[str]) -> list[dict[str, Any]]:
    payloads = build_ingredient_payloads(items)
    for row in payloads:
        if row["unit"] is None:
            row["unit"] = default_unit_for_quantity(row["quantity"])
    return payloads


def extract_instruction_items(recipe_ld: dict[str, Any]) -> list[str]:
    items: list[str] = []
    for entry in recipe_ld.get("recipeInstructions", []):
        if isinstance(entry, str):
            text = unescape_ld_text(entry)
        elif isinstance(entry, dict):
            text = unescape_ld_text(entry.get("text") or entry.get("name"))
            if not text:
                for nested in entry.get("itemListElement", []):
                    if isinstance(nested, dict):
                        nested_text = unescape_ld_text(nested.get("text") or nested.get("name"))
                        if nested_text:
                            items.append(nested_text)
                continue
        else:
            text = ""
        if text:
            items.append(text)
    return items


def scrape_recipe(url: str) -> dict[str, Any]:
    html = fetch_html(url)
    soup = BeautifulSoup(html, "html.parser")
    recipe_ld = load_json_ld_recipe(soup)
    if not recipe_ld:
        raise RuntimeError(f"no JSON-LD Recipe object found for {url}")

    slug = slug_from_url(url)
    title = unescape_ld_text(recipe_ld.get("name")) or extract_meta_content(soup, "og:title", "twitter:title") or slug.replace("-", " ").title()
    description = unescape_ld_text(recipe_ld.get("description")) or extract_meta_content(soup, "description", "og:description")
    image_url = extract_image_url(recipe_ld, soup)
    video_url = extract_video_url(recipe_ld)

    # The catalog convention stores ingredient names in lowercase (the
    # Panlasang Pinoy payloads do the same, including brand names); the
    # source JSON-LD is typically Title Case, so fold it before parsing.
    ingredient_items = [unescape_ld_text(item).lower() for item in recipe_ld.get("recipeIngredient", []) if unescape_ld_text(item)]
    instruction_items = extract_instruction_items(recipe_ld)

    times = {
        "preparation_time_minutes": parse_iso_duration_to_minutes(recipe_ld.get("prepTime")),
        "cooking_time_minutes": parse_iso_duration_to_minutes(recipe_ld.get("cookTime")),
        "rest_time_minutes": None,
    }
    if all(value is None for value in times.values()):
        times = extract_time_block(soup.select_one("main"))
    else:
        fallback_times = extract_time_block(soup.select_one("main"))
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
        "cuisine": unescape_ld_text(recipe_ld.get("recipeCuisine")) or None,
        "instructions": instructions,
        "servings": parse_servings(recipe_ld.get("recipeYield")),
        "cooking_time_minutes": times["cooking_time_minutes"],
        "preparation_time_minutes": times["preparation_time_minutes"],
        "rest_time_minutes": times["rest_time_minutes"],
        "difficulty": difficulty,
        "image_url": image_url,
        "video_url": video_url,
        "additional_media": [],
        "nutritional_information": recipe_ld.get("nutrition") if isinstance(recipe_ld.get("nutrition"), dict) else None,
        "dietary_labels": [],
        "allergens": [],
        "equipment": [],
        "ingredients": build_ingredients_with_default_units(ingredient_items),
        "categories": categories,
        "tags": tags,
        "related_recipe_ids": [],
        "recipe_notes": [],
        "storage_notes": [],
        "source": {
            "name": "Allrecipes",
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


def main() -> int:
    args = parse_args()
    urls = list(args.urls)
    for path in args.urls_file:
        urls.extend(load_urls_from_file(path))
    urls = dedupe_preserve_order(urls)
    total_urls = len(urls)

    recipes: list[dict[str, Any]] = []
    failed_urls: list[tuple[str, str]] = []
    for index, url in enumerate(urls, start=1):
        print_progress(f"[{index}/{total_urls}] Scraping {url}")
        try:
            recipes.append(scrape_recipe(url))
        except Exception as exc:
            error_message = str(exc) or exc.__class__.__name__
            failed_urls.append((url, error_message))
            print_progress(f"[{index}/{total_urls}] Skipping {url}: {error_message}")

    if not recipes:
        print_progress("No recipes were scraped successfully.")
        if failed_urls:
            print_progress("Failed URLs:")
            for failed_url, error_message in failed_urls:
                print_progress(f"- {failed_url} :: {error_message}")
        return 1

    total_recipes = len(recipes)

    if args.output_dir:
        for index, recipe in enumerate(recipes, start=1):
            print_progress(f"[{index}/{total_recipes}] Writing output file for {recipe['slug']}")
            write_output(args.output_dir, recipe)

    if args.write_catalog or args.catalog_recipes_dir:
        catalog_recipes_dir = args.catalog_recipes_dir or DEFAULT_CATALOG_RECIPES_DIR
        assets_dir = args.assets_dir or DEFAULT_CATALOG_ASSETS_DIR
        for index, recipe in enumerate(recipes, start=1):
            print_progress(f"[{index}/{total_recipes}] Downloading assets and writing catalog file for {recipe['slug']}")
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
        for index, recipe in enumerate(recipes, start=1):
            print_progress(f"[{index}/{total_recipes}] Downloading assets for {recipe['slug']}")
            asset_path = download_recipe_image(args.assets_dir, recipe)
            if asset_path is not None:
                recipe["image_url"] = build_public_asset_url(
                    asset_path,
                    REPO_ROOT,
                    args.public_repo_url,
                    args.public_repo_branch,
                )

    print_progress(
        f"Completed {total_recipes}/{total_urls} recipe(s) successfully. Writing JSON output."
    )
    if failed_urls:
        print_progress(f"Skipped {len(failed_urls)} URL(s) due to errors:")
        for failed_url, error_message in failed_urls:
            print_progress(f"- {failed_url} :: {error_message}")

    payload: Any = recipes[0] if len(recipes) == 1 else recipes
    json.dump(payload, sys.stdout, indent=2, ensure_ascii=True)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())