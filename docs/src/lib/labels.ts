/**
 * Category and tag names come from the recipe catalog, which is not
 * case-consistent: many tag names are stored all-lowercase ("adobo with eggs").
 * Labels are displayed in title case, but names that already carry their own
 * capitals are left untouched so "BBQ", "4th of July" and "Amish and Mennonite"
 * keep their intended casing.
 */

const MINOR_WORDS = new Set([
	'a',
	'al',
	'ala',
	'an',
	'and',
	'as',
	'at',
	'but',
	'by',
	'de',
	'del',
	'du',
	'for',
	'from',
	'in',
	'into',
	'la',
	'le',
	'na',
	'ng',
	'nor',
	'of',
	'on',
	'or',
	'over',
	'per',
	'sa',
	'the',
	'to',
	'via',
	'vs',
	'with',
]);

const ACRONYMS: Record<string, string> = {
	bbq: 'BBQ',
	bbqs: 'BBQs',
	pbj: 'PB&J',
};

const capitalize = (word: string) =>
	word.replace(/(^|[-/])([a-z])/g, (_, separator: string, letter: string) => `${separator}${letter.toUpperCase()}`);

export const formatLabel = (value: string): string => {
	const trimmed = value.trim();
	if (!trimmed || trimmed !== trimmed.toLowerCase()) return trimmed;

	const words = trimmed.split(/\s+/);

	return words
		.map((word, index) => {
			const acronym = ACRONYMS[word];
			if (acronym) return acronym;
			if (index > 0 && index < words.length - 1 && MINOR_WORDS.has(word)) return word;

			return capitalize(word);
		})
		.join(' ');
};
