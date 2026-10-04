#!/usr/bin/env python3
"""Deterministic repair of Allrecipes-style ingredient parse corruption.

Allrecipes JSON-LD ingredient lines use packaging notation the generic
parser cannot handle, e.g.::

    2 (.25 ounce) packages dry yeast
    1 (15 ounce) can diced tomatoes, no salt added
    12 (4 inch) flour tortillas
    7 Ball® or Kerr® pint (16 oz) jars with lids and bands
    1 cup warm water (110 degrees F/45 degrees C)

The shared ``split_ingredient_text`` parses these into corrupted rows such
as ``name="ounce) packages dry yeast", quantity="2 (.25"``.

This module reconstructs the original line from an already-corrupted row
and re-parses it with targeted preprocessing rules:

* ``N (Q unit) packaging-noun``  -> ``N*Q unit`` total amount (e.g.
  ``2 (.25 ounce) packages`` -> ``0.5 ounce``).
* ``N (Q inch)``                -> ``N`` (the parenthesis is a size
  descriptor, not an amount).
* ``unit (Q unit2) packaging``  -> ``unit`` (e.g. ``pint (16 oz) jars``).
* temperature parentheticals    -> dropped.
* ``®™`` brand marks            -> dropped.
* stray unbalanced parentheses  -> dropped.

Rows that still look corrupt after re-parsing are reported as residuals in
the batch report (diagnostics, not a model stage).
"""
from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent / "scraper"))

from panlasang_pinoy import (  # noqa: E402
    INGREDIENT_NAMESPACE,
    KNOWN_UNITS,
    clean_ingredient_name,
    clean_text,
    is_amount_token,
    normalize_ingredient_text,
    normalize_name,
    split_ingredient_text,
    stable_uuid,
    strip_outer_parentheses,
)

# ── embedded measurements ────────────────────────────────────────────────────

EMBED_AMOUNT = r"(?:\d+(?:\.\d+)?|\d+/\d+|\d+\s+\d+/\d+)"
EMBED_UNIT = (
    r"(?:ozs?\.?|ounces?\.?|pounds?\.?|lbs?\.?|grams?\.?|gs?\.?|kilograms?\.?|kgs?\.?|"
    r"cups?\.?|tablespoons?\.?|tbsps?\.?|teaspoons?\.?|tsps?\.?|cloves?\.?|"
    r"pints?\.?|quarts?\.?|gallons?\.?|kg|bunches?|heads?|slices?|strips?|knobs?|count)"
)
# "plus 2 tablespoons butter" (remainder of "1/2 cup plus 2 tablespoons butter")
PLUS_LEAD_RE = re.compile(
    rf"^plus\s+(?P<amount>{EMBED_AMOUNT})(?:\s+to\s+{EMBED_AMOUNT})?\s+"
    rf"(?P<unit>{EMBED_UNIT})(?:\s+of\s+)?\b",
    re.IGNORECASE,
)
# "about 8 oz. self-rising flour" / "1 1/2 oz celery stalk"
EMBEDDED_LEAD_RE = re.compile(
    rf"^(?:about\s+)?(?P<amount>{EMBED_AMOUNT})(?:-{EMBED_AMOUNT})?(?:\s+to\s+{EMBED_AMOUNT})?\s+"
    rf"(?P<unit>{EMBED_UNIT})(?:\s+of\s+)?\b",
    re.IGNORECASE,
)
ARTICLE_LEAD_RE = re.compile(r"^(?:a few|an?)\s+", re.IGNORECASE)
TRAILING_NOTE_RE = re.compile(r"\s+(?:optional|for garnish|for serving|divided)\s*$", re.IGNORECASE)
# Mid-name measurement notes on rows that already carry structured
# quantities: "medium 1 1/2 oz. celery stalks" / "about 1 heaping cup 72
# dark chocolate chips". Stripped only when the row has quantity/unit set
# (otherwise the phrase may be the row's only amount), and never when the
# phrase follows "plus" ("brown sugar, plus 2 tablespoons butter" is a
# second add-on, not a note).
MID_MEASUREMENT_UNIT = (
    r"ozs?\.?|ounces?\.?|pounds?\.?|lbs?\.?|grams?\.?|gs?\.?|kilograms?\.?|kgs?\.?|"
    r"cups?\.?|tablespoons?\.?|tbsps?\.?|teaspoons?\.?|tsps?\.?|cans?\.?|"
    r"pints?\.?|quarts?\.?|gallons?\.?|kg"
)
# Amounts possibly joined with "and" ("2 and 1/2 lbs") or ranged.
EMBED_AMOUNT_UNIT = EMBED_AMOUNT + r"(?:\s+and\s+" + EMBED_AMOUNT + r")?"
MID_MEASUREMENT_RE = re.compile(
    rf"(?<!plus\s)(?<!or\s)(?<!and\s)\b(?:about\s+)?{EMBED_AMOUNT_UNIT}(?:\s+to\s+{EMBED_AMOUNT_UNIT})?\s+"
    rf"(?:(?:heaping|packed|tightly packed)\s+)?(?P<unit>{MID_MEASUREMENT_UNIT})(?:\s+of\s+)?",
    re.IGNORECASE,
)
EMPTY_BRACKET_RE = re.compile(r"\[\s*\]")


# Persistent per-row fixes for rows whose food was lost upstream (usually a
# comma-split moved the food into ``preparation``) or whose name is a broken
# "unit, plus ..." fragment.
CLEANUP_OVERRIDES: dict[tuple[str, int], dict[str, str | None]] = {
    # "1 cup slivered, toasted almonds" -> food landed in preparation
    ("20446751-ed17-52dc-8b63-f0d7e085fe86", 18): {"name": "slivered almonds", "preparation": "toasted"},
    # "1/2 cup crushed, sliced almonds" -> ditto
    ("6e5ec039-1803-571f-9545-d7ab7f923217", 11): {"name": "sliced almonds", "preparation": "crushed"},
    # "3/4 cup lightly packed, shredded sharp cheddar cheese"
    ("b202818c-797a-58a7-a31c-0fba7893489e", 3): {
        "name": "sharp cheddar cheese",
        "preparation": "lightly packed, shredded",
    },
    # "1/4 cup canned, diced tomatoes"
    ("29c01f26-2502-56f1-b942-fb85223da33e", 10): {"name": "canned tomato", "preparation": "diced"},
    # "8 quarts minced, cored apples" -> food and prep swapped
    ("2610ad0a-eed4-5ffc-82a8-d641b702ff4f", 2): {"name": "apple", "preparation": "cored, minced"},
    # "1/2 teaspoon salt, plus more to taste"
    ("61ae534b-e81a-527d-93de-b13c10dcf029", 15): {"name": "salt", "preparation": "plus more to taste"},
    # author note in a parenthetical: "2 quarts frying oil (I use canola or grapeseed)"
    ("56882fa7-345f-5ec1-ba4d-6651cf8c3d6e", 13): {
        "name": "canola oil",
        "preparation": "for frying, or grapeseed oil",
    },
    # "cup, drained, sliced pepperoncini peppers" -> food trapped in preparation
    ("6a540f06-8eab-5349-91ba-d34362f0b568", 3): {
        "name": "pepperoncini pepper",
        "preparation": "drained, sliced",
    },
    # "12.42-ounce candy bars" glued into "ouncedy bars"
    ("da6b05c6-e9d7-54c0-bc3c-bc0a5749b790", 1): {"name": "candy bars", "unit": "ounce"},
    # scraper joined two source lines: "oil for deep-frying" + "ranch dressing"
    ("3830912c-7f76-5dc2-a9b1-e1a71596b778", 9): {"name": "oil for deep frying", "preparation": None},
    # spurious "gallon" prefix on a row measured in cups
    ("ed88f1d3-0647-5f8b-81d3-b46655abfbe7", 3): {
        "name": "chilled hawaiian punch green berry rush juice drink",
    },
    # comma-split "unit, plus ..." fragments: quantity is the compound amount
    ("ab21d3b3-008a-5bd2-86d3-4bc18239f8b5", 3): {
        "name": "kosher salt",
        "quantity": "1 tablespoon plus 1 teaspoon",
        "unit": None,
        "preparation": "divided",
    },
    ("9e28b34b-5d09-5aa8-8e69-5c87e095c89e", 3): {
        "name": "kosher salt",
        "quantity": "2 tablespoons plus 1 1/2 teaspoons",
        "unit": None,
        "preparation": None,
    },
    ("7efad926-6299-5a57-8900-3e45ac8201d0", 1): {
        "name": "kosher salt",
        "quantity": "1 tablespoon plus 1/2 teaspoon",
        "unit": None,
        "preparation": "divided",
    },
    ("0026e558-5f0a-57b2-aa52-da77f6f5112b", 1): {
        "name": "kosher salt",
        "quantity": "2 tablespoons plus 2 teaspoons",
        "unit": None,
        "preparation": "divided",
    },
    ("84a54176-be2f-585e-8733-45eb7b7e0b26", 3): {
        "name": "kosher salt",
        "quantity": "2 tablespoons plus 1 teaspoon",
        "unit": None,
        "preparation": "divided",
    },
    ("1593025a-aafc-552d-8657-58f2f25736ba", 7): {
        "name": "yellow mustard",
        "quantity": "1 tablespoon plus 3/4 teaspoon",
        "unit": None,
        "preparation": "divided",
    },
}


