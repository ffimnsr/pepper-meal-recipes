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
    def setUp(self) -> None:
        # Canonicalization consults the repo's persisted ignore list; tests
        # must not depend on the current review state.
        self._original_ignored = generate_catalog.IGNORED_INGREDIENT_IDS
        generate_catalog.IGNORED_INGREDIENT_IDS = set()

    def tearDown(self) -> None:
        generate_catalog.IGNORED_INGREDIENT_IDS = self._original_ignored

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

    def test_late_peel_after_descriptor_stripping(self) -> None:
        recipe = {
            "id": "11111111-1111-5111-8111-111111111111",
            "slug": "test-recipe",
            "name": "Test Recipe",
        }
        cases = [
            ("fresh minced parsley", "parsley", "minced"),
            ("fresh chopped basil", "basil", "chopped"),
            ("large chopped onion", "onion", "chopped"),
            ("medium thinly sliced onion", "onion", "thinly sliced"),
            ("bunch finely chopped fresh parsley", "parsley", "finely chopped"),
            ("bunch chopped fresh cilantro", "cilantro", "chopped"),
            ("small finely chopped green bell pepper", "green bell pepper", "finely chopped"),
            ("raw cleaned whole pumpkin seeds", "whole pumpkin seeds", "cleaned"),
            ("dash crushed red pepper", "red pepper", "crushed"),
            ("thinly shaved parmesan cheese", "parmesan cheese", "thinly shaved"),
            ("thinly bias-sliced green onions", "green onion", "thinly bias-sliced"),
            ("finely pre-shredded italian cheese blend", "italian cheese blend", "finely pre-shredded"),
            (". thinly sliced pepperoni", "pepperoni", "thinly sliced"),
        ]

        for source_name, expected_name, expected_preparation in cases:
            with self.subTest(source_name=source_name):
                ingredient = {
                    "name": source_name,
                    "quantity": None,
                    "unit": None,
                    "preparation": None,
                    "position": 1,
                }

                result = generate_catalog.canonicalize_ingredient_entry(recipe, ingredient)

                self.assertEqual(len(result.ingredients), 1)
                self.assertEqual(result.ingredients[0]["name"], expected_name)
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

    def test_flags_and_connector_names_for_review(self) -> None:
        recipe = {
            "id": "11111111-1111-5111-8111-111111111111",
            "slug": "test-recipe",
            "name": "Test Recipe",
        }
        names = ["ketchup and mustard", "garlic and onion", "butter and/or margarine"]

        for name in names:
            with self.subTest(name=name):
                ingredient = {
                    "name": name,
                    "quantity": "1",
                    "unit": "cup",
                    "preparation": None,
                    "position": 1,
                }

                result = generate_catalog.canonicalize_ingredient_entry(recipe, ingredient)

                self.assertEqual(result.ingredients, [])
                self.assertEqual(len(result.review_entries), 1)
                review = result.review_entries[0]
                self.assertIn("ambiguous_connector", review["issue_types"])
                self.assertEqual(review["cleaned_name"], name)

    def test_flags_or_connector_names_for_review(self) -> None:
        recipe = {
            "id": "11111111-1111-5111-8111-111111111111",
            "slug": "test-recipe",
            "name": "Test Recipe",
        }
        ingredient = {
            "name": "grape seed or coconut oil",
            "quantity": "2",
            "unit": "tablespoons",
            "preparation": None,
            "position": 1,
        }

        result = generate_catalog.canonicalize_ingredient_entry(recipe, ingredient)

        self.assertEqual(result.ingredients, [])
        self.assertEqual(len(result.review_entries), 1)
        self.assertIn("ambiguous_connector", result.review_entries[0]["issue_types"])

    def test_safe_compound_split_takes_precedence_over_and_flag(self) -> None:
        recipe = {
            "id": "11111111-1111-5111-8111-111111111111",
            "slug": "test-recipe",
            "name": "Test Recipe",
        }
        ingredient = {
            "name": "salt and pepper",
            "quantity": "1",
            "unit": "teaspoon",
            "preparation": None,
            "position": 1,
        }

        result = generate_catalog.canonicalize_ingredient_entry(recipe, ingredient)

        self.assertEqual([item["name"] for item in result.ingredients], ["salt", "pepper"])
        self.assertEqual(len(result.review_entries), 1)
        self.assertIn("compound_split", result.review_entries[0]["issue_types"])
        self.assertNotIn("ambiguous_connector", result.review_entries[0]["issue_types"])

    def test_does_not_flag_and_inside_a_word(self) -> None:
        recipe = {
            "id": "11111111-1111-5111-8111-111111111111",
            "slug": "test-recipe",
            "name": "Test Recipe",
        }
        ingredient = {
            "name": "dijon mustard",
            "quantity": "1",
            "unit": "tablespoon",
            "preparation": None,
            "position": 1,
        }

        result = generate_catalog.canonicalize_ingredient_entry(recipe, ingredient)

        self.assertEqual(len(result.ingredients), 1)
        self.assertEqual(result.ingredients[0]["name"], "dijon mustard")
        self.assertEqual(result.review_entries, [])

    def test_ignored_ingredient_is_indexed_without_review(self) -> None:
        recipe = {
            "id": "11111111-1111-5111-8111-111111111111",
            "slug": "test-recipe",
            "name": "Test Recipe",
        }
        ingredient = {
            "name": "ketchup and mustard",
            "quantity": "1",
            "unit": "cup",
            "preparation": None,
            "position": 1,
        }
        ignored_id = generate_catalog.stable_uuid(
            generate_catalog.INGREDIENT_NAMESPACE, "ketchup and mustard"
        )
        original_ignored = generate_catalog.IGNORED_INGREDIENT_IDS
        try:
            generate_catalog.IGNORED_INGREDIENT_IDS = {ignored_id}
            result = generate_catalog.canonicalize_ingredient_entry(recipe, ingredient)
        finally:
            generate_catalog.IGNORED_INGREDIENT_IDS = original_ignored

        self.assertEqual(len(result.ingredients), 1)
        self.assertEqual(result.ingredients[0]["name"], "ketchup and mustard")
        self.assertEqual(result.ingredients[0]["ingredient_id"], ignored_id)
        self.assertEqual(result.review_entries, [])

    def test_ignored_ingredient_takes_precedence_over_safe_compound_split(self) -> None:
        recipe = {
            "id": "11111111-1111-5111-8111-111111111111",
            "slug": "test-recipe",
            "name": "Test Recipe",
        }
        ingredient = {
            "name": "salt and pepper",
            "quantity": "1",
            "unit": "teaspoon",
            "preparation": None,
            "position": 1,
        }
        ignored_id = generate_catalog.stable_uuid(
            generate_catalog.INGREDIENT_NAMESPACE, "salt and pepper"
        )
        original_ignored = generate_catalog.IGNORED_INGREDIENT_IDS
        try:
            generate_catalog.IGNORED_INGREDIENT_IDS = {ignored_id}
            result = generate_catalog.canonicalize_ingredient_entry(recipe, ingredient)
        finally:
            generate_catalog.IGNORED_INGREDIENT_IDS = original_ignored

        self.assertEqual(len(result.ingredients), 1)
        self.assertEqual(result.ingredients[0]["name"], "salt and pepper")
        self.assertEqual(result.review_entries, [])

    def test_ignored_name_is_not_late_peeled(self) -> None:
        recipe = {
            "id": "11111111-1111-5111-8111-111111111111",
            "slug": "test-recipe",
            "name": "Test Recipe",
        }
        ingredient = {
            "name": "fresh minced parsley",
            "quantity": "2",
            "unit": "tablespoons",
            "preparation": None,
            "position": 1,
        }
        ignored_id = generate_catalog.stable_uuid(
            generate_catalog.INGREDIENT_NAMESPACE, "minced parsley"
        )
        original_ignored = generate_catalog.IGNORED_INGREDIENT_IDS
        try:
            generate_catalog.IGNORED_INGREDIENT_IDS = {ignored_id}
            result = generate_catalog.canonicalize_ingredient_entry(recipe, ingredient)
        finally:
            generate_catalog.IGNORED_INGREDIENT_IDS = original_ignored

        self.assertEqual(len(result.ingredients), 1)
        self.assertEqual(result.ingredients[0]["name"], "minced parsley")
        self.assertEqual(result.ingredients[0]["ingredient_id"], ignored_id)
        self.assertEqual(result.review_entries, [])


