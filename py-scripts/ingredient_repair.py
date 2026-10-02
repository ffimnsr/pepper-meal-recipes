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

    Legit ranges like "3/4 to 1" or "1 1/2 to 2" pass.
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
    return any(
        token not in {"to", "or"} and not is_amount_token(token) for token in tokens
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
# "1 1/2-inch thick filet mignon steaks".
BARE_INCH_RE = re.compile(
    r"(?:^|\s+)\d+(?:\.\d+)?(?:\s+\d+/\d+)?(?:\s+to\s+\d+(?:\.\d+)?(?:\s+\d+/\d+)?)?"
    r"\s+inche?s?(?:[\s-]+(?:thick|wide|square))?(?=\s|$|[,)])"
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