# ── residual-name cleanup (newer scrapes) ───────────────────────────────────

# Volume units that end up leading the row name when the quantity was parsed
# but the unit slot kept the scraper default: "6 fluid ounces tequila",
# "3 quarts water", "1 pint cherry tomatoes". Weight units are absent on
# purpose ("pound cake" is a food, not a quantity leak).
UNIT_NAME_LEAD_RE = re.compile(
    r"^(?P<unit>fluid ounces?|fluid oz|milliliters?|liters?|pints?|quarts?|gallons?|"
    r"cups?|tablespoons?|teaspoons?)\s+(?P<rest>.+)$",
    re.IGNORECASE,
)
# One-word rests that must never be split off their unit word ("cup cake").
DENY_ONE_WORD = {"cake", "cupcake", "glass"}
# "cold water" / "warm milk" state notes on unit-lead rows ("quarts cold
# water" -> unit quart, name water, preparation cold).
TEMP_STATE_LEAD_RE = re.compile(
    r"^(?P<temp>warm|hot|cold|chilled|cool|boiling|iced|lukewarm)\s+("
    r"?P<liq>water|milk|broth|stock)\b",
    re.IGNORECASE,
)
# "quart vegetable oil for frying" -> "vegetable oil" (prep "for frying").
FOR_FRYING_RE = re.compile(
    r"\s+(?:for|to)\s+(?:deep[ -]?fry(?:ing)?|fried|frying)\s*$", re.IGNORECASE
)
# "large cloves garlic" / "fresh cloves of garlic" / "cloves garlic clove"
# -> name garlic, unit clove.
CLOVE_GARLIC_RE = re.compile(
    r"^(?:(?:extra[ -]large|large|medium|small|jumbo|fresh|organic)\s+)*"
    r"cloves?(?:\s+of)?\s+garlic(?:\s+clove)?$",
    re.IGNORECASE,
)
# "water 110 degrees f" / "water as needed" / "water at room temperature".
WATER_NOTE_RE = re.compile(r"^water\s+(?P<note>\S.*)$", re.IGNORECASE)
WATER_TEMP_RE = re.compile(r"^\d")
# "scoop vanilla protein powder" -> "vanilla protein powder".
SCOOP_LEAD_RE = re.compile(r"^scoops?\s+(?P<rest>\S.*)$", re.IGNORECASE)
# Typo'd "tablespoon"/"teaspoon" variants with the food after a space.
TABLEPOON_TYPO_RE = re.compile(
    r"^(?:tablepoons?|tablepsoons?|tablespooons?|tablspoons?|table spoon|"
    r"teasoons?|teaspooons?)\s+(?P<rest>.+)$",
    re.IGNORECASE,
)
# Preparation run leading the food after a typo unit ("teaspooon finely minced garlic").
TYPO_PREP_RUN_RE = re.compile(
    r"^(?P<mod>finely|thinly|roughly|coarsely|freshly)?\s*"
    r"(?P<verb>minced|chopped|diced|sliced|grated|crushed|peeled)\s+(?P<food>.+)$",
    re.IGNORECASE,
)
# Unit word glued to the food ("tablespoonketchup", "tablespoonrice vinegar").
MERGE_UNIT_RE = re.compile(
    r"^(?P<unit>tablespoons?|teaspoons?|tbsps?|tsps?)(?P<rest>[a-z].*)$",
    re.IGNORECASE,
)
# "all-purposeflour" -> "all-purpose flour" (hyphen + word merge).
GLUED_FLOUR_RE = re.compile(r"([a-z])flour$", re.IGNORECASE)
# "about 4 red onion slivers" / "about 30 pepperoni slices such as hormel
# from 1 6 oz pkg".
ABOUT_LEAD_RE = re.compile(r"^about\s+\d+(?:\.\d+)?\s+(?P<rest>.+)$", re.IGNORECASE)
FROM_PKG_RE = re.compile(
    r"\s+from\s+\d+(?:\s+[\d/]+)?(?:\s+(?:oz|ounce)s?)?\s+pkg\.?.*$", re.IGNORECASE
)
TRAILING_COUNT_NOUN_RE = re.compile(r"\s+(?P<noun>slivers?|slices?|pieces?|strips?)$", re.IGNORECASE)
# Junk prefixes from newer scrapes.
OPTIONAL_PLUS_AMOUNT_RE = re.compile(r"^(?:optional|plus)\s+\d+(?:\.\d+)?(?:/\s*\d+)?\s+", re.IGNORECASE)
INCH_MARK_RE = re.compile(r"^\d+(?:\.\d+)?(?:/\d+)?[\u201d\u201c\"]\s+")
SIZE_DASH_RE = re.compile(r"^(?:extra[ -]large|large|medium|small|jumbo)\s+\d+-\s+", re.IGNORECASE)
PACK_COUNT_RE = re.compile(r"^pack\s+(?:of\s+)?\d+\s+count\s+", re.IGNORECASE)
PACK_OF_IN_RE = re.compile(r"^pack\s+of\s+\d+\s+in\s+", re.IGNORECASE)
COUNT_IN_RE = re.compile(r"^\d+\s+in\s+", re.IGNORECASE)
DOT_LEAD_RE = re.compile(r"^(?:[\d.]+\.|\.)\s+")
BANG_FRACTION_RE = re.compile(r"^!/")
# "1/2 lime" with the quantity missing from the row -> move it into quantity.
LEAD_AMOUNT_RE = re.compile(
    r"^(?P<qty>\d+(?:\.\d+)?(?:/\s*\d+)?(?:\s+to\s+\d+(?:\.\d+)?(?:/\s*\d+)?)?)\s+(?P<rest>\S.*)$"
)
# "pkg frozen puff pastry" / "pk pkg kings hawaiian roll" / "i sheet ...".
PACKAGING_LEAD_RE = re.compile(
    r"^(?P<unit>packages|package|packets|packet|pkgs|pkg|pkt|pk|sheet|sheets|tray|trays|"
    r"bag|bags|bottle|bottles|box|boxes|"
    r"bunch|bunches|can|cans|carton|cartons|container|containers|envelope|envelopes|jar|jars|"
    r"tin|tins|dash|dashes|wrap|wraps)\.?\s+(?P<rest>.+)$",
    re.IGNORECASE,
)
PACKAGING_UNIT_ALIASES = {"pk": "package", "pkg": "package", "pkgs": "packages", "pkt": "packet"}
# % product names that are parse artifacts rather than legitimate percent
# products (compare the intentional "2% milk", "85% lean ground beef",
# "100% pure pumpkin" family kept by convention).
PERCENT_RENAMES: tuple[tuple[re.Pattern[str], str, str | None], ...] = (
    (re.compile(r"^1% fat milk$", re.IGNORECASE), "1% milk", None),
    (re.compile(r"^2% low-fat milk$", re.IGNORECASE), "low-fat milk", None),
    (re.compile(r"^2% reduced[-\s]?fat milk$", re.IGNORECASE), "reduced-fat milk", None),
    (re.compile(r"^2% plain greek yogurt$", re.IGNORECASE), "plain greek yogurt", None),
    (re.compile(r"^2% high protein milk$", re.IGNORECASE), "high protein milk", None),
    (re.compile(r"^8% fat lean ground chicken$", re.IGNORECASE), "lean ground chicken", None),
    (
        re.compile(r"^4% milkfat small curd cottage cheese$", re.IGNORECASE),
        "small curd cottage cheese",
        None,
    ),
    (re.compile(r"^100% natural hardwood lump charcoal$", re.IGNORECASE), "natural hardwood lump charcoal", None),
    (re.compile(r"^100% smoked beef sticks?$", re.IGNORECASE), "smoked beef stick", None),
    (re.compile(r"^70% cocoa dark chocolate$", re.IGNORECASE), "dark chocolate", None),
    (re.compile(r"^70% dark chocolate$", re.IGNORECASE), "dark chocolate", None),
    (re.compile(r"^70% to 80% dark chocolate$", re.IGNORECASE), "dark chocolate", None),
    (re.compile(r"^56% cacao semisweet chocolate bar$", re.IGNORECASE), "semisweet chocolate bar", None),
    (re.compile(r"^60% cacao bittersweet chocolate bar$", re.IGNORECASE), "bittersweet chocolate bar", None),
    (re.compile(r"^60% cacao dark chocolate chips?$", re.IGNORECASE), "dark chocolate chip", None),
    (re.compile(r"^85% lean ground beef chuc$", re.IGNORECASE), "85% lean ground beef", None),
    # food landed in preparation ("3 pounds 85% organic grass-fed lean ground beef")
    (re.compile(r"^85% organic$", re.IGNORECASE), "85% lean ground beef", "grass-fed organic"),
)
# "2 (12 ounce) cans evaporated milk" -> the "2%" is the can count.
EVAPORATED_MILK_RE = re.compile(r"^2% evaporated milk$", re.IGNORECASE)
# "93-lean ground turkey" / "93%-lean ground turkey" -> "93 lean ground turkey".
LEAN_HYPHEN_RE = re.compile(r"^(?P<n>\d+)(?:%)?-lean\s+(?P<rest>.+)$", re.IGNORECASE)

