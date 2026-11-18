const SAVED_KEY = 'pmp-saved-recipes';

const readSaved = (): Set<string> => {
	try {
		const value: unknown = JSON.parse(localStorage.getItem(SAVED_KEY) ?? '[]');
		return new Set(Array.isArray(value) ? value.filter((entry): entry is string => typeof entry === 'string') : []);
	} catch {
		return new Set();
	}
};

const saved = readSaved();

const persist = () => {
	try {
		localStorage.setItem(SAVED_KEY, JSON.stringify([...saved]));
	} catch {
		/* storage can be unavailable in private browsing modes */
	}
};

const buttons = Array.from(document.querySelectorAll<HTMLButtonElement>('[data-bookmark]'));

const nameFor = (button: HTMLButtonElement): string =>
	button.dataset.name ??
	button.closest('article')?.querySelector('h3')?.textContent?.trim() ??
	'recipe';

const paint = (button: HTMLButtonElement) => {
	const slug = button.dataset.bookmark ?? '';
	const isSaved = saved.has(slug);
	const defaultLabel = button.dataset.label ?? 'Save';
	const savedLabel = button.dataset.savedLabel ?? 'Saved';

	button.classList.toggle('is-saved', isSaved);
	button.setAttribute('aria-pressed', String(isSaved));
	button.setAttribute('aria-label', `${isSaved ? 'Remove' : 'Save'} ${nameFor(button)}`);

	const label = button.querySelector<HTMLElement>('[data-bookmark-label]');
	if (label) {
		label.textContent = isSaved ? savedLabel : defaultLabel;
	}
};

const toggle = (button: HTMLButtonElement) => {
	const slug = button.dataset.bookmark;
	if (!slug) return;

	if (saved.has(slug)) {
		saved.delete(slug);
	} else {
		saved.add(slug);
	}

	paint(button);
	persist();
};

for (const button of buttons) {
	paint(button);
	button.addEventListener('click', () => toggle(button));
}

export {};
