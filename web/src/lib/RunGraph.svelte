<script>
	// The run page's graph: stacked panels on one x axis, one cursor across all of them (uPlot sync).
	//   Glucose     one smooth line through every reading (live 1 min or 15-min history), coloured by range,
	//               broken only where readings are missing (chartdraw.js); target band, insulin and carb
	//               markers, the run shaded; optional lag-corrected line (glucose drawn LAG_S earlier, dashed).
	//   Heart rate  1 s, as recorded.
	//   Pace        runs, walks, hikes: min/km on an inverted axis (faster is up). Rides: speed in km/h.
	//               Smoothed over SMOOTH_S for display only (1 s GPS speed is noisy); the stored data is untouched.
	//   Elevation   as recorded.
	// Swims and gym sessions get glucose and heart rate only (pace has no meaning, and GPS is absent).
	// Views: the glucose window (60 min before to 120 min after), the run only, or by distance (the run only,
	// glucose placed by the distance covered at its time). Laps are vertical rules.
	// Colour: glucose keeps the series colour; everything else is ink or grey, as the interface rules ask.
	import { onThemeChange } from './theme.js';
	import { onDestroy, onMount } from 'svelte';
	import uPlot from 'uplot';
	import 'uplot/dist/uPlot.min.css';
	import { css, drawEventMarkers, drawTargetBand, joinGaps, rangeStroke, smooth as curve } from './chartdraw.js';
	import { groupEvents } from './events.js';

	/** @type {{ data: any }} */
	let { data } = $props();

	// From Settings > Training (current version), served with the graph.
	const TARGET_LOW = data.settings?.glucose_low ?? 70;
	const TARGET_HIGH = data.settings?.glucose_high ?? 180;
	const LAG_S = (data.settings?.lag_min ?? 8) * 60;
	/** Lowest bpm of zones 2-5; zone 1 is below the first. */
	const ZONE_FLOORS = data.settings?.hr_zone_floors ?? null;
	const SMOOTH_S = 15;
	const MIN_PACE_SPEED = 1.0; // m/s: slower is a stop, and has no pace
	const Y_AXIS_PX = 52;
	const SYNC_KEY = 'run'; // every panel joins this cursor group
	const GLUCOSE_SNAP_S = 8 * 60;

	const rec = data.records;
	const sport = data.sport;
	const has = (/** @type {(number | null)[]} */ a) => a.some((v) => v !== null && v !== undefined);
	const gps = !['swimming', 'training'].includes(sport) && has(rec.speed_mps);
	const isRide = sport === 'cycling';
	const hasDistance = gps && has(rec.distance_m);
	const events = groupEvents(data.log);
	const runStart = data.start;
	const runEnd = data.end;

	/** @type {'window' | 'run' | 'distance'} */
	let view = $state('window');
	let lag = $state(false);
	/** What the cursor points at, for the readout. @type {any} */
	let at = $state(null);

	/** @type {Record<string, HTMLDivElement>} */
	const els = {};
	/** @type {uPlot[]} */
	let plots = [];
	/** @type {ResizeObserver | null} */
	let resizeObserver = null;
	/** @type {(() => void) | null} */
	let stopTheme = null;
	/** @type {HTMLDivElement} */
	let wrap;
	// After a touch, browsers send emulated mouse events that can end in mouseleave, which would hide the
	// cursor right after the finger set it. A mouseleave this soon after a touch is ignored.
	const TOUCH_GRACE_MS = 1500;
	let lastTouch = 0;

	// --- derived series (computed once) -----------------------------------------------------------

	/** Rolling mean over the last SMOOTH_S seconds, by time (records can have gaps). @param {(number|null)[]} ys */
	function smooth(ys) {
		const out = new Array(ys.length).fill(null);
		let lo = 0;
		let sum = 0;
		let n = 0;
		for (let i = 0; i < ys.length; i++) {
			if (ys[i] !== null) {
				sum += /** @type {number} */ (ys[i]);
				n++;
			}
			while (rec.t[i] - rec.t[lo] >= SMOOTH_S) {
				if (ys[lo] !== null) {
					sum -= /** @type {number} */ (ys[lo]);
					n--;
				}
				lo++;
			}
			out[i] = n ? sum / n : null;
		}
		return out;
	}
	const speed = smooth(rec.speed_mps);
	const pace = speed.map((v) => (v !== null && v >= MIN_PACE_SPEED ? 1000 / v : null)); // s per km
	const kmh = speed.map((v) => (v === null ? null : v * 3.6));
	const km = rec.distance_m.map((/** @type {number | null} */ v) => (v === null ? null : v / 1000));

	/** Distance (km) at time t, by interpolation between records; null outside the run. @param {number} t */
	function kmAt(t) {
		if (!hasDistance || t < rec.t[0] || t > rec.t[rec.t.length - 1]) return null;
		let lo = 0;
		let hi = rec.t.length - 1;
		while (hi - lo > 1) {
			const mid = (lo + hi) >> 1;
			if (rec.t[mid] <= t) lo = mid;
			else hi = mid;
		}
		const a = km[lo];
		const b = km[hi];
		if (a === null || b === null) return a ?? b;
		const span = rec.t[hi] - rec.t[lo];
		return span ? a + ((b - a) * (t - rec.t[lo])) / span : a;
	}

	/** Index of the last element <= v in a sorted array (or 0). @param {(number|null)[]} xs @param {number} v */
	function indexAt(xs, v) {
		let lo = 0;
		let hi = xs.length - 1;
		if (hi < 0) return -1;
		while (lo < hi) {
			const mid = (lo + hi + 1) >> 1;
			if (/** @type {number} */ (xs[mid] ?? -Infinity) <= v) lo = mid;
			else hi = mid - 1;
		}
		return lo;
	}

	/** Nearest glucose reading within GLUCOSE_SNAP_S of time t. @param {number} t */
	function glucoseAt(t) {
		let best = null;
		for (const p of data.glucose) {
			const d = Math.abs(p[0] - t);
			if (d <= GLUCOSE_SNAP_S && (!best || d < Math.abs(best[0] - t))) best = p;
		}
		return best;
	}

	// --- formatting --------------------------------------------------------------------------------

	const clock = (/** @type {number} */ t) =>
		new Date(t * 1000).toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit', hour12: false });
	/** "+12:30", "-5:00", "+1:02:03" relative to the start. @param {number} t */
	function elapsed(t) {
		const s = Math.round(t - runStart);
		const a = Math.abs(s);
		const h = Math.floor(a / 3600);
		const m = Math.floor((a % 3600) / 60);
		const sec = String(a % 60).padStart(2, '0');
		return `${s < 0 ? '-' : '+'}${h ? `${h}:${String(m).padStart(2, '0')}` : m}:${sec}`;
	}
	const mmss = (/** @type {number} */ s) => `${Math.floor(s / 60)}:${String(Math.round(s % 60)).padStart(2, '0')}`;

	// --- building the plots -------------------------------------------------------------------------

	function xRange() {
		if (view === 'window') return [data.window.from, data.window.to];
		if (view === 'run') return [runStart, runEnd];
		const last = km.findLast((/** @type {number | null} */ v) => v !== null) ?? 0;
		return [0, Math.max(0.01, last)];
	}

	/** Record x values for the current view. */
	const recX = () => (view === 'distance' ? km : rec.t);

	/** One slot per minute across the range, so missing minutes are real gaps (as on the home chart). */
	function glucoseSeries() {
		if (view === 'distance') {
			const pts = data.glucose
				.filter((/** @type {any} */ p) => p[0] >= runStart && p[0] <= runEnd)
				.map((/** @type {any} */ p) => [kmAt(p[0]), p[1]])
				.filter((/** @type {any} */ p) => p[0] !== null);
			return [pts.map((/** @type {any} */ p) => p[0]), pts.map((/** @type {any} */ p) => p[1]), pts.map(() => null)];
		}
		const [from, to] = xRange();
		const start = Math.floor(from / 60) * 60;
		const n = Math.max(1, Math.floor((to - start) / 60) + 1);
		const xs = Array.from({ length: n }, (_, i) => start + i * 60);
		const ys = new Array(n).fill(null);
		const live = new Array(n).fill(false);
		const lagged = new Array(n).fill(null);
		for (const [t, v, isLive] of data.glucose) {
			const i = Math.round((t - start) / 60);
			// A live reading wins over a history one in the same minute.
			if (i >= 0 && i < n && !(live[i] && !isLive)) {
				ys[i] = v;
				live[i] = isLive;
			}
			const j = Math.round((t - LAG_S - start) / 60);
			if (lag && j >= 0 && j < n) lagged[j] = v;
		}
		return [xs, ys, lagged];
	}

	function glucoseRange() {
		let lo = 50;
		let hi = 220;
		for (const p of data.glucose) {
			lo = Math.min(lo, p[1] - 10);
			hi = Math.max(hi, p[1] + 10);
		}
		return [Math.max(20, lo), Math.min(500, hi)];
	}

	/** Range of the values, with padding and an optional minimum span. @param {(number|null)[]} ys @param {number} pad @param {number} minSpan */
	function spanOf(ys, pad, minSpan) {
		const v = /** @type {number[]} */ (ys.filter((x) => x !== null));
		if (!v.length) return [0, 1];
		let lo = Math.min(...v) - pad;
		let hi = Math.max(...v) + pad;
		if (hi - lo < minSpan) {
			const mid = (hi + lo) / 2;
			lo = mid - minSpan / 2;
			hi = mid + minSpan / 2;
		}
		return [lo, hi];
	}

	/** Pace range from the 2nd to 98th percentile, so a few spikes do not flatten the line. */
	function paceRange() {
		const v = /** @type {number[]} */ (pace.filter((x) => x !== null)).sort((a, b) => a - b);
		if (!v.length) return [240, 480];
		const p = (/** @type {number} */ q) => v[Math.min(v.length - 1, Math.floor(q * v.length))];
		return [p(0.02) - 15, p(0.98) + 15];
	}

	// Heights grow with the width (a share of it, between a phone floor and a desktop ceiling), so on a wide
	// screen the curves are not thin strips. Glucose, the reason for the page, gets the most.
	const panels = [
		{ key: 'glucose', label: 'Glucose', min: 210, share: 0.3, max: 340 },
		...(has(rec.hr) ? [{ key: 'hr', label: 'Heart rate', min: 130, share: 0.16, max: 190 }] : []),
		...(gps ? [{ key: 'pace', label: isRide ? 'Speed' : 'Pace', min: 130, share: 0.16, max: 190 }] : []),
		...(gps && has(rec.altitude_m) ? [{ key: 'alt', label: 'Elevation', min: 90, share: 0.09, max: 120 }] : [])
	];
	/** @param {{ min: number, share: number, max: number }} p @param {number} width */
	const heightOf = (p, width) => Math.round(Math.max(p.min, Math.min(p.max, p.share * width)));

	/** Vertical rules where each lap after the first starts. @param {any} u */
	function drawLaps(u) {
		if (data.laps.length < 2) return;
		const { ctx } = u;
		const r = uPlot.pxRatio;
		ctx.save();
		ctx.strokeStyle = css('--chart-axis');
		ctx.lineWidth = r;
		for (const lap of data.laps.slice(1)) {
			const v = view === 'distance' ? kmAt(lap.start) : lap.start;
			if (v === null || v < u.scales.x.min || v > u.scales.x.max) continue;
			const x = Math.round(u.valToPos(v, 'x', true)) + 0.5;
			ctx.beginPath();
			ctx.moveTo(x, u.bbox.top);
			ctx.lineTo(x, u.bbox.top + u.bbox.height);
			ctx.stroke();
		}
		ctx.restore();
	}

	/** Zone (1-5) of a heart rate. @param {number} hr */
	const zoneOf = (hr) => (ZONE_FLOORS ? 1 + ZONE_FLOORS.filter((/** @type {number} */ f) => hr >= f).length : null);

	/** Heart-rate zones as bands behind the line, each labelled at the right edge. @param {any} u */
	function drawZones(u) {
		if (!ZONE_FLOORS) return;
		const { ctx } = u;
		const r = uPlot.pxRatio;
		const edges = [-Infinity, ...ZONE_FLOORS, Infinity];
		ctx.save();
		ctx.font = `${10 * r}px ${css('--font')}`;
		ctx.textAlign = 'right';
		ctx.textBaseline = 'middle';
		for (let z = 0; z < 5; z++) {
			const lo = Math.max(edges[z], u.scales.y.min);
			const hi = Math.min(edges[z + 1], u.scales.y.max);
			if (hi <= lo) continue;
			const yTop = u.valToPos(hi, 'y', true);
			const yBottom = u.valToPos(lo, 'y', true);
			ctx.globalAlpha = 0.22;
			ctx.fillStyle = css(`--zone-${z + 1}`);
			ctx.fillRect(u.bbox.left, yTop, u.bbox.width, yBottom - yTop);
			// The zone's floor as a thin line in its own colour, so neighbouring tints never blur together.
			if (edges[z] > u.scales.y.min && edges[z] < u.scales.y.max) {
				ctx.globalAlpha = 0.9;
				ctx.strokeStyle = css(`--zone-${z + 1}`);
				ctx.lineWidth = r;
				ctx.beginPath();
				ctx.moveTo(u.bbox.left, Math.round(yBottom) + 0.5);
				ctx.lineTo(u.bbox.left + u.bbox.width, Math.round(yBottom) + 0.5);
				ctx.stroke();
			}
			if (yBottom - yTop >= 12 * r) {
				ctx.globalAlpha = 1;
				ctx.fillStyle = css('--muted');
				ctx.fillText(`Z${z + 1}`, u.bbox.left + u.bbox.width - 4 * r, (yTop + yBottom) / 2);
			}
		}
		ctx.restore();
	}

	/** The run shaded in the window view, so before and after read at a glance. @param {any} u */
	function drawRunSpan(u) {
		if (view !== 'window') return;
		const { ctx } = u;
		const a = u.valToPos(runStart, 'x', true);
		const b = u.valToPos(runEnd, 'x', true);
		ctx.save();
		ctx.globalAlpha = 0.08;
		ctx.fillStyle = css('--accent');
		ctx.fillRect(a, u.bbox.top, b - a, u.bbox.height);
		ctx.restore();
	}

	/** @param {any} u */
	function onCursor(u) {
		const left = u.cursor.left;
		if (left === null || left < 0 || u.cursor.idx === null || u.cursor.idx === undefined) {
			at = null;
			return;
		}
		const xv = u.posToVal(left, 'x');
		const xs = recX();
		let i = indexAt(xs, xv);
		const t = view === 'distance' ? (i >= 0 ? rec.t[i] : null) : xv;
		if (t === null) return (at = null);
		if (view !== 'distance') i = rec.t.length && t >= rec.t[0] && t <= rec.t[rec.t.length - 1] ? indexAt(rec.t, t) : -1;
		const g = glucoseAt(t);
		const during = t >= runStart && t <= runEnd;
		const lapIdx = during && data.laps.length > 1 ? data.laps.findLastIndex((/** @type {any} */ l) => l.start <= t) : -1;
		at = {
			t,
			km: i >= 0 ? km[i] : null,
			hr: i >= 0 ? rec.hr[i] : null,
			pace: i >= 0 ? pace[i] : null,
			during,
			kmh: i >= 0 ? kmh[i] : null,
			alt: i >= 0 ? rec.altitude_m[i] : null,
			glucose: g ? g[1] : null,
			glucoseLive: g ? g[2] : null,
			lap: lapIdx >= 0 ? lapIdx + 1 : null
		};
	}

	function build() {
		plots.forEach((p) => p.destroy());
		plots = [];
		const width = wrap.clientWidth;
		const ink = css('--text');
		const muted = css('--muted');
		const surface = css('--bg');
		const grid = css('--chart-grid');
		const axisLine = css('--chart-axis');
		const band = css('--chart-band');
		const font = `12px ${css('--font')}`;
		const xr = xRange();
		const isTime = view !== 'distance';
		const axisBase = { stroke: muted, font, grid: { stroke: grid, width: 1 }, ticks: { stroke: axisLine, width: 1, size: 4 } };

		panels.forEach((panel, k) => {
			const last = k === panels.length - 1;
			const xAxis = last
				? {
						...axisBase,
						size: 34,
						values: isTime
							? (/** @type {any} */ _u, /** @type {number[]} */ vals) => vals.map(clock)
							: (/** @type {any} */ _u, /** @type {number[]} */ vals) => vals.map((v) => `${+v.toFixed(2)} km`)
					}
				: { ...axisBase, size: 6, values: (/** @type {any} */ _u, /** @type {number[]} */ vals) => vals.map(() => ''), ticks: { show: false } };
			/** @type {any} */
			let opts = {
				width,
				height: heightOf(panel, width),
				padding: [panel.key === 'glucose' ? 26 : 8, 22, 0, 0], // right: room for the last time label
				legend: { show: false },
				cursor: {
					sync: { key: SYNC_KEY },
					y: false,
					points: { size: 8, width: 2, fill: surface },
					drag: { x: false, y: false },
					bind: {
						mouseleave: (/** @type {any} */ _u, /** @type {any} */ _t, /** @type {(e: MouseEvent) => any} */ handler) => (/** @type {MouseEvent} */ e) =>
							Date.now() - lastTouch < TOUCH_GRACE_MS ? null : handler(e)
					}
				},
				scales: { x: { time: isTime, range: () => xr } },
				axes: [xAxis, { ...axisBase, size: Y_AXIS_PX }],
				hooks: { setCursor: [onCursor] }
			};
			/** @type {any[]} */
			let series;
			if (panel.key === 'glucose') {
				series = glucoseSeries();
				opts.scales.y = { range: () => glucoseRange() };
				const low = TARGET_LOW;
				const high = TARGET_HIGH;
				const byRange = (/** @type {any} */ u) => rangeStroke(u, low, high);
				opts.series = [
					{},
					{ stroke: byRange, width: 2.5, paths: curve, spanGaps: false, gaps: joinGaps, points: { show: false } },
					{ stroke: muted, width: 1.5, dash: [5, 4], paths: curve, spanGaps: false, gaps: joinGaps, points: { show: false } }
				];
				// By distance there are only the readings during the run: a line through them, each one marked.
				if (view === 'distance') opts.series[1] = { stroke: byRange, width: 2.5, spanGaps: true, points: { show: true, size: 5, stroke: byRange, fill: surface } };
				opts.hooks.drawClear = [(/** @type {any} */ u) => drawRunSpan(u), (/** @type {any} */ u) => drawTargetBand(u, TARGET_LOW, TARGET_HIGH)];
				opts.hooks.draw = [
					drawLaps,
					(/** @type {any} */ u) => drawEventMarkers(u, events, (e) => (view === 'distance' ? kmAt(e.t) : e.t))
				];
			} else if (panel.key === 'hr') {
				series = [recX(), rec.hr];
				opts.scales.y = { range: () => spanOf(rec.hr, 5, 20) };
				opts.series = [{}, { stroke: ink, width: 1.25, spanGaps: false, points: { show: false } }];
				opts.hooks.drawClear = [drawZones];
				opts.hooks.draw = [drawLaps];
			} else if (panel.key === 'pace') {
				series = [recX(), isRide ? kmh : pace];
				opts.scales.y = isRide ? { range: () => spanOf(kmh, 2, 5) } : { range: () => paceRange(), dir: -1 };
				opts.axes[1].values = isRide
					? (/** @type {any} */ _u, /** @type {number[]} */ vals) => vals.map((v) => `${+v.toFixed(0)}`)
					: (/** @type {any} */ _u, /** @type {number[]} */ vals) => vals.map((v) => mmss(v));
				opts.series = [{}, { stroke: ink, width: 1.25, spanGaps: false, points: { show: false } }];
				opts.hooks.draw = [drawLaps];
			} else {
				series = [recX(), rec.altitude_m];
				opts.scales.y = { range: () => spanOf(rec.altitude_m, 3, 20) };
				opts.series = [{}, { stroke: muted, width: 1, fill: band, spanGaps: true, points: { show: false } }];
				opts.hooks.draw = [drawLaps];
			}
			const u = new uPlot(opts, series, els[panel.key]);
			touchCursor(u);
			plots.push(u);
		});
	}

	/** Touch: a horizontal drag moves the cursor on every panel; a vertical one still scrolls the page. @param {uPlot} u */
	function touchCursor(u) {
		const over = u.over;
		over.style.touchAction = 'pan-y';
		/** @param {PointerEvent} e */
		const move = (e) => {
			if (e.pointerType === 'mouse') return;
			lastTouch = Date.now();
			const rect = over.getBoundingClientRect();
			const left = Math.max(0, Math.min(rect.width, e.clientX - rect.left));
			for (const p of plots) p.setCursor({ left, top: 10 });
		};
		over.addEventListener('pointerdown', move);
		over.addEventListener('pointermove', move);
	}

	$effect(() => {
		view;
		lag;
		if (wrap) build();
	});

	onMount(() => {
		resizeObserver = new ResizeObserver(() =>
			plots.forEach((p, k) => p.setSize({ width: wrap.clientWidth, height: heightOf(panels[k], wrap.clientWidth) }))
		);
		resizeObserver.observe(wrap);
		stopTheme = onThemeChange(build);
	});

	onDestroy(() => {
		resizeObserver?.disconnect();
		stopTheme?.();
		plots.forEach((p) => p.destroy());
	});

	// Panel readouts: the value at the cursor, or the activity's summary when the cursor is away.
	const avgPace = data.moving_s && data.distance_m ? (data.moving_s / data.distance_m) * 1000 : null;
	const avgKmh = data.moving_s && data.distance_m ? (data.distance_m / data.moving_s) * 3.6 : null;
	function readout(/** @type {string} */ key) {
		if (key === 'glucose') {
			if (at) return at.glucose === null ? 'no reading' : `${at.glucose} mg/dL`;
			return data.glucose.length ? '' : 'no readings around this activity';
		}
		if (key === 'hr') {
			if (at) return at.hr === null ? '' : `${at.hr} bpm${zoneOf(at.hr) ? `, zone ${zoneOf(at.hr)}` : ''}`;
			return data.avg_hr ? `avg ${data.avg_hr}, max ${data.max_hr} bpm` : '';
		}
		if (key === 'pace') {
			if (isRide) return at ? (at.kmh === null || !at.during ? '' : `${at.kmh.toFixed(1)} km/h`) : avgKmh ? `avg ${avgKmh.toFixed(1)} km/h` : '';
			if (at) return !at.during ? '' : at.pace === null ? 'stopped' : `${mmss(at.pace)} /km`;
			return avgPace ? `avg ${mmss(avgPace)} /km` : '';
		}
		if (key === 'alt') return at ? (at.alt === null ? '' : `${Math.round(at.alt)} m`) : data.ascent_m ? `+${Math.round(data.ascent_m)} m` : '';
		return '';
	}