# OCR-style "i can ..." / "i cup ..." used for "1 can ..." in some sources.
LEAD_ONE_WORDS = (
    "can|cans|cups?|large|medium|small|sheets?|trays?|unbaked|frozen|refrigerated|"
    "packages?|pkgs?|pkt|jars?|bottles?|pounds?|lbs?|ounces?|oz|sticks?|cloves?|slices?|"
    "pieces?|quarts?|gallons?|liters?|ml|tablespoons?|teaspoons?|pinch|dash|heads?|bunch(?:es)?|"
    "sprigs?|tubs?|box(?:es)?|bags?|packets?|envelopes?|containers?|canisters?|inch(?:es)?|"
    "whole|strips?|chunks?|cubes?"
)
LEAD_I_RE = re.compile(rf"^i\s+(?=(?:{LEAD_ONE_WORDS})\b)", re.IGNORECASE)
# "3/4 inch pieces peeled butternut squash" -> strip the size phrase.
INCH_PIECE_LEAD_RE = re.compile(
    r"^\d+(?:\.\d+)?(?:\s+\d+/\d+)?(?:/\d+)?\s+inch(?:es)?\s+"
    r"(?:(?:pieces?|slices?|cubes?|chunks?|strips?|long)\s+)?",
    re.IGNORECASE,
)
# Trailing provenance notes ("... from 1 small squash").
TRAILING_FROM_RE = re.compile(r"\s+from\s+\d+(?:\s+\S+)*$", re.IGNORECASE)
# "12.42-ounce white cake mix" glued into "ounceed white cake mix".
GLUED_OUNCE_RE = re.compile(r"^(?P<unit>ounces?)(?P<glue>dy|ed)?\s*(?P<rest>[a-z].*)$", re.IGNORECASE)
# Leading processing runs that belong in the preparation field
# ("finely chopped onion" -> onion + finely chopped). Bare "ground" is
# deliberately absent ("ground beef"/"ground cinnamon" are products); it
# only moves with a modifier.
PREP_MODIFIER = r"(?:(?:finely|thinly|roughly|coarsely|freshly|well)[-\s]+)?"
PREP_ACTION = (
    r"chopped|diced|sliced|minced|grated|shredded|crushed|crumbled|julienned|cubed|"
    r"quartered|halved|peeled|seeded|cored|pitted|deveined|cleaned|trimmed|fried|grilled|"
    r"shaved|pre-shredded|bias[-\s]?sliced"
)
PREP_PEEL_LEAD_RE = re.compile(
    rf"^(?P<preparation>{PREP_MODIFIER}(?:{PREP_ACTION}))\s+(?P<name>\S.*)$",
    re.IGNORECASE,
)
# A state/size/measure word can hide the verb from the peel ("fresh minced
# parsley", "large chopped onion", "dash crushed red pepper"): the verb
# still moves to the preparation field, keeping the qualifier with the food.
PREP_LEAD_QUALIFIER = r"(?:fresh|raw|large|medium|small|jumbo|extra[ -]large|bunch(?:es)?|dash(?:es)?)"
BLOCKED_PREP_LEAD_RE = re.compile(
    rf"^(?P<qualifier>{PREP_LEAD_QUALIFIER})\s+"
    rf"(?P<preparation>{PREP_MODIFIER}(?:{PREP_ACTION}))\s+(?P<name>\S.*)$",
    re.IGNORECASE,
)
PREP_GROUND_LEAD_RE = re.compile(
    r"^(?P<preparation>(?:finely|freshly|coarsely)\s+ground(?:ed)?)\s+(?P<name>\S.*)$",
    re.IGNORECASE,
)
# Glued verb + food ("mincedginger") and glued cheeses ("mozzarellacheese").
GLUED_PREP_RE = re.compile(rf"^(?P<preparation>{PREP_ACTION})(?P<name>[a-z].*)$", re.IGNORECASE)
GLUED_CHEESE_RE = re.compile(r"([a-z])cheese$", re.IGNORECASE)
# Comma-split "unit, plus amount unit food, divided" fragments (comma optional).
COMMA_PLUS_RE = re.compile(
    r"^(?P<main_unit>tablespoons?|teaspoons?|cups?)\s*,?\s*plus\s+"
    r"(?P<amount>[\d/ .]+?)\s+(?P<extra_unit>tablespoons?\.?|teaspoons?\.?|tbsps?\.?|tsps?\.?|cups?\.?)\s+"
    r"(?P<food>[^,]+?)\s*(?:,\s*(?P<note>.*))?$",
    re.IGNORECASE,
)
COMMA_PLUS_TRAILING_RE = re.compile(
    r"\s+(?P<note>(?:divided|softened|melted|at room temperature)(?:\s+.*)?|plus more.*|or more.*|for sprinkling.*)$",
    re.IGNORECASE,
)
EXTRA_UNIT_WORDS = {
    "tbsp": "tablespoon",
    "tbsps": "tablespoons",
    "tsp": "teaspoon",
    "tsps": "teaspoons",
}
# Fat-percent prefixes that describe chocolate type, not a product family.
CHOCOLATE_PERCENT_RE = re.compile(
    r"^\d+(?:\.\d+)?%(?:\s+to\s+\d+(?:\.\d+)?%)?\s+"
    r"(?=dark chocolate|semisweet chocolate|bittersweet chocolate|milk chocolate)",
    re.IGNORECASE,
)
# A lone descriptor whose food was comma-split into the preparation.
DESCRIPTOR_SWAP_LEAD = {
    "skinless", "boneless", "frozen", "thawed", "refrigerated", "fresh",
    "chopped", "cooked", "cleaned", "plain", "diced", "sliced", "shredded",
    "minced", "grated", "crushed", "melted", "softened", "peeled", "halved",
    "quartered", "toasted", "roasted", "drained", "rinsed", "pitted", "seeded",
    "cored", "cubed",
}
PEEL_MODIFIERS = {"finely", "thinly", "roughly", "coarsely", "freshly", "well"}
PEEL_VERBS = {
    "chopped", "diced", "sliced", "minced", "peeled", "seeded", "grated",
    "crushed", "cubed", "halved", "quartered", "toasted", "roasted",
    "shredded", "trimmed", "pitted", "cleaned", "deveined", "drained",
    "rinsed", "melted", "softened", "cooked", "fried", "grilled", "cored",
}
PEEL_VERB_RE = re.compile(
    r"^(?:(?P<mod>finely|thinly|roughly|coarsely|freshly|well)\s+)?"
    r"(?P<verb>" + "|".join(sorted(PEEL_VERBS, key=len, reverse=True)) + r")\b\s*,?\s*",
    re.IGNORECASE,
)
# "cloves" measured in spoons is the ground spice, never a garlic clove.
SPICE_CLOVE_RE = re.compile(r"^cloves?$", re.IGNORECASE)
MEASURE_UNITS = {"teaspoon", "teaspoons", "tablespoon", "tablespoons", "pinch", "pinches", "dash", "dashes"}
# "a small bunch of cilantro" / "dash of garlic powder" lost their food.
PACK_OF_RE = re.compile(
    r"^(?:(?:small|large|medium|a|an)\s+)?(?P<noun>bunch|bunches|dash|hint|handful)\s+of\s+(?P<food>.+)$",
    re.IGNORECASE,
)
LEAD_OF_RE = re.compile(r"^of\s+(?P<food>.+)$", re.IGNORECASE)
# "oil as needed to seal edges" -> preparation note.
AS_NEEDED_RE = re.compile(r"^(?P<food>.+?)\s+as needed(?:\s+(?P<tail>.+))?$", re.IGNORECASE)
# "parsley for garnish" / "cooked rice for serving" / "... for optional garnish".
SERVING_NOTE_RE = re.compile(
    r"^(?P<food>.+?)\s+"
    r"(?P<note>for (?:(?:an?\s+)?optional\s+)?(?:garnish|serving|topping|dipping|drizzling)|to serve)$",
    re.IGNORECASE,
)
TRAILING_OPTIONAL_RE = re.compile(r"\s+optional$", re.IGNORECASE)

