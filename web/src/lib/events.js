// Logged events: what one Log tap saved (carbs and/or a dose, same time). The API stores carbs and doses
// as separate rows; here they are paired back up by their shared time.

/**
 * @param {{ carbs: any[], doses: any[] } | null} log
 * @returns {{ key: string, t: number, carbs: any, dose: any }[]} newest first
 */
export function groupEvents(log) {
	/** @type {Map<number, { key: string, t: number, carbs: any, dose: any }>} */
	const byTime = new Map();
	/** @type {{ key: string, t: number, carbs: any, dose: any }[]} */
	const extra = [];
	for (const c of log?.carbs ?? []) {
		if (byTime.has(c.t)) extra.push({ key: `c${c.id}`, t: c.t, carbs: c, dose: null });
		else byTime.set(c.t, { key: `c${c.id}`, t: c.t, carbs: c, dose: null });
	}
	for (const d of log?.doses ?? []) {
		const e = byTime.get(d.t);
		if (e && !e.dose) {
			e.dose = d;
			e.key += `d${d.id}`;
		} else if (!e) byTime.set(d.t, { key: `d${d.id}`, t: d.t, carbs: null, dose: d });
		else extra.push({ key: `d${d.id}`, t: d.t, carbs: null, dose: d });
	}
	return [...byTime.values(), ...extra].sort((a, b) => b.t - a.t || a.key.localeCompare(b.key));
}

/** Short food name: the part before the first comma ("Pasta, dry, cooked" -> "Pasta"). @param {string} name */
export const shortName = (name) => name.split(',')[0].trim();

/** "2.5 raciones", "45 g carbs" or "Pasta 150 g + Chicken breast 120 g (47.1 g carbs)" @param {any} c */
export function carbsText(c) {
	const r = Number(c.raciones);
	if (c.entry === 'meal' && c.meal) {
		const foods = c.meal.items.map((/** @type {any} */ i) => `${shortName(i.name)} ${Number(i.grams)} g`).join(' + ');
		return `${foods} (${Number(c.grams)} g carbs)`;
	}
	return c.entry === 'raciones' ? `${r} ${r === 1 ? 'racion' : 'raciones'}` : `${Number(c.grams)} g carbs`;
}

/** "4 u Humalog" or "20 u Lantus (basal)" @param {any} d */
export function doseText(d) {
	return `${Number(d.units)} u ${d.insulin_name}${d.kind === 'basal' ? ' (basal)' : ''}`;
}

/** One line for an event: "2.5 raciones (25 g) + 4 u Humalog" @param {{ carbs: any, dose: any }} e */
export function describe(e) {
	const parts = [];
	if (e.carbs) parts.push(e.carbs.entry === 'raciones' ? `${carbsText(e.carbs)} (${Number(e.carbs.grams)} g)` : carbsText(e.carbs));
	if (e.dose) parts.push(doseText(e.dose));
	return parts.join(' + ');
}
