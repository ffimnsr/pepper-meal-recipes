import importlib.util
import sys
from pathlib import Path
from unittest import TestCase, mock

from scripts.generate_catalog import INGREDIENT_NAMESPACE, stable_uuid


SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "recipe-editor.py"
SPEC = importlib.util.spec_from_file_location("recipe_editor", SCRIPT_PATH)
recipe_editor = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = recipe_editor
SPEC.loader.exec_module(recipe_editor)


class RecipeEditorTests(TestCase):
    def test_merge_rewrites_all_matching_rows(self) -> None:
        recipe = {
            "ingredients": [
                {
                    "ingredient_id": "11111111-1111-5111-8111-111111111111",
                    "name": "a pich of salt",
                    "normalized_name": "a pich of salt",
                    "position": 1,
                },
                {
                    "ingredient_id": "22222222-2222-5222-8222-222222222222",
                    "name": "a pinch salt",
                    "normalized_name": "a pinch salt",
                    "position": 2,
                },
                {
                    "ingredient_id": "33333333-3333-5333-8333-333333333333",
                    "name": "pepper",
                    "normalized_name": "pepper",
                    "position": 3,
                },
            ]
        }

        target = recipe_editor.IngredientTarget(
            ingredient_id="11111111-1111-5111-8111-111111111111",
            name="a pich of salt",
            normalized_name="a pich of salt",
        )

        updated = recipe_editor.update_recipe_ingredients(
            recipe,
            {
                "11111111-1111-5111-8111-111111111111",
                "22222222-2222-5222-8222-222222222222",
            },
            target,
        )

        self.assertEqual(updated, 2)
        self.assertEqual(recipe["ingredients"][0]["ingredient_id"], target.ingredient_id)
        self.assertEqual(recipe["ingredients"][1]["ingredient_id"], target.ingredient_id)
        self.assertEqual(recipe["ingredients"][0]["name"], "a pich of salt")
        self.assertEqual(recipe["ingredients"][1]["normalized_name"], "a pich of salt")
        self.assertEqual(recipe["ingredients"][2]["ingredient_id"], "33333333-3333-5333-8333-333333333333")

    def test_rename_builds_deterministic_target(self) -> None:
        ingredient_id = "11111111-1111-5111-8111-111111111111"
        ingredient_name = "a pinch of salt"

        with mock.patch.object(
            recipe_editor,
            "load_ingredient_index",
            return_value={
                ingredient_id: {
                    "id": ingredient_id,
                    "name": "a pich of salt",
                    "normalized_name": "a pich of salt",
                }
            },
        ), mock.patch.object(recipe_editor, "rewrite_recipes", return_value=recipe_editor.EditStats()) as rewrite:
            stats = recipe_editor.run_rename(ingredient_id, ingredient_name)

        rewrite.assert_called_once()
        self.assertEqual(stats.recipe_files_updated, 0)
        self.assertEqual(stats.ingredient_rows_updated, 0)

        source_ids, target = rewrite.call_args.args
        self.assertEqual(source_ids, {ingredient_id})
        self.assertEqual(target.name, "a pinch of salt")
        self.assertEqual(target.normalized_name, "a pinch of salt")
        self.assertEqual(target.ingredient_id, stable_uuid(INGREDIENT_NAMESPACE, "a pinch of salt"))


if __name__ == "__main__":
    from unittest import main

    main()
