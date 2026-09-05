#!/usr/bin/env python3

"""Collect Allrecipes recipe URLs by crawling the A-Z topic index.

Traversal follows the site's hierarchy:

    index  https://www.allrecipes.com/recipes-a-z-6735880   (topic links, A-Z)
    topic  https://www.allrecipes.com/recipes/<id>/<path>/  (recipe cards)
    recipe https://www.allrecipes.com/<slug>-recipe-<id>

Topic pages list recipe cards statically; pagination is not used by the
current site, but any pagination links (rel=next, ?page=, ?start=,
numbered pager widgets) found on a topic page are followed generically so
crawls keep working if that changes. Recipe URLs are deduplicated by
canonical URL and by the numeric recipe id, so the same recipe reached
through legacy (/recipe/<id>/...) or new-style (<slug>-recipe-<id>) URLs
or repeated across topics is captured once.
"""

from __future__ import annotations

import argparse
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from urllib.parse import urljoin, urlparse, urlunparse

from bs4 import BeautifulSoup
from requests import HTTPError, RequestException

from panlasang_pinoy import fetch_html, load_json_ld_recipe

A_Z_INDEX_URL = "https://www.allrecipes.com/recipes-a-z-6735880"
SITE_HOST = "allrecipes.com"

TOPIC_PATH_RE = re.compile(r"^/recipes/(?P<id>\d+)/")
RECIPE_ID_RE = re.compile(r"(?:^|-)recipe-(?P<id>\d+)/?$")
LEGACY_RECIPE_PATH_RE = re.compile(r"^/recipe/(?P<id>\d+)/")
RECIPE_URL_RE = re.compile(r"/(?P<slug>[^/]+)-recipe-(?P<id>\d+)/?$")
PAGINATION_QUERY_RE = re.compile(r"[?&](?:page|start|offset|pn)=(\d+)")

CARD_SELECTOR = "main a[href]"
ADJACENT_PAGE_SELECTOR = (
    'link[rel="next"], a[rel="next"], '
    'a[aria-label="Next"], a[aria-label*="Next Page"], a[aria-label*="next page"]'
)
PAGINATION_WIDGET_SELECTOR = (
    "nav[aria-label*='page' i] a, [class*='pagination' i] a, [class*='paging' i] a"
)
QUERY_PAGE_SELECTOR = 'a[href*="?page="], a[href*="?start="], a[href*="?offset="], a[href*="?pn="]'

MAX_PAGES_PER_TOPIC = 100
FETCH_ATTEMPTS = 3
FETCH_RETRY_DELAY_SECONDS = 5


def fetch_with_retry(url: str) -> str:
    """Fetch a page, retrying transient 5xx/403 responses with backoff."""
    for attempt in range(1, FETCH_ATTEMPTS + 1):
        try:
            return fetch_html(url)
        except (RequestException, HTTPError) as exc:
            is_retryable = isinstance(exc, HTTPError) and exc.response is not None and (
                exc.response.status_code >= 500 or exc.response.status_code == 403
            )
            if attempt >= FETCH_ATTEMPTS or not is_retryable:
                raise
            delay = FETCH_RETRY_DELAY_SECONDS * attempt
            print(
                f"retrying {url} in {delay}s after {exc.__class__.__name__}",
                file=sys.stderr,
                flush=True,
            )
            time.sleep(delay)
    raise RuntimeError(f"unreachable: {url}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Collect Allrecipes recipe URLs by crawling the A-Z topic index "
            "and following index -> topic -> recipe links."
        ),
    )
    parser.add_argument(
        "start_urls",
        nargs="*",
        default=[A_Z_INDEX_URL],
        help=(
            "Index or topic page(s) to start crawling from. Defaults to the "
            "A-Z index page."
        ),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("urls.txt"),
        help="Output text file for collected recipe URLs.",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=8,
        help="Worker count used when fetching topics, pagination pages, and validation.",
    )
    parser.add_argument(
        "--max-pages",
        type=int,
        default=MAX_PAGES_PER_TOPIC,
        help="Maximum pagination pages to follow per topic.",
    )
    parser.add_argument(
        "--validate",
        action="store_true",
        help="Fetch each collected recipe URL and keep only pages with a JSON-LD Recipe object.",
    )
    return parser.parse_args()