class GenerateCatalogCanonicalNameTests(TestCase):
    @staticmethod
    def canonical_name(source: str) -> str:
        """The name canonicalize_ingredient_entry would store."""
        base, _ = generate_catalog.split_embedded_preparation(source)
        return generate_catalog.normalize_core_ingredient_name(base)

    def test_singularize_keeps_uncountables_and_fixes_shes_halves(self) -> None:
        cases = [
            ("molasses", "molasses"),
            ("radishes", "radish"),
            ("dishes", "dish"),
            ("halves", "half"),
            ("peaches", "peach"),
            ("tomatoes", "tomato"),
        ]
        for source, expected in cases:
            with self.subTest(source=source):
                self.assertEqual(generate_catalog.singularize_token(source), expected)

    def test_canonical_name_moves_leading_preparation(self) -> None:
        cases = [
            ("finely chopped parsley", "parsley"),
            ("thinly sliced onion", "onion"),
            ("minced garlic", "garlic"),
            ("grated parmesan cheese", "parmesan cheese"),
            ("shredded cheddar cheese", "cheddar cheese"),
            ("diced tomatoes", "tomato"),
            ("freshly ground black pepper", "black pepper"),
            ("finely chopped onion", "onion"),
        ]
        for source, expected in cases:
            with self.subTest(source=source):
                self.assertEqual(self.canonical_name(source), expected)

    def test_moved_preparation_lands_in_preparation_field(self) -> None:
        base_name, parts = generate_catalog.split_embedded_preparation("finely chopped fresh parsley")
        self.assertEqual(base_name, "fresh parsley")
        self.assertEqual(parts, ["finely chopped"])
        base_name, parts = generate_catalog.split_embedded_preparation("freshly ground black pepper")
        self.assertEqual(base_name, "black pepper")
        self.assertEqual(parts, ["freshly ground"])

    def test_bare_ground_stays_a_product_name(self) -> None:
        for source in ["ground beef", "ground cinnamon", "ground turkey", "ground black pepper"]:
            with self.subTest(source=source):
                self.assertEqual(generate_catalog.normalize_core_ingredient_name(source), source)

    def test_canonical_name_keeps_attributive_preparation(self) -> None:
        cases = [
            ("canned sliced mushrooms", "canned sliced mushroom"),
            ("frozen chopped spinach", "frozen chopped spinach"),
            ("pkg chopped romaine hearts", "pkg chopped romaine heart"),
            ("fully trimmed pork tenderloins", "fully trimmed pork tenderloin"),
        ]
        for source, expected in cases:
            with self.subTest(source=source):
                self.assertEqual(generate_catalog.normalize_core_ingredient_name(source), expected)

    def test_canonical_name_still_strips_trailing_preparation(self) -> None:
        cases = [
            ("chicken breast halves cubed", "chicken breast half"),
            ("tomatoes diced", "tomato"),
        ]
        for source, expected in cases:
            with self.subTest(source=source):
                self.assertEqual(generate_catalog.normalize_core_ingredient_name(source), expected)

    def test_canonical_name_strips_leading_of_and_trailing_optional(self) -> None:
        cases = [
            ("small bunch of cilantro", "cilantro"),
            ("dash of garlic powder optional", "garlic powder"),
            ("of soy sauce", "soy sauce"),
        ]
        for source, expected in cases:
            with self.subTest(source=source):
                self.assertEqual(generate_catalog.normalize_core_ingredient_name(source), expected)

    def test_excluded_non_food_patterns(self) -> None:
        for name in ["non food", "non food item", "non-food item", "Non Food Items"]:
            with self.subTest(name=name):
                self.assertTrue(generate_catalog.is_excluded_ingredient_name(name))


if __name__ == "__main__":
    from unittest import main

    main()
