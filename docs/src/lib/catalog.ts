import { createHash } from 'node:crypto';
import recipesIndexJson from '../../../recipes/v1/indexes/recipes.index.json';
import categoriesIndexJson from '../../../recipes/v1/indexes/categories.index.json';
import tagsIndexJson from '../../../recipes/v1/indexes/tags.index.json';
import type { CatalogPayload, CatalogRow } from './catalog-types';

// The repo indexes live outside this package, so TypeScript resolves the JSON
// modules to `{}`. Describe only the fields this build reads.
type RecipeIndexEntry = {
	slug: string;
	name: string;
	brief_description: string | null;
	cuisine: string | null;
	image_url: string | null;
	difficulty: string | null;
	servings: number | null;
	total_time_minutes: number | null;
	category_slugs: string[];
	tag_slugs: string[];
	ingredient_names: string[];
	recipe_type: string | null;
};

type CategoryIndexEntry = { slug: string; name: string };

type TagsIndexEntry = { slug: string };

const recipesIndex = recipesIndexJson as unknown as { recipes: RecipeIndexEntry[] };
const categoriesIndex = categoriesIndexJson as unknown as { categories: CategoryIndexEntry[] };
const tagsIndex = tagsIndexJson as unknown as { tags: TagsIndexEntry[] };

/** How many cards are server-rendered and added per "Load more" click. */
export const CARD_BATCH_SIZE = 24;

/** Recipes with no usable cover fall back to the illustrated placeholder. */
const IMAGE_BASE = 'https://github.com/ffimnsr/pepper-meal-recipes/raw/master/recipes/v1/assets/by-id/';
const IMAGE_EXTENSIONS = ['jpg', 'webp', 'jpeg'];

/**
 * The catalog JSON is the only thing the browser needs to search and render
 * cards. Image paths are rebuilt from a recipe's slug, so we don't ship 16k
 * UUIDs: the repo derives each recipe id as UUIDv5(RECIPE_NAMESPACE, slug).
 * https://github.com/ffimnsr/pepper-meal-recipes/blob/master/py-scripts/generate_catalog.py
 */
const CATALOG_NAMESPACE = '8d7c8f42-d53a-4d2d-9d67-935eeea8d7c4';

const uuid5 = (namespace: string, name: string): string => {
	const namespaceBytes = Buffer.from(namespace.replace(/-/g, ''), 'hex');
	const bytes = createHash('sha1').update(Buffer.concat([namespaceBytes, Buffer.from(name, 'utf8')])).digest();
	bytes[6] = (bytes[6] & 0x0f) | 0x50;
	bytes[8] = (bytes[8] & 0x3f) | 0x80;
	const hex = bytes.subarray(0, 16).toString('hex');

	return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
};

export const RECIPE_ID_NAMESPACE = uuid5(CATALOG_NAMESPACE, 'recipe');

export const DIFFICULTIES = ['easy', 'medium', 'hard', 'expert'] as const;

export const categoryNameBySlug = new Map(
	categoriesIndex.categories.map((category) => [category.slug, category.name]),
);

export const recipes = [...recipesIndex.recipes].sort((a, b) => a.name.localeCompare(b.name));

/** Card copy is clamped to two lines, so ship a trimmed description to save bytes. */
const trimDescription = (value: string | null | undefined, max = 120): string => {
	const text = (value ?? '').trim();
	if (text.length <= max) return text;

	return `${text.slice(0, max).replace(/\s+\S*$/, '')}…`;
};

const unique = <T>(values: Iterable<T>): T[] => [...new Set(values)];

export const buildCatalogPayload = (): CatalogPayload => {
	const categories = unique([
		...categoriesIndex.categories.map((category) => category.slug),
		...recipes.flatMap((recipe) => recipe.category_slugs),
	])
		.sort()
		.map((slug): [string, string] => [slug, categoryNameBySlug.get(slug) ?? slug]);
	const categoryIndex = new Map(categories.map(([slug], index) => [slug, index]));

	const tags = unique([...tagsIndex.tags.map((tag) => tag.slug), ...recipes.flatMap((recipe) => recipe.tag_slugs)]).sort();
	const tagIndex = new Map(tags.map((slug, index) => [slug, index]));

	const ingredients = unique(recipes.flatMap((recipe) => recipe.ingredient_names)).sort();
	const ingredientIndex = new Map(ingredients.map((name, index) => [name, index]));

	const rows: CatalogRow[] = recipes.map((recipe) => {
		const imageMatch = recipe.image_url?.match(/cover\.(jpg|jpeg|webp)$/);
		// Dedupe only for the search index; the card count mirrors the recipe's
		// ingredient list length, duplicates included.
		const ingredientNames = unique(recipe.ingredient_names);
		const difficultyIndex = DIFFICULTIES.indexOf((recipe.difficulty ?? '') as (typeof DIFFICULTIES)[number]);

		return [
			recipe.slug,
			recipe.name,
			trimDescription(recipe.brief_description),
			recipe.total_time_minutes ?? 0,
			recipe.servings ?? 0,
			difficultyIndex,
			recipe.category_slugs.map((slug) => categoryIndex.get(slug) ?? -1).filter((index) => index >= 0),
			recipe.tag_slugs.map((slug) => tagIndex.get(slug) ?? -1).filter((index) => index >= 0),
			recipe.ingredient_names.length,
			ingredientNames.map((name) => ingredientIndex.get(name) ?? -1).filter((index) => index >= 0),
			recipe.recipe_type ?? '',
			recipe.cuisine ?? '',
			imageMatch ? IMAGE_EXTENSIONS.indexOf(imageMatch[1]) : -1,
		];
	});

	return {
		v: 1,
		imageBase: IMAGE_BASE,
		imageExts: IMAGE_EXTENSIONS,
		idNamespace: RECIPE_ID_NAMESPACE,
		categories,
		tags,
		ingredients,
		rows,
	};
};