CLEANUP_LEAD_RE = re.compile(
    r"^(?:"
    r"(?:fluid ounces?|fluid oz|milliliters?|liters?|pints?|quarts?|gallons?|cups?|"
    r"tablespoons?|teaspoons?|scoops?)(?=\s|,)|"
    r"tablepoons?|tablepsoons?|tablespooons?|tablspoons?|table spoon(?=\s)|teasoons?|teaspooons?|"
    r"ounces?(?=[a-z])|(?:pkg|pk|pkt)s?\.?(?=\s)|"
    r"of\s+|dash\s+of\s+|(?:bunch|bunches|handful)\s+of\s+"
    r"|about\s+\d+|optional\s+\d+|plus\s+\d+|water\s+\d+|"
    r"water\s+(?:as needed|at room temperature)|pack\s+|!/(?=\d)|"
    r"(?:extra[ -]large|large|medium|small|jumbo)\s+\d+-|(?:tablespoons?|teaspoons?)(?=[a-z])"
    r")",
    re.IGNORECASE,
)


def is_cleanup_row(row: dict[str, Any]) -> bool:
    """A row whose name carries a residual cleanup pattern from the newer
    scrapes (unit-word lead, percent artifacts, scoop/typo leads, ...)."""
    name = clean_text(row.get("name"))
    if not name:
        return False
    if any(char in name for char in "%\u201d\u201c\""):
        return True
    if LEAN_HYPHEN_RE.match(name):
        return True
    if DOT_LEAD_RE.match(name) or COUNT_IN_RE.match(name) or FROM_PKG_RE.search(name):
        return True
    glued = GLUED_FLOUR_RE.search(name)
    if glued and " " not in name[: glued.end() - len("flour")]:
        return True
    if name.lower() in DESCRIPTOR_SWAP_LEAD and clean_text(row.get("preparation")):
        return True
    if (
        PREP_PEEL_LEAD_RE.match(name)
        or PREP_GROUND_LEAD_RE.match(name)
        or BLOCKED_PREP_LEAD_RE.match(name)
        or GLUED_PREP_RE.match(name)
    ):
        return True
    if re.search(r"\bfryin\b", name, re.IGNORECASE):
        return True
    if clean_text(row.get("preparation")):
        name_parts, name_food = _peel_lone_preparation(name)
        if name_parts and not name_food:
            return True
    if SPICE_CLOVE_RE.match(name) and (clean_text(row.get("unit")) or "").lower() in MEASURE_UNITS:
        return True
    if AS_NEEDED_RE.match(name) or PACK_OF_RE.match(name) or LEAD_OF_RE.match(name):
        return True
    if SERVING_NOTE_RE.match(name) or re.match(r"^\d+\s+serving size", name, re.IGNORECASE):
        return True
    if TRAILING_OPTIONAL_RE.search(name) or LEAD_I_RE.match(name):
        return True
    if INCH_PIECE_LEAD_RE.match(name) or (TRAILING_FROM_RE.search(name) and not FROM_PKG_RE.search(name)):
        return True
    return bool(CLOVE_GARLIC_RE.match(name) or CLEANUP_LEAD_RE.match(name))


def _peel_preparation(text: str) -> tuple[list[str], str]:
    """Split a preparation note into leading descriptor/verb runs and the
    food that follows: "peeled, and chopped cucumber" ->
    (["peeled", "chopped"], "cucumber"), "boneless chicken thighs, cut
    into strips" -> (["cut into strips"], "boneless chicken thighs").
    """
    parts: list[str] = []
    remaining = clean_text(text)
    while remaining:
        match = PEEL_VERB_RE.match(remaining)
        if match:
            label = clean_text(f"{match.group('mod') or ''} {match.group('verb')}")
            parts.append(label)
            remaining = remaining[match.end():].strip()
            continue
        connector = re.match(r"^(?:and|or)\s+", remaining, re.IGNORECASE)
        if connector:
            remaining = remaining[connector.end():].strip()
            continue
        break
    if not remaining:
        return parts, ""
    split = re.split(r"\s+-\s+", remaining, maxsplit=1)
    if len(split) == 2:
        parts.append(clean_text(split[1]))
        return parts, clean_text(split[0])
    while remaining:
        head, separator, tail = remaining.partition(",")
        if not separator:
            return parts, clean_text(remaining)
        head = clean_text(head)
        tail = clean_text(tail)
        # A duplicated descriptor leaked into the head segment
        # ("boneless, boneless chicken breasts, ..."): keep peeling.
        if head and tail.lower().startswith(head.lower() + " "):
            remaining = tail
            continue
        if tail:
            parts.append(tail)
        return parts, head
    return parts, remaining


SIZE_LEAD_RE = re.compile(r"^(?:large|medium|small)\s+", re.IGNORECASE)


def _peel_lone_preparation(name: str) -> tuple[list[str], str]:
    """``_peel_preparation`` for a name that is only a preparation run, with
    an optional size word in front ("cored", "large peeled" ->
    (["peeled"], ""))."""
    parts, food = _peel_preparation(name)
    if not parts and SIZE_LEAD_RE.match(name):
        parts, food = _peel_preparation(SIZE_LEAD_RE.sub("", name, count=1))
    return parts, food


def _merge_preparation(current: str | None, extra: str | None) -> str | None:
    if not extra:
        return current
    parts = [part for part in [current, extra] if part]
    return ", ".join(dict.fromkeys(parts)) or None