def canonicalize_url(url: str) -> str:
    parsed = urlparse(url)
    scheme = parsed.scheme or "https"
    netloc = parsed.netloc.lower()
    path = parsed.path or "/"
    if path != "/":
        path = path.rstrip("/")
    return urlunparse((scheme, netloc, path, "", "", ""))


def is_site_url(url: str) -> bool:
    hostname = urlparse(url).hostname or ""
    return hostname == SITE_HOST or hostname.endswith(f".{SITE_HOST}")


def is_topic_url(url: str) -> bool:
    return bool(TOPIC_PATH_RE.match(urlparse(url).path))


def is_recipe_url(url: str) -> bool:
    path = urlparse(url).path
    return bool(RECIPE_URL_RE.search(path) or LEGACY_RECIPE_PATH_RE.match(path))


def recipe_id_from_url(url: str) -> str | None:
    path = urlparse(url).path
    match = RECIPE_ID_RE.search(path)
    if match:
        return match.group("id")
    match = LEGACY_RECIPE_PATH_RE.match(path)
    return match.group("id") if match else None


def topic_base_path(url: str) -> str:
    return urlparse(url).path.rstrip("/") or "/"


def pagination_page_number(url: str) -> int | None:
    match = PAGINATION_QUERY_RE.search(urlparse(url).query)
    return int(match.group(1)) if match else None


