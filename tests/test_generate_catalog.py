import unittest
from unittest import mock

import scripts.generate_catalog as generate_catalog
from scripts.generate_catalog import (
    CanonicalRecipe,
    build_ingredient_review_index,
    canonicalize_ingredient_entry,
    normalize_ingredient_record,
)


RECIPE = {
    "id": "11111111-1111-5111-8111-111111111111",
    "slug": "test-recipe",
    "name": "Test Recipe",
}


def canonicalize(name: str, quantity: str | None = None, unit: str | None = None, preparation: str | None = None):
    ingredient = {
        "ingredient_id": "00000000-0000-5000-8000-000000000000",
        "name": name,
        "normalized_name": name,
        "quantity": quantity,
        "unit": unit,
        "preparation": preparation,
        "position": 1,
    }
    normalize_ingredient_record(ingredient)
    return canonicalize_ingredient_entry(RECIPE, ingredient)


class GenerateCatalogTests(unittest.TestCase):
    def test_egg_and_eggs_share_identity(self) -> None:
        egg = canonicalize("egg").ingredients[0]
        eggs = canonicalize("eggs (beaten)").ingredients[0]
        self.assertEqual(egg["normalized_name"], "egg")
        self.assertEqual(eggs["normalized_name"], "egg")
        self.assertEqual(egg["ingredient_id"], eggs["ingredient_id"])

    def test_parenthetical_preparation_moves_out_of_identity(self) -> None:
        result = canonicalize("pig kidney (cleaned)")
        self.assertEqual(result.ingredients[0]["name"], "pig kidney")
        self.assertEqual(result.ingredients[0]["preparation"], "cleaned")

    def test_tilapia_cleanup(self) -> None:
        result = canonicalize("whole large tilapia (cleaned (scales and gut removed))")
        self.assertEqual(result.ingredients[0]["name"], "tilapia")

    def test_green_onion_cleanup(self) -> None:
        result = canonicalize("A bunch of green onions (cut in 3 inch length)")
        self.assertEqual(result.ingredients[0]["name"], "green onion")

    def test_packaging_phrase_cleanup(self) -> None:
        result = canonicalize("a can of tuna")
        self.assertEqual(result.ingredients[0]["name"], "canned tuna")

    def test_boiling_water_is_excluded(self) -> None:
        result = canonicalize("Water for boiling egg")
        self.assertEqual(result.ingredients, [])
        self.assertEqual(result.review_entries[0]["resolution"], "excluded")

    def test_double_boiler_is_excluded(self) -> None:
        result = canonicalize("A double boiler")
        self.assertEqual(result.ingredients, [])
        self.assertEqual(result.review_entries[0]["resolution"], "excluded")

    def test_safe_compound_is_split(self) -> None:
        result = canonicalize("salt and ground black pepper")
        self.assertEqual([item["name"] for item in result.ingredients], ["salt", "ground black pepper"])
        self.assertEqual(result.review_entries[0]["resolution"], "split")

    def test_ambiguous_alternative_goes_to_review(self) -> None:
        result = canonicalize("apple cider or apple juice")
        self.assertEqual(result.ingredients, [])
        self.assertEqual(result.review_entries[0]["resolution"], "review")

    def test_review_index_is_deterministic(self) -> None:
        with mock.patch.object(generate_catalog, "validate_payload"):
            review_index = build_ingredient_review_index(
                [
                    CanonicalRecipe(
                        source_path=None,
                        output_path=None,
                        payload={},
                        review_entries=[
                            {
                                "recipe_id": RECIPE["id"],
                                "recipe_slug": RECIPE["slug"],
                                "recipe_name": RECIPE["name"],
                                "position": 2,
                                "original_text": "salt and pepper",
                                "quantity": None,
                                "unit": None,
                                "cleaned_name": "salt and pepper",
                                "replacements": ["salt", "pepper"],
                                "issue_types": ["compound_split"],
                                "resolution": "split",
                            }
                        ],
                    )
                ],
                "2026-06-13T00:00:00Z",
                2,
            )
        self.assertEqual(review_index["entries"][0]["original_text"], "salt and pepper")


if __name__ == "__main__":
    unittest.main()