def fix_cleanup_row(row: dict[str, Any]) -> dict[str, Any] | None:
    """Repair names from the newer scrapes where the food is still present
    but buried under units, percents, packaging words, or typo prefixes.

    Returns the fixed row (ingredient_id recomputed) or ``None`` when the
    row does not match any cleanup rule.
    """
    name = clean_text(row.get("name"))
    if not name:
        return None
    quantity = clean_text(row.get("quantity"))
    unit = clean_text(row.get("unit"))
    preparation = clean_text(row.get("preparation"))

    def add_prep(extra: str | None) -> None:
        nonlocal preparation
        if extra:
            preparation = _merge_preparation(preparation, extra)

    def strip_unit_lead(text: str) -> tuple[str, str | None] | None:
        """'3 quarts water' -> ('water', 'quart'); None when not a unit lead."""
        match = UNIT_NAME_LEAD_RE.match(text)
        if not match:
            return None
        if unit and unit not in DEFAULT_COUNT_UNITS:
            return None  # the row already has a real unit: leave it alone
        rest = clean_text(match.group("rest"))
        if rest.lower().startswith("of "):
            rest = clean_text(rest[3:])
        tokens = rest.split()
        if len(tokens) == 1 and rest.lower() in DENY_ONE_WORD:
            return None  # "cup cake" is a food name, not a quantity + "cake"
        temp_match = TEMP_STATE_LEAD_RE.match(rest)
        if temp_match:
            add_prep(temp_match.group("temp").lower())
            rest = clean_text(temp_match.group("liq") + rest[temp_match.end():])
        frying = FOR_FRYING_RE.search(rest)
        if frying:
            add_prep(frying.group(0).strip())
            rest = clean_text(rest[: frying.start()])
        new_unit = match.group("unit").lower()
        return rest, UNIT_SINGULAR.get(new_unit, new_unit)

    # 1) junk prefixes that simply drop off
    previous = None
    while previous != name:
        previous = name
        name = LEAD_I_RE.sub("1 ", name, count=1)
        name = OPTIONAL_PLUS_AMOUNT_RE.sub("", name, count=1)
        name = INCH_MARK_RE.sub("", name, count=1)
        name = INCH_PIECE_LEAD_RE.sub("", name, count=1)
        name = SIZE_DASH_RE.sub("", name, count=1)
        name = PACK_COUNT_RE.sub("", name, count=1)
        name = PACK_OF_IN_RE.sub("", name, count=1)
        name = COUNT_IN_RE.sub("", name, count=1)
        name = DOT_LEAD_RE.sub("", name, count=1)
        name = BANG_FRACTION_RE.sub("1/", name, count=1)
        name = clean_text(name)

    # 1b) leading "of" / "bunch of" / "dash of" fragments, trailing notes
    pack_of = PACK_OF_RE.match(name)
    if pack_of:
        noun = pack_of.group("noun").lower()
        name = clean_text(pack_of.group("food"))
        if noun.startswith("bunch") and (not unit or unit in DEFAULT_COUNT_UNITS):
            unit = "bunch"
    lead_of = LEAD_OF_RE.match(name)
    if lead_of:
        name = clean_text(lead_of.group("food"))
    if TRAILING_OPTIONAL_RE.search(name):
        add_prep("optional")
        name = clean_text(TRAILING_OPTIONAL_RE.sub("", name))
    name = re.sub(r"\bfryin\b", "frying", name, flags=re.IGNORECASE)
    as_needed = AS_NEEDED_RE.match(name)
    if as_needed:
        tail = clean_text(as_needed.group("tail") or "")
        add_prep(f"as needed {tail}".strip())
        name = clean_text(as_needed.group("food"))
    serving_note = SERVING_NOTE_RE.match(name)
    if serving_note:
        add_prep(serving_note.group("note").lower())
        name = clean_text(serving_note.group("food"))
    from_note = TRAILING_FROM_RE.search(name)
    if from_note and not FROM_PKG_RE.search(name):
        head = clean_text(name[: from_note.start()])
        if head:
            add_prep(clean_text(from_note.group(0).strip()))
            name = head

    # 2) move a leading amount into quantity when the row lost it
    if not quantity:
        amount_match = LEAD_AMOUNT_RE.match(name)
        if amount_match:
            quantity = re.sub(r"/\s+", "/", amount_match.group("qty"))
            name = clean_text(amount_match.group("rest"))

    # 2b) packaging prefix ("1 can chunk chicken", "pkg. chopped romaine",
    # "pk pkg kings hawaiian")
    while True:
        pack_match = PACKAGING_LEAD_RE.match(name)
        if not pack_match:
            break
        rest = clean_text(pack_match.group("rest"))
        if rest.lower().startswith("of "):
            rest = clean_text(rest[3:])
        if not normalize_name(rest):
            break
        name = rest
        if not unit or unit in DEFAULT_COUNT_UNITS:
            unit = PACKAGING_UNIT_ALIASES.get(pack_match.group("unit").lower(), pack_match.group("unit").lower())
    name = re.sub(r"^\d+\s+serving size\s+", "", name, flags=re.IGNORECASE)

    # 1d) leading processing runs belong in preparation
    # ("finely chopped onion" -> onion + finely chopped). A leading state or
    # size word ("fresh minced parsley", "large chopped onion") hides the
    # verb from the pattern: keep the qualifier with the food and move only
    # the verb.
    while True:
        prep_lead = PREP_PEEL_LEAD_RE.match(name) or PREP_GROUND_LEAD_RE.match(name)
        if prep_lead:
            rest = clean_text(prep_lead.group("name"))
            lowered = rest.lower()
            if not rest or lowered in {"and", "or"} or lowered.startswith(("and ", "or ")):
                break
            add_prep(prep_lead.group("preparation"))
            name = rest
            continue
        blocked = BLOCKED_PREP_LEAD_RE.match(name)
        if not blocked:
            break
        rest = clean_text(blocked.group("name"))
        lowered = rest.lower()
        if not rest or lowered in {"and", "or"} or lowered.startswith(("and ", "or ")):
            break
        add_prep(blocked.group("preparation"))
        name = clean_text(f"{blocked.group('qualifier').lower()} {rest}")
    glued_prep = GLUED_PREP_RE.match(name)
    if glued_prep and " " not in glued_prep.group("preparation"):
        rest = clean_text(glued_prep.group("name"))
        if rest and not rest[0].isdigit():
            add_prep(glued_prep.group("preparation"))
            name = rest
    name = GLUED_CHEESE_RE.sub(r"\1 cheese", name)

    # 3) percent artifacts and hyphenated lean products
    name = CHOCOLATE_PERCENT_RE.sub("", name)
    evaporated = EVAPORATED_MILK_RE.match(name)
    if evaporated:
        name = "evaporated milk"
        quantity = format_amount(parse_amount(quantity or "12") * 2)
    for pattern, target, extra_prep in PERCENT_RENAMES:
        if pattern.match(name):
            name = target
            add_prep(extra_prep)
            break
    lean = LEAN_HYPHEN_RE.match(name)
    if lean:
        name = f"{lean.group('n')} lean {clean_text(lean.group('rest'))}"

    # 4) clove-of-garlic family -> structured unit row
    if CLOVE_GARLIC_RE.match(name):
        name = "garlic"
        unit = "clove"
    elif SPICE_CLOVE_RE.match(name) and unit.lower() in MEASURE_UNITS:
        name = "ground cloves"

    # 5) water temperature / note rows
    water_match = WATER_NOTE_RE.match(name)
    if water_match:
        note = water_match.group("note")
        if WATER_TEMP_RE.match(note):
            add_prep("warm")
            name = "water"
        elif note.lower() == "as needed":
            add_prep("as needed")
            name = "water"
        elif note.lower() == "at room temperature":
            add_prep("room temperature")
            name = "water"

    # 6) "about N ..." amounts (approximate; the count is not structured)
    about_match = ABOUT_LEAD_RE.match(name)
    if about_match:
        rest = clean_text(about_match.group("rest"))
        rest = FROM_PKG_RE.sub("", rest)
        rest = clean_text(rest)
        count_match = TRAILING_COUNT_NOUN_RE.search(rest)
        if count_match:
            noun = count_match.group("noun").lower()
            rest = clean_text(rest[: count_match.start()])
            add_prep("slivered" if noun.startswith("sliver") else "sliced")
        name = rest or name
    # packaging note hanging off the name ("... such as hormel from 1 6 oz pkg")
    if FROM_PKG_RE.search(name):
        name = clean_text(FROM_PKG_RE.sub("", name))

    # 6b) comma-split "unit, plus amount unit food, divided" fragments
    comma_plus = COMMA_PLUS_RE.match(name)
    if comma_plus:
        main_unit = comma_plus.group("main_unit").lower()
        amount = clean_text(comma_plus.group("amount"))
        extra_unit = comma_plus.group("extra_unit").lower().rstrip(".")
        extra_unit = EXTRA_UNIT_WORDS.get(extra_unit, extra_unit)
        if re.fullmatch(r"\d+", amount) and int(amount) > 1 and extra_unit in {"tablespoon", "teaspoon"}:
            extra_unit += "s"
        food = clean_text(comma_plus.group("food"))
        note = clean_text(comma_plus.group("note") or "").strip(", ")
        trailing_note = ""
        trail_match = COMMA_PLUS_TRAILING_RE.search(food)
        if trail_match:
            trailing_note = clean_text(trail_match.group("note"))
            food = clean_text(food[: trail_match.start()])
        if food and normalize_name(food):
            quantity = (
                f"{quantity} {main_unit} plus {amount} {extra_unit}".strip()
                if quantity
                else f"{amount} {extra_unit}"
            )
            unit = None
            name = food
            if preparation and preparation.lower().lstrip().startswith("plus"):
                preparation = None
            if note:
                add_prep(note)
            if trailing_note:
                add_prep(trailing_note)

    # 6c) glued ounce rows ("ounceed white cake mix", "ouncecan tomato paste")
    glued_ounce = GLUED_OUNCE_RE.match(name)
    if glued_ounce and (not unit or unit in DEFAULT_COUNT_UNITS):
        rest = clean_text(glued_ounce.group("rest"))
        ounce_pack = PACKAGING_LEAD_RE.match(rest)
        if ounce_pack:
            rest = clean_text(ounce_pack.group("rest"))
            if rest.lower().startswith("of "):
                rest = clean_text(rest[3:])
        if normalize_name(rest):
            name = rest
            unit = "ounce"

    # 7) unit-word lead ("3 quarts water" -> unit quart)
    unit_fix = strip_unit_lead(name)
    if unit_fix:
        name, unit = unit_fix

    # 7b) a lone descriptor/prep word whose food landed in preparation
    if name.lower() in DESCRIPTOR_SWAP_LEAD and preparation:
        peeled_prep, food = _peel_preparation(preparation)
        if food and normalize_name(food):
            preparation = ", ".join([name.lower()] + peeled_prep)
            name = food
    elif preparation:
        # multi-word prep runs ("coarsely chopped, pitted kalamata olives")
        name_parts, name_food = _peel_lone_preparation(name)
        if name_parts and not name_food:
            peeled_prep, food = _peel_preparation(preparation)
            if food and normalize_name(food):
                preparation = ", ".join(name_parts + peeled_prep)
                name = food

    # 8) scoop / tablespoon-typo / glued-unit leads
    scoop_match = SCOOP_LEAD_RE.match(name)
    if scoop_match:
        rest = clean_text(scoop_match.group("rest"))
        if len(rest.split()) >= 2 and normalize_name(rest):
            name = rest
    typo_match = TABLEPOON_TYPO_RE.match(name)
    if typo_match:
        typo_word = name.split()[0].lower()
        rest = clean_text(typo_match.group("rest"))
        rest = re.sub(r"\bshoaxing\b", "shaoxing", rest, flags=re.IGNORECASE)
        prep_run = TYPO_PREP_RUN_RE.match(rest)
        if prep_run:
            add_prep(
                clean_text(f"{prep_run.group('mod') or ''} {prep_run.group('verb')}")
            )
            rest = clean_text(prep_run.group("food"))
        name = rest
        if not unit or unit in DEFAULT_COUNT_UNITS:
            unit = "teaspoon" if typo_word.startswith("tea") else "tablespoon"
    merge_match = MERGE_UNIT_RE.match(name)
    if merge_match and (not unit or unit in DEFAULT_COUNT_UNITS):
        rest = clean_text(merge_match.group("rest"))
        if normalize_name(rest):
            name = rest
            unit = merge_match.group("unit").lower().rstrip("s")
    glued = GLUED_FLOUR_RE.search(name)
    if glued and " " not in name[: glued.end() - len("flour")]:
        name = GLUED_FLOUR_RE.sub(r"\1 flour", name)

    name = clean_text(name)
    normalized_name = normalize_name(name)
    if not normalized_name or normalized_name == normalize_name(clean_text(row.get("name"))):
        return None

    fixed: dict[str, Any] = dict(row)
    fixed.update(
        {
            "ingredient_id": stable_uuid(INGREDIENT_NAMESPACE, normalized_name),
            "name": name,
            "normalized_name": normalized_name,
            "quantity": quantity or None,
            "unit": unit or None,
            "preparation": preparation or None,
        }
    )
    return fixed


