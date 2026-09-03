#!/usr/bin/env python3

from __future__ import annotations

import argparse
import base64
import json
import os
import re
import shutil
import tempfile
import time
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import requests


REPO_ROOT = Path(__file__).resolve().parents[1]
COVERS_DIR = REPO_ROOT / "recipes" / "v1" / "assets" / "by-id"
DEFAULT_STATE_FILE = REPO_ROOT / ".flux-image-optimizer-state.json"
BFL_ENDPOINT = "https://api.bfl.ai/v1/flux-2-klein-4b"
DEFAULT_PROMPT = (
    "make this recipe image 2.5d, a presentable cooked food, appetizing, background of the plates solid wood, no watermarks, no artifacts\n"
)
SUPPORTED_COVER_NAMES = ("cover.jpg", "cover.jpeg", "cover.webp")
TERMINAL_FAILURE_STATUSES = {
    "Content Moderated",
    "Error",
    "Failed",
    "Request Moderated",
    "Task not found",
}


class OptimizerError(RuntimeError):
    pass


@dataclass
class PendingTask:
    recipe_id: str
    task_id: str
    polling_url: str
    output_downloaded: bool = False


@dataclass
class OptimizerState:
    completed_ids: list[str]
    pending: PendingTask | None = None


def atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, ensure_ascii=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, path)
    finally:
        temporary_path.unlink(missing_ok=True)


def load_state(path: Path) -> OptimizerState:
    if not path.exists():
        return OptimizerState(completed_ids=[])

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise OptimizerError(f"Could not read state file {path}: {error}") from error

    if not isinstance(payload, dict) or payload.get("version") != 1:
        raise OptimizerError(f"Unsupported or invalid state file: {path}")

    completed_ids = payload.get("completed_ids")
    if not isinstance(completed_ids, list) or not all(isinstance(item, str) for item in completed_ids):
        raise OptimizerError(f"Invalid completed_ids in state file: {path}")

    pending_payload = payload.get("pending")
    pending = None
    if pending_payload is not None:
        if not isinstance(pending_payload, dict):
            raise OptimizerError(f"Invalid pending task in state file: {path}")
        try:
            pending = PendingTask(
                recipe_id=str(pending_payload["recipe_id"]),
                task_id=str(pending_payload["task_id"]),
                polling_url=str(pending_payload["polling_url"]),
                output_downloaded=bool(pending_payload.get("output_downloaded", False)),
            )
        except KeyError as error:
            raise OptimizerError(f"Invalid pending task in state file {path}: missing {error.args[0]}") from error

    return OptimizerState(completed_ids=list(dict.fromkeys(completed_ids)), pending=pending)


def save_state(path: Path, state: OptimizerState) -> None:
    atomic_write_json(
        path,
        {
            "version": 1,
            "completed_ids": list(dict.fromkeys(state.completed_ids)),
            "pending": asdict(state.pending) if state.pending is not None else None,
        },
    )


def natural_name_key(value: str) -> tuple[int | str, ...]:
    return tuple(int(part) if part.isdigit() else part.casefold() for part in re.split(r"(\d+)", value))


def list_cover_images(covers_dir: Path = COVERS_DIR) -> list[Path]:
    if not covers_dir.is_dir():
        raise OptimizerError(f"Cover image directory does not exist: {covers_dir}")

    covers: list[Path] = []
    for recipe_dir in covers_dir.iterdir():
        if not recipe_dir.is_dir():
            continue
        candidates = [recipe_dir / name for name in SUPPORTED_COVER_NAMES if (recipe_dir / name).is_file()]
        if not candidates:
            continue

        recipe_id = recipe_dir.name
        try:
            parsed_id = uuid.UUID(recipe_id)
        except ValueError as error:
            raise OptimizerError(f"Cover directory is not a UUID: {recipe_dir}") from error
        if str(parsed_id) != recipe_id:
            raise OptimizerError(f"Cover directory must use a canonical lowercase UUID: {recipe_dir}")
        covers.append(candidates[0])

    return sorted(covers, key=lambda path: natural_name_key(path.parent.name))


