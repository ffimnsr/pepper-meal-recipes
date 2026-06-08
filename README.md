# pepper-meal-recipes

Static recipe catalog repository for Pepper Meal Planner (PMP).

The catalog is published as versioned JSON metadata and per-recipe payloads so mobile clients can bootstrap from a small release file, fetch browse indexes, and apply incremental sync manifests.

## Layout

```text
scripts/
  generate_catalog.py
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
python3 scripts/generate_catalog.py
```

To publish a new catalog revision with a new manifest sequence:

```bash
python3 scripts/generate_catalog.py --bump-sequence
```

The generator will:

1. Assign stable UUIDv5 identifiers from recipe and taxonomy keys.
2. Rebuild recipe, category, tag, and ingredient indexes from recipe payloads.
3. Rebuild the current manifest `upserts` from the recipe files.
4. Refresh `release.json` timestamps and SHA-256 hashes.

The generator validates recipe payloads and generated artifacts against the JSON Schemas before writing catalog metadata.

UUID generation is deterministic so repeated runs do not churn identifiers.

## Sync Model

Clients should:

1. Fetch `recipes/v1/release.json`.
2. Compare `repo_sequence` with the locally stored checkpoint.
3. Download any missing manifest files up to the current sequence.
4. Fetch changed recipe files listed in manifest `upserts` when `file_sha256` differs locally.
5. Apply manifest `removals` to local storage.

`release.json` is the only mutable bootstrap target. Recipe files and manifests are intended to be immutable once published.

Recipe payloads do not embed their own file hashes. Integrity data lives in indexes and manifests so clients can verify downloaded bytes without self-referential metadata.
