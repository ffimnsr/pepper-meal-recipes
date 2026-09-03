import argparse
import importlib.util
import json
import sys
import tempfile
from pathlib import Path
from unittest import TestCase, mock


SCRIPT_PATH = Path(__file__).resolve().parents[1] / "py-scripts" / "optimize_recipe_images.py"
SPEC = importlib.util.spec_from_file_location("optimize_recipe_images", SCRIPT_PATH)
assert SPEC is not None
optimize_recipe_images = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = optimize_recipe_images
SPEC.loader.exec_module(optimize_recipe_images)


class FakeResponse:
    def __init__(self, payload: dict[str, object]) -> None:
        self.payload = payload
        self.status_code = 200
        self.text = json.dumps(payload)

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict[str, object]:
        return self.payload


class OptimizeRecipeImagesTests(TestCase):
    def test_list_cover_images_uses_dolphin_style_natural_sorting(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            covers_dir = Path(temp_dir)
            recipe_ids = [
                "0a4be0a7-987c-53f3-8016-3eb592f5c864",
                "000a3a92-fc5f-51d0-8a32-0dc27fc278f4",
                "0a2d9881-c6eb-5e98-8789-f45520ab7584",
                "0a0b5029-cbdf-503e-b26d-c9ef939efff1",
            ]
            for index, recipe_id in enumerate(recipe_ids):
                recipe_dir = covers_dir / recipe_id
                recipe_dir.mkdir()
                cover_name = "cover.webp" if index >= 2 else "cover.jpg"
                (recipe_dir / cover_name).write_bytes(b"image")

            covers = optimize_recipe_images.list_cover_images(covers_dir)

        self.assertEqual(
            [path.parent.name for path in covers],
            [recipe_ids[3], recipe_ids[2], recipe_ids[1], recipe_ids[0]],
        )
        self.assertEqual([path.suffix for path in covers[:2]], [".webp", ".webp"])

    def test_state_round_trip_preserves_pending_task(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            state_path = Path(temp_dir) / "state.json"
            state = optimize_recipe_images.OptimizerState(
                completed_ids=["first"],
                pending=optimize_recipe_images.PendingTask(
                    recipe_id="second",
                    task_id="task-id",
                    polling_url="https://api.bfl.ai/v1/get_result?id=task-id",
                    output_downloaded=True,
                ),
            )

            optimize_recipe_images.save_state(state_path, state)
            loaded = optimize_recipe_images.load_state(state_path)

        self.assertEqual(loaded, state)

    def test_submit_task_base64_encodes_cover(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            cover_path = Path(temp_dir) / "cover.jpg"
            cover_path.write_bytes(b"jpeg bytes")
            session = mock.Mock()
            session.post.return_value = FakeResponse(
                {
                    "id": "task-id",
                    "polling_url": "https://api.bfl.ai/v1/get_result?id=task-id",
                }
            )
            args = argparse.Namespace(
                prompt="Improve it",
                safety_tolerance=2,
                width=512,
                height=512,
                request_timeout=120.0,
            )

            pending = optimize_recipe_images.submit_task(session, "secret", cover_path, args)

        request_payload = session.post.call_args.kwargs["json"]
        self.assertEqual(request_payload["input_image"], "anBlZyBieXRlcw==")
        self.assertEqual(request_payload["output_format"], "jpeg")
        self.assertEqual(pending.task_id, "task-id")

    def test_install_staged_result_converts_webp_and_keeps_stage(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            directory = Path(temp_dir)
            source_path = directory / "cover.webp"
            output_path = directory / "cover.jpg"
            staged_path = directory / ".cover.flux-output.jpg"
            source_path.write_bytes(b"old")
            staged_path.write_bytes(b"new")

            optimize_recipe_images.install_staged_result(staged_path, output_path, mode_source=source_path)
            optimize_recipe_images.remove_alternate_cover_formats(directory)

            self.assertEqual(output_path.read_bytes(), b"new")
            self.assertFalse(source_path.exists())
            self.assertEqual(staged_path.read_bytes(), b"new")

    def test_next_cover_skips_completed_ids(self) -> None:
        covers = [
            Path("assets/00000000-0000-5000-8000-000000000001/cover.jpg"),
            Path("assets/00000000-0000-5000-8000-000000000002/cover.jpg"),
        ]

        result = optimize_recipe_images.next_cover(
            covers,
            {"00000000-0000-5000-8000-000000000001"},
        )

        self.assertEqual(result, covers[1])
