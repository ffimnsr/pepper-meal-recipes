/**
 * [slug, name, description, total minutes, servings, difficulty index,
 *  category indexes, tag indexes, ingredient count, ingredient indexes,
 *  recipe type, cuisine, image extension index (-1 when unknown)]
 */
export type CatalogRow = [
	string,
	string,
	string,
	number,
	number,
	number,
	number[],
	number[],
	number,
	number[],
	string,
	string,
	number,
];

export type CatalogPayload = {
	v: 1;
	imageBase: string;
	imageExts: string[];
	idNamespace: string;
	/** [slug, display name] pairs, referenced by the row category indexes. */
	categories: [string, string][];
	/** Tag slugs, referenced by the row tag indexes. */
	tags: string[];
	/** Ingredient names, referenced by the row ingredient indexes. */
	ingredients: string[];
	rows: CatalogRow[];
};
