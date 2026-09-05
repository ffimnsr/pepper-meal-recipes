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

SCRIPT_PATH = REPO_ROOT / "py-scripts" / "resolve_ingredient_review.py"
SPEC = importlib.util.spec_from_file_location("resolve_ingredient_review", SCRIPT_PATH)
resolve = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = resolve
SPEC.loader.exec_module(resolve)

MIRIN_ROW = {
    "ingredient_id": "aaaaaaaa-aaaa-5aaa-8aaa-aaaaaaaaaaaa",
    "name": "Mirin or cooking wine",
    "normalized_name": "mirin or cooking wine",
    "quantity": "2",
    "unit": "tablespoons",
    "preparation": None,
    "position": 3,
}
RICE_ROW = {
    "ingredient_id": "bbbbbbbb-bbbb-5bbb-8bbb-bbbbbbbbbbbb",
    "name": "long grain white rice (sinandomeng or dinorado)",
    "normalized_name": "long grain white rice (sinandomeng or dinorado)",
    "quantity": "1",
    "unit": "cup",
    "preparation": None,
    "position": 1,
}
SALT_ROW = {
    "ingredient_id": "cccccccc-cccc-5ccc-8ccc-cccccccccccc",
    "name": "salt",
    "normalized_name": "salt",
    "quantity": "1/4",
    "unit": "teaspoon",
    "preparation": None,
    "position": 3,
}
REVIEW_ENTRY = {
    "recipe_id": "recipe-a",
    "recipe_slug": "chicken-teriyaki",
    "recipe_name": "Chicken Teriyaki",
    "position": 3,
    "original_text": "2 tablespoons Mirin or cooking wine",
    "quantity": "2",
    "unit": "tablespoons",
    "cleaned_name": "mirin or cooking wine",
    "replacements": [],
    "issue_types": ["ambiguous_connector"],
    "resolution": "review",
}


def write_json(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload), encoding="utf-8")


def recipe_payload() -> dict:
    return {
        "id": "recipe-a",
        "ingredients": [
            {
                "ingredient_id": "11111111-1111-5111-8111-111111111111",
                "name": "soy sauce",
                "normalized_name": "soy sauce",
                "quantity": "1/4",
                "unit": "cup",
                "preparation": None,
                "position": 1,
            },
            {
                "ingredient_id": "22222222-2222-5222-8222-222222222222",
                "name": "brown sugar",
                "normalized_name": "brown sugar",
                "quantity": "2",
                "unit": "tablespoons",
                "preparation": None,
                "position": 2,
            },
            dict(MIRIN_ROW),
            dict(SALT_ROW),
        ],
    }


