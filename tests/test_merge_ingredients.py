import importlib.util
import json
import sys
import tempfile
from pathlib import Path
from unittest import TestCase, mock

REPO_ROOT = Path(__file__).resolve().parents[1]
GENERATE_CATALOG_PATH = REPO_ROOT / "py-scripts" / "generate_catalog.py"
GENERATE_CATALOG_SPEC = importlib.util.spec_from_file_location("generate_catalog", GENERATE_CATALOG_PATH)
generate_catalog = importlib.util.module_from_spec(GENERATE_CATALOG_SPEC)
assert GENERATE_CATALOG_SPEC.loader is not None
sys.modules[GENERATE_CATALOG_SPEC.name] = generate_catalog
GENERATE_CATALOG_SPEC.loader.exec_module(generate_catalog)

SCRIPT_PATH = REPO_ROOT / "py-scripts" / "merge_ingredients.py"
SPEC = importlib.util.spec_from_file_location("merge_ingredients", SCRIPT_PATH)
merge_ingredients = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = merge_ingredients
SPEC.loader.exec_module(merge_ingredients)

INGREDIENT_IDS = {
    "kosher salt": "11111111-1111-5111-8111-111111111111",
    "sea salt": "22222222-2222-5222-8222-222222222222",
    "garlic": "33333333-3333-5333-8333-333333333333",
}
INGREDIENTS = [
    {
        "id": INGREDIENT_IDS["kosher salt"],
        "name": "kosher salt",
        "normalized_name": "kosher salt",
        "recipe_count": 1,
    },
    {
        "id": INGREDIENT_IDS["sea salt"],
        "name": "sea salt",
        "normalized_name": "sea salt",
        "recipe_count": 1,
    },
    {
        "id": INGREDIENT_IDS["garlic"],
        "name": "Garlic",
        "normalized_name": "garlic",
        "recipe_count": 0,
    },
]


def write_json(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload), encoding="utf-8")


def salt_row(ingredient_id: str, name: str) -> dict:
    return {
        "ingredient_id": ingredient_id,
        "name": name,
        "normalized_name": name,
        "quantity": "1",
        "unit": "tablespoon",
        "preparation": None,
        "position": 1,
    }