def is_embedded_row(row: dict[str, Any]) -> bool:
    """A row whose name still carries a measurement, "plus" fragment, or
    leading article (e.g. name="plus 2 tablespoons butter"), or a mid-name
    measurement note on an already-structured row."""
    name = clean_text(row.get("name"))
    if not name:
        return False
    if PLUS_LEAD_RE.match(name) or EMBEDDED_LEAD_RE.match(name) or ARTICLE_LEAD_RE.match(name):
        return True
    if (clean_text(row.get("quantity")) or clean_text(row.get("unit"))) and MID_MEASUREMENT_RE.search(name):
        return True
    return False


def fix_embedded_row(row: dict[str, Any]) -> dict[str, Any] | None:
    """Move embedded measurements out of the row name into the structured
    fields; returns the fixed row or ``None`` when not repairable."""
    name = clean_text(row.get("name"))
    if not name:
        return None
    quantity = clean_text(row.get("quantity"))
    unit = clean_text(row.get("unit"))
    preparation = clean_text(row.get("preparation"))

    plus_match = PLUS_LEAD_RE.match(name)
    embedded_match = None if plus_match else EMBEDDED_LEAD_RE.match(name)
    if plus_match:
        amount = plus_match.group("amount")
        unit_word = plus_match.group("unit").rstrip(".")
        remainder = clean_text(name[plus_match.end():])
        if not remainder or not normalize_name(remainder):
            return None
        extra = f"{amount} {unit_word}"
        if quantity:
            new_quantity = (
                f"{quantity} {unit} plus {extra}" if unit else f"{quantity} plus {extra}"
            )
        else:
            new_quantity = extra
        quantity, unit = new_quantity, None
        name = remainder
    elif embedded_match:
        amount = embedded_match.group("amount")
        unit_word = embedded_match.group("unit").rstrip(".")
        remainder = clean_text(name[embedded_match.end():])
        # "about 11 1/4 oz. cooked" — the food is gone; refuse the guess.
        if not remainder or len(remainder.split()) < 2:
            return None
        if not quantity:
            quantity = f"{amount} {unit_word}"
            unit = None
        # "about 4 ounces cup sifted powdered sugar"
        remainder = re.sub(r"^cup\s+", "", remainder)
        name = remainder
    else:
        article_match = ARTICLE_LEAD_RE.match(name)
        if article_match:
            name = clean_text(name[article_match.end():])

    name = MID_MEASUREMENT_RE.sub(" ", name) if quantity or unit else name
    name = EMPTY_BRACKET_RE.sub("", name)
    name = BARE_INCH_RE.sub(" ", name)
    name = clean_text(name)
    note = TRAILING_NOTE_RE.search(name)
    if note:
        note_text = note.group(0).strip()
        name = clean_text(name[: note.start()])
        preparation = ", ".join(part for part in [preparation, note_text] if part) or None
    if not normalize_name(name):
        return None

    fixed: dict[str, Any] = dict(row)
    fixed.update(
        {
            "ingredient_id": stable_uuid(INGREDIENT_NAMESPACE, normalize_name(name)),
            "name": name,
            "normalized_name": normalize_name(name),
            "quantity": quantity or None,
            "unit": unit or None,
            "preparation": preparation or None,
        }
    )
    return fixed


# ── corruption signals ────────────────────────────────────────────────────────

NAME_PAREN_RE = re.compile(r"[()]")
UNIT_PAREN_RE = re.compile(r"[()]")
# Trademark marks in display names ("ball® pickle crisp® granules").
NAME_BRAND_RE = re.compile(r"[®™©]")
# A name that still leads with the size word from a lost parenthetical
# ("inch cucumbers", "inch slices of fresh chives").
LEADING_INCH_RE = re.compile(r"^(?:inches?|inch)\b")
LEADING_SIZE_OR_UNIT_RE = re.compile(
    r"^(?:"
    + "|".join(sorted(KNOWN_UNITS, key=len, reverse=True))
    + r"|inches?)\b"
)
ALLRECIPES_UNIT_WORDS = re.compile(
    r"^(?:ounces?|ozs?|pounds?|lbs?|grams?|gs?|kgs?|kilograms?|cups?|"
    r"tablespoons?|tbsps?|teaspoons?|tsps?|milliliters?|mls?|liters?|ls?|"
    r"pints?|quarts?|gallons?|inches?|fluid ounces?|fluid oz)\b"
)