def staging_path(cover_path: Path) -> Path:
    return cover_path.with_name(".cover.flux-output.jpg")


def validate_https_url(value: Any, description: str) -> str:
    if not isinstance(value, str):
        raise OptimizerError(f"BFL response did not include a valid {description}")
    parsed = urlparse(value)
    if parsed.scheme != "https" or not parsed.netloc:
        raise OptimizerError(f"BFL returned an invalid {description}: {value!r}")
    return value


def response_json(response: requests.Response, action: str) -> dict[str, Any]:
    try:
        response.raise_for_status()
    except requests.HTTPError as error:
        body = response.text.strip().replace("\n", " ")[:500]
        detail = f": {body}" if body else ""
        raise OptimizerError(f"BFL {action} failed with HTTP {response.status_code}{detail}") from error

    try:
        payload = response.json()
    except requests.JSONDecodeError as error:
        raise OptimizerError(f"BFL {action} returned invalid JSON") from error
    if not isinstance(payload, dict):
        raise OptimizerError(f"BFL {action} returned an unexpected response")
    return payload


def submit_task(
    session: requests.Session,
    api_key: str,
    cover_path: Path,
    args: argparse.Namespace,
) -> PendingTask:
    encoded_image = base64.b64encode(cover_path.read_bytes()).decode("ascii")
    try:
        response = session.post(
            BFL_ENDPOINT,
            headers={
                "accept": "application/json",
                "Content-Type": "application/json",
                "x-key": api_key,
            },
            json={
                "prompt": args.prompt,
                "safety_tolerance": args.safety_tolerance,
                "width": args.width,
                "height": args.height,
                "output_format": "jpeg",
                "input_image": encoded_image,
            },
            timeout=args.request_timeout,
        )
    except requests.RequestException as error:
        raise OptimizerError(f"Could not submit {cover_path.parent.name} to BFL: {error}") from error

    payload = response_json(response, "submission")
    task_id = payload.get("id")
    if not isinstance(task_id, str) or not task_id:
        raise OptimizerError("BFL submission response did not include a task id")
    polling_url = validate_https_url(payload.get("polling_url"), "polling URL")
    return PendingTask(recipe_id=cover_path.parent.name, task_id=task_id, polling_url=polling_url)


def poll_for_result(
    session: requests.Session,
    api_key: str,
    pending: PendingTask,
    args: argparse.Namespace,
) -> str:
    deadline = time.monotonic() + args.poll_timeout if args.poll_timeout > 0 else None
    previous_status = None

    while True:
        if deadline is not None and time.monotonic() >= deadline:
            raise OptimizerError(
                f"Timed out waiting for BFL task {pending.task_id}; the pending task is saved and will resume next run"
            )
        try:
            response = session.get(
                pending.polling_url,
                headers={"accept": "application/json", "x-key": api_key},
                timeout=args.request_timeout,
            )
        except requests.RequestException as error:
            raise OptimizerError(
                f"Could not poll BFL task {pending.task_id}; the pending task is saved: {error}"
            ) from error

        payload = response_json(response, "result polling")
        status = payload.get("status")
        if not isinstance(status, str):
            raise OptimizerError(f"BFL task {pending.task_id} returned no status")
        if status != previous_status:
            print(f"  BFL status: {status}", flush=True)
            previous_status = status

        if status == "Ready":
            result = payload.get("result")
            if not isinstance(result, dict):
                raise OptimizerError(f"BFL task {pending.task_id} returned no result object")
            return validate_https_url(result.get("sample"), "result image URL")
        if status in TERMINAL_FAILURE_STATUSES:
            details = payload.get("details") or payload.get("result") or "no details"
            raise OptimizerError(f"BFL task {pending.task_id} ended with {status}: {details}")

        time.sleep(args.poll_interval)