class ResolveIngredientReviewTests(TestCase):
    def test_rendered_ingredient_text_joins_quantity_unit_and_name(self) -> None:
        self.assertEqual(resolve.rendered_ingredient_text(MIRIN_ROW), "2 tablespoons Mirin or cooking wine")

    def test_find_ingredient_row_matches_by_position_and_text(self) -> None:
        recipe = recipe_payload()
        index, row = resolve.find_ingredient_row(recipe, REVIEW_ENTRY)
        self.assertEqual(index, 2)
        self.assertEqual(row, MIRIN_ROW)

    def test_find_ingredient_row_falls_back_to_rendered_text_after_shift(self) -> None:
        recipe = recipe_payload()
        # Simulate an earlier edit that renumbered rows: move salt into the position 3 slot.
        recipe["ingredients"].pop(2)
        resolve.renumber_ingredient_positions(recipe)
        index, row = resolve.find_ingredient_row(recipe, REVIEW_ENTRY)
        self.assertEqual(row, SALT_ROW)
        self.assertEqual(recipe["ingredients"][index]["name"], "salt")

    def test_split_name_suggestion_uses_replacements_then_connector(self) -> None:
        entry = dict(REVIEW_ENTRY)
        entry["replacements"] = ["salt", "pepper"]
        self.assertEqual(resolve.split_name_suggestion(entry, MIRIN_ROW), ["salt", "pepper"])

        entry = dict(REVIEW_ENTRY)
        entry["replacements"] = []
        self.assertEqual(resolve.split_name_suggestion(entry, MIRIN_ROW), ["mirin", "cooking wine"])

        rice_entry = dict(REVIEW_ENTRY)
        rice_entry["cleaned_name"] = "long grain white rice"
        rice_entry["original_text"] = "1 cup long grain white rice (sinandomeng or dinorado)"
        self.assertEqual(resolve.split_name_suggestion(rice_entry, RICE_ROW), [])

    def test_update_ingredient_name_assigns_canonical_identity(self) -> None:
        row = dict(MIRIN_ROW)
        self.assertTrue(resolve.update_ingredient_name(row, "mirin"))
        self.assertEqual(row["name"], "mirin")
        self.assertEqual(row["normalized_name"], "mirin")
        self.assertEqual(
            row["ingredient_id"],
            generate_catalog.stable_uuid(generate_catalog.INGREDIENT_NAMESPACE, "mirin"),
        )
        self.assertFalse(resolve.update_ingredient_name(row, "mirin"))

    def test_rename_hints_report_merge_into_existing_ingredient(self) -> None:
        existing_id = generate_catalog.stable_uuid(generate_catalog.INGREDIENT_NAMESPACE, "mirin")
        messages: list[str] = []
        resolve.render_rename_hints(
            "mirin",
            {existing_id: {"name": "mirin", "recipe_count": 4}},
            output=messages.append,
        )
        self.assertTrue(any("merges into existing ingredient 'mirin' (4 recipe(s))" in message for message in messages))

    def test_rename_hints_warn_about_ambiguous_or_excluded_names(self) -> None:
        messages: list[str] = []
        warnings = resolve.render_rename_hints("mirin or sake", {}, output=messages.append)
        self.assertTrue(any("still flags" in message for message in messages))
        self.assertEqual(warnings, ["the generator still flags 'or'/'and/or' names as ambiguous"])

        messages = []
        warnings = resolve.render_rename_hints("water for boiling", {}, output=messages.append)
        self.assertEqual(warnings, ["the generator excludes this name as a non-ingredient"])

    def test_split_ingredient_row_replaces_one_row_and_renumbers(self) -> None:
        recipe = recipe_payload()
        answers = iter(["mirin|cooking wine", "", "", "", "", "", ""])

        def answer(prompt: str) -> str:
            return next(answers)

        changed = resolve.split_ingredient_row(
            recipe,
            2,
            REVIEW_ENTRY,
            input_fn=answer,
            output=lambda _message: None,
        )

        self.assertTrue(changed)
        self.assertEqual(len(recipe["ingredients"]), 5)
        self.assertEqual(
            [ingredient.get("name") for ingredient in recipe["ingredients"]],
            ["soy sauce", "brown sugar", "mirin", "cooking wine", "salt"],
        )
        self.assertEqual(
            [ingredient.get("position") for ingredient in recipe["ingredients"]],
            [1, 2, 3, 4, 5],
        )
        self.assertEqual(recipe["ingredients"][2]["quantity"], "2")
        self.assertEqual(recipe["ingredients"][2]["unit"], "tablespoons")
        self.assertEqual(
            recipe["ingredients"][3]["ingredient_id"],
            generate_catalog.stable_uuid(generate_catalog.INGREDIENT_NAMESPACE, "cooking wine"),
        )

    def test_exclude_row_removes_and_renumbers(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            recipe_path = Path(temporary_directory) / "recipe.json"
            recipe = recipe_payload()
            write_json(recipe_path, recipe)
            answers = iter(["e", "y"])

            def answer(prompt: str) -> str:
                return next(answers)

            changed, stats = resolve.resolve_entry(
                REVIEW_ENTRY,
                recipe_path,
                recipe,
                2,
                recipe["ingredients"][2],
                index_by_id={},
                number=1,
                total=1,
                input_fn=answer,
                output=lambda _message: None,
            )

            self.assertTrue(changed)
            self.assertEqual(stats.ingredient_rows_updated, 1)
            self.assertEqual(
                [ingredient.get("name") for ingredient in recipe["ingredients"]],
                ["soy sauce", "brown sugar", "salt"],
            )
            self.assertEqual(
                [ingredient.get("position") for ingredient in recipe["ingredients"]],
                [1, 2, 3],
            )

    def test_edit_other_fields_updates_quantity_unit_and_preparation(self) -> None:
        row = dict(MIRIN_ROW)
        answers = iter(["3", "", "mixed"])

        def answer(prompt: str) -> str:
            return next(answers)

        self.assertTrue(resolve.edit_other_fields(row, input_fn=answer))
        self.assertEqual(row["quantity"], "3")
        self.assertEqual(row["unit"], "tablespoons")
        self.assertEqual(row["preparation"], "mixed")

    def test_checkpoint_round_trip(self) -> None:
        original_root = resolve.REPO_ROOT
        try:
            with tempfile.TemporaryDirectory() as temporary_directory:
                resolve.REPO_ROOT = Path(temporary_directory)
                keys = {("recipe-a", 3, "2 tablespoons Mirin or cooking wine")}
                resolve.save_review_checkpoint(keys)
                self.assertEqual(resolve.load_review_checkpoint(), keys)
                resolve.clear_review_checkpoint()
                self.assertEqual(resolve.load_review_checkpoint(), set())
        finally:
            resolve.REPO_ROOT = original_root

    def test_save_ignored_ingredient_round_trip(self) -> None:
        original_root = resolve.REPO_ROOT
        try:
            with tempfile.TemporaryDirectory() as temporary_directory:
                resolve.REPO_ROOT = Path(temporary_directory)
                ingredient_id = generate_catalog.stable_uuid(
                    generate_catalog.INGREDIENT_NAMESPACE, "mirin or cooking wine"
                )
                resolve.save_ignored_ingredient(ingredient_id, "mirin or cooking wine")
                resolve.save_ignored_ingredient(ingredient_id, "mirin or cooking wine")
                ignored = resolve.load_ignored_ingredients()
                self.assertEqual(len(ignored), 1)
                self.assertEqual(ignored[0]["ingredient_id"], ingredient_id)
        finally:
            resolve.REPO_ROOT = original_root

    def test_run_resolve_ignores_entry_and_writes_ignore_file(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            indexes_dir = root / "indexes"
            indexes_dir.mkdir()
            recipes_dir = root / "recipes"
            recipes_dir.mkdir()
            write_json(root / resolve.INGREDIENT_REVIEW_FILE.name, {"entries": [dict(REVIEW_ENTRY)]})
            write_json(indexes_dir / "ingredients.index.json", {"ingredients": []})
            recipe_path = recipes_dir / "recipe-a.json"
            write_json(recipe_path, recipe_payload())
            original_contents = recipe_path.read_text(encoding="utf-8")

            answers = iter(["i", "y"])

            def answer(prompt: str) -> str:
                return next(answers)

            original_root = resolve.REPO_ROOT
            try:
                resolve.REPO_ROOT = root
                with mock.patch.object(resolve, "INDEXES_DIR", indexes_dir), mock.patch.object(
                    resolve, "RECIPES_DIR", recipes_dir
                ), mock.patch.object(
                    resolve, "INGREDIENT_REVIEW_FILE", root / resolve.INGREDIENT_REVIEW_FILE.name
                ):
                    stats = resolve.run_resolve(input_fn=answer, output=lambda _message: None)
            finally:
                resolve.REPO_ROOT = original_root

            self.assertEqual(stats.ingredients_ignored, 1)
            self.assertEqual(stats.entries_resolved, 1)
            self.assertEqual(stats.recipe_files_updated, 0)
            self.assertEqual(recipe_path.read_text(encoding="utf-8"), original_contents)
            payload = json.loads((root / resolve.IGNORE_FILE_NAME).read_text(encoding="utf-8"))
            expected_id = generate_catalog.stable_uuid(
                generate_catalog.INGREDIENT_NAMESPACE, "mirin or cooking wine"
            )
            self.assertEqual(payload["ignored"][0]["ingredient_id"], expected_id)

    def test_run_resolve_end_to_end(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            indexes_dir = root / "indexes"
            indexes_dir.mkdir()
            recipes_dir = root / "recipes"
            recipes_dir.mkdir()
            write_json(root / resolve.INGREDIENT_REVIEW_FILE.name, {"entries": [dict(REVIEW_ENTRY)]})
            write_json(indexes_dir / "ingredients.index.json", {"ingredients": []})
            recipe_path = recipes_dir / "recipe-a.json"
            write_json(recipe_path, recipe_payload())

            answers = iter(["mirin", "y"])

            def answer(prompt: str) -> str:
                return next(answers)

            with mock.patch.object(resolve, "INDEXES_DIR", indexes_dir), mock.patch.object(
                resolve, "RECIPES_DIR", recipes_dir
            ), mock.patch.object(
                resolve, "INGREDIENT_REVIEW_FILE", root / resolve.INGREDIENT_REVIEW_FILE.name
            ):
                stats = resolve.run_resolve(input_fn=answer, output=lambda _message: None)

            self.assertEqual(stats.recipe_files_updated, 1)
            self.assertEqual(stats.ingredient_rows_updated, 1)
            self.assertEqual(stats.entries_resolved, 1)
            recipe = json.loads(recipe_path.read_text(encoding="utf-8"))
            self.assertEqual(recipe["ingredients"][2]["name"], "mirin")
            self.assertEqual(
                recipe["ingredients"][2]["ingredient_id"],
                generate_catalog.stable_uuid(generate_catalog.INGREDIENT_NAMESPACE, "mirin"),
            )
            self.assertFalse((root / resolve.CHECKPOINT_FILE_NAME).exists())

    def test_run_resolve_quit_midway_keeps_checkpoint_and_earlier_edits(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            indexes_dir = root / "indexes"
            indexes_dir.mkdir()
            recipes_dir = root / "recipes"
            recipes_dir.mkdir()
            first_entry = dict(REVIEW_ENTRY)
            second_entry = dict(REVIEW_ENTRY)
            second_entry["recipe_id"] = "recipe-b"
            second_entry["recipe_slug"] = "crispy-crablets"
            second_entry["recipe_name"] = "Crispy Crablets"
            second_entry["position"] = 6
            second_entry["original_text"] = "1/4 cup gin or sherry"
            second_entry["quantity"] = "1/4"
            second_entry["unit"] = "cup"
            second_entry["cleaned_name"] = "gin or sherry"
            write_json(
                root / resolve.INGREDIENT_REVIEW_FILE.name,
                {"entries": [first_entry, second_entry]},
            )
            write_json(indexes_dir / "ingredients.index.json", {"ingredients": []})
            recipes_dir.mkdir(exist_ok=True)
            recipe_a_path = recipes_dir / "recipe-a.json"
            recipe_b_path = recipes_dir / "recipe-b.json"
            write_json(recipe_a_path, recipe_payload())
            write_json(
                recipe_b_path,
                {
                    "id": "recipe-b",
                    "ingredients": [
                        {
                            "ingredient_id": "33333333-3333-5333-8333-333333333333",
                            "name": "gin or sherry",
                            "normalized_name": "gin or sherry",
                            "quantity": "1/4",
                            "unit": "cup",
                            "preparation": None,
                            "position": 6,
                        }
                    ],
                },
            )

            answers = iter(["mirin", "q"])

            def answer(prompt: str) -> str:
                return next(answers)

            with mock.patch.object(resolve, "INDEXES_DIR", indexes_dir), mock.patch.object(
                resolve, "RECIPES_DIR", recipes_dir
            ), mock.patch.object(resolve, "REPO_ROOT", root), mock.patch.object(
                resolve, "INGREDIENT_REVIEW_FILE", root / resolve.INGREDIENT_REVIEW_FILE.name
            ), self.assertRaises(SystemExit):
                resolve.run_resolve(input_fn=answer, output=lambda _message: None)

            checkpoint_path = root / resolve.CHECKPOINT_FILE_NAME
            self.assertTrue(checkpoint_path.exists())
            checkpoint = json.loads(checkpoint_path.read_text(encoding="utf-8"))
            self.assertEqual(
                checkpoint["resolved"],
                [{"recipe_id": "recipe-a", "position": 3, "original_text": "2 tablespoons Mirin or cooking wine"}],
            )
            recipe_a = json.loads(recipe_a_path.read_text(encoding="utf-8"))
            self.assertEqual(recipe_a["ingredients"][2]["name"], "mirin")
            recipe_b = json.loads(recipe_b_path.read_text(encoding="utf-8"))
            self.assertEqual(recipe_b["ingredients"][0]["name"], "gin or sherry")

    def test_run_resolve_skips_already_resolved_entries(self) -> None:
        original_root = resolve.REPO_ROOT
        try:
            with tempfile.TemporaryDirectory() as temporary_directory:
                root = Path(temporary_directory)
                indexes_dir = root / "indexes"
                indexes_dir.mkdir()
                recipes_dir = root / "recipes"
                recipes_dir.mkdir()
                resolve.REPO_ROOT = root
                write_json(root / resolve.INGREDIENT_REVIEW_FILE.name, {"entries": [dict(REVIEW_ENTRY)]})
                write_json(indexes_dir / "ingredients.index.json", {"ingredients": []})
                recipe_path = recipes_dir / "recipe-a.json"
                original = recipe_payload()
                write_json(recipe_path, original)

                resolve.save_review_checkpoint({("recipe-a", 3, "2 tablespoons Mirin or cooking wine")})
                messages: list[str] = []
                with mock.patch.object(resolve, "INDEXES_DIR", indexes_dir), mock.patch.object(
                    resolve, "RECIPES_DIR", recipes_dir
                ), mock.patch.object(
                    resolve, "INGREDIENT_REVIEW_FILE", root / resolve.INGREDIENT_REVIEW_FILE.name
                ):
                    stats = resolve.run_resolve(input_fn=lambda _prompt: "k", output=messages.append)

                self.assertEqual(stats.recipe_files_updated, 0)
                self.assertTrue(any("already resolved" in message for message in messages))
                self.assertEqual(json.loads(recipe_path.read_text(encoding="utf-8")), original)
        finally:
            resolve.REPO_ROOT = original_root

    def test_run_resolve_skips_entries_without_a_recipe_file(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            indexes_dir = root / "indexes"
            indexes_dir.mkdir()
            recipes_dir = root / "recipes"
            recipes_dir.mkdir()
            write_json(root / resolve.INGREDIENT_REVIEW_FILE.name, {"entries": [dict(REVIEW_ENTRY)]})
            write_json(indexes_dir / "ingredients.index.json", {"ingredients": []})

            messages: list[str] = []
            with mock.patch.object(resolve, "INDEXES_DIR", indexes_dir), mock.patch.object(
                resolve, "RECIPES_DIR", recipes_dir
            ), mock.patch.object(resolve, "REPO_ROOT", root), mock.patch.object(
                resolve, "INGREDIENT_REVIEW_FILE", root / resolve.INGREDIENT_REVIEW_FILE.name
            ):
                stats = resolve.run_resolve(input_fn=lambda _prompt: "k", output=messages.append)

            self.assertEqual(stats.recipe_files_updated, 0)
            self.assertTrue(any("recipe file not found" in message for message in messages))