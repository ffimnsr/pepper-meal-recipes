import importlib.util
import sys
from pathlib import Path
from unittest import TestCase


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = REPO_ROOT / "py-scripts" / "generate_catalog.py"
SPEC = importlib.util.spec_from_file_location("generate_catalog_ingredient_tests", SCRIPT_PATH)
assert SPEC is not None
generate_catalog = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = generate_catalog
SPEC.loader.exec_module(generate_catalog)


class GenerateCatalogIngredientTests(TestCase):
    def test_preserves_existing_structured_fields(self) -> None:
        ingredient = {
            "ingredient_id": "unused",
            "name": "7 Up",
            "normalized_name": "7 up",
            "quantity": "1",
            "unit": "liter",
            "preparation": None,
            "position": 1,
        }

        generate_catalog.normalize_ingredient_record(ingredient)

        self.assertEqual(ingredient["name"], "7 Up")
        self.assertEqual(ingredient["normalized_name"], "7 up")
        self.assertEqual(ingredient["quantity"], "1")
        self.assertEqual(ingredient["unit"], "liter")

    def test_parses_measurement_when_record_is_unstructured(self) -> None:
        ingredient = {
            "ingredient_id": "unused",
            "name": "1 cup flour",
            "normalized_name": "1 cup flour",
            "quantity": None,
            "unit": None,
            "preparation": None,
            "position": 1,
        }

        generate_catalog.normalize_ingredient_record(ingredient)

        self.assertEqual(ingredient["name"], "flour")
        self.assertEqual(ingredient["normalized_name"], "flour")
        self.assertEqual(ingredient["quantity"], "1")
        self.assertEqual(ingredient["unit"], "cup")

    def test_moves_selected_leading_words_to_preparation(self) -> None:
        recipe = {
            "id": "11111111-1111-5111-8111-111111111111",
            "slug": "test-recipe",
            "name": "Test Recipe",
        }
        cases = [
            ("diced tomatoes", "tomato", "diced"),
            ("fried egg", "egg", "fried"),
            ("grilled pork belly", "pork belly", "grilled"),
            ("hard boiled eggs", "egg", "hard-boiled"),
            ("sliced ginger", "ginger", "sliced"),
        ]

        for source_name, expected_name, expected_preparation in cases:
            with self.subTest(source_name=source_name):
                ingredient = {
                    "name": source_name,
                    "quantity": "1",
                    "unit": "piece",
                    "preparation": None,
                    "position": 1,
                }

                result = generate_catalog.canonicalize_ingredient_entry(recipe, ingredient)

                self.assertEqual(len(result.ingredients), 1)
                self.assertEqual(result.ingredients[0]["name"], expected_name)
                self.assertEqual(result.ingredients[0]["normalized_name"], expected_name)
                self.assertEqual(result.ingredients[0]["preparation"], expected_preparation)

    def test_moves_solution_clause_to_preparation(self) -> None:
        recipe = {
            "id": "11111111-1111-5111-8111-111111111111",
            "slug": "test-recipe",
            "name": "Test Recipe",
        }
        ingredient = {
            "name": "all-purpose flour diluted in 1/2 cup water",
            "quantity": "3",
            "unit": "tablespoons",
            "preparation": None,
            "position": 1,
        }

        result = generate_catalog.canonicalize_ingredient_entry(recipe, ingredient)

        self.assertEqual(len(result.ingredients), 1)
        self.assertEqual(result.ingredients[0]["name"], "all-purpose flour")
        self.assertEqual(result.ingredients[0]["normalized_name"], "all-purpose flour")
        self.assertEqual(result.ingredients[0]["preparation"], "diluted in 1/2 cup water")

    def test_does_not_move_unselected_identity_descriptor(self) -> None:
        recipe = {
            "id": "11111111-1111-5111-8111-111111111111",
            "slug": "test-recipe",
            "name": "Test Recipe",
        }
        cases = ["ground black pepper", "whole grain bread"]

        for name in cases:
            with self.subTest(name=name):
                ingredient = {
                    "name": name,
                    "quantity": "1",
                    "unit": "teaspoon",
                    "preparation": None,
                    "position": 1,
                }

                result = generate_catalog.canonicalize_ingredient_entry(recipe, ingredient)

                self.assertEqual(result.ingredients[0]["name"], name)
                self.assertIsNone(result.ingredients[0]["preparation"])


if __name__ == "__main__":
    from unittest import main

    main()
