// @ts-check
import { defineConfig } from 'astro/config';
import { fileURLToPath } from 'node:url';

// https://astro.build/config
const repoRoot = fileURLToPath(new URL('..', import.meta.url));

export default defineConfig({
	vite: {
		server: {
			fs: {
				allow: [repoRoot],
			},
		},
	},
});