def quantity_is_junk(quantity: str | None) -> bool:
    """Quantity text is corrupt when it swallowed a parenthetical chunk
    ("2 (.25") or ends in a dangling range token ("1 to").

    Legit ranges like "3/4 to 1" or "1 1/2 to 2" pass, as do compound
    quantities produced by the embedded-measurement fixer ("2 tablespoons
    plus 2 teaspoons", "1/3 cup plus 1 tablespoon").
    """
    if not quantity:
        return False
    text = quantity.lower()
    if "(" in text or ")" in text:
        return True
    tokens = text.replace("-", " ").split()
    if not tokens:
        return False
    if tokens[-1] == "to":
        return True
    additional = {"plus", "to", "or", "and", "each"} | KNOWN_UNITS
    return any(
        token not in additional and not is_amount_token(token) for token in tokens
    )


def is_corrupt_row(row: dict[str, Any]) -> bool:
    """Heuristic: does this ingredient row show parse corruption?"""
    name = clean_text(row.get("name"))
    normalized = clean_text(row.get("normalized_name"))
    quantity = clean_text(row.get("quantity"))
    unit = clean_text(row.get("unit"))
    return bool(
        NAME_PAREN_RE.search(name)
        or NAME_PAREN_RE.search(normalized)
        or NAME_BRAND_RE.search(name)
        or LEADING_INCH_RE.match(name)
        or quantity_is_junk(quantity)
        or (unit and UNIT_PAREN_RE.search(unit))
    )


def residual_corruption(row: dict[str, Any]) -> bool:
    """Corruption signals that survive re-parsing (name-only, brand-lead, ...).

    ``is_corrupt_row`` is the trigger for repair; this stricter predicate is
    applied after re-parsing so repaired rows are only accepted when the
    name is clean.
    """
    name = clean_text(row.get("name"))
    normalized = clean_text(row.get("normalized_name"))
    quantity = clean_text(row.get("quantity"))
    unit = clean_text(row.get("unit"))
    if NAME_PAREN_RE.search(name) or NAME_PAREN_RE.search(normalized):
        return True
    if NAME_BRAND_RE.search(name) or LEADING_INCH_RE.match(name):
        return True
    if quantity_is_junk(quantity):
        return True
    if unit and UNIT_PAREN_RE.search(unit):
        return True
    # A name that still leads with a size/unit word, e.g. "inch cucumbers"
    # or "ounce packages dry yeast", is unrepaired parse residue.
    return bool(LEADING_SIZE_OR_UNIT_RE.match(name))


# ── reconstruction ────────────────────────────────────────────────────────────

DEFAULT_COUNT_UNITS = {"piece", "pieces", ""}


def reconstruct_original(row: dict[str, Any]) -> str:
    """Best-effort reconstruction of the pre-parse ingredient line.

    The default ``pieces`` unit injected by the Allrecipes scraper is not
    part of the original text, so it is dropped. Real units ("pounds",
    "teaspoon", ...) are kept so semantics survive re-parsing.
    """
    name = clean_text(row.get("name"))
    quantity = clean_text(row.get("quantity"))
    unit = clean_text(row.get("unit"))
    preparation = clean_text(row.get("preparation"))

    if quantity:
        base = f"{quantity} {name}" if name else quantity
    else:
        base = name or ""
    if unit and unit not in DEFAULT_COUNT_UNITS:
        base = f"{quantity or ''} {unit} {name}".strip() if quantity else f"{unit} {name}".strip()
    if preparation:
        base = f"{base}, {preparation}" if base else preparation
    return clean_text(base)


# ── preprocessing rules ───────────────────────────────────────────────────────

AMOUNT_IN_PAREN = (
    r"(?:(?:\d+(?:\.\d+)?|\.\d+|\d+/\d+)(?:\s+\d+/\d+)?)"
    r"(?:\s+to\s+(?:(?:\d+(?:\.\d+)?|\.\d+|\d+/\d+)(?:\s+\d+/\d+)?))?"
)
UNITS_IN_PAREN = (
    r"(?:(?:fluid\s+)?(?:ounces?|ozs?)|pounds?|lbs?|grams?|gs?|kilograms?|kgs?|"
    r"milliliters?|mls?|liters?|ls?|cups?|tablespoons?|tbsps?|teaspoons?|tsps?|"
    r"pints?|quarts?|gallons?|inche?s?)(?:\s+thick)?"
)

PACKAGING_NOUNS = {
    "bag", "bags", "bottle", "bottles", "box", "boxes", "bunch", "bunches",
    "can", "cans", "carton", "cartons", "container", "containers",
    "envelope", "envelopes", "jar", "jars", "package", "packages",
    "packet", "packets", "tin", "tins", "wrap", "wraps",
}
PACKAGING_ALT = "|".join(sorted(PACKAGING_NOUNS, key=len, reverse=True))

UNIT_SINGULAR = {
    "ounces": "ounce", "ozs": "ounce", "oz": "ounce", "fluid ounces": "ounce",
    "fluid oz": "ounce", "pounds": "pound", "lbs": "pound", "lb": "pound",
    "grams": "gram", "gs": "gram", "g": "gram", "kilograms": "kilogram",
    "kgs": "kilogram", "kg": "kilogram", "milliliters": "milliliter",
    "mls": "milliliter", "ml": "milliliter", "liters": "liter", "ls": "liter",
    "l": "liter", "cups": "cup", "tablespoons": "tablespoon",
    "tbsps": "tablespoon", "tbsp": "tablespoon", "teaspoons": "teaspoon",
    "tsps": "teaspoon", "tsp": "teaspoon", "pints": "pint", "quarts": "quart",
    "gallons": "gallon", "inches": "inch",
}
UNIT_SINGULAR.setdefault("ounce", "ounce")
UNIT_SINGULAR.setdefault("pound", "pound")
UNIT_SINGULAR.setdefault("gram", "gram")
UNIT_SINGULAR.setdefault("kilogram", "kilogram")
UNIT_SINGULAR.setdefault("milliliter", "milliliter")
UNIT_SINGULAR.setdefault("liter", "liter")
UNIT_SINGULAR.setdefault("cup", "cup")
UNIT_SINGULAR.setdefault("tablespoon", "tablespoon")
UNIT_SINGULAR.setdefault("teaspoon", "teaspoon")
UNIT_SINGULAR.setdefault("pint", "pint")
UNIT_SINGULAR.setdefault("quart", "quart")
UNIT_SINGULAR.setdefault("gallon", "gallon")

# "2 (.25 ounce) packages" / "1 (15 ounce) can" / "12 (4 inch)"
# The optional trailing group consumes packaging nouns ("can or bottle").
PACK_AMOUNT_RE = re.compile(
    rf"(?P<count>{AMOUNT_IN_PAREN})\s*\(\s*(?P<qty>{AMOUNT_IN_PAREN})\s+"
    rf"(?P<unit>{UNITS_IN_PAREN})\s*\)"
    rf"(?:\s+(?P<first_pack>{PACKAGING_ALT})"
    rf"(?:\s+or\s+(?P<second_pack>{PACKAGING_ALT}))?)?"
)
# "pint (16 oz)" — parenthesis annotates the preceding unit word
UNIT_AMOUNT_RE = re.compile(
    rf"(?P<unitword>pints?|quarts?|gallons?|liters?|ls?|milliliters?|mls?|"
    rf"cups?|ounces?|ozs?|pounds?|lbs?|grams?|gs?)\s*"
    rf"\(\s*(?P<qty>{AMOUNT_IN_PAREN})\s+(?P<unit>{UNITS_IN_PAREN})\s*\)"
)
TEMPERATURE_RE = re.compile(
    r"\(\s*(?:about\s+)?\d+\s*(?:degrees?\s+)?(?:°\s*)?[fc]\s*"
    r"(?:/\s*\d+\s*(?:degrees?\s+)?(?:°\s*)?[fc])?\s*\)",
    re.IGNORECASE,
)
# "(1 inch) pieces" / "(1/4 inch thick)" / "(3.5 inch square)" — size
# parenthetical (plus optional count noun): a description, not an amount.
INCH_PIECES_RE = re.compile(
    r"\s*\(\s*[^()]+?\s+(?:inches?|inch)(?:[\s-]+(?:thick|wide|square))?\s*\)"
    r"(?:\s+(?:pieces?|slices?))?"
)
# Bare size phrases left over when the parenthesis was already lost:
# "8 to 10 inch flour tortillas", "1 1/2 inch thick slice ...",
# "1 1/2-inch thick filet mignon steaks", "3 inch-long pepper strips".
BARE_INCH_RE = re.compile(
    r"(?:^|\s+)\d+(?:\.\d+)?(?:\s+\d+/\d+)?(?:\s+to\s+\d+(?:\.\d+)?(?:\s+\d+/\d+)?)?"
    r"\s+inche?s?(?:[\s-]+(?:thick|wide|square|long))?(?=\s|$|[,)])"
)
BRAND_MARK_RE = re.compile(r"[®™©]+")
STRAY_PAREN_RE = re.compile(r"[()]")


