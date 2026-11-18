import type { CatalogPayload } from '../lib/catalog-types';

const BATCH_SIZE = 24;
const DIFFICULTIES = ['easy', 'medium', 'hard', 'expert'];
const base = import.meta.env.BASE_URL;

type Entry = {
	slug: string;
	name: string;
	description: string;
	minutes: number;
	servings: number;
	difficulty: string;
	categories: string[];
	categoryLabel: string;
	tags: string[];
	ingredientCount: number;
	type: string;
	imageExt: string;
	/** Lowercased haystack used by the search box. */
	search: string;
};

const grid = document.querySelector<HTMLElement>('#recipe-grid');
const searchInput = document.querySelector<HTMLInputElement>('#search');
const searchShortcut = document.querySelector<HTMLElement>('#search-shortcut');
const categoryFilter = document.querySelector<HTMLSelectElement>('#category-filter');
const tagFilter = document.querySelector<HTMLSelectElement>('#tag-filter');
const difficultyFilter = document.querySelector<HTMLSelectElement>('#difficulty-filter');
const timeFilter = document.querySelector<HTMLSelectElement>('#time-filter');
const resetButton = document.querySelector<HTMLButtonElement>('#reset-filters');
const resultSummary = document.querySelector<HTMLElement>('#result-summary');
const loadMoreBtn = document.querySelector<HTMLButtonElement>('#load-more-btn');
const loadMoreLabel = document.querySelector<HTMLElement>('#load-more-label');
const loadMoreHint = document.querySelector<HTMLElement>('#load-more-hint');

const titleCase = (value: string) =>
	value
		.split(/([\s_-]+)/)
		.map((part) => (/[\s_-]+/.test(part) ? part : part.charAt(0).toUpperCase() + part.slice(1).toLowerCase()))
		.join('');

const normalize = (value: string | null | undefined) => String(value ?? '').trim().toLowerCase();

const escapeHtml = (value: string) =>
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
				return '&#39;';
		}
	});

const FALLBACK_MEDIA = `<div class="card-media-fallback" aria-hidden="true">
	<svg viewBox="0 0 24 24" fill="currentColor"><path d="M12 2C8.13 2 5 5.13 5 9c0 5.25 7 13 7 13s7-7.75 7-13c0-3.87-3.13-7-7-7zm0 9.5c-1.38 0-2.5-1.12-2.5-2.5s1.12-2.5 2.5-2.5 2.5 1.12 2.5 2.5-1.12 2.5-2.5 2.5z"/></svg>
</div>`;

const cardHtml = (entry: Entry) => {
	const timeLabel = entry.minutes ? `${entry.minutes} min` : 'Time n/a';
	const difficultyBadge = entry.difficulty
		? `<span class="badge badge-difficulty is-${entry.difficulty}">${escapeHtml(titleCase(entry.difficulty))}</span>`
		: '';
	const media = entry.imageExt
		? `<img data-slug="${escapeHtml(entry.slug)}" data-ext="${escapeHtml(entry.imageExt)}" alt="" loading="lazy" decoding="async" />`
		: FALLBACK_MEDIA;
	const foot = entry.servings ? `Serves ${entry.servings}` : escapeHtml(titleCase(entry.type || 'Recipe'));

	return `<article class="recipe-card">
	<div class="card-media">
		${media}
		<div class="card-badges">
			<span class="badge badge-type">${escapeHtml(entry.categoryLabel)}</span>
			${difficultyBadge}
		</div>
		<button class="bookmark-btn" type="button" data-bookmark="${escapeHtml(entry.slug)}" data-name="${escapeHtml(entry.name)}" aria-pressed="false" aria-label="Save ${escapeHtml(entry.name)}">
			<svg class="icon" aria-hidden="true"><use href="#i-bookmark"></use></svg>
		</button>
	</div>
	<div class="card-body">
		<div class="card-meta">
			<span class="time-chip"><svg class="icon" aria-hidden="true"><use href="#i-clock"></use></svg>${timeLabel}</span>
			<span class="meta-dot" aria-hidden="true">•</span>
			<span>${entry.ingredientCount} ingredients</span>
		</div>
		<h3 class="card-title">${escapeHtml(entry.name)}</h3>
		<p class="card-description">${escapeHtml(entry.description)}</p>
	</div>
	<div class="card-foot">
		<span class="card-servings">${foot}</span>
		<span class="card-cta">View recipe <svg class="icon" aria-hidden="true"><use href="#i-arrow"></use></svg></span>
	</div>
	<a class="card-hit" href="${base}recipes/${encodeURIComponent(entry.slug)}/" aria-label="Open ${escapeHtml(entry.name)}"></a>
</article>`;
};

