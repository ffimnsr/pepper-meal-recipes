# Pepper Meal Recipes docs

Astro site for browsing the versioned recipe catalog stored in this repository.

The homepage reads from `recipes/v1/indexes/*` and renders a static recipe browser with search and filters.

## 🚀 Project Structure

Inside of your Astro project, you'll see the following folders and files:

```text
/
├── public/
├── src/
│   ├── components/
│   │   └── LegalPage.astro    shared legal/policy page layout
│   ├── layouts/
│   │   └── SiteLayout.astro   shared header, footer, and page head
│   ├── lib/
│   │   ├── catalog.ts         builds the compact catalog payload from the repo indexes
│   │   └── catalog-types.ts   payload types shared with the browser
│   ├── scripts/
│   │   ├── bookmarks.ts       saved recipes in localStorage
│   │   └── catalog.ts         client-side search, filters, and card rendering
│   ├── styles/
│   │   └── site.css           design tokens and shared components
│   └── pages/
│       ├── index.astro        recipe catalog
│       ├── catalog.json.ts    static JSON index used by the catalog page
│       ├── sitemap.xml.ts     sitemap for the catalog and every recipe page
│       ├── privacy-policy.astro
│       ├── terms-and-conditions.astro
│       └── recipes/[slug].astro
└── package.json
```

Astro looks for `.astro` or `.md` files in the `src/pages/` directory. Each page is exposed as a route based on its file name.

There's nothing special about `src/components/`, but that's where we like to put any Astro/React/Vue/Svelte/Preact components.

Any static assets, like images, can be placed in the `public/` directory.

## 🧞 Commands

Commands are run from the `docs` directory with [Bun](https://bun.sh) (the version is pinned in `package.json`):

| Command                   | Action                                           |
| :------------------------ | :----------------------------------------------- |
| `bun install`             | Installs dependencies                            |
| `bun run dev`             | Starts local dev server at `localhost:4321`      |
| `bun run build`           | Build your production site to `./dist/`          |
| `bun run preview`         | Preview your build locally, before deploying     |
| `bun run astro ...`       | Run CLI commands like `astro add`, `astro check` |
| `bun run astro -- --help` | Get help using the Astro CLI                     |

## Recipe browser notes

- The browser is data-driven from the repo indexes, so new catalog revisions can be surfaced without manual page edits.
- `recipes/v1/release.json` remains the source of truth for catalog versioning and sync metadata.
- The catalog grid only server-renders the first 24 cards. Everything else comes from `catalog.json` (built at deploy time by `src/pages/catalog.json.ts`), which the page fetches on first search, filter, or idle time.
- That payload is columnar and interned: category/tag/ingredient names live in dictionaries, and recipe UUIDs are not shipped at all — cover paths are rebuilt from the slug with UUIDv5, matching `py-scripts/generate_catalog.py`.
- Because the grid no longer links every recipe in HTML, `sitemap.xml` lists all recipe pages for crawlers; it is referenced from `public/robots.txt`.
- Saved recipes are stored in `localStorage` under `pmp-saved-recipes`; bookmark buttons are bound through event delegation so client-rendered cards work too.