def download_result(
    session: requests.Session,
    result_url: str,
    destination: Path,
    request_timeout: float,
) -> None:
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{destination.name}.", dir=destination.parent)
    temporary_path = Path(temporary_name)
    try:
        try:
            with session.get(result_url, stream=True, timeout=request_timeout) as response:
                response.raise_for_status()
                with os.fdopen(descriptor, "wb") as handle:
                    descriptor = -1
                    for chunk in response.iter_content(chunk_size=1024 * 1024):
                        if chunk:
                            handle.write(chunk)
                    handle.flush()
                    os.fsync(handle.fileno())
        except requests.RequestException as error:
            raise OptimizerError(f"Could not download generated image: {error}") from error

        with temporary_path.open("rb") as handle:
            signature = handle.read(3)
        if signature != b"\xff\xd8\xff":
            raise OptimizerError("Downloaded result is not a JPEG image; original cover was not changed")
        os.replace(temporary_path, destination)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        temporary_path.unlink(missing_ok=True)


def install_staged_result(staged_path: Path, cover_path: Path, mode_source: Path | None = None) -> None:
    if not staged_path.is_file():
        raise OptimizerError(
            f"State says the result was downloaded, but the staged image is missing: {staged_path}"
        )

    descriptor, temporary_name = tempfile.mkstemp(prefix=".cover.install.", dir=cover_path.parent)
    temporary_path = Path(temporary_name)
    try:
        with staged_path.open("rb") as source, os.fdopen(descriptor, "wb") as destination:
            descriptor = -1
            shutil.copyfileobj(source, destination)
            destination.flush()
            os.fsync(destination.fileno())
        mode_path = cover_path if cover_path.exists() else mode_source
        if mode_path is not None and mode_path.exists():
            os.chmod(temporary_path, mode_path.stat().st_mode & 0o777)
        os.replace(temporary_path, cover_path)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        temporary_path.unlink(missing_ok=True)


def remove_alternate_cover_formats(recipe_dir: Path) -> None:
    for name in ("cover.jpeg", "cover.webp"):
        (recipe_dir / name).unlink(missing_ok=True)


def next_cover(covers: list[Path], completed_ids: set[str]) -> Path | None:
    return next((path for path in covers if path.parent.name not in completed_ids), None)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Optimize recipe covers with FLUX.2 Klein 4B in natural A-Z UUID-folder order."
    )
    parser.add_argument("--prompt", default=DEFAULT_PROMPT, help="Image editing prompt sent to BFL.")
    parser.add_argument("--width", type=int, default=512, help="Output width in pixels (default: 512).")
    parser.add_argument("--height", type=int, default=512, help="Output height in pixels (default: 512).")
    parser.add_argument(
        "--safety-tolerance",
        type=int,
        choices=range(0, 6),
        default=2,
        metavar="0-5",
        help="BFL safety tolerance (default: 2).",
    )
    parser.add_argument("--poll-interval", type=float, default=1.0, help="Seconds between status checks.")
    parser.add_argument(
        "--poll-timeout",
        type=float,
        default=900.0,
        help="Maximum seconds to poll one task; 0 disables the limit (default: 900).",
    )
    parser.add_argument(
        "--request-timeout",
        type=float,
        default=120.0,
        help="HTTP request timeout in seconds (default: 120).",
    )
    parser.add_argument("--limit", type=int, help="Maximum number of images to complete this run.")
    parser.add_argument("--state-file", type=Path, default=DEFAULT_STATE_FILE, help="Resume state path.")
    parser.add_argument(
        "--discard-pending",
        action="store_true",
        help="Discard a saved in-flight BFL task and submit that recipe again (may incur another charge).",
    )
    parser.add_argument("--dry-run", action="store_true", help="List pending covers without calling BFL.")
    args = parser.parse_args()

    if args.width < 64 or args.height < 64:
        parser.error("--width and --height must be at least 64")
    if args.poll_interval <= 0:
        parser.error("--poll-interval must be greater than 0")
    if args.poll_timeout < 0:
        parser.error("--poll-timeout cannot be negative")
    if args.request_timeout <= 0:
        parser.error("--request-timeout must be greater than 0")
    if args.limit is not None and args.limit <= 0:
        parser.error("--limit must be greater than 0")
    return args


