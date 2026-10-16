// Drawing helpers shared by the home glucose chart and the run page (uPlot draw hooks).
// Logged events are drawn in ink, not the series colour, so they never read as glucose:
//   insulin -> triangle above the plot with its units (hollow for basal)
//   carbs   -> square at the bottom of the plot with its amount
//   each with a faint dashed guide down the plot, to follow the curve after it.
import uPlot from 'uplot';

/** A CSS custom property of the page (the theme tokens in app.css). @param {string} name */
export const css = (name) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();

/** Short marker labels: "4 u", "2.5 rac", "45 g". */
const doseLabel = (/** @type {any} */ d) => `${Number(d.units)} u`;
const carbLabel = (/** @type {any} */ c) => (c.entry === 'raciones' ? `${Number(c.raciones)} rac` : `${Number(c.grams)} g`);

// --- the glucose line ---------------------------------------------------------------------------
// One line for every reading, live (1 min) or 15-min history, as LibreLink draws it: joined across the
// 15-min steps, broken only where readings are really missing (no reading for longer than JOIN_S), drawn as
// a smooth curve, and coloured by range with the same three colours as the current number.

/** Readings further apart than this are a real gap in the line. */
export const JOIN_S = 25 * 60;
/** Above this a high is level 2, red like a low (as the current number; app.run_metrics.HIGH_MG_DL). */
export const VERY_HIGH = 250;

/** Smooth curve through the readings (monotone, so it never overshoots a reading). */
export const smooth = uPlot.paths.spline ? uPlot.paths.spline() : undefined;

/**
 * uPlot `gaps` for a series on one-minute slots with spanGaps false: keep only the gaps longer than JOIN_S,
 * so 15-min history joins into a line and real missing data stays a gap. Gaps are in canvas pixels.
 * @param {any} u @param {number} _sidx @param {number} _i0 @param {number} _i1 @param {[number, number][]} gaps
 */
export function joinGaps(u, _sidx, _i0, _i1, gaps) {
	const x0 = u.scales.x.min;
	const minPx = u.valToPos(x0 + JOIN_S, 'x', true) - u.valToPos(x0, 'x', true);
	return gaps.filter(([a, b]) => b - a > minPx);
}

/**
 * The line's colour by range: a vertical gradient with hard stops at the thresholds, so the line is green in
 * range, ochre above it, red below the low or above VERY_HIGH. Used as a series stroke.
 * @param {any} u @param {number} low @param {number} high
 */
export function rangeStroke(u, low, high) {
	const top = u.bbox.top;
	const h = u.bbox.height || 1;
	const at = (/** @type {number} */ v) => Math.min(1, Math.max(0, (u.valToPos(v, 'y', true) - top) / h));
	const good = css('--glucose-good');
	const above = css('--glucose-high');
	const bad = css('--glucose-bad');
	const g = u.ctx.createLinearGradient(0, top, 0, top + h);
	const [pv, ph, pl] = [at(VERY_HIGH), at(high), at(low)];
	g.addColorStop(0, bad);
	g.addColorStop(pv, bad);
	g.addColorStop(pv, above);
	g.addColorStop(ph, above);
	g.addColorStop(ph, good);
	g.addColorStop(pl, good);
	g.addColorStop(pl, bad);
	g.addColorStop(1, bad);
	return g;
}

/**
 * The target range as a neutral band across the plot.
 * @param {any} u @param {number} low @param {number} high
 */
export function drawTargetBand(u, low, high) {
	const { ctx } = u;
	const top = u.valToPos(high, 'y', true);
	const bottom = u.valToPos(low, 'y', true);
	ctx.save();
	ctx.fillStyle = css('--chart-band');
	ctx.fillRect(u.bbox.left, top, u.bbox.width, bottom - top);
	ctx.restore();
}

/**
 * Insulin and carb markers with labels where they fit.
 * @param {any} u
 * @param {{ t: number, carbs: any, dose: any }[]} events
 * @param {(e: any) => number | null} [xOf] the event's x value (default its time); null leaves it out
 */
