#!/usr/bin/env python3

from __future__ import annotations

import argparse
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from urllib.parse import urljoin, urlparse, urlunparse

from bs4 import BeautifulSoup

from panlasang_pinoy import DEFAULT_SELECTOR, fetch_html, find_recipe_container, load_json_ld_recipe


BASE_INDEX_URL = "https://panlasangpinoy.com/recipes/"
SITE_HOST = "panlasangpinoy.com"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Collect Panlasang Pinoy recipe URLs from the Recipe Index pages.",
    )
    parser.add_argument(
        "start_urls",
        nargs="*",
        default=[BASE_INDEX_URL],
        help="Recipe index page(s) to start crawling from.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("urls.txt"),
        help="Output text file for collected recipe URLs.",
    )
    parser.add_argument(
        "--selector",
        default=DEFAULT_SELECTOR,
        help="Recipe container selector used when validating candidate recipe pages.",
    )
    parser.add_argument(
        "--validate-ambiguous",
        action="store_true",
        help="Fetch ambiguous listing links and keep only those that resolve to real recipe pages.",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=8,
        help="Worker count used when validating candidate recipe pages.",
    )
    return parser.parse_args()


def canonicalize_url(url: str) -> str:
    parsed = urlparse(url)
    scheme = parsed.scheme or "https"
    netloc = parsed.netloc.lower()
    path = parsed.path or "/"
    if path != "/":
        path = path.rstrip("/") + "/"
    return urlunparse((scheme, netloc, path, "", "", ""))


def is_site_url(url: str) -> bool:
    hostname = urlparse(url).hostname or ""
    return hostname == SITE_HOST or hostname.endswith(f".{SITE_HOST}")


def is_index_page(url: str) -> bool:
    path = urlparse(url).path.rstrip("/")
    return path == "/recipes" or (
        path.startswith("/recipes/page/") and path.split("/")[-1].isdigit()
    )


def index_page_number(url: str) -> int | None:
    path = urlparse(url).path.rstrip("/")
    if path == "/recipes":
        return 1
    if path.startswith("/recipes/page/") and path.split("/")[-1].isdigit():
        return int(path.split("/")[-1])
    return None


def build_index_page_url(page_number: int) -> str:
    if page_number <= 1:
        return BASE_INDEX_URL
    return f"{BASE_INDEX_URL}page/{page_number}/"


def article_looks_like_recipe(article_classes: list[str]) -> bool:
    for class_name in article_classes:
        if not class_name.startswith("category-"):
            continue
        if class_name == "category-recipes" or class_name.endswith("-recipes"):
            return True
    return False


def extract_recipe_links(page_url: str) -> tuple[list[str], list[str], list[str]]:
    soup = BeautifulSoup(fetch_html(page_url), "html.parser")

    recipe_links: list[str] = []
    ambiguous_links: list[str] = []
    for article in soup.select("main article"):
        anchor = article.select_one("a[href]")
        if anchor is None:
            continue
        href = canonicalize_url(urljoin(page_url, anchor["href"]))
        if not is_site_url(href) or is_index_page(href):
            continue

        if article_looks_like_recipe(article.get("class") or []):
            recipe_links.append(href)
        else:
            ambiguous_links.append(href)

    next_pages: list[str] = []
    for anchor in soup.select('a[href].page-numbers, .pagination a[href], nav.pagination a[href]'):
        href = canonicalize_url(urljoin(page_url, anchor["href"]))
        if is_site_url(href) and is_index_page(href):
            next_pages.append(href)

    return dedupe(recipe_links), dedupe(ambiguous_links), dedupe(next_pages)


def dedupe(items: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        if item in seen:
            continue
        seen.add(item)
        result.append(item)
    return result


def looks_like_recipe(url: str, selector: str) -> bool:
    soup = BeautifulSoup(fetch_html(url), "html.parser")
    if load_json_ld_recipe(soup):
        return True
    return find_recipe_container(soup, selector) is not None


def validate_recipe_links(urls: list[str], selector: str, workers: int) -> list[str]:
    validated: dict[int, bool] = {}
    max_workers = max(1, workers)

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_index = {
            executor.submit(looks_like_recipe, url, selector): index
            for index, url in enumerate(urls)
        }
        for future in as_completed(future_to_index):
            index = future_to_index[future]
            url = urls[index]
            try:
                validated[index] = future.result()
            except Exception as exc:
                print(f"warning: failed to validate {url}: {exc}", file=sys.stderr)
                validated[index] = False

    return [url for index, url in enumerate(urls) if validated.get(index)]


def crawl_index(start_urls: list[str], workers: int) -> tuple[list[str], list[str]]:
    pending = dedupe([canonicalize_url(url) for url in start_urls])
    recipe_urls: list[str] = []
    ambiguous_urls: list[str] = []
    discovered_pages = set(pending)
    fetched_pages: set[str] = set()

    for page_url in pending:
        page_recipe_links, page_ambiguous_links, page_links = extract_recipe_links(page_url)
        recipe_urls.extend(page_recipe_links)
        ambiguous_urls.extend(page_ambiguous_links)

        fetched_pages.add(page_url)
        discovered_pages.update(page_links)

    max_page = max((index_page_number(url) or 1 for url in discovered_pages), default=1)
    all_pages = [build_index_page_url(page_number) for page_number in range(1, max_page + 1)]
    remaining_pages = [page_url for page_url in all_pages if page_url not in fetched_pages]

    max_workers = max(1, workers)
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_page = {
            executor.submit(extract_recipe_links, page_url): page_url
            for page_url in remaining_pages
        }
        for future in as_completed(future_to_page):
            page_url = future_to_page[future]
            try:
                page_recipe_links, page_ambiguous_links, _ = future.result()
            except Exception as exc:
                print(f"warning: failed to crawl index page {page_url}: {exc}", file=sys.stderr)
                continue

            recipe_urls.extend(page_recipe_links)
            ambiguous_urls.extend(page_ambiguous_links)

    return dedupe(recipe_urls), dedupe(ambiguous_urls)


def main() -> int:
    args = parse_args()
    urls, ambiguous_urls = crawl_index(args.start_urls, args.workers)
    if args.validate_ambiguous:
        urls.extend(validate_recipe_links(ambiguous_urls, args.selector, args.workers))

    urls = dedupe(urls)

    args.output.write_text("\n".join(urls) + "\n", encoding="utf-8")
    for url in urls:
        print(url)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