class MergeIngredientsTests(TestCase):
    def test_parse_selection_ranges_accepts_numbers_commas_and_ranges(self) -> None:
        self.assertEqual(merge_ingredients.parse_selection_ranges("1,3,5-7"), [(1, 1), (3, 3), (5, 7)])
        self.assertEqual(merge_ingredients.parse_selection_ranges(" 2 , 4 "), [(2, 2), (4, 4)])
        self.assertEqual(merge_ingredients.parse_selection_ranges("5-5"), [(5, 5)])

    def test_parse_selection_ranges_rejects_search_terms_and_malformed_ranges(self) -> None:
        self.assertIsNone(merge_ingredients.parse_selection_ranges("onion"))
        self.assertIsNone(merge_ingredients.parse_selection_ranges("1-"))
        self.assertIsNone(merge_ingredients.parse_selection_ranges("-3"))
        self.assertIsNone(merge_ingredients.parse_selection_ranges("4-1"))
        self.assertIsNone(merge_ingredients.parse_selection_ranges("0"))
        self.assertIsNone(merge_ingredients.parse_selection_ranges("1-3-5"))

    def test_filter_ingredient_entries_matches_name_case_insensitively(self) -> None:
        self.assertEqual(merge_ingredients.filter_ingredient_entries(INGREDIENTS, "GARLIC"), [INGREDIENTS[2]])
        self.assertEqual(merge_ingredients.filter_ingredient_entries(INGREDIENTS, "salt"), INGREDIENTS[:2])
        self.assertEqual(merge_ingredients.filter_ingredient_entries(INGREDIENTS, "pepper"), [])

    def test_select_ingredient_entries_search_filter_and_toggle(self) -> None:
        answers = iter(["salt", "1,3", "2", "d"])

        def answer(prompt: str) -> str:
            return next(answers)

        messages: list[str] = []
        selected = merge_ingredients.select_ingredient_entries(
            INGREDIENTS,
            input_fn=answer,
            output=messages.append,
        )

        self.assertEqual(
            [entry["id"] for entry in selected],
            [INGREDIENT_IDS["kosher salt"], INGREDIENT_IDS["sea salt"]],
        )
        self.assertIn("Ignoring 3-3: only 2 items are listed.", messages)

    def test_select_ingredient_entries_reset_and_clear(self) -> None:
        answers = iter(["salt", "1", "r", "3", "c", "d"])

        def answer(prompt: str) -> str:
            return next(answers)

        messages: list[str] = []
        selected = merge_ingredients.select_ingredient_entries(
            INGREDIENTS,
            input_fn=answer,
            output=messages.append,
        )

        self.assertEqual(selected, [])
        self.assertIn("Selection cleared.", messages)

    def test_choose_merged_name_uses_a_selected_ingredient_number(self) -> None:
        answers = iter(["1", "2", "d"])

        def answer(prompt: str) -> str:
            return next(answers)

        selected = merge_ingredients.select_ingredient_entries(
            INGREDIENTS,
            input_fn=answer,
            output=lambda _message: None,
        )
        name = merge_ingredients.choose_merged_name(
            selected,
            input_fn=lambda _prompt: "2",
            output=lambda _message: None,
        )
        self.assertEqual(name, "sea salt")

    def test_choose_merged_name_accepts_a_new_name(self) -> None:
        selected = INGREDIENTS[:2]
        answers = iter(["0", "12", "Himalayan salt"])

        def answer(prompt: str) -> str:
            return next(answers)

        messages: list[str] = []
        name = merge_ingredients.choose_merged_name(selected, input_fn=answer, output=messages.append)

        self.assertEqual(name, "Himalayan salt")
        self.assertIn("Number must be between 1 and 2.", messages)

    def test_merge_ingredient_identities_rewrites_every_referencing_row(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            recipes_dir = Path(temporary_directory) / "recipes"
            recipes_dir.mkdir()
            first_path = recipes_dir / "first.json"
            second_path = recipes_dir / "unrelated.json"
            write_json(
                first_path,
                {
                    "ingredients": [
                        salt_row(INGREDIENT_IDS["kosher salt"], "kosher salt"),
                        salt_row(INGREDIENT_IDS["sea salt"], "sea salt"),
                        salt_row(INGREDIENT_IDS["garlic"], "Garlic"),
                    ]
                },
            )
            write_json(second_path, {"ingredients": [salt_row(INGREDIENT_IDS["garlic"], "Garlic")]})

            target = merge_ingredients.IngredientIdentity(
                id="99999999-9999-5999-8999-999999999999",
                name="seasoned salt",
                normalized_name="seasoned salt",
            )
            stats = merge_ingredients.merge_ingredient_identities(
                [first_path, second_path],
                {INGREDIENT_IDS["kosher salt"], INGREDIENT_IDS["sea salt"]},
                target,
            )

            self.assertEqual(stats.recipe_files_updated, 1)
            self.assertEqual(stats.ingredient_rows_updated, 2)
            recipe = json.loads(first_path.read_text(encoding="utf-8"))
            self.assertEqual(recipe["ingredients"][0]["ingredient_id"], target.id)
            self.assertEqual(recipe["ingredients"][0]["name"], target.name)
            self.assertEqual(recipe["ingredients"][0]["normalized_name"], target.normalized_name)
            self.assertEqual(recipe["ingredients"][1]["ingredient_id"], target.id)
            self.assertEqual(recipe["ingredients"][2]["ingredient_id"], INGREDIENT_IDS["garlic"])
            self.assertEqual(recipe["ingredients"][2]["name"], "Garlic")

    def test_merge_ingredient_identities_skips_rows_already_at_target(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            recipe_path = Path(temporary_directory) / "recipe.json"
            target = merge_ingredients.IngredientIdentity(
                id=INGREDIENT_IDS["sea salt"],
                name="sea salt",
                normalized_name="sea salt",
            )
            write_json(
                recipe_path,
                {
                    "ingredients": [
                        salt_row(INGREDIENT_IDS["kosher salt"], "kosher salt"),
                        salt_row(INGREDIENT_IDS["sea salt"], "sea salt"),
                    ]
                },
            )

            stats = merge_ingredients.merge_ingredient_identities(
                [recipe_path],
                {INGREDIENT_IDS["kosher salt"], INGREDIENT_IDS["sea salt"]},
                target,
            )

            self.assertEqual(stats.recipe_files_updated, 1)
            self.assertEqual(stats.ingredient_rows_updated, 1)
            recipe = json.loads(recipe_path.read_text(encoding="utf-8"))
            self.assertEqual(recipe["ingredients"][0]["name"], "sea salt")

    def test_run_merge_end_to_end(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            indexes_dir = root / "indexes"
            indexes_dir.mkdir()
            recipes_dir = root / "recipes"
            recipes_dir.mkdir()
            write_json(
                indexes_dir / "ingredients.index.json",
                {"ingredients": INGREDIENTS},
            )
            first_path = recipes_dir / "first.json"
            second_path = recipes_dir / "second.json"
            write_json(
                first_path,
                {
                    "ingredients": [
                        salt_row(INGREDIENT_IDS["kosher salt"], "kosher salt"),
                        salt_row(INGREDIENT_IDS["sea salt"], "sea salt"),
                    ]
                },
            )
            write_json(
                second_path,
                {
                    "ingredients": [
                        salt_row(INGREDIENT_IDS["sea salt"], "sea salt"),
                        salt_row(INGREDIENT_IDS["garlic"], "Garlic"),
                    ]
                },
            )

            answers = iter(["1,2", "", "1", "y"])

            def answer(prompt: str) -> str:
                return next(answers)

            with mock.patch.object(merge_ingredients, "INDEXES_DIR", indexes_dir), mock.patch.object(
                merge_ingredients, "RECIPES_DIR", recipes_dir
            ):
                stats = merge_ingredients.run_merge(input_fn=answer, output=lambda _message: None)

            self.assertEqual(stats.recipe_files_updated, 2)
            self.assertEqual(stats.ingredient_rows_updated, 3)
            expected_id = generate_catalog.stable_uuid(generate_catalog.INGREDIENT_NAMESPACE, "kosher salt")
            for path in (first_path, second_path):
                recipe = json.loads(path.read_text(encoding="utf-8"))
                for ingredient in recipe["ingredients"]:
                    if ingredient["name"] != "Garlic":
                        self.assertEqual(ingredient["ingredient_id"], expected_id)
                        self.assertEqual(ingredient["name"], "kosher salt")
                        self.assertEqual(ingredient["normalized_name"], "kosher salt")

    def test_run_merge_cancelled_after_selection_makes_no_changes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            indexes_dir = root / "indexes"
            indexes_dir.mkdir()
            recipes_dir = root / "recipes"
            recipes_dir.mkdir()
            write_json(
                indexes_dir / "ingredients.index.json",
                {"ingredients": INGREDIENTS},
            )
            recipe_path = recipes_dir / "recipe.json"
            write_json(
                recipe_path,
                {"ingredients": [salt_row(INGREDIENT_IDS["kosher salt"], "kosher salt")]},
            )

            answers = iter(["1,2", "", "1", "n"])

            def answer(prompt: str) -> str:
                return next(answers)

            with mock.patch.object(merge_ingredients, "INDEXES_DIR", indexes_dir), mock.patch.object(
                merge_ingredients, "RECIPES_DIR", recipes_dir
            ):
                stats = merge_ingredients.run_merge(input_fn=answer, output=lambda _message: None)

            self.assertEqual(stats.recipe_files_updated, 0)
            self.assertEqual(stats.ingredient_rows_updated, 0)
            recipe = json.loads(recipe_path.read_text(encoding="utf-8"))
            self.assertEqual(recipe["ingredients"][0]["ingredient_id"], INGREDIENT_IDS["kosher salt"])