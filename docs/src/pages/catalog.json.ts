import type { APIRoute } from 'astro';
import { buildCatalogPayload } from '../lib/catalog';

/**
 * Static JSON index consumed by the catalog page. Built once per deploy and
 * served straight from GitHub Pages like any other asset.
 */
let body: string | undefined;

export const GET: APIRoute = () => {
	body ??= JSON.stringify(buildCatalogPayload());

	return new Response(body, {
		headers: {
			'content-type': 'application/json; charset=utf-8',
		},
	});
};
