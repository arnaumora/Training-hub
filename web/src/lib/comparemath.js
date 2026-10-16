// Small pure helpers for the run comparison (Compare.svelte): colour steps, axis ticks, nearest mark.

export const STEPS = 5;

/**
 * Five equal-width steps over the known values. Returns the step (1-5) of a value, or 0 when the value is
 * unknown, and the range of each step for the legend.
 * @param {(number | null)[]} values
 */
export function steps(values) {
	const known = /** @type {number[]} */ (values.filter((v) => v !== null && v !== undefined));
	if (!known.length) return { of: () => 0, ranges: [] };
	const lo = Math.min(...known);
	const hi = Math.max(...known);
	if (lo === hi) return { of: (/** @type {number | null} */ v) => (v === null || v === undefined ? 0 : 3), ranges: [[lo, hi, 3]] };
	const w = (hi - lo) / STEPS;
	const of = (/** @type {number | null} */ v) => (v === null || v === undefined ? 0 : Math.min(STEPS, 1 + Math.floor((v - lo) / w)));
	const ranges = Array.from({ length: STEPS }, (_, i) => [lo + i * w, i === STEPS - 1 ? hi : lo + (i + 1) * w, i + 1]);
	return { of, ranges };
}

/**
 * Round ticks covering [lo, hi], about `count` of them.
 * @param {number} lo @param {number} hi @param {number} count
 */
export function ticks(lo, hi, count) {
	const raw = (hi - lo) / Math.max(1, count);
	const mag = 10 ** Math.floor(Math.log10(raw || 1));
	const step = [1, 2, 2.5, 5, 10].map((m) => m * mag).find((s) => s >= raw) ?? 10 * mag;
	const out = [];
	for (let v = Math.ceil(lo / step) * step; v <= hi + 1e-9; v += step) out.push(Math.round(v * 1000) / 1000);
	return out;
}

/**
 * The mark nearest a pointer, within `max` px.
 * @param {{ id: number, x: number, y: number }[]} marks @param {number} px @param {number} py @param {number} max
 */
export function nearest(marks, px, py, max) {
	let best = null;
	let bestD = max * max;
	for (const m of marks) {
		const d = (m.x - px) ** 2 + (m.y - py) ** 2;
		if (d <= bestD) {
			bestD = d;
			best = m;
		}
	}
	return best;
}
