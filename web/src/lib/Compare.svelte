<script module>
	// Kept while the app is open, so coming back from a run returns to the same comparison.
	const saved = { sport: /** @type {string | null} */ (null), period: 'all', distance: 'any', colour: 'iob_at_start', x: 'glucose_start' };
</script>

<script>
	// Cross-run comparison (roadmap step 7, change 3), from GET /api/compare:
	//   Overlay  every run in the filter as glucose change from its start against minutes, to 2 h after the
	//            end, coloured by one context variable in five steps (one hue, light = low). Unknown values in
	//            grey, under 80 % coverage dashed, the end of each activity a dot.
	//   Scatter  change per 10 min against a chosen variable, one dot per run (hollow: under 80 % coverage).
	//   Table    every run in the filter with its numbers, also the ones that could not be drawn.
	// Every chart says its n. Hover or tap a line or dot to read it; click it, or "Open run", to open the run.
	import { api, Unauthorized } from './api.js';
	import { km as kmText, sportName, when } from './activityformat.js';
	import { nearest, steps, ticks } from './comparemath.js';

	/** @type {{ onOpen: (id: number) => void, onUnauthorized: () => void }} */
	let { onOpen, onUnauthorized } = $props();

	const PERIODS = [
		['all', 'All time', null],
		['30', 'Last 30 days', 30],
		['90', 'Last 90 days', 90],
		['365', 'Last 12 months', 365]
	];
	const DISTANCES = [
		['any', 'Any distance', null, null],
		['lt5', 'Under 5 km', null, 5],
		['5-10', '5 to 10 km', 5, 10],
		['10-21', '10 to 21 km', 10, 21],
		['gt21', 'Over 21 km', 21, null]
	];

	const signed = (/** @type {number} */ v) => (v > 0 ? `+${v}` : `${v}`);
	const r1 = (/** @type {number} */ v) => Math.round(v * 10) / 10;
	/** "35 min" or "2 h 05 min". @param {number} m */
	const minutes = (m) => (Math.round(m) < 60 ? `${Math.round(m)} min` : `${Math.floor(Math.round(m) / 60)} h ${String(Math.round(m) % 60).padStart(2, '0')} min`);
	/** 7.5 -> "07:30". @param {number} h */
	const clock = (h) => `${String(Math.floor(h)).padStart(2, '0')}:${String(Math.round((h % 1) * 60) % 60).padStart(2, '0')}`;

	/** The context variables: what colours the overlay, and what the scatter can put on its x axis. */
	const VARS = {
		iob_at_start: { label: 'IOB at start', fmt: (/** @type {number} */ v) => `${r1(v)} u` },
		glucose_start: { label: 'Glucose at start', fmt: (/** @type {number} */ v) => `${Math.round(v)} mg/dL` },
		local_hour: { label: 'Time of day', fmt: clock },
		min_since_last_meal: { label: 'Time since last meal', fmt: minutes },
		pre_run_carbs: { label: 'Carbs from 1 h before', fmt: (/** @type {number} */ v) => `${Math.round(v)} g` },
		duration_min: { label: 'Duration', fmt: minutes },
		avg_hr: { label: 'Average heart rate', fmt: (/** @type {number} */ v) => `${Math.round(v)} bpm` },
		km: { label: 'Distance', fmt: (/** @type {number} */ v) => `${r1(v)} km` }
	};
	const COLOUR_KEYS = ['iob_at_start', 'glucose_start', 'local_hour', 'min_since_last_meal', 'pre_run_carbs'];
	const X_KEYS = [...COLOUR_KEYS, 'duration_min', 'avg_hr', 'km'];
	/** @param {string} k */
	const v = (k) => /** @type {{ label: string, fmt: (v: number) => string }} */ (VARS[/** @type {keyof typeof VARS} */ (k)]);

	let sport = $state(saved.sport ?? 'running');
	let period = $state(saved.period);
	let distance = $state(saved.distance);
	let colour = $state(saved.colour);
	let xKey = $state(saved.x);
	$effect(() => {
		Object.assign(saved, { sport, period, distance, colour, x: xKey });
	});

	/** @type {any} */
	let data = $state(null);
	let error = $state('');

	$effect(() => {
		const p = PERIODS.find((x) => x[0] === period);
		const d = DISTANCES.find((x) => x[0] === distance);
		const q = new URLSearchParams();
		if (sport !== 'all') q.set('sport', sport);
		if (p?.[2]) q.set('days', String(p[2]));
		if (d?.[2] !== null && d?.[2] !== undefined) q.set('min_km', String(d[2]));
		if (d?.[3] !== null && d?.[3] !== undefined) q.set('max_km', String(d[3]));
		const want = q.toString();
		api(`/api/compare?${want}`)
			.then((out) => {
				for (const r of out.runs) {
					r.duration_min = (r.moving_s ?? r.elapsed_s) / 60;
					r.km = r.distance_m ? r.distance_m / 1000 : null;
				}
				data = out;
				error = '';
			})
			.catch((e) => {
				if (e instanceof Unauthorized) return onUnauthorized();
				error = 'Could not load the comparison. Try again in a moment.';
			});
	});

	const runs = $derived(/** @type {any[]} */ (data?.runs ?? []));
	const drawn = $derived(runs.filter((r) => r.curve.length > 1));
	const notDrawn = $derived(runs.length - drawn.length);
	const colourSteps = $derived(steps(drawn.map((r) => r[colour])));
	const unknownColour = $derived(drawn.filter((r) => r[colour] === null).length);
	const anyLowCoverage = $derived(drawn.some((r) => r.low_coverage));
	/** @param {any} r */
	const stroke = (r) => {
		const s = colourSteps.of(r[colour]);
		return s ? `var(--step-${s})` : 'var(--chart-unknown)';
	};

	// --- layout ------------------------------------------------------------------------------------------
	let width = $state(0);
	const M = { left: 44, right: 12, top: 22, bottom: 30 };
	const OVERLAY_H = 300;
	const SCATTER_H = 260;

	// --- overlay -----------------------------------------------------------------------------------------
	const ov = $derived.by(() => {
		if (!drawn.length || !width) return null;
		const xMax = Math.max(60, ...drawn.map((r) => r.curve[r.curve.length - 1][0]));
		const ys = drawn.flatMap((r) => r.curve.map((/** @type {number[]} */ p) => p[1]));
		const yLo = Math.min(0, ...ys) - 10;
		const yHi = Math.max(0, ...ys) + 10;
		const w = width - M.left - M.right;
		const h = OVERLAY_H - M.top - M.bottom;
		const x = (/** @type {number} */ m) => M.left + (m / xMax) * w;
		const y = (/** @type {number} */ d) => M.top + (1 - (d - yLo) / (yHi - yLo)) * h;
		const lines = drawn.map((r) => {
			const pts = r.curve.map((/** @type {number[]} */ p) => [x(p[0]), y(p[1])]);
			// The end of the activity on its own line: interpolated between the readings around it.
			const endMin = (r.end - r.start) / 60;
			let endPt = null;
			for (let i = 1; i < r.curve.length; i++) {
				const [m0, d0] = r.curve[i - 1];
				const [m1, d1] = r.curve[i];
				if (m0 <= endMin && endMin <= m1) {
					endPt = [x(endMin), y(d0 + ((d1 - d0) * (endMin - m0)) / (m1 - m0 || 1))];
					break;
				}
			}
			return { r, d: pts.map((/** @type {number[]} */ p, /** @type {number} */ i) => `${i ? 'L' : 'M'}${p[0].toFixed(1)},${p[1].toFixed(1)}`).join(''), pts, endPt };
		});
		return {
			x, y, lines,
			// Minutes in round steps (15, 30, 60...), as many as fit.
			xTicks: (() => {
				const step = [15, 30, 60, 90, 120].find((s) => xMax / s <= Math.max(2, Math.floor(w / 60))) ?? 120;
				return Array.from({ length: Math.floor(xMax / step) + 1 }, (_, i) => i * step);
			})(),
			yTicks: ticks(yLo, yHi, 5),
			marks: lines.flatMap((l) => l.pts.map((/** @type {number[]} */ p, /** @type {number} */ i) => ({ id: l.r.id, x: p[0], y: p[1], i })))
		};
	});

	// --- scatter -----------------------------------------------------------------------------------------
	const sc = $derived.by(() => {
		const pts = runs.filter((r) => r.rate_per_10min !== null && r[xKey] !== null && r[xKey] !== undefined);
		if (!pts.length || !width) return { pts, missing: runs.filter((r) => r.rate_per_10min !== null).length - pts.length, plot: null };
		const xs = pts.map((r) => r[xKey]);
		const yVals = pts.map((r) => r.rate_per_10min);
		let xLo = Math.min(...xs);
		let xHi = Math.max(...xs);
		const pad = (xHi - xLo) * 0.06 || Math.max(1, Math.abs(xLo) * 0.1);
		xLo -= pad;
		xHi += pad;
		const yLo = Math.min(0, ...yVals) - 3;
		const yHi = Math.max(0, ...yVals) + 3;
		const w = width - M.left - M.right;
		const h = SCATTER_H - M.top - M.bottom;
		const x = (/** @type {number} */ val) => M.left + ((val - xLo) / (xHi - xLo)) * w;
		const y = (/** @type {number} */ d) => M.top + (1 - (d - yLo) / (yHi - yLo)) * h;
		return {
			pts,
			missing: runs.filter((r) => r.rate_per_10min !== null).length - pts.length,
			plot: {
				x, y,
				xTicks: ticks(xLo, xHi, Math.max(3, Math.floor(w / 90))),
				yTicks: ticks(yLo, yHi, 5),
				marks: pts.map((r) => ({ id: r.id, x: x(r[xKey]), y: y(r.rate_per_10min), r }))
			}
		};
	});

	// --- hover and tap -----------------------------------------------------------------------------------
	/** @type {{ chart: 'overlay' | 'scatter', id: number, x: number, y: number, i?: number } | null} */
	let focus = $state(null);
	let touched = false;

	/** @param {PointerEvent} e @param {'overlay' | 'scatter'} chart */
	function point(e, chart) {
		const box = /** @type {SVGSVGElement} */ (e.currentTarget).getBoundingClientRect();
		const marks = chart === 'overlay' ? ov?.marks ?? [] : sc.plot?.marks ?? [];
		const m = nearest(marks, e.clientX - box.left, e.clientY - box.top, e.pointerType === 'mouse' ? 18 : 28);
		focus = m ? { chart, id: m.id, x: m.x, y: m.y, i: /** @type {any} */ (m).i } : null;
	}
	/** @param {PointerEvent} e @param {'overlay' | 'scatter'} chart */
	function move(e, chart) {
		if (e.pointerType === 'mouse') point(e, chart);
	}
	/** Mouse: open the run under the pointer. Touch: the first tap shows it, "Open run" opens it.
	 * @param {PointerEvent} e @param {'overlay' | 'scatter'} chart */
	function up(e, chart) {
		if (e.pointerType === 'mouse') {
			if (focus?.chart === chart) onOpen(focus.id);
			return;
		}
		touched = true;
		point(e, chart);
	}
	function leave() {
		if (!touched) focus = null;
	}

	const focusRun = $derived(focus ? runs.find((r) => r.id === focus?.id) : null);
	/** The change at the point under the cursor, on the overlay. */
	const focusPoint = $derived(focus?.chart === 'overlay' && focusRun && focus.i !== undefined ? focusRun.curve[focus.i] : null);
	// The tip sits above the point (below it near the top), never over it: the pointer must stay on the chart.
	let tipH = $state(96);
	const tipLeft = $derived(focus ? Math.min(Math.max(focus.x - 110, 0), Math.max(0, width - 220)) : 0);
	const tipTop = $derived(focus ? (focus.y - tipH - 14 >= 0 ? focus.y - tipH - 14 : focus.y + 14) : 0);

	const dateText = (/** @type {any} */ r) => when(r.start);
	const sportsList = $derived(/** @type {{ sport: string, n: number }[]} */ (data?.sports ?? []));
	const showSport = $derived(sport === 'all');
	const newestFirst = $derived([...runs].reverse());

	// About how many runs before a pattern here is worth trusting (two or three runs a week, two months).
	const TRUST_N = 20;

	// The table scrolls sideways on a phone: say so while columns are hidden to the right.
	/** @type {HTMLDivElement | undefined} */
	let tableWrap = $state();
	let tableScrolls = $state(false);
	function checkScroll() {
		if (tableWrap) tableScrolls = tableWrap.scrollLeft + tableWrap.clientWidth < tableWrap.scrollWidth - 4;
	}
	$effect(() => {
		newestFirst;
		colour;
		if (!tableWrap) return;
		checkScroll();
		const ro = new ResizeObserver(checkScroll);
		ro.observe(tableWrap);
		return () => ro.disconnect();
	});
