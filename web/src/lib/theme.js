// Light or dark: follows the phone ("system") unless the switch in Settings pins one. The choice is kept
// per device in localStorage and applied as data-theme on <html> (app.html applies it before first paint,
// so the page never flashes the other theme). Charts read their colours from CSS variables when they draw,
// so they listen with onThemeChange and redraw.

const KEY = 'theme';
// --bg of each theme (app.css); app.html carries the same two values.
const LIGHT_BG = '#f4f2ed';
const DARK_BG = '#1b1a18';
/** @typedef {'system' | 'light' | 'dark'} Theme */

/** @returns {Theme} */
export function getTheme() {
	try {
		const t = localStorage.getItem(KEY);
		return t === 'light' || t === 'dark' ? t : 'system';
	} catch {
		return 'system';
	}
}

/** @param {Theme} t */
export function setTheme(t) {
	try {
		if (t === 'system') localStorage.removeItem(KEY);
		else localStorage.setItem(KEY, t);
	} catch {
		// private mode or storage off: the choice lasts until the page closes
	}
	apply(t);
	window.dispatchEvent(new Event('themechange'));
}

/** @param {Theme} t */
function apply(t) {
	const root = document.documentElement;
	if (t === 'system') delete root.dataset.theme;
	else root.dataset.theme = t;
	// The browser chrome (status bar, address bar) follows too: the page background of the theme in use,
	// or, on "Phone setting", one value per scheme again so it keeps following the phone.
	const [light, dark] = document.querySelectorAll('meta[name="theme-color"]');
	if (!light || !dark) return;
	if (t === 'system') {
		light.setAttribute('media', '(prefers-color-scheme: light)');
		dark.setAttribute('media', '(prefers-color-scheme: dark)');
		light.setAttribute('content', LIGHT_BG);
		dark.setAttribute('content', DARK_BG);
	} else {
		for (const m of [light, dark]) {
			m.removeAttribute('media');
			m.setAttribute('content', t === 'dark' ? DARK_BG : LIGHT_BG);
		}
	}
}

/** Call `cb` when the effective theme may have changed. Returns the unsubscribe function.
 * @param {() => void} cb */
export function onThemeChange(cb) {
	const q = matchMedia('(prefers-color-scheme: dark)');
	q.addEventListener('change', cb);
	window.addEventListener('themechange', cb);
	return () => {
		q.removeEventListener('change', cb);
		window.removeEventListener('themechange', cb);
	};
}
