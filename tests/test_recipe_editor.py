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

SCRIPT_PATH = REPO_ROOT / "py-scripts" / "recipe_editor.py"
SPEC = importlib.util.spec_from_file_location("recipe_editor", SCRIPT_PATH)
recipe_editor = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = recipe_editor
SPEC.loader.exec_module(recipe_editor)


def write_json(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload), encoding="utf-8")


class RecipeEditorTests(TestCase):
    def test_yes_no_prompts_accept_their_default_when_blank(self) -> None:
        self.assertTrue(recipe_editor.ask_yes_no("Continue?", default=True, input_fn=lambda _prompt: ""))
        self.assertFalse(recipe_editor.ask_yes_no("Merge?", default=False, input_fn=lambda _prompt: ""))
        with self.assertRaises(recipe_editor.PreviousIngredientRequested):
            recipe_editor.ask_review_confirmation("Correct?", input_fn=lambda _prompt: "r")

    def test_name_completion_uses_previously_accepted_names(self) -> None:
        original_names = recipe_editor.ACCEPTED_NAME_CHANGES.copy()
        try:
            recipe_editor.ACCEPTED_NAME_CHANGES.clear()
            recipe_editor.ACCEPTED_NAME_CHANGES.update({"cayenne powder", "Cayenne pepper", "kale leaves"})
            self.assertEqual(recipe_editor.name_completion_matches("cay"), ["Cayenne pepper", "cayenne powder"])
            self.assertEqual(recipe_editor.name_completion_matches("KA"), ["kale leaves"])
        finally:
            recipe_editor.ACCEPTED_NAME_CHANGES.clear()
            recipe_editor.ACCEPTED_NAME_CHANGES.update(original_names)

    def test_checkpoint_restores_accepted_name_completions(self) -> None:
        original_names = recipe_editor.ACCEPTED_NAME_CHANGES.copy()
        try:
            with tempfile.TemporaryDirectory() as temporary_directory, mock.patch.object(
                recipe_editor, "REPO_ROOT", Path(temporary_directory)
            ):
                recipe_editor.ACCEPTED_NAME_CHANGES.clear()
                recipe_editor.ACCEPTED_NAME_CHANGES.add("cayenne powder")
                recipe_editor.save_review_checkpoint({"11111111-1111-5111-8111-111111111111"})
                recipe_editor.ACCEPTED_NAME_CHANGES.clear()

                checked_ids = recipe_editor.load_review_checkpoint()

                self.assertEqual(checked_ids, {"11111111-1111-5111-8111-111111111111"})
                self.assertEqual(recipe_editor.name_completion_matches("cay"), ["cayenne powder"])
        finally:
            recipe_editor.ACCEPTED_NAME_CHANGES.clear()
            recipe_editor.ACCEPTED_NAME_CHANGES.update(original_names)

    def test_name_completion_uses_the_libedit_tab_binding(self) -> None:
        readline = mock.Mock(backend="editline", __doc__="libedit readline")
        with mock.patch.object(recipe_editor, "readline", readline):
            recipe_editor.configure_name_completion()
        readline.parse_and_bind.assert_called_once_with("bind ^I rl_complete")

    def test_name_completion_uses_the_gnu_readline_tab_binding(self) -> None:
        readline = mock.Mock(backend="readline", __doc__="GNU readline")
        with mock.patch.object(recipe_editor, "readline", readline):
            recipe_editor.configure_name_completion()
        readline.parse_and_bind.assert_called_once_with("tab: complete")

    def test_row_correction_accepts_a_new_name_in_the_reuse_prompt(self) -> None:
        ingredient = {
            "ingredient_id": "11111111-1111-5111-8111-111111111111",
            "name": "canned",
            "normalized_name": "canned",
            "quantity": "3",
            "unit": "ounces",
            "preparation": "diced tomatoes",
            "position": 1,
        }
        answers = iter(["kale leaves", "", "", ""])
        prompts: list[str] = []

        def answer(prompt: str) -> str:
            prompts.append(prompt)
            return next(answers)

        changed = recipe_editor.apply_row_correction(
            ingredient,
            copy_name_from="basil",
            input_fn=answer,
        )

        self.assertTrue(changed)
        self.assertEqual(prompts[0], 'Use the first ingredient name "basil"? [Y/name]: ')
        self.assertEqual(ingredient["name"], "kale leaves")
        self.assertEqual(
            ingredient["ingredient_id"],
            generate_catalog.stable_uuid(generate_catalog.INGREDIENT_NAMESPACE, "kale leaves"),
        )

    def test_confirmed_correct_ingredient_becomes_a_name_completion(self) -> None:
        original_names = recipe_editor.ACCEPTED_NAME_CHANGES.copy()
        try:
            with tempfile.TemporaryDirectory() as temporary_directory:
                recipe_path = Path(temporary_directory) / "recipe.json"
                ingredient_id = "11111111-1111-5111-8111-111111111111"
                write_json(
                    recipe_path,
                    {
                        "ingredients": [
                            {
                                "ingredient_id": ingredient_id,
                                "name": "cayenne powder",
                                "normalized_name": "cayenne powder",
                                "position": 1,
                            }
                        ]
                    },
                )
                recipe_editor.ACCEPTED_NAME_CHANGES.clear()
                with mock.patch.object(recipe_editor, "display_ingredient_references"):
                    recipe_editor.review_ingredient(
                        {"id": ingredient_id, "name": "cayenne powder"},
                        [recipe_path],
                        input_fn=lambda _prompt: "",
                        output=lambda _text: None,
                    )
                self.assertEqual(recipe_editor.name_completion_matches("cay"), ["cayenne powder"])
        finally:
            recipe_editor.ACCEPTED_NAME_CHANGES.clear()
            recipe_editor.ACCEPTED_NAME_CHANGES.update(original_names)

    def test_review_prompt_shows_the_next_three_ingredient_names(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            recipe_path = Path(temporary_directory) / "recipe.json"
            ingredient_id = "11111111-1111-5111-8111-111111111111"
            write_json(
                recipe_path,
                {
                    "ingredients": [
                        {
                            "ingredient_id": ingredient_id,
                            "name": "button mushroom",
                            "normalized_name": "button mushroom",
                            "position": 1,
                        }
                    ]
                },
            )
            prompts: list[str] = []

            def answer(prompt: str) -> str:
                prompts.append(prompt)
                return "y"

            with mock.patch.object(recipe_editor, "display_ingredient_references"):
                recipe_editor.review_ingredient(
                    {"id": ingredient_id, "name": "button mushroom"},
                    [recipe_path],
                    next_names=["butter", "butterfly", "buttermilk"],
                    input_fn=answer,
                    output=lambda _text: None,
                )

            self.assertEqual(
                prompts,
                ['Queue (next items: "butter", "butterfly", "buttermilk"). Are all rows for "button mushroom" correct? [Y/n/r]: '],
            )

    def test_review_corrects_a_recipe_row(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            indexes_dir = root / "indexes"
            recipes_dir = root / "by-id"
            indexes_dir.mkdir()
            recipes_dir.mkdir()
            ingredient_id = "11111111-1111-5111-8111-111111111111"
            write_json(
                indexes_dir / "ingredients.index.json",
                {"ingredients": [{"id": ingredient_id, "name": "a pich of salt"}]},
            )
            recipe_path = recipes_dir / "recipe.json"
            write_json(
                recipe_path,
                {
                    "ingredients": [
                        {
                            "ingredient_id": ingredient_id,
                            "name": "a pich of salt",
                            "normalized_name": "a pich of salt",
                            "quantity": "1",
                            "unit": None,
                            "preparation": None,
                            "position": 1,
                        }
                    ]
                },
            )

            answers = iter(["n", "o", "a pinch of salt", "2", "tsp", "ground", "n"])
            outputs: list[str] = []
            with mock.patch.object(recipe_editor, "REPO_ROOT", root), mock.patch.object(
                recipe_editor, "INDEXES_DIR", indexes_dir
            ), mock.patch.object(recipe_editor, "RECIPES_DIR", recipes_dir), mock.patch.object(
                recipe_editor, "display_ingredient_references"
            ):
                stats = recipe_editor.run_interactive_review(input_fn=lambda _prompt: next(answers), output=outputs.append)

            self.assertIn(
                json.dumps(
                    {
                        "ingredient_id": ingredient_id,
                        "name": "a pich of salt",
                        "normalized_name": "a pich of salt",
                        "quantity": "1",
                        "unit": None,
                        "preparation": None,
                        "position": 1,
                    },
                    indent=2,
                    ensure_ascii=True,
                ),
                outputs,
            )
            ingredient = json.loads(recipe_path.read_text(encoding="utf-8"))["ingredients"][0]
            self.assertEqual(stats.recipe_files_updated, 1)
            self.assertEqual(stats.ingredient_rows_updated, 1)
            self.assertEqual(ingredient["name"], "a pinch of salt")
            self.assertEqual(ingredient["quantity"], "2")
            self.assertEqual(ingredient["unit"], "tsp")
            self.assertEqual(ingredient["preparation"], "ground")
            self.assertEqual(ingredient["normalized_name"], "a pinch of salt")
            self.assertEqual(
                ingredient["ingredient_id"],
                generate_catalog.stable_uuid(generate_catalog.INGREDIENT_NAMESPACE, "a pinch of salt"),
            )

    def test_review_can_copy_the_first_corrected_name(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            recipe_path = Path(temporary_directory) / "recipe.json"
            ingredient_id = "11111111-1111-5111-8111-111111111111"
            write_json(
                recipe_path,
                {
                    "ingredients": [
                        {
                            "ingredient_id": ingredient_id,
                            "name": "a pich of salt",
                            "normalized_name": "a pich of salt",
                            "quantity": "1",
                            "unit": "tsp",
                            "preparation": None,
                            "position": 1,
                        },
                        {
                            "ingredient_id": ingredient_id,
                            "name": "salt typo",
                            "normalized_name": "salt typo",
                            "quantity": "2",
                            "unit": "tsp",
                            "preparation": None,
                            "position": 2,
                        },
                    ]
                },
            )

            answers = iter(["n", "o", "salt", "", "", "", "y", "", "", ""])
            with mock.patch.object(recipe_editor, "display_ingredient_references"):
                stats = recipe_editor.review_ingredient(
                    {"id": ingredient_id, "name": "a pich of salt"},
                    [recipe_path],
                    input_fn=lambda _prompt: next(answers),
                    output=lambda _text: None,
                )

            ingredients = json.loads(recipe_path.read_text(encoding="utf-8"))["ingredients"]
            self.assertEqual(stats.ingredient_rows_updated, 2)
            self.assertEqual([ingredient["name"] for ingredient in ingredients], ["salt", "salt"])
            self.assertEqual(
                [ingredient["ingredient_id"] for ingredient in ingredients],
                [
                    generate_catalog.stable_uuid(generate_catalog.INGREDIENT_NAMESPACE, "salt"),
                    generate_catalog.stable_uuid(generate_catalog.INGREDIENT_NAMESPACE, "salt"),
                ],
            )

    def test_review_name_only_updates_every_matching_recipe_row(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            ingredient_id = "11111111-1111-5111-8111-111111111111"
            recipe_paths = [root / "first.json", root / "second.json"]
            for position, recipe_path in enumerate(recipe_paths, start=1):
                write_json(
                    recipe_path,
                    {
                        "ingredients": [
                            {
                                "ingredient_id": ingredient_id,
                                "name": "a pich of salt",
                                "normalized_name": "a pich of salt",
                                "quantity": str(position),
                                "unit": "tsp",
                                "preparation": "ground",
                                "position": 1,
                            }
                        ]
                    },
                )

            answers = iter(["n", "asparagus"])
            with mock.patch.object(recipe_editor, "display_ingredient_references"):
                stats = recipe_editor.review_ingredient(
                    {"id": ingredient_id, "name": "a pich of salt"},
                    recipe_paths,
                    input_fn=lambda _prompt: next(answers),
                    output=lambda _text: None,
                )

            expected_id = generate_catalog.stable_uuid(generate_catalog.INGREDIENT_NAMESPACE, "asparagus")
            self.assertEqual(stats.recipe_files_updated, 2)
            self.assertEqual(stats.ingredient_rows_updated, 2)
            for recipe_path in recipe_paths:
                ingredient = json.loads(recipe_path.read_text(encoding="utf-8"))["ingredients"][0]
                self.assertEqual(ingredient["name"], "asparagus")
                self.assertEqual(ingredient["ingredient_id"], expected_id)
                self.assertEqual(ingredient["unit"], "tsp")
                self.assertEqual(ingredient["preparation"], "ground")

    def test_review_checkpoint_resumes_after_interruption(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            indexes_dir = root / "indexes"
            recipes_dir = root / "by-id"
            indexes_dir.mkdir()
            recipes_dir.mkdir()
            first_id = "11111111-1111-5111-8111-111111111111"
            second_id = "22222222-2222-5222-8222-222222222222"
            write_json(
                indexes_dir / "ingredients.index.json",
                {
                    "ingredients": [
                        {"id": first_id, "name": "first"},
                        {"id": second_id, "name": "second"},
                    ]
                },
            )

            with mock.patch.object(recipe_editor, "REPO_ROOT", root), mock.patch.object(
                recipe_editor, "INDEXES_DIR", indexes_dir
            ), mock.patch.object(recipe_editor, "RECIPES_DIR", recipes_dir), mock.patch.object(
                recipe_editor,
                "review_ingredient",
                side_effect=[recipe_editor.EditStats(), KeyboardInterrupt()],
            ):
                with self.assertRaises(KeyboardInterrupt):
                    recipe_editor.run_interactive_review(output=lambda _text: None)
                self.assertEqual(recipe_editor.load_review_checkpoint(), {first_id})

            with mock.patch.object(recipe_editor, "REPO_ROOT", root), mock.patch.object(
                recipe_editor, "INDEXES_DIR", indexes_dir
            ), mock.patch.object(recipe_editor, "RECIPES_DIR", recipes_dir), mock.patch.object(
                recipe_editor, "review_ingredient", return_value=recipe_editor.EditStats()
            ) as review:
                recipe_editor.run_interactive_review(input_fn=lambda _prompt: "n", output=lambda _text: None)

            self.assertEqual(review.call_count, 1)
            self.assertEqual(review.call_args.args[0]["id"], second_id)
            self.assertFalse((root / recipe_editor.CHECKPOINT_FILE_NAME).exists())

    def test_review_can_return_to_the_previous_ingredient(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            indexes_dir = root / "indexes"
            recipes_dir = root / "by-id"
            indexes_dir.mkdir()
            recipes_dir.mkdir()
            first_id = "11111111-1111-5111-8111-111111111111"
            second_id = "22222222-2222-5222-8222-222222222222"
            write_json(
                indexes_dir / "ingredients.index.json",
                {
                    "ingredients": [
                        {"id": first_id, "name": "first"},
                        {"id": second_id, "name": "second"},
                    ]
                },
            )
            outputs: list[str] = []

            with mock.patch.object(recipe_editor, "REPO_ROOT", root), mock.patch.object(
                recipe_editor, "INDEXES_DIR", indexes_dir
            ), mock.patch.object(recipe_editor, "RECIPES_DIR", recipes_dir), mock.patch.object(
                recipe_editor,
                "review_ingredient",
                side_effect=[
                    recipe_editor.EditStats(),
                    recipe_editor.PreviousIngredientRequested(),
                    recipe_editor.EditStats(),
                    recipe_editor.EditStats(),
                ],
            ) as review:
                recipe_editor.run_interactive_review(input_fn=lambda _prompt: "n", output=outputs.append)

            self.assertEqual(
                [call.args[0]["id"] for call in review.call_args_list],
                [first_id, second_id, first_id, second_id],
            )
            self.assertIn("Returning to the previous ingredient.", outputs)
            self.assertFalse((root / recipe_editor.CHECKPOINT_FILE_NAME).exists())

    def test_merge_same_name_ingredients_uses_one_deterministic_uuid(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            recipe_path = Path(temporary_directory) / "recipe.json"
            write_json(
                recipe_path,
                {
                    "ingredients": [
                        {
                            "ingredient_id": "11111111-1111-5111-8111-111111111111",
                            "name": "All-Purpose Flour",
                            "normalized_name": "stale value",
                            "position": 1,
                        },
                        {
                            "ingredient_id": "22222222-2222-5222-8222-222222222222",
                            "name": "all-purpose flour",
                            "normalized_name": "all-purpose flour",
                            "position": 2,
                        },
                    ]
                },
            )

            stats = recipe_editor.merge_same_name_ingredients([recipe_path])

            ingredients = json.loads(recipe_path.read_text(encoding="utf-8"))["ingredients"]
            expected_id = generate_catalog.stable_uuid(generate_catalog.INGREDIENT_NAMESPACE, "all-purpose flour")
            self.assertEqual(stats.recipe_files_updated, 1)
            self.assertEqual(stats.ingredient_rows_updated, 2)
            self.assertEqual([ingredient["ingredient_id"] for ingredient in ingredients], [expected_id, expected_id])
            self.assertEqual([ingredient["normalized_name"] for ingredient in ingredients], ["all-purpose flour", "all-purpose flour"])


if __name__ == "__main__":
    from unittest import main

    main()
