import type { APIRoute } from 'astro';
import { recipes } from '../lib/catalog';

const escapeXml = (value: string) =>
	value.replace(/[&<>"']/g, (character) => {
		switch (character) {
			case '&':
				return '&amp;';
			case '<':
				return '&lt;';
			case '>':
				return '&gt;';
			case '"':
				return '&quot;';
			default:
				return '&apos;';
		}
	});

/**
 * The catalog grid now renders cards from `catalog.json`, so recipe pages are
 * no longer linked from the home page HTML. The sitemap keeps them crawlable.
 */
export const GET: APIRoute = ({ site }) => {
	const origin = (site?.toString() ?? '').replace(/\/$/, '');
	const paths = [
		'/',
		'/privacy-policy/',
		'/terms-and-conditions/',
		...recipes.map((recipe) => `/recipes/${recipe.slug}/`),
	];

	const body = [
		'<?xml version="1.0" encoding="UTF-8"?>',
		'<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">',
		...paths.map((path) => `  <url><loc>${escapeXml(`${origin}${path}`)}</loc></url>`),
		'</urlset>',
		'',
	].join('\n');

	return new Response(body, {
		headers: {
			'content-type': 'application/xml; charset=utf-8',
		},
	});
};
