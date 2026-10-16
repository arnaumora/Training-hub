// Food values in the browser: parsing typed values and previewing a recipe's values per 100 g.
// Display only. The API computes and checks the stored values itself (api/app/own_foods.py), with the
// same formula and the same typo guards, so a preview can never become a stored value by mistake.

export const NUTRIENTS = ['carbs', 'sugars', 'fibre', 'protein', 'fat', 'salt', 'kcal'];
export const LABELS = {
	carbs: 'Carbs',
	sugars: 'of which sugars',
	fibre: 'Fibre',
	protein: 'Protein',
	fat: 'Fat',
	salt: 'Salt',
	kcal: 'Energy'
};
export const UNITS = { kcal: 'kcal' };
export const MAX_MACROS = 102; // carbs + protein + fat + fibre per 100 g, with room for label rounding

/**
 * A value per 100 g as typed: '' is empty (null); otherwise a number with at most 2 decimals in [0, max]
 * ("4,5" is accepted: the Spanish keypad types a comma). Returns undefined when it is not valid.
 * @param {string} text @param {number} max
 * @returns {number | null | undefined}
 */
export function parseValue(text, max) {
	const s = text.trim().replace(',', '.');
	if (s === '') return null;
	if (!/^\d{1,3}(\.\d{1,2})?$/.test(s)) return undefined;
	const v = Number(s);
	return v <= max ? v : undefined;
}

/**
 * Grams as typed: more than 0, at most `max`, at most one decimal. Empty or invalid: null.
 * @param {string} text @param {number} max
 */
export function parseGrams(text, max) {
	const s = text.trim().replace(',', '.');
	if (!/^\d{1,5}(\.\d)?$/.test(s)) return null;
	const v = Number(s);
	return v > 0 && v <= max ? v : null;
}

/**
 * What is wrong with a set of values per 100 g, or '' (same rules as the API).
 * @param {Record<string, number | null>} v
 */
export function valuesProblem(v) {
	if (v.carbs !== null && v.sugars !== null && v.sugars > v.carbs) return 'Sugars are part of the carbs, so they cannot be more.';
	const macros = ['carbs', 'protein', 'fat', 'fibre'].reduce((sum, k) => sum + (v[k] ?? 0), 0);
	if (macros > MAX_MACROS) return `Carbs, protein, fat and fibre add up to ${round(macros, 1)} g in 100 g. Check the values.`;
	return '';
}

/**
 * A recipe's values per 100 g: sum(grams * value / 100) / weight * 100, where weight is the weight when
 * done or, when empty, the sum of the ingredients. A value is null when any ingredient has none.
 * @param {{ grams: number, per100: Record<string, number | null> }[]} items @param {number | null} batch
 */
export function recipePer100(items, batch) {
	const total = items.reduce((s, i) => s + i.grams, 0);
	const weight = batch ?? total;
	/** @type {Record<string, number | null>} */
	const out = {};
	for (const n of NUTRIENTS) {
		if (!items.length || items.some((i) => i.per100?.[n] === null || i.per100?.[n] === undefined)) {
			out[n] = null;
			continue;
		}
		const amount = items.reduce((s, i) => s + (i.grams * /** @type {number} */ (i.per100[n])) / 100, 0);
		out[n] = round((amount / weight) * 100, n === 'kcal' ? 1 : 2);
	}
	return out;
}

/** @param {number} v @param {number} digits */
export function round(v, digits) {
	const f = 10 ** digits;
	return Math.round(v * f) / f;
}

/** A value for display: no trailing zeros, "-" when unknown. @param {number | null | undefined} v @param {number} [digits] */
export function show(v, digits = 1) {
	return v === null || v === undefined ? '-' : String(round(v, digits));
}

/** Lower case, accents removed: for matching names typed with or without accents. @param {string} s */
export function normalize(s) {
	return s.normalize('NFKD').replace(/\p{M}/gu, '').toLowerCase().replace(/\s+/g, ' ').trim();
}