</script>

<div class="controls">
	<div class="seg" role="group" aria-label="What the graph shows">
		<button aria-pressed={view === 'window'} aria-label="1 h before to 2 h after" onclick={() => (view = 'window')}>-1 h to +2 h</button>
		<button aria-pressed={view === 'run'} onclick={() => (view = 'run')}>{sport === 'running' ? 'Run' : 'Activity'}</button>
		{#if hasDistance}
			<button aria-pressed={view === 'distance'} onclick={() => (view = 'distance')}>Distance</button>
		{/if}
	</div>
</div>

<p class="cursor-line" aria-live="off">
	{#if at}
		<strong>{clock(at.t)}</strong>
		<span>{elapsed(at.t)}</span>
		{#if at.km !== null}<span>{at.km.toFixed(2)} km</span>{/if}
		{#if at.lap}<span>lap {at.lap}</span>{/if}
	{/if}
</p>

<div class="panels" bind:this={wrap}>
	{#each panels as panel (panel.key)}
		<div class="panel">
			<p class="panel-head">
				<!-- The glucose panel sits under the page's "Glucose" heading; its readout still shows here. -->
				<span class="name">{panel.key === 'glucose' ? '' : panel.label}</span>
				<span class="value" class:glucose={panel.key === 'glucose'}>{readout(panel.key)}</span>
			</p>
			<div bind:this={els[panel.key]}></div>
		</div>
	{/each}
</div>

{#if view !== 'distance'}
	<label class="lag">
		<input type="checkbox" bind:checked={lag} />
		<span>Lag-corrected glucose ({LAG_S / 60} min earlier, dashed)</span>
	</label>
{/if}

<style>
	.controls {
		margin: var(--s-3) 0;
	}
	.cursor-line {
		display: flex;
		flex-wrap: wrap;
		gap: 0 var(--s-4);
		min-height: 1.5em;
		margin: 0 0 var(--s-2);
	}
	.cursor-line strong {
		font-weight: 700;
	}
	.panels {
		display: grid;
		gap: var(--s-4);
	}
	.panel-head {
		display: flex;
		align-items: baseline;
		justify-content: space-between;
		gap: 1rem;
		margin: 0;
		font-size: var(--fs-small);
	}
	.name {
		color: var(--muted);
	}
	.value {
		font-weight: 700;
	}
	.value.glucose {
		color: var(--accent);
	}
	/* The whole line is the hit area (44 px tall), not just the box. */
	.lag {
		display: flex;
		gap: var(--s-3);
		align-items: center;
		min-height: var(--tap);
		max-width: 44rem;
		margin-top: var(--s-2);
		color: var(--muted);
		font-size: var(--fs-small);
		cursor: pointer;
	}
	.lag input {
		flex: none;
		width: 1.25rem;
		height: 1.25rem;
		margin: 0;
		accent-color: var(--accent);
	}
</style>