const init = () => {
	if (
		!grid ||
		!searchInput ||
		!categoryFilter ||
		!tagFilter ||
		!difficultyFilter ||
		!timeFilter ||
		!resetButton ||
		!resultSummary ||
		!loadMoreBtn ||
		!loadMoreLabel ||
		!loadMoreHint
	) {
		return;
	}

	let entries: Entry[] = [];
	let matching: Entry[] = [];
	let visibleLimit = BATCH_SIZE;
	let renderedCount = 0;
	let loading: Promise<void> | null = null;
	let ready = false;
	let filterQueued = false;

	let imageBase = '';
	let namespaceBytes: Uint8Array | null = null;
	const imageUrlCache = new Map<string, Promise<string>>();

	const encoder = new TextEncoder();

	/* ------------------------------ image paths ---------------------------- */

	const uuid5 = async (name: string): Promise<string> => {
		if (!namespaceBytes || !globalThis.crypto?.subtle) return '';

		const payload = new Uint8Array(namespaceBytes.length + encoder.encode(name).length);
		payload.set(namespaceBytes, 0);
		payload.set(encoder.encode(name), namespaceBytes.length);

		const hash = new Uint8Array(await crypto.subtle.digest('SHA-1', payload));
		hash[6] = (hash[6] & 0x0f) | 0x50;
		hash[8] = (hash[8] & 0x3f) | 0x80;

		const hex = Array.from(hash.subarray(0, 16), (byte) => byte.toString(16).padStart(2, '0')).join('');

		return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
	};

	const resolveImage = (slug: string, ext: string): Promise<string> => {
		const cached = imageUrlCache.get(slug);
		if (cached) return cached;

		const promise = uuid5(slug).then((id) => (id ? `${imageBase}${id}/cover.${ext}` : ''));
		imageUrlCache.set(slug, promise);

		return promise;
	};

	const hydrateImages = (root: ParentNode) => {
		if (!namespaceBytes || !globalThis.crypto?.subtle) return;

		for (const image of root.querySelectorAll<HTMLImageElement>('img[data-slug]')) {
			if (image.hasAttribute('src')) continue;

			const slug = image.dataset.slug ?? '';
			const ext = image.dataset.ext ?? '';
			if (!slug || !ext) continue;

			void resolveImage(slug, ext).then((url) => {
				if (url && image.isConnected && !image.hasAttribute('src')) image.src = url;
			});
		}
	};

	/* ------------------------------- rendering ----------------------------- */

	const updateStats = () => {
		const total = matching.length;
		const limit = Math.min(visibleLimit, total);

		if (total === 0) {
			resultSummary.textContent = 'No recipes found matching your criteria.';
		} else if (limit < total) {
			resultSummary.textContent = `Showing ${limit.toLocaleString()} of ${total.toLocaleString()} matching recipes`;
		} else if (total === entries.length) {
			resultSummary.textContent = `Showing all ${total.toLocaleString()} recipes`;
		} else {
			resultSummary.textContent = `Showing all ${total.toLocaleString()} matching recipes`;
		}

		loadMoreLabel.textContent = `Load ${Math.min(BATCH_SIZE, total - limit).toLocaleString()} more recipes`;
		loadMoreHint.textContent =
			total === 0 ? 'No recipes to show' : `Viewing ${limit.toLocaleString()} of ${total.toLocaleString()} recipes`;
		loadMoreBtn.hidden = limit >= total;
	};

	const render = (append: boolean) => {
		const limit = Math.min(visibleLimit, matching.length);

		if (!append) {
			grid.replaceChildren();
			renderedCount = 0;
		}

		const batch = matching.slice(renderedCount, limit);
		if (batch.length > 0) {
			grid.insertAdjacentHTML('beforeend', batch.map(cardHtml).join(''));
			hydrateImages(grid);
		}

		renderedCount = limit;
		updateStats();
		document.dispatchEvent(new CustomEvent('pmp:cards-rendered'));
	};

	/* ------------------------------- filtering ----------------------------- */

	const matchesTime = (minutes: number, filter: string) => {
		if (!filter) return true;
		if (filter === 'slow') return minutes >= 120;
		const limit = Number(filter);

		return Number.isFinite(limit) ? minutes > 0 && minutes <= limit : true;
	};

	const syncChips = () => {
		const selectedCategory = normalize(categoryFilter.value);
		const selectedTag = normalize(tagFilter.value);

		for (const chip of document.querySelectorAll<HTMLElement>('[data-category]')) {
			const isActive = Boolean(selectedCategory) && chip.dataset.category === selectedCategory;
			chip.classList.toggle('is-active', isActive);
			chip.setAttribute('aria-pressed', String(isActive));
		}

		for (const chip of document.querySelectorAll<HTMLElement>('[data-tag]')) {
			const isActive = Boolean(selectedTag) && chip.dataset.tag === selectedTag;
			chip.classList.toggle('is-active', isActive);
			chip.setAttribute('aria-pressed', String(isActive));
		}
	};

	const applyFilters = () => {
		if (!ready) {
			filterQueued = true;
			resultSummary.textContent = 'Loading recipe catalog…';
			void loadCatalog().then(() => {
				if (ready) applyFilters();
			});
			return;
		}

		const query = normalize(searchInput.value);
		const selectedCategory = normalize(categoryFilter.value);
		const selectedTag = normalize(tagFilter.value);
		const selectedDifficulty = normalize(difficultyFilter.value);
		const selectedTime = normalize(timeFilter.value);

		matching = entries.filter(
			(entry) =>
				(!query || entry.search.includes(query)) &&
				(!selectedCategory || entry.categories.includes(selectedCategory)) &&
				(!selectedTag || entry.tags.includes(selectedTag)) &&
				(!selectedDifficulty || entry.difficulty === selectedDifficulty) &&
				matchesTime(entry.minutes, selectedTime),
		);

		syncChips();
		visibleLimit = BATCH_SIZE;
		render(false);
	};

	/* -------------------------------- loading ------------------------------ */

	const toEntries = (payload: CatalogPayload): Entry[] => {
		imageBase = payload.imageBase;
		namespaceBytes = new Uint8Array(
			payload.idNamespace.replace(/-/g, '').match(/.{2}/g)?.map((byte) => Number.parseInt(byte, 16)) ?? [],
		);

		const { categories, tags, ingredients } = payload;

		return payload.rows.map((row) => {
			const [
				slug,
				name,
				description,
				minutes,
				servings,
				difficulty,
				categoryIds,
				tagIds,
				count,
				ingredientIds,
				type,
				cuisine,
				extIndex,
			] = row;
			const categorySlugs = categoryIds.map((id) => categories[id]?.[0] ?? '');
			const tagSlugs = tagIds.map((id) => tags[id] ?? '');
			const ingredientNames = ingredientIds.map((id) => ingredients[id] ?? '');
			const difficultyName = DIFFICULTIES[difficulty] ?? '';

			return {
				slug,
				name,
				description,
				minutes,
				servings,
				difficulty: difficultyName,
				categories: categorySlugs,
				categoryLabel: categories[categoryIds[0]]?.[1] ?? titleCase(type || 'Recipe'),
				tags: tagSlugs,
				ingredientCount: count,
				type,
				imageExt: payload.imageExts[extIndex] ?? '',
				search: [name, description, cuisine, type, ...categorySlugs, ...tagSlugs, ...ingredientNames, difficultyName]
					.join(' ')
					.replace(/-/g, ' ')
					.toLowerCase(),
			};
		});
	};

	const loadCatalog = (): Promise<void> => {
		if (ready) return Promise.resolve();
		if (loading) return loading;

		loading = fetch(`${base}catalog.json`)
			.then((response) => {
				if (!response.ok) throw new Error(`catalog request failed: ${response.status}`);
				return response.json() as Promise<CatalogPayload>;
			})
			.then((payload) => {
				entries = toEntries(payload);
				matching = entries;
				ready = true;

				if (!filterQueued) {
					visibleLimit = BATCH_SIZE;
					renderedCount = Math.min(BATCH_SIZE, entries.length);
					updateStats();
				}
				filterQueued = false;
			})
			.catch(() => {
				loading = null;
				resultSummary.textContent = 'Could not load the recipe catalog. Check your connection and try again.';
			});

		return loading;
	};

	/* -------------------------------- controls ----------------------------- */

	let debounceTimer: number | undefined;
	const scheduleFilter = () => {
		window.clearTimeout(debounceTimer);
		debounceTimer = window.setTimeout(applyFilters, 120);
	};

	searchInput.addEventListener('input', scheduleFilter);
	searchInput.addEventListener('focus', () => void loadCatalog(), { once: true });

	for (const select of [categoryFilter, tagFilter, difficultyFilter, timeFilter]) {
		select.addEventListener('change', applyFilters);
	}

	for (const chip of document.querySelectorAll<HTMLElement>('[data-category], [data-tag]')) {
		chip.addEventListener('click', () => {
			const select = chip.dataset.category ? categoryFilter : tagFilter;
			const value = chip.dataset.category ?? chip.dataset.tag ?? '';

			select.value = select.value === value ? '' : value;
			applyFilters();
		});
	}

	resetButton.addEventListener('click', () => {
		searchInput.value = '';
		categoryFilter.value = '';
		tagFilter.value = '';
		difficultyFilter.value = '';
		timeFilter.value = '';
		applyFilters();
	});

	loadMoreBtn.addEventListener('click', () => {
		if (!ready) {
			void loadCatalog().then(() => {
				if (ready) {
					visibleLimit += BATCH_SIZE;
					render(true);
				}
			});
			return;
		}

		visibleLimit += BATCH_SIZE;
		render(true);
	});

	const focusSearch = () => {
		searchInput.focus();
		searchInput.select();
	};

	searchShortcut?.addEventListener('click', focusSearch);

	document.addEventListener('keydown', (event) => {
		if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === 'k') {
			event.preventDefault();
			focusSearch();
		}
	});

	// Warm the index once the page is idle so the first search feels instant,
	// without making every visitor wait for it on load.
	const prefetch = () => void loadCatalog().catch(() => {});
	if (typeof window.requestIdleCallback === 'function') {
		window.requestIdleCallback(prefetch, { timeout: 3000 });
	} else {
		window.setTimeout(prefetch, 1500);
	}
};

init();

export {};
