<script>
	// Glucose over time: one smooth line through every reading, live (1 min) or 15-min history, joined
	// across the 15-min steps and broken only where readings are really missing (chartdraw.js), coloured
	// by range like the current number (green in range, ochre above, red below the low or above 250).
	// The target range is a neutral band. Hover shows the nearest reading.
	// Logged events, in ink (not the series colour) so they never read as glucose:
	//   insulin -> triangle above the plot with its units (hollow for basal)
	//   carbs   -> square at the bottom of the plot with its amount
	//   each with a faint dashed guide down the plot, to follow the curve after it.
	import { onThemeChange } from './theme.js';
	import { onDestroy, onMount } from 'svelte';
	import uPlot from 'uplot';
	import 'uplot/dist/uPlot.min.css';
	import { css, drawEventMarkers, drawTargetBand, joinGaps, rangeStroke, smooth } from './chartdraw.js';
	import { describe } from './events.js';

	/**
	 * @type {{
	 *   data: { from: number, to: number, points: [number, number, boolean][] },
	 *   events?: { t: number, carbs: any, dose: any }[]
	 * }}
	 */
	let { data, events = [] } = $props();

	const HEIGHT = 260;
	const TARGET_LOW = 70;
	const TARGET_HIGH = 180;
	const SNAP_MINUTES = 8;
	const Y_AXIS_PX = 44;
	const TOP_PAD_PX = 26; // room above the plot for the insulin markers
	const EVENT_SNAP_S = 10 * 60; // hover shows a logged event within 10 min of the cursor

	/** @type {HTMLDivElement} */
	let el;
	/** @type {HTMLDivElement} */
	let tip;
	/** @type {uPlot | null} */
	let plot = null;
	/** @type {ResizeObserver | null} */
	let resizeObserver = null;
	/** @type {(() => void) | null} */
	let stopTheme = null;

	const fmtTime = (t) =>
		new Date(t * 1000).toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit', hour12: false });
	const fmtDay = (t) => new Date(t * 1000).toLocaleDateString('en-GB', { day: 'numeric', month: 'short' });

	/** X tick labels: 24 h time; the date ("12 Oct") under the first tick and under each new day. */
	function xValues(u, splits) {
		let prevDay = null;
		return splits.map((t) => {
			const day = new Date(t * 1000).toDateString();
			const label = day !== prevDay ? `${fmtTime(t)}\n${fmtDay(t)}` : fmtTime(t);
			prevDay = day;
			return label;
		});
	}

	/** One slot per minute across the window; a live reading wins over a history one in the same minute. */
	function toSeries(d) {
		const start = Math.floor(d.from / 60) * 60;
		const end = Math.floor(d.to / 60) * 60;
		const n = Math.max(1, (end - start) / 60 + 1);
		const xs = new Array(n);
		const ys = new Array(n).fill(null);
		const live = new Array(n).fill(false);
		for (let i = 0; i < n; i++) xs[i] = start + i * 60;
		for (const [t, v, isLive] of d.points) {
			const i = Math.round((t - start) / 60);
			if (i < 0 || i >= n || (live[i] && !isLive)) continue;
			ys[i] = v;
			live[i] = isLive;
		}
		return [xs, ys];
	}

	function yRange(d) {
		let lo = 50;
		let hi = 220;
		for (const p of d.points) {
			lo = Math.min(lo, p[1] - 10);
			hi = Math.max(hi, p[1] + 10);
		}
		return [Math.max(20, lo), Math.min(500, hi)];
	}

	/** Nearest minute with a reading, within SNAP_MINUTES of the cursor. */
	function nearestReading(u, idx) {
		for (let k = 0; k <= SNAP_MINUTES; k++) {
			for (const i of [idx - k, idx + k]) {
				if (i < 0 || i >= u.data[0].length) continue;
				if (u.data[1][i] != null) return { i, v: u.data[1][i] };
			}
		}
		return null;
	}

	function updateTip(u) {
		const idx = u.cursor.idx;
		if (idx == null || u.cursor.left < 0) {
			tip.style.display = 'none';
			return;
		}
		const hit = nearestReading(u, idx);
		const t = hit ? u.data[0][hit.i] : u.data[0][idx];
		const cursorT = u.data[0][idx];
		let near = null;
		for (const e of events) {
			if (Math.abs(e.t - cursorT) <= EVENT_SNAP_S && (!near || Math.abs(e.t - cursorT) < Math.abs(near.t - cursorT))) near = e;
		}
		tip.textContent =
			(hit
				? `${fmtTime(t)}   ${hit.v} mg/dL`
				: `${fmtTime(t)}   no reading`) + (near ? `\n${fmtTime(near.t)}   ${describe(near)}` : '');
		tip.style.display = 'block';
		const x = u.over.offsetLeft + (hit ? u.valToPos(t, 'x') : u.cursor.left);
		const maxLeft = el.clientWidth - tip.offsetWidth;
		tip.style.left = `${Math.max(0, Math.min(maxLeft, x - tip.offsetWidth / 2))}px`;
	}

	function drawEvents(u) {
		drawEventMarkers(u, events);
	}

	function drawBand(u) {
		// The target range from Settings > Training, served with the readings.
		drawTargetBand(u, data.target?.low ?? TARGET_LOW, data.target?.high ?? TARGET_HIGH);
	}

	function create() {
		plot?.destroy();
		const low = data.target?.low ?? TARGET_LOW;
		const high = data.target?.high ?? TARGET_HIGH;
		const surface = css('--bg'); // the chart sits on the page, no card: hollow marks are filled with the page colour
		const muted = css('--muted');
		const grid = css('--chart-grid');
		const axisLine = css('--chart-axis');
		const font = `12px ${css('--font')}`;
		const [lo, hi] = yRange(data);
		const axis = {
			stroke: muted,
			font,
			grid: { stroke: grid, width: 1 },
			ticks: { stroke: axisLine, width: 1, size: 4 }
		};
		plot = new uPlot(
			{
				width: el.clientWidth,
				height: HEIGHT,
				padding: [TOP_PAD_PX, 20, 0, 0],
				scales: { x: { time: true }, y: { range: [lo, hi] } },
				legend: { show: false },
				cursor: {
					y: false,
					points: { size: 9, width: 2, fill: surface },
					drag: { x: false, y: false }
				},
				series: [
					{},
					{
						label: 'Glucose',
						stroke: (/** @type {any} */ u) => rangeStroke(u, low, high),
						width: 2.5,
						paths: smooth,
						spanGaps: false,
						gaps: joinGaps,
						points: { show: false }
					}
				],
				axes: [
					{ ...axis, size: 48, values: xValues },
					{ ...axis, size: Y_AXIS_PX, values: (u, vals) => vals.map((v) => `${v}`) }
				],
				hooks: { drawClear: [drawBand], draw: [drawEvents], setCursor: [updateTip] }
			},
			toSeries(data),
			el
		);
		plot.over.addEventListener('mouseleave', () => (tip.style.display = 'none'));
	}

	onMount(() => {
		create();
		resizeObserver = new ResizeObserver(() => plot?.setSize({ width: el.clientWidth, height: HEIGHT }));
		resizeObserver.observe(el);
		stopTheme = onThemeChange(create);
	});

	// New data from the parent (refresh or a different window): rebuild, since the y range may change.
	$effect(() => {
		data;
		events;
		if (plot) create();
	});

	onDestroy(() => {
		resizeObserver?.disconnect();
		stopTheme?.();
		plot?.destroy();
	});
