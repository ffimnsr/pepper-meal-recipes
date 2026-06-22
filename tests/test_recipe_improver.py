import importlib.util
import json
import os
import sys
import tempfile
from pathlib import Path
from unittest import TestCase, mock


SCRIPT_PATH = Path(__file__).resolve().parents[1] / "py-scripts" / "recipe_improver.py"
SPEC = importlib.util.spec_from_file_location("recipe_improver", SCRIPT_PATH)
recipe_improver = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = recipe_improver
SPEC.loader.exec_module(recipe_improver)


class RecipeImproverTests(TestCase):
    def test_parse_args_supports_yes_flag(self) -> None:
        with mock.patch.object(sys, "argv", ["recipe_improver.py", "-y"]):
            args = recipe_improver.parse_args()

        self.assertTrue(args.yes)

    def test_parse_model_response_accepts_code_fence(self) -> None:
        payload = recipe_improver.parse_model_response(
            """```json
            {"recipe": {"name": "Improved"}, "summary": "Better"}
            ```"""
        )

        self.assertEqual(payload["recipe"]["name"], "Improved")
        self.assertEqual(payload["summary"], "Better")

    def test_parse_model_response_accepts_trailing_text(self) -> None:
        payload = recipe_improver.parse_model_response(
            '{"recipe": {"name": "Improved"}, "summary": "Better"}\n\nNotes: done.'
        )

        self.assertEqual(payload["recipe"]["name"], "Improved")
        self.assertEqual(payload["summary"], "Better")

    def test_next_recipe_path_skips_reviewed_ids(self) -> None:
        with mock.patch.object(
            recipe_improver,
            "list_recipe_files",
            return_value=[Path("/tmp/a.json"), Path("/tmp/b.json")],
        ):
            path = recipe_improver.next_recipe_path(recipe_improver.ResumeState(["a"], []))

        self.assertEqual(path, Path("/tmp/b.json"))

    def test_state_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            state_path = Path(temp_dir) / "state.json"
            recipe_improver.save_state(state_path, recipe_improver.ResumeState(["a", "b"], ["b"]))
            state = recipe_improver.load_state(state_path)

        self.assertEqual(state.reviewed_ids, ["a", "b"])
        self.assertEqual(state.written_ids, ["b"])

    def test_build_payload_uses_model_argument(self) -> None:
        payload = recipe_improver.build_payload({"name": "Test"}, "test/model")

        self.assertEqual(payload["model"], "test/model")
        self.assertEqual(payload["messages"][1]["content"], json.dumps({"name": "Test"}, indent=2, ensure_ascii=False))
        self.assertEqual(payload["response_format"], {"type": "json_object"})
        self.assertEqual(payload["plugins"], [{"id": "response-healing"}])
        self.assertIn("categories and tags must remain arrays of objects", payload["messages"][0]["content"])

    def test_normalize_improved_recipe_restores_invalid_tags(self) -> None:
        original = {
            "id": "orig-id",
            "slug": "orig-slug",
            "schema_version": 1,
            "updated_at": 1,
            "revision": 1,
            "categories": [{"id": "c1", "slug": "condiment", "name": "Condiment"}],
            "tags": [{"id": "t1", "slug": "spicy", "name": "Spicy"}],
        }
        improved = {
            "id": "new-id",
            "slug": "new-slug",
            "schema_version": 99,
            "updated_at": 2,
            "revision": 3,
            "categories": [{"id": "c2", "slug": "dip", "name": "Dip"}],
            "tags": ["spicy"],
        }

        normalized, repairs = recipe_improver.normalize_improved_recipe(original, improved)

        self.assertEqual(normalized["id"], "orig-id")
        self.assertEqual(normalized["slug"], "orig-slug")
        self.assertEqual(normalized["schema_version"], 1)
        self.assertEqual(normalized["updated_at"], 1)
        self.assertEqual(normalized["revision"], 1)
        self.assertEqual(normalized["categories"], [{"id": "c2", "slug": "dip", "name": "Dip"}])
        self.assertEqual(normalized["tags"], [{"id": "t1", "slug": "spicy", "name": "Spicy"}])
        self.assertTrue(any("restored tags" in repair for repair in repairs))

    def test_validate_recipe_with_manual_fix_revalidates_after_edit(self) -> None:
        original = {
            "id": "orig-id",
            "slug": "orig-slug",
            "schema_version": 1,
            "updated_at": 1,
            "revision": 1,
            "categories": [],
            "tags": [],
        }
        invalid = dict(original, tags=["spicy"])
        edited = dict(original, tags=[{"id": "t1", "slug": "spicy", "name": "Spicy"}])

        validation_error = recipe_improver.ValidationError("'spicy' is not of type 'object'")

        with mock.patch.object(
            recipe_improver,
            "validate_recipe",
            side_effect=[validation_error, None],
        ), mock.patch.object(
            recipe_improver,
            "prompt_yes_no",
            return_value=True,
        ), mock.patch.object(
            recipe_improver,
            "edit_recipe_in_vim",
            return_value=edited,
        ):
            result = recipe_improver.validate_recipe_with_manual_fix(original, invalid, validator=object())

        self.assertEqual(result["tags"][0]["slug"], "spicy")

    def test_call_openrouter_parses_recipe_response(self) -> None:
        class Response:
            headers = {
                "X-Generation-Id": "gen-test",
            }

            def raise_for_status(self) -> None:
                return None

            def json(self) -> dict:
                return {
                    "usage": {
                        "prompt_tokens": 10,
                        "completion_tokens": 20,
                        "total_tokens": 30,
                    },
                    "openrouter_metadata": {
                        "strategy": "direct",
                        "attempt": 1,
                        "summary": "available=1, selected=MockProvider",
                        "attempts": [
                            {
                                "provider": "MockProvider",
                                "model": "test/model",
                                "status": 200,
                            }
                        ],
                        "pipeline": [
                            {"type": "response_healing", "name": "response-healing"}
                        ],
                    },
                    "choices": [
                        {
                            "message": {
                                "content": "{\"recipe\": {\"name\": \"Improved\"}, \"summary\": \"ok\"}",
                            }
                        }
                    ]
                }

        with mock.patch.dict(os.environ, {"OPENROUTER_API_KEY": "test-key"}, clear=False), mock.patch(
            "requests.post", return_value=Response()
        ) as post:
            result = recipe_improver.call_openrouter(
                {"name": "Test"},
                mock.Mock(api_key=None, model="test/model", site_url=None, app_name=None, timeout=5, resume=False, recipe_id=None, state_file=Path("state.json")),
            )

        self.assertEqual(result["recipe"]["name"], "Improved")
        self.assertEqual(post.call_args.kwargs["json"]["model"], "test/model")
        self.assertEqual(
            post.call_args.kwargs["headers"]["X-OpenRouter-Title"],
            "Pepper Meal Recipe Improver",
        )
        self.assertEqual(
            post.call_args.kwargs["headers"]["X-OpenRouter-Metadata"],
            "enabled",
        )

    def test_run_recipe_yes_flag_skips_overwrite_prompt(self) -> None:
        recipe_path = Path("/tmp/test-recipe.json")
        recipe = {
            "id": "recipe-1",
            "name": "Test Recipe",
            "slug": "test-recipe",
            "schema_version": 1,
            "updated_at": 1,
            "revision": 1,
            "categories": [],
            "tags": [],
        }
        args = mock.Mock(yes=True)

        with mock.patch.object(recipe_improver, "load_recipe", return_value=recipe), mock.patch.object(
            recipe_improver,
            "call_openrouter",
            return_value={"recipe": recipe, "summary": "Improved"},
        ), mock.patch.object(
            recipe_improver,
            "validate_recipe_with_manual_fix",
            return_value=recipe,
        ), mock.patch.object(
            recipe_improver,
            "write_improved_recipe",
        ) as write_recipe, mock.patch.object(
            recipe_improver,
            "prompt_yes_no",
            side_effect=AssertionError("overwrite prompt should be skipped when --yes is set"),
        ):
            wrote, improved = recipe_improver.run_recipe(recipe_path, args, validator=None)

        self.assertTrue(wrote)
        self.assertEqual(improved, recipe)
        write_recipe.assert_called_once_with(recipe_path, recipe)


if __name__ == "__main__":
    from unittest import main

    main()
