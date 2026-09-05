# pepper-meal-recipes

Static recipe catalog repository for Pepper Meal Planner (PMP).

The catalog is published as versioned JSON metadata and per-recipe payloads so mobile clients can bootstrap from a small release file, fetch browse indexes, and apply incremental sync manifests.

## Layout

```text
py-scripts/
  generate_catalog.py
  merge_ingredients.py
  resolve_ingredient_review.py
recipes/
  v1/
    release.json
    indexes/
      recipes.index.json
      categories.index.json
      tags.index.json
      ingredients.index.json
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

To interactively review each indexed ingredient, display its referenced recipe rows with `jq`, and optionally merge matching ingredient names into one UUID:

```bash
python3 py-scripts/recipe_editor.py
```

For incorrect ingredients, enter a corrected name to apply it to every matching recipe row, or enter `o` for **other fields** and per-row name, quantity, unit, and preparation editing. In per-row mode, each row after the first accepts Enter or `y` to reuse the first corrected ingredient name, or a different name directly. In a readline-enabled terminal, Tab completes names accepted earlier, including names restored when resuming from the checkpoint. Press Enter to preserve a value, or enter `-` to clear quantity, unit, or preparation. At an ingredient confirmation prompt, enter `r` to restore and revisit the immediately previous ingredient from the current session. The editor saves completed UUIDs in the root-level `.recipe-editor-state.json`; interrupt it with Ctrl+C and rerun the command to resume. The checkpoint is removed after a successful completed review. Run `python3 py-scripts/generate_catalog.py` after editing recipes so the indexes and review queue are rebuilt from the updated payloads.

To merge several indexed ingredient identities into a single canonical ingredient — for example combining `ketchup`, `catsup`, and `banana ketchup` — run:

```bash
python3 py-scripts/merge_ingredients.py
```

The script lists every ingredient from `ingredients.index.json` together with its UUID. Type numbers (e.g. `1,3,5-7`) to toggle the listed items, type text to filter the list by name, or use `l`, `r`, `c`, `d`, and `q` to re-list, reset the filter, clear the selection, continue, or quit. Once at least two ingredients are selected, the script asks which selected name — or a newly typed name — should become the merged identity, previews the target name, normalized name, UUID, and affected rows, and then rewrites `ingredient_id`, `name`, and `normalized_name` on every reference in `recipes/v1/recipes/by-id/*.json`. Run `python3 py-scripts/generate_catalog.py` afterwards so the ingredient index is rebuilt with the merged identity.

To work through the flagged ingredient lines in the root-level `.ingredient-review.json` — lines the generator could not index cleanly, such as `mirin or cooking wine`, multi-ingredient `and` compounds like `salt and pepper`, or names with ambiguous parentheticals — run:

```bash
python3 py-scripts/resolve_ingredient_review.py
```

The script walks each review entry with its recipe, position, original text, and current row, and shows what the entry means for `ingredients.index.json`. Enter a new ingredient name to rename the row (Tab completes indexed names, and the script reports whether the name merges into an existing indexed ingredient or creates a new one), or use `s` to split the row into several ingredients (e.g. `mirin|cooking wine`), `e` to remove a non-ingredient row from the recipe, `o` to edit quantity, unit, or preparation, `i` to ignore the ingredient name everywhere (index it as-is and stop flagging it for review; the verdict is persisted by UUID in the root-level `.ingredient-ignored.json`, which `generate_catalog.py` reads so the name is indexed normally without a review entry — remove the entry from that file to un-ignore), `k` to keep the row, `p` to return to the previous entry, and `q` to quit. Rows are edited in `recipes/v1/recipes/by-id/*.json`; entries can only leave the review queue after `python3 py-scripts/generate_catalog.py` regenerates it from the corrected rows. Progress is saved in the root-level `.ingredient-review-state.json` and cleared when the review is completed, so an interrupted or quit run can be resumed with the same command.

The generator will:

1. Assign stable UUIDv5 identifiers from recipe and taxonomy keys.
2. Rebuild recipe, category, tag, and ingredient indexes from recipe payloads.
3. Rebuild the current manifest `upserts` from the recipe files.
4. Refresh `release.json` timestamps and SHA-256 hashes.
5. Emit the root-level `.ingredient-review.json` queue for excluded, split, or ambiguous ingredient lines that need human review.

The generator validates recipe payloads and generated artifacts against the JSON Schemas before writing catalog metadata.

UUID generation is deterministic so repeated runs do not churn identifiers.

## FLUX Cover Image Optimizer

`py-scripts/optimize_recipe_images.py` sends each `recipes/v1/assets/by-id/<recipe-uuid>/cover.{jpg,jpeg,webp}` to the BFL FLUX.2 Klein 4B image-editing endpoint, then atomically saves the generated image as `cover.jpg`. Source WebP and `.jpeg` files are removed only after the new JPEG is safely installed. Images are processed sequentially using natural A–Z sorting by UUID folder name, matching Dolphin's name ordering.

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