</script>

<section class="compare" aria-labelledby="compare-title">
	<h2 id="compare-title" class="visually-hidden">Compare runs</h2>

	<div class="filters">
		<label>
			<span>Sport</span>
			<select bind:value={sport}>
				<option value="all">All sports</option>
				{#each sportsList as s}
					<option value={s.sport}>{sportName({ sport: s.sport, sub_sport: null })} ({s.n})</option>
				{/each}
				{#if !sportsList.some((s) => s.sport === sport) && sport !== 'all'}
					<option value={sport}>{sportName({ sport, sub_sport: null })} (0)</option>
				{/if}
			</select>
		</label>
		<label>
			<span>Period</span>
			<select bind:value={period}>
				{#each PERIODS as p}<option value={p[0]}>{p[1]}</option>{/each}
			</select>
		</label>
		<label>
			<span>Distance</span>
			<select bind:value={distance}>
				{#each DISTANCES as d}<option value={d[0]}>{d[1]}</option>{/each}
			</select>
		</label>
	</div>

	{#if error}
		<p class="error" role="alert">{error}</p>
	{:else if !data}
		<p class="muted">Loading...</p>
	{:else}
		<!-- Overlay ---------------------------------------------------------------------------------------->
		<section class="sec" aria-labelledby="overlay-title">
		<div class="head">
			<h3 id="overlay-title">Glucose change from the start</h3>
			<label class="inline">
				<span>Colour by</span>
				<select bind:value={colour}>
					{#each COLOUR_KEYS as k}<option value={k}>{v(k).label}</option>{/each}
				</select>
			</label>
		</div>
		<p class="n">
			n = {drawn.length} {drawn.length === 1 ? 'activity' : 'activities'}{notDrawn ? `; ${notDrawn} more in the filter without a reading at the start` : ''}.
		</p>

		<div class="chart" bind:clientWidth={width}>
			{#if !drawn.length}
				<p class="empty">
					No activity in this filter has glucose at its start yet.
				</p>
			{:else if ov}
				<svg
					width={width}
					height={OVERLAY_H}
					role="img"
					aria-label="Glucose change from the start of {drawn.length} activities, coloured by {v(colour).label.toLowerCase()}. The table below has every value."
					onpointermove={(e) => move(e, 'overlay')}
					onpointerup={(e) => up(e, 'overlay')}
					onpointerleave={leave}
				>
					{#each ov.yTicks as t}
						<line class="grid" class:zero={t === 0} x1={M.left} x2={width - M.right} y1={ov.y(t)} y2={ov.y(t)} />
						<text class="tick" x={M.left - 6} y={ov.y(t) + 4} text-anchor="end">{signed(t)}</text>
					{/each}
					{#each ov.xTicks as t}
						<line class="grid" x1={ov.x(t)} x2={ov.x(t)} y1={M.top} y2={OVERLAY_H - M.bottom} />
						<text class="tick" x={ov.x(t)} y={OVERLAY_H - M.bottom + 18} text-anchor="middle">{t}</text>
					{/each}
					<text class="tick" x={width - M.right} y={OVERLAY_H - 2} text-anchor="end">min from the start</text>
					<text class="tick" x={M.left - 6} y="12" text-anchor="end">mg/dL</text>
					{#each ov.lines as l (l.r.id)}
						<path
							d={l.d}
							class="line"
							class:dim={focus?.chart === 'overlay' && focus.id !== l.r.id}
							class:dashed={l.r.low_coverage}
							style="stroke: {stroke(l.r)}"
						/>
					{/each}
					{#each ov.lines as l (l.r.id)}
						{#if l.endPt}
							<circle class="end" class:dim={focus?.chart === 'overlay' && focus.id !== l.r.id} cx={l.endPt[0]} cy={l.endPt[1]} r="3.5" style="fill: {stroke(l.r)}" />
						{/if}
					{/each}
					{#if focus?.chart === 'overlay'}
						{@const l = ov.lines.find((x) => x.r.id === focus?.id)}
						{#if l}
							<path d={l.d} class="line top" class:dashed={l.r.low_coverage} style="stroke: {stroke(l.r)}" />
							<circle class="cursor" cx={focus.x} cy={focus.y} r="4.5" />
						{/if}
					{/if}
				</svg>
				{#if focus?.chart === 'overlay' && focusRun}
					<div class="tip" style="left: {tipLeft}px; top: {tipTop}px" role="status" bind:clientHeight={tipH}>
						<strong>{sportName(focusRun)}, {dateText(focusRun)}</strong>
						{#if focusPoint}<span>{signed(focusPoint[1])} mg/dL at {Math.round(focusPoint[0])} min</span>{/if}
						<span>{v(colour).label}: {focusRun[colour] === null ? 'unknown' : v(colour).fmt(focusRun[colour])}</span>
						<button class="link" onclick={() => onOpen(focusRun.id)}>Open run</button>
					</div>
				{/if}
			{/if}
		</div>

		{#if drawn.length}
			<ul class="legend" aria-label="Colour key">
				{#each colourSteps.ranges as [lo, hi, s]}
					<li><i style="background: var(--step-{s})"></i>{lo === hi ? v(colour).fmt(lo) : `${v(colour).fmt(lo)} to ${v(colour).fmt(hi)}`}</li>
				{/each}
				{#if unknownColour}<li><i style="background: var(--chart-unknown)"></i>Unknown ({unknownColour})</li>{/if}
				<li class="key"><i class="dot"></i>End of the activity</li>
				{#if anyLowCoverage}<li class="key"><i class="dash"></i>Under 80 % coverage</li>{/if}
			</ul>
		{/if}
		</section>

		<!-- Scatter ---------------------------------------------------------------------------------------->
		<section class="sec" aria-labelledby="scatter-title">
		<div class="head">
			<h3 id="scatter-title">Change per 10 min against</h3>
			<label class="inline">
				<span class="visually-hidden">Variable</span>
				<select bind:value={xKey}>
					{#each X_KEYS as k}<option value={k}>{v(k).label}</option>{/each}
				</select>
			</label>
		</div>
		<p class="n">n = {sc.pts.length}{sc.missing ? `; ${sc.missing} without a value for ${v(xKey).label} not shown` : ''}.</p>
		<div class="chart">
			{#if sc.plot}
				<svg
					width={width}
					height={SCATTER_H}
					role="img"
					aria-label="Change per 10 minutes against {v(xKey).label.toLowerCase()}, {sc.pts.length} activities."
					onpointermove={(e) => move(e, 'scatter')}
					onpointerup={(e) => up(e, 'scatter')}
					onpointerleave={leave}
				>
					{#each sc.plot.yTicks as t}
						<line class="grid" class:zero={t === 0} x1={M.left} x2={width - M.right} y1={sc.plot.y(t)} y2={sc.plot.y(t)} />
						<text class="tick" x={M.left - 6} y={sc.plot.y(t) + 4} text-anchor="end">{signed(t)}</text>
					{/each}
					{#each sc.plot.xTicks as t}
						<line class="grid" x1={sc.plot.x(t)} x2={sc.plot.x(t)} y1={M.top} y2={SCATTER_H - M.bottom} />
						<text class="tick" x={sc.plot.x(t)} y={SCATTER_H - M.bottom + 18} text-anchor="middle">{v(xKey).fmt(t)}</text>
					{/each}
					<text class="tick" x={M.left - 6} y="12">mg/dL per 10 min</text>
					{#each sc.plot.marks as m (m.id)}
						<circle class="dot" class:hollow={m.r.low_coverage} cx={m.x} cy={m.y} r={focus?.chart === 'scatter' && focus.id === m.id ? 7 : 5} />
					{/each}
				</svg>
				{#if focus?.chart === 'scatter' && focusRun}
					<div class="tip" style="left: {tipLeft}px; top: {tipTop}px" role="status" bind:clientHeight={tipH}>
						<strong>{sportName(focusRun)}, {dateText(focusRun)}</strong>
						<span>{signed(focusRun.rate_per_10min)} mg/dL per 10 min</span>
						<span>{v(xKey).label}: {v(xKey).fmt(focusRun[xKey])}</span>
						<button class="link" onclick={() => onOpen(focusRun.id)}>Open run</button>
					</div>
				{/if}
			{:else}
				<p class="empty">Nothing to plot yet: no activity in this filter has both a change per 10 min and a value for {v(xKey).label}.</p>
			{/if}
		</div>
		{#if sc.plot && sc.pts.some((r) => r.low_coverage)}
			<ul class="legend"><li class="key"><i class="ring"></i>Under 80 % coverage</li></ul>
		{/if}
		</section>

		<!-- Table ------------------------------------------------------------------------------------------>
		{#if runs.length}
			<section class="sec" aria-labelledby="table-title">
			<div class="head">
				<h3 id="table-title">Every activity</h3>
				{#if tableScrolls}<span class="n">Swipe for more columns</span>{/if}
			</div>
			<div class="table-wrap" bind:this={tableWrap} onscroll={checkScroll}>
				<table>
					<thead>
						<tr>
							<th scope="col">Date</th>
							{#if showSport}<th scope="col">Sport</th>{/if}
							<th scope="col">Distance</th>
							<th scope="col">Start</th>
							<th scope="col">Change</th>
							<th scope="col">Per 10 min</th>
							<th scope="col">Lowest</th>
							<th scope="col">Coverage</th>
							<th scope="col">{v(colour).label}</th>
						</tr>
					</thead>
					<tbody>
						{#each newestFirst as r (r.id)}
							<tr onclick={() => onOpen(r.id)}>
								<td><button class="link row" onclick={(e) => { e.stopPropagation(); onOpen(r.id); }}>{dateText(r)}</button></td>
								{#if showSport}<td>{sportName(r)}</td>{/if}
								<td>{kmText(r.distance_m)}</td>
								<td>{r.glucose_start ?? '-'}</td>
								<td>{r.delta === null ? '-' : signed(r.delta)}</td>
								<td>{r.rate_per_10min === null ? '-' : signed(r.rate_per_10min)}</td>
								<td>{r.nadir ?? '-'}</td>
								<td class:thin={r.low_coverage}>{r.coverage_pct === null ? '-' : `${Math.round(r.coverage_pct)} %`}</td>
								<td>{r[colour] === null || r[colour] === undefined ? '-' : v(colour).fmt(r[colour])}</td>
							</tr>
						{/each}
					</tbody>
				</table>
			</div>
			</section>
		{:else}
			<p class="muted">No activities in this filter.</p>
		{/if}
		{#if runs.length < TRUST_N}
			<p class="muted small">Patterns need about {TRUST_N} runs; {runs.length} so far.</p>
		{/if}
	{/if}
</section>

<style>
	.compare {
		display: grid;
		gap: var(--s-4);
	}
	.filters {
		display: flex;
		flex-wrap: wrap;
		gap: var(--s-2) var(--s-5);
	}
	label {
		display: grid;
		gap: 0.1rem;
		color: var(--muted);
		font-size: var(--fs-caption);
	}
	label.inline {
		display: flex;
		align-items: baseline;
		gap: 0.5rem;
	}
	select {
		padding: 0.3rem 1.2rem 0.3rem 0;
		background: none;
		border: none;
		border-bottom: 1.5px solid var(--border);
		border-radius: 0;
		color: var(--text);
		font: inherit;
		font-size: max(16px, 1rem);
	}
	select:focus {
		outline: none;
		border-bottom-color: var(--accent);
	}
	select option {
		background: var(--surface);
		color: var(--text);
	}
	.head {
		display: flex;
		flex-wrap: wrap;
		align-items: baseline;
		justify-content: space-between;
		gap: var(--s-1) var(--s-4);
	}
	h3 {
		margin: 0;
		font-size: var(--fs-title);
		font-weight: 700;
		line-height: 1.25;
	}
	.n {
		margin: 0;
		color: var(--muted);
		font-size: var(--fs-small);
	}
	.muted {
		margin: 0;
		color: var(--muted);
	}
	.small {
		font-size: var(--fs-small);
	}
	.error {
		margin: 0;
		color: var(--danger);
	}
	.empty {
		margin: 0.5rem 0;
		color: var(--muted);
	}
	.chart {
		position: relative;
		min-width: 0;
	}
	svg {
		display: block;
		touch-action: pan-y;
		font-family: var(--font);
		font-variant-numeric: tabular-nums;
		user-select: none;
		-webkit-user-select: none;
	}
	.grid {
		stroke: var(--chart-grid);
		stroke-width: 1;
	}
	.grid.zero {
		stroke: var(--chart-axis);
		stroke-width: 1.5;
	}
	.tick {
		fill: var(--muted);
		font-size: 11px;
	}
	.line {
		fill: none;
		stroke-width: 2;
		stroke-linejoin: round;
		stroke-linecap: round;
	}
	.line.dashed {
		stroke-dasharray: 5 4;
	}
	.line.top {
		stroke-width: 3;
	}
	.dim {
		opacity: 0.25;
	}
	.end {
		stroke: var(--bg);
		stroke-width: 2;
	}
	.cursor {
		fill: var(--text);
		stroke: var(--bg);
		stroke-width: 2;
		pointer-events: none;
	}
	circle.dot {
		fill: var(--chart-line);
		stroke: var(--bg);
		stroke-width: 2;
	}
	circle.dot.hollow {
		fill: var(--bg);
		stroke: var(--chart-line);
		stroke-width: 2;
	}
	.tip {
		position: absolute;
		display: grid;
		gap: 0.1rem;
		width: 220px;
		padding: 0.5rem 0.65rem;
		background: var(--surface);
		border: 1px solid var(--border);
		border-radius: var(--radius);
		font-size: var(--fs-caption);
		pointer-events: auto;
	}
	.tip .link {
		justify-self: start;
		margin-top: 0.15rem;
	}
	.legend {
		display: flex;
		flex-wrap: wrap;
		gap: 0.3rem 1.1rem;
		margin: 0.4rem 0 0;
		padding: 0;
		list-style: none;
		font-size: var(--fs-caption);
		color: var(--muted);
	}
	.legend i {
		display: inline-block;
		width: 0.9rem;
		height: 0.55rem;
		margin-right: 0.35rem;
		border-radius: 2px;
		vertical-align: baseline;
	}
	.legend i.dot {
		width: 0.55rem;
		border-radius: 50%;
		background: var(--muted);
	}
	.legend i.dash {
		height: 0;
		border-top: 2px dashed var(--muted);
		border-radius: 0;
		vertical-align: middle;
	}
	.legend i.ring {
		width: 0.6rem;
		height: 0.6rem;
		border: 2px solid var(--chart-line);
		border-radius: 50%;
		vertical-align: middle;
	}
	.table-wrap {
		overflow-x: auto;
	}
	table {
		width: 100%;
		border-collapse: collapse;
		font-size: var(--fs-small);
		font-variant-numeric: tabular-nums;
		white-space: nowrap;
	}
	th,
	td {
		padding: 0.4rem 0.75rem 0.4rem 0;
		text-align: right;
		border-bottom: 1px solid var(--border);
	}
	/* The date stays in view while the other columns scroll under it. */
	th:first-child,
	td:first-child {
		position: sticky;
		left: 0;
		z-index: 1;
		text-align: left;
		background: var(--surface);
	}
	th {
		color: var(--muted);
		font-weight: 400;
	}
	tbody tr {
		cursor: pointer;
	}
	.row {
		color: var(--text);
	}
	@media (hover: hover) {
		tbody tr:hover .row {
			color: var(--accent);
		}
	}
	/* Under 80 % coverage: data quality, so muted, never amber. */
	td.thin {
		color: var(--muted);
		text-decoration: underline dashed;
		text-underline-offset: 3px;
	}
</style>