export function drawEventMarkers(u, events, xOf = (e) => e.t) {
	if (!events.length) return;
	const { ctx } = u;
	const r = uPlot.pxRatio;
	const ink = css('--text');
	const muted = css('--muted');
	const hole = css('--bg');
	const top = u.bbox.top;
	const bottom = u.bbox.top + u.bbox.height;
	const left = u.bbox.left;
	const right = u.bbox.left + u.bbox.width;

	ctx.save();
	ctx.font = `${11 * r}px ${css('--font')}`;
	ctx.textBaseline = 'middle';
	ctx.textAlign = 'left'; // uPlot leaves it at 'center' after drawing the axes

	const visible = events
		.map((e) => ({ e, v: xOf(e) }))
		.filter(({ v }) => v !== null && v >= u.scales.x.min && v <= u.scales.x.max)
		.sort((a, b) => /** @type {number} */ (a.v) - /** @type {number} */ (b.v))
		.map(({ e, v }) => ({ e, x: Math.round(u.valToPos(v, 'x', true)) + 0.5 }));

	for (const { x } of visible) {
		ctx.strokeStyle = css('--chart-axis');
		ctx.lineWidth = r;
		ctx.setLineDash([3 * r, 3 * r]);
		ctx.beginPath();
		ctx.moveTo(x, top);
		ctx.lineTo(x, bottom);
		ctx.stroke();
	}
	ctx.setLineDash([]);

	const doses = visible.filter((v) => v.e.dose).map((v) => ({ x: v.x, d: v.e.dose }));
	const carbs = visible.filter((v) => v.e.carbs).map((v) => ({ x: v.x, c: v.e.carbs }));

	const ds = 5 * r;
	const dy = top - 11 * r;
	for (const { x, d } of doses) {
		ctx.beginPath();
		ctx.moveTo(x - ds, dy - ds * 0.8);
		ctx.lineTo(x + ds, dy - ds * 0.8);
		ctx.lineTo(x, dy + ds * 0.8);
		ctx.closePath();
		if (d.kind === 'basal') {
			ctx.fillStyle = hole;
			ctx.fill();
			ctx.lineWidth = 1.5 * r;
			ctx.strokeStyle = ink;
			ctx.stroke();
		} else {
			ctx.fillStyle = ink;
			ctx.fill();
		}
	}
	const cs = 4 * r;
	const cy = bottom - 8 * r;
	ctx.fillStyle = ink;
	for (const { x } of carbs) ctx.fillRect(x - cs, cy - cs, 2 * cs, 2 * cs);

	// Labels only where they fit: right of the marker if there is room before the next marker
	// in the same row, else left of it, else none (hovering still shows the details).
	ctx.fillStyle = muted;
	const pad = 3 * r;
	/** @param {{ x: number, text: string }[]} row @param {number} s @param {number} y */
	const labelRow = (row, s, y) => {
		let usedTo = -Infinity; // right end of the last label drawn
		row.forEach(({ x, text }, i) => {
			const w = ctx.measureText(text).width;
			const prevX = i > 0 ? row[i - 1].x + s : left;
			const nextX = i < row.length - 1 ? row[i + 1].x - s : right;
			const rStart = x + s + pad;
			const lStart = x - s - pad - w;
			if (rStart + w + pad <= nextX && rStart >= usedTo + pad) {
				ctx.fillText(text, rStart, y);
				usedTo = rStart + w;
			} else if (lStart >= Math.max(prevX + pad, usedTo + pad)) {
				ctx.fillText(text, lStart, y);
				usedTo = x - s;
			}
		});
	};
	labelRow(doses.map(({ x, d }) => ({ x, text: doseLabel(d) })), ds, dy);
	labelRow(carbs.map(({ x, c }) => ({ x, text: carbLabel(c) })), cs, cy);
	ctx.restore();
}