def parse_amount(value: str) -> float:
    value = value.strip()
    if "/" in value and " " in value and not value.startswith(("1/", "2/", "3/", "4/", "5/", "6/", "7/", "8/", "9/")):
        whole, _, fraction = value.rpartition(" ")
        return float(whole) + parse_amount(fraction)
    if "/" in value:
        numerator, _, denominator = value.partition("/")
        return float(numerator) / float(denominator)
    return float(value)


def format_amount(value: float) -> str:
    if value.is_integer():
        return str(int(value))
    return f"{value:.3f}".rstrip("0").rstrip(".")


# Leading preparation words that move out of the name (aligned with the
# canonicalizer's LEADING_PREPARATION_RE, plus the Allrecipes sizes).
LEADING_PREP_WORDS = {
    "diced", "fried", "grilled", "sliced", "quartered", "marinated",
}
TEMP_STATE_WORDS = {"warm", "hot", "cold", "chilled", "cool", "boiling", "iced"}
LIQUID_WORDS = {"water", "milk", "broth", "stock"}
DESCRIPTOR_SWAP_WORDS = {"skinless", "boneless"}


def preprocess_allrecipes_text(text: str) -> str:
    """Apply the packaging/parenthetical/brand rules to a raw ingredient line."""
    text = clean_text(text)
    text = BRAND_MARK_RE.sub("", text)
    text = TEMPERATURE_RE.sub("", text)
    text = INCH_PIECES_RE.sub("", text)
    text = BARE_INCH_RE.sub(" ", text)

    def multiply_match(match: re.Match[str]) -> str:
        count = match.group("count")
        unit = match.group("unit").lower()
        qty_text = match.group("qty")
        if unit.startswith("inch"):
            # Size descriptor, not an amount: keep the count, drop the parens.
            if re.search(r"\s+to\s+", count):
                return count
            return format_amount(parse_amount(count))

        def scale(amount: str) -> str:
            return format_amount(parse_amount(amount))

        def multiply(amount: str) -> str:
            if re.search(r"\s+to\s+", amount):
                low, _, high = amount.partition(" to ")
                return f"{scale(low)} to {scale(high)}"
            return scale(amount)

        if re.search(r"\s+to\s+", count):
            low, _, high = count.partition(" to ")
            low_total = format_amount(parse_amount(low) * parse_amount(qty_text))
            high_total = format_amount(parse_amount(high) * parse_amount(qty_text))
            total = f"{low_total} to {high_total}"
        else:
            factor = parse_amount(count)
            if re.search(r"\s+to\s+", qty_text):
                low, _, high = qty_text.partition(" to ")
                total = (
                    f"{format_amount(factor * parse_amount(low))} "
                    f"to {format_amount(factor * parse_amount(high))}"
                )
            else:
                total = format_amount(factor * parse_amount(qty_text))
        return f"{total} {UNIT_SINGULAR.get(unit, unit)}"

    # Repeatedly apply so chains like "1 (8 ounce) (16 ounce)" collapse.
    previous = None
    while previous != text:
        previous = text
        text = PACK_AMOUNT_RE.sub(multiply_match, text)
        text = UNIT_AMOUNT_RE.sub(
            lambda match: match.group("unitword").lower(), text
        )
    text = STRAY_PAREN_RE.sub("", text)
    return clean_text(text)


# ── repair ────────────────────────────────────────────────────────────────────

def parse_cleaned_line(text: str) -> tuple[str | None, str | None, str, str | None]:
    """Split a preprocessed line via the shared parser with extended units."""
    quantity, unit, name, preparation = split_ingredient_text(text)
    preps: list[str] = []
    tokens = normalize_ingredient_text(name).split()

    # Comma-split gone wrong: "skinless, boneless chicken breasts" leaves
    # a one-word descriptor as the name and the food in the preparation.
    if (
        len(tokens) == 1
        and tokens[0].lower() in DESCRIPTOR_SWAP_WORDS
        and preparation
        and len(preparation.split()) > 1
    ):
        preps.append(tokens[0].lower())
        tokens = normalize_ingredient_text(preparation).split()
        preparation = None

    # A name that still leads with an amount, unit, packaging noun, size
    # word, or preparation word (e.g. "inch cucumbers", "ounce packages dry
    # yeast", "bunch green onions", "quartered marinated artichoke hearts")
    # after preprocessing. Strip the words, keep the rest.
    while tokens:
        first = tokens[0].lower().rstrip(".")
        if first in ("inch", "inches") or LEADING_INCH_RE.match(tokens[0]):
            # "inch slices of fresh chives" -> "fresh chives"
            tokens.pop(0)
            while tokens and tokens[0].lower() in ("thick", "wide", "square", "slices", "slice", "pieces", "piece", "strips", "strip", "of"):
                tokens.pop(0)
            continue
        # Two-token volume unit: "fluid ounce" / "fluid oz" -> "ounce".
        if (
            first == "fluid"
            and len(tokens) > 1
            and tokens[1].lower().rstrip(".") in {"ounce", "ounces", "oz"}
        ):
            if unit is None:
                unit = "ounce"
            tokens.pop(0)
            tokens.pop(0)
            continue
        if len(tokens) > 1 and first == "hard" and tokens[1].lower() == "boiled":
            preps.append("hard-boiled")
            tokens.pop(0)
            tokens.pop(0)
            continue
        if first in LEADING_PREP_WORDS:
            preps.append(first)
            tokens.pop(0)
            continue
        if first in TEMP_STATE_WORDS and len(tokens) > 1 and tokens[1].lower().rstrip(".") in LIQUID_WORDS:
            preps.append(first)
            tokens.pop(0)
            continue
        next_word = tokens[1] if len(tokens) > 1 else None
        extended = next_word is not None and (
            next_word in PACKAGING_NOUNS or next_word.lower() == "of"
        )
        if first in KNOWN_UNITS or (ALLRECIPES_UNIT_WORDS.match(first) and extended):
            if unit is None and first not in ("inch", "inches"):
                unit = first
            tokens.pop(0)
        elif is_amount_token(tokens[0]):
            tokens.pop(0)
        elif tokens[0].lower() == "of":
            tokens.pop(0)
        elif first in PACKAGING_NOUNS:
            tokens.pop(0)
        else:
            break
    if tokens:
        name = clean_ingredient_name(" ".join(tokens)) or name
    merged_preparation = ", ".join(preps + ([preparation] if preparation else [])) or None
    return quantity, unit, name, merged_preparation


def repair_row(row: dict[str, Any]) -> dict[str, Any] | None:
    """Reconstruct, preprocess, and re-parse one corrupted ingredient row.

    Returns the repaired row (with a recomputed ``ingredient_id``) or ``None``
    when the row is not repairable deterministically.
    """
    original = reconstruct_original(row)
    if not original:
        return None
    cleaned = preprocess_allrecipes_text(original)
    quantity, unit, name, preparation = parse_cleaned_line(cleaned)
    if not name:
        return None
    normalized_name = normalize_name(name) or normalize_name(cleaned)
    if not normalized_name:
        return None

    repaired: dict[str, Any] = dict(row)
    repaired.update(
        {
            "ingredient_id": stable_uuid(INGREDIENT_NAMESPACE, normalized_name),
            "name": name,
            "normalized_name": normalized_name,
            "quantity": quantity,
            "unit": unit,
            "preparation": preparation,
        }
    )
    # Keep the count-based default unit convention for unitless repairs.
    if unit is None and clean_text(row.get("unit")) in {"piece", "pieces"}:
        repaired["unit"] = row["unit"]
    if residual_corruption(repaired):
        return None
    return repaired


def repair_if_needed(row: dict[str, Any]) -> dict[str, Any] | None:
    """Repair a row only when it is actually corrupt; otherwise leave it."""
    if not is_corrupt_row(row):
        return None
    return repair_row(row)