</script>

<div class="wrap">
	<div class="tip" bind:this={tip} role="status" aria-live="polite"></div>
	<div bind:this={el} role="img" aria-label="Glucose chart"></div>
</div>

<div class="legend" aria-hidden="true">
	<span><i class="key-line"></i>Glucose: in range, above, below</span>
	<span><i class="key-band"></i>Target {data.target?.low ?? TARGET_LOW}-{data.target?.high ?? TARGET_HIGH}</span>
	<span><i class="key-dose"></i>Insulin</span>
	<span><i class="key-carbs"></i>Carbs</span>
</div>

<style>
	.wrap {
		position: relative;
	}
	.tip {
		display: none;
		position: absolute;
		top: -2px;
		z-index: 2;
		padding: 2px 6px;
		background: var(--surface);
		border: 1px solid var(--border);
		border-radius: var(--radius);
		font-size: 12px;
		white-space: pre;
		pointer-events: none;
	}

	.legend {
		display: flex;
		flex-wrap: wrap;
		gap: 0.3rem 1.1rem;
		margin-top: 0.5rem;
		font-size: 0.78rem;
		color: var(--muted);
	}
	.legend span {
		display: inline-flex;
		align-items: center;
		gap: 0.4rem;
	}
	/* The line's three range colours, side by side (hard stops, not a blend). */
	.key-line {
		width: 24px;
		height: 3px;
		background: linear-gradient(
			to right,
			var(--glucose-good) 0 33%,
			var(--glucose-high) 33% 66%,
			var(--glucose-bad) 66% 100%
		);
	}
	.key-dose {
		width: 0;
		height: 0;
		border-left: 5px solid transparent;
		border-right: 5px solid transparent;
		border-top: 8px solid var(--text);
	}
	.key-carbs {
		width: 8px;
		height: 8px;
		background: var(--text);
	}
	.key-band {
		width: 14px;
		height: 10px;
		background: var(--chart-band);
		border: 1px solid var(--border);
	}
</style>
