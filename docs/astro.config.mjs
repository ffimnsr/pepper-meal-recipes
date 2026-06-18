// @ts-check
import { defineConfig } from 'astro/config';
import { fileURLToPath } from 'node:url';

// https://astro.build/config
const repoRoot = fileURLToPath(new URL('..', import.meta.url));
const isProduction = process.env.NODE_ENV === 'production';

export default defineConfig({
	base: isProduction ? '/pepper-meal-recipes/' : '/',
	vite: {
		server: {
			fs: {
				allow: [repoRoot],
			},
		},
	},
});