def run(args: argparse.Namespace) -> None:
    covers = list_cover_images()
    cover_by_id = {path.parent.name: path for path in covers}
    state = load_state(args.state_file)

    if args.discard_pending and state.pending is not None:
        print(f"Discarding pending task {state.pending.task_id} for {state.pending.recipe_id}.")
        staging_path(cover_by_id.get(state.pending.recipe_id, COVERS_DIR / state.pending.recipe_id / "cover.jpg")).unlink(
            missing_ok=True
        )
        state.pending = None
        save_state(args.state_file, state)

    completed = set(state.completed_ids)
    if args.dry_run:
        pending_covers = [path for path in covers if path.parent.name not in completed]
        if state.pending is not None:
            print(f"Saved in-flight task: {state.pending.recipe_id} ({state.pending.task_id})")
        shown = pending_covers[: args.limit] if args.limit is not None else pending_covers
        for cover_path in shown:
            print(cover_path.relative_to(REPO_ROOT))
        print(f"{len(pending_covers)} of {len(covers)} cover images remain.")
        return

    api_key = os.environ.get("BFL_API_KEY", "").strip()
    if not api_key:
        raise OptimizerError("BFL_API_KEY is not set")

    completed_this_run = 0
    with requests.Session() as session:
        while args.limit is None or completed_this_run < args.limit:
            if state.pending is not None:
                pending = state.pending
                cover_path = cover_by_id.get(pending.recipe_id)
                if cover_path is None:
                    raise OptimizerError(
                        f"Pending recipe {pending.recipe_id} no longer has a cover; use --discard-pending to clear it"
                    )
                print(f"Resuming {pending.recipe_id} (BFL task {pending.task_id})...", flush=True)
            else:
                cover_path = next_cover(covers, completed)
                if cover_path is None:
                    break
                recipe_id = cover_path.parent.name
                print(f"Submitting {recipe_id}...", flush=True)
                pending = submit_task(session, api_key, cover_path, args)
                state.pending = pending
                save_state(args.state_file, state)
                print(f"  BFL task: {pending.task_id}", flush=True)

            staged_path = staging_path(cover_path)
            output_path = cover_path.with_name("cover.jpg")
            if not pending.output_downloaded:
                result_url = poll_for_result(session, api_key, pending, args)
                download_result(session, result_url, staged_path, args.request_timeout)
                pending.output_downloaded = True
                save_state(args.state_file, state)

            install_staged_result(staged_path, output_path, mode_source=cover_path)
            remove_alternate_cover_formats(cover_path.parent)
            state.completed_ids.append(pending.recipe_id)
            completed.add(pending.recipe_id)
            state.pending = None
            save_state(args.state_file, state)
            staged_path.unlink(missing_ok=True)
            completed_this_run += 1
            print(
                f"Optimized {pending.recipe_id} ({len(completed)}/{len(covers)} complete).",
                flush=True,
            )

    remaining = len(covers) - len(completed)
    if remaining:
        print(f"Stopped after {completed_this_run} image(s); {remaining} remain.")
    else:
        print(f"All {len(covers)} cover images are optimized.")


def main() -> None:
    args = parse_args()
    try:
        run(args)
    except KeyboardInterrupt:
        print("\nStopped. Saved progress will resume on the next run.")
        raise SystemExit(130) from None
    except OptimizerError as error:
        raise SystemExit(f"error: {error}") from error


if __name__ == "__main__":
    main()
