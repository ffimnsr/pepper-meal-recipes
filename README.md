# pepper-meal-recipes

Static recipe catalog repository for Pepper Meal Planner (PMP).

The catalog is published as versioned JSON metadata and per-recipe payloads so mobile clients can bootstrap from a small release file, fetch browse indexes, and apply incremental sync manifests.

## Layout

```text
py-scripts/
  generate_catalog.py
recipes/
  v1/
    release.json
    indexes/
      recipes.index.json
      categories.index.json
      tags.index.json
      ingredients.index.json
      ingredients.review.json
    manifests/
      0000000001.json
    recipes/
      by-id/
        <recipe-uuid>.json
    schemas/
      release.schema.json
      manifest.schema.json
      recipe.schema.json
      recipes-index.schema.json
      categories-index.schema.json
      tags-index.schema.json
      ingredients-index.schema.json
```

## Generator

Run the catalog generator after editing recipe payloads:

```bash
python3 -m pip install -r requirements-dev.txt
python3 py-scripts/generate_catalog.py
```

To publish a new catalog revision with a new manifest sequence:

```bash
python3 py-scripts/generate_catalog.py --bump-sequence
```

To rewrite ingredient ids across recipe payloads:

```bash
./scripts/recipe-editor.py merge <id_1> <id_2> [<id_n>...]
./scripts/recipe-editor.py rename <id> <ingredient_name>
```

Run `python3 py-scripts/generate_catalog.py` after editing recipes so the indexes and review queue are rebuilt from the updated payloads.

The generator will:

1. Assign stable UUIDv5 identifiers from recipe and taxonomy keys.
2. Rebuild recipe, category, tag, and ingredient indexes from recipe payloads.
3. Rebuild the current manifest `upserts` from the recipe files.
4. Refresh `release.json` timestamps and SHA-256 hashes.
5. Emit `indexes/ingredients.review.json` for excluded, split, or ambiguous ingredient lines that need human review.

The generator validates recipe payloads and generated artifacts against the JSON Schemas before writing catalog metadata.

UUID generation is deterministic so repeated runs do not churn identifiers.

## FLUX Cover Image Optimizer

`py-scripts/optimize_recipe_images.py` sends each `recipes/v1/assets/by-id/<recipe-uuid>/cover.jpg` to the BFL FLUX.2 Klein 4B image-editing endpoint, then atomically replaces the original cover with the generated JPEG. Images are processed sequentially in UUID order.

Install dependencies, export the API key, and inspect the work queue without making API calls:

```bash
python3 -m pip install -r requirements-dev.txt
export BFL_API_KEY="your_key_here"
python3 py-scripts/optimize_recipe_images.py --dry-run
```

Process all remaining covers, or limit a run while testing:

```bash
python3 py-scripts/optimize_recipe_images.py
python3 py-scripts/optimize_recipe_images.py --limit 1
```

Progress and any in-flight BFL task are saved atomically in `.flux-image-optimizer-state.json`. It is safe to stop with Ctrl+C and run the same command again. If a saved task can no longer be retrieved, `--discard-pending` clears it and resubmits that recipe; doing so may incur another API charge.

Use `--help` to customize the prompt, dimensions, safety tolerance, polling intervals, and state-file path.

## Sync Model

Clients should:

1. Fetch `recipes/v1/release.json`.
2. Compare `repo_sequence` with the locally stored checkpoint.
3. Download any missing manifest files up to the current sequence.
4. Fetch changed recipe files listed in manifest `upserts` when `file_sha256` differs locally.
5. Apply manifest `removals` to local storage.

`release.json` is the only mutable bootstrap target. Recipe files and manifests are intended to be immutable once published.

Recipe payloads do not embed their own file hashes. Integrity data lives in release metadata and manifests so clients can verify downloaded bytes without self-referential metadata.

The recipes browse index is intentionally lighter weight than the manifests. Per-recipe sync fields such as `file_sha256` and `recipe_path` are published in manifest `upserts`, not duplicated into `indexes/recipes.index.json`.