def dedupe(items: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        if item in seen:
            continue
        seen.add(item)
        result.append(item)
    return result


def extract_recipe_links(page_url: str, soup: BeautifulSoup) -> list[str]:
    recipe_links: list[str] = []
    for anchor in soup.select(CARD_SELECTOR):
        href = anchor.get("href")
        if not isinstance(href, str) or not href:
            continue
        canonical = canonicalize_url(urljoin(page_url, href))
        if not is_site_url(canonical) or not is_recipe_url(canonical):
            continue
        recipe_links.append(canonical)
    return dedupe(recipe_links)


def extract_topic_links(page_url: str, soup: BeautifulSoup) -> list[str]:
    topic_links: list[str] = []
    seen_ids: set[str] = set()
    for anchor in soup.select("main a[href]"):
        href = anchor.get("href")
        if not isinstance(href, str) or not href:
            continue
        canonical = canonicalize_url(urljoin(page_url, href))
        if not is_site_url(canonical):
            continue
        match = TOPIC_PATH_RE.match(urlparse(canonical).path)
        if not match:
            continue
        topic_id = match.group("id")
        if topic_id in seen_ids:
            continue
        seen_ids.add(topic_id)
        topic_links.append(canonical)
    return topic_links


def extract_pagination_links(page_url: str, soup: BeautifulSoup) -> list[str]:
    base_path = topic_base_path(page_url)
    adjacent: list[str] = []
    for link in soup.select(ADJACENT_PAGE_SELECTOR + ", " + PAGINATION_WIDGET_SELECTOR):
        href = link.get("href")
        if not isinstance(href, str) or not href:
            continue
        adjacent.append(urljoin(page_url, href))

    queried: list[str] = []
    for anchor in soup.select(QUERY_PAGE_SELECTOR):
        href = anchor.get("href")
        if not isinstance(href, str) or not href:
            continue
        queried.append(urljoin(page_url, href))

    candidates: list[str] = []
    for href in adjacent + queried:
        canonical = canonicalize_url(href)
        if not is_site_url(canonical) or not is_topic_url(canonical):
            continue
        if canonical == canonicalize_url(page_url):
            continue
        path = canonical_path(canonical)
        if not (path == base_path or path.startswith(base_path + "/")):
            # Do not let pagination wander into sibling topics.
            continue
        if href in queried and pagination_page_number(canonical) is None:
            continue
        candidates.append(canonical)
    return dedupe(candidates)


def canonical_path(url: str) -> str:
    return urlparse(url).path.rstrip("/") or "/"


def looks_like_recipe(url: str) -> bool:
    soup = BeautifulSoup(fetch_with_retry(url), "html.parser")
    return bool(load_json_ld_recipe(soup))


def crawl_topic(topic_url: str, max_pages: int, workers: int) -> tuple[list[str], str | None]:
    base = canonicalize_url(topic_url)
    try:
        page_recipe_links, page_pagination_links = crawl_page(base)
    except Exception as exc:
        return [], f"{base} :: {exc}"

    recipe_links = list(page_recipe_links)
    fetched_pages = {base}
    pending_pages: list[str] = []
    for href in page_pagination_links:
        if href in fetched_pages or len(fetched_pages) >= max_pages:
            continue
        pending_pages.append(href)
        fetched_pages.add(href)

    if not pending_pages:
        return dedupe(recipe_links), None

    max_workers = max(1, workers)
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_page = {
            executor.submit(crawl_page, page_url): page_url for page_url in pending_pages
        }
        for future in as_completed(future_to_page):
            page_url = future_to_page[future]
            try:
                page_recipe_links, _ = future.result()
            except Exception as exc:
                print(f"warning: failed to crawl pagination page {page_url}: {exc}", file=sys.stderr)
                continue
            recipe_links.extend(page_recipe_links)

    return dedupe(recipe_links), None


def crawl_page(url: str) -> tuple[list[str], list[str]]:
    """Fetch one index or topic page; return (recipe_links, next_page_links)."""
    soup = BeautifulSoup(fetch_with_retry(url), "html.parser")
    recipe_links = extract_recipe_links(url, soup)
    pagination_links = extract_pagination_links(url, soup) if is_topic_url(url) else []
    return recipe_links, pagination_links


def crawl_index(start_urls: list[str], max_pages: int, workers: int) -> tuple[list[str], list[str]]:
    topics: list[str] = []
    for start_url in start_urls:
        canonical = canonicalize_url(start_url)
        if is_topic_url(canonical):
            topics.append(canonical)
            continue
        soup = BeautifulSoup(fetch_html(canonical), "html.parser")
        topics.extend(extract_topic_links(canonical, soup))

    topics = dedupe(topics)
    total_topics = len(topics)

    all_recipes: list[str] = []
    failed_topics: list[str] = []
    completed = 0
    max_workers = max(1, workers)
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_topic = {
            executor.submit(crawl_topic, topic_url, max_pages, workers): topic_url
            for topic_url in topics
        }
        for future in as_completed(future_to_topic):
            topic_url = future_to_topic[future]
            completed += 1
            print(f"[{completed}/{total_topics}] Crawled topic {topic_url}", file=sys.stderr, flush=True)
            try:
                recipe_links, failure = future.result()
            except Exception as exc:
                failure = f"{topic_url} :: {exc}"
                recipe_links = []
            if failure:
                failed_topics.append(failure)
                print(f"warning: failed to crawl topic {failure}", file=sys.stderr)
            all_recipes.extend(recipe_links)

    return dedupe_recipes(all_recipes), failed_topics


def dedupe_recipes(recipe_urls: list[str]) -> list[str]:
    """Deduplicate by canonical URL then by numeric recipe id.

    The same recipe can appear under legacy (/recipe/<id>/...) and
    new-style (<slug>-recipe-<id>) URLs, or repeated across topics;
    prefer the first-seen URL and keep one entry per recipe id.
    """
    by_url = dedupe(recipe_urls)
    recipe_id_to_url: dict[str, str] = {}
    result: list[str] = []
    dropped = 0
    for url in by_url:
        recipe_id = recipe_id_from_url(url)
        if recipe_id is None:
            result.append(url)
            continue
        if recipe_id in recipe_id_to_url:
            dropped += 1
            continue
        recipe_id_to_url[recipe_id] = url
        result.append(url)
    if dropped:
        print(
            f"deduped {dropped} duplicate recipe URL(s) by recipe id",
            file=sys.stderr,
            flush=True,
        )
    return result


def validate_recipe_urls(urls: list[str], workers: int) -> list[str]:
    validated: dict[int, bool] = {}
    max_workers = max(1, workers)

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_index = {
            executor.submit(looks_like_recipe, url): index for index, url in enumerate(urls)
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


def main() -> int:
    args = parse_args()
    urls, failed_topics = crawl_index(args.start_urls, args.max_pages, args.workers)

    if args.validate:
        print(f"validating {len(urls)} recipe URL(s)...", file=sys.stderr, flush=True)
        urls = validate_recipe_urls(urls, args.workers)

    urls = dedupe(urls)

    args.output.write_text("\n".join(urls) + "\n", encoding="utf-8")
    for url in urls:
        print(url)
    print(f"Collected {len(urls)} recipe URL(s).", file=sys.stderr)
    if failed_topics:
        print(f"Skipped {len(failed_topics)} topic(s) due to errors:", file=sys.stderr)
        for failure in failed_topics:
            print(f"- {failure}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())