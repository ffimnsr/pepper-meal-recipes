import sys
from pathlib import Path
from unittest import TestCase


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRAPER_DIR = REPO_ROOT / "py-scripts" / "scraper"
sys.path.insert(0, str(SCRAPER_DIR))

from allrecipes import build_ingredients_with_default_units, default_unit_for_quantity  # noqa: E402


class AllrecipesUnitDefaultTests(TestCase):
    def test_default_unit_for_quantity(self) -> None:
        cases = [
            ("1", "piece"),
            ("2", "pieces"),
            ("3", "pieces"),
            ("1/2", "pieces"),
            ("1 1/2", "pieces"),
            ("2 1/2", "pieces"),
            ("18-24", "pieces"),
            (None, "pieces"),
        ]
        for quantity, expected in cases:
            with self.subTest(quantity=quantity):
                self.assertEqual(default_unit_for_quantity(quantity), expected)

    def test_keeps_existing_units(self) -> None:
        ingredients = build_ingredients_with_default_units(
            ["2 cups beef broth", "1 tablespoon olive oil", "1 pinch cayenne"]
        )
        units = [row["unit"] for row in ingredients]
        self.assertEqual(units, ["cups", "tablespoon", "pinch"])

    def test_defaults_missing_units_to_piece_or_pieces(self) -> None:
        ingredients = build_ingredients_with_default_units(
            [
                "2 yellow onions, cut into 1-inch pieces",
                "1 onion",
                "4 carrots, peeled and cut into 1-inch pieces",
            ]
        )
        self.assertEqual(ingredients[0]["quantity"], "2")
        self.assertEqual(ingredients[0]["unit"], "pieces")
        self.assertEqual(ingredients[1]["name"], "onion")
        self.assertEqual(ingredients[1]["unit"], "piece")
        self.assertEqual(ingredients[2]["unit"], "pieces")


if __name__ == "__main__":
    from unittest import main

    main()