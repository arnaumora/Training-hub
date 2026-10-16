<script>
	// One training week's figures (roadmap step 8, change 2), from GET /api/weeks/{monday}, shown under the
	// calendar's week view since the rework (step 6, 2026-10-15: Calendar and Week summary were two different
	// "week" screens). In order: what needs attention (flags decided by code), then `children` (the calendar's
	// days, passed in), then the load, glucose for the week against the consensus targets, the nights after
	// training, each day's log, and the AI summary. A week that has not started shows only `children`.
	import AiReport from './AiReport.svelte';
	import { api, Unauthorized } from './api.js';
	import { sportName } from './activityformat.js';

	/** @type {{ monday: string, children?: import('svelte').Snippet, onUnauthorized: () => void }} */
	let { monday, children, onUnauthorized } = $props();

	/** Monday of this week, YYYY-MM-DD in local time. */
	const thisMonday = (() => {
		const x = new Date();
		x.setDate(x.getDate() - ((x.getDay() + 6) % 7));
		return `${x.getFullYear()}-${String(x.getMonth() + 1).padStart(2, '0')}-${String(x.getDate()).padStart(2, '0')}`;
	})();
	const started = $derived(monday <= thisMonday);

	/** @type {any} */
	let w = $state(null);
	let error = $state('');
	$effect(() => {
		const which = monday;
		w = null;
		error = '';
		if (which > thisMonday) return;
		api(`/api/weeks/${which}`)
			.then((r) => {
				if (which === monday) w = r;
			})
			.catch((e) => {
				if (e instanceof Unauthorized) return onUnauthorized();
				error = 'Could not load the week figures. Try again in a moment.';
			});
	});

	// Flags decided by code (app/week.py). Lows in the low colour; the coverage one is the badge by "Glucose".
	const LOWS = ['went_low', 'night_low', 'time_low'];
	const MIN_COVERAGE = 70;
	const flags = $derived(w ? w.flags.filter((/** @type {any} */ f) => f.code !== 'coverage') : []);
	const thin = $derived(w ? w.glucose.coverage_pct < MIN_COVERAGE : false);
	const pct = (/** @type {number | null} */ v) => (v === null || v === undefined ? '-' : `${v} %`);
	const zoneTotal = $derived(w ? w.zones_min.reduce((/** @type {number} */ a, /** @type {number} */ b) => a + b, 0) : 0);
	const num = (/** @type {number} */ v) => (Number.isInteger(v) ? `${v}` : v.toFixed(1));
	/** "45 min" or "2 h 39 min". @param {number} m */
	const hm = (m) => (m < 60 ? `${m} min` : `${Math.floor(m / 60)} h ${String(m % 60).padStart(2, '0')} min`);
</script>

{#if w && flags.length}
	<div class="flags" role="note" aria-label="This week">
		{#each flags as f}
			<p class={f.kind !== 'safety' ? 'flag-quality' : LOWS.includes(f.code) ? 'flag-low' : 'flag-safety'}>{f.text}</p>
		{/each}
	</div>
{/if}

{#if children}{@render children()}{/if}

{#if started}
	{#if error}
		<p class="error" role="alert">{error}</p>
	{:else if !w}
		<p class="muted">Loading the week's figures...</p>
	{:else}
		<div class="cols">
			<section class="sec" aria-labelledby="load-title">
				<div class="head">
					<h3 id="load-title">Load</h3>
					{#if !w.complete}<span class="badge">so far</span>{/if}
				</div>
				{#if w.by_sport.length}
					<ul class="load">
						{#each w.by_sport as b}
							<li>
								<span>{sportName({ sport: b.sport, sub_sport: null })}</span>
								<span>{b.n} x, {hm(b.minutes)}{b.km ? `, ${num(b.km)} km` : ''}</span>
							</li>
						{/each}
					</ul>
					{#if zoneTotal > 0}
						<div class="bar" role="img" aria-label="Minutes in zones 1 to 5: {w.zones_min.map((/** @type {number} */ m) => Math.round(m)).join(', ')}">
							{#each w.zones_min as m, i}
								{#if m > 0}<i style="flex-grow: {m}; background: var(--zone-{i + 1})"></i>{/if}
							{/each}
						</div>
						<ul class="legend">
							{#each w.zones_min as m, i}
								<li><i style="background: var(--zone-{i + 1})"></i>Zone {i + 1} <strong>{Math.round(m)} min</strong></li>
							{/each}
						</ul>
					{/if}
				{:else}
					<p class="muted">No sessions.</p>
				{/if}
			</section>

			<section class="sec" aria-labelledby="glucose-week-title">
				<div class="head">
					<h3 id="glucose-week-title">Glucose</h3>
					<span class="badge" class:thin title={thin
							? `Under ${MIN_COVERAGE} %: the time-in-range figures are not reliable yet (the consensus asks for ${MIN_COVERAGE} %).`
							: "Share of the week's minutes with a reading"}>{pct(w.glucose.coverage_pct)} coverage</span>
				</div>
				<dl class="figures">
					<div>
						<dt>In range</dt>
						<dd class:ok={w.glucose.in_range_pct !== null && w.glucose.in_range_pct > 70}>{pct(w.glucose.in_range_pct)}<span class="sub">week before {pct(w.previous.in_range_pct)}</span></dd>
					</div>
					<div><dt>Mean</dt><dd>{w.glucose.mean ?? '-'}</dd></div>
					<div><dt>Below {w.glucose.low}</dt><dd class:low={w.glucose.below_pct >= 4}>{pct(w.glucose.below_pct)}</dd></div>
					<div><dt>Below 54</dt><dd class:low={w.glucose.very_low_pct >= 1}>{pct(w.glucose.very_low_pct)}</dd></div>
					<div><dt>Above {w.glucose.high}</dt><dd>{pct(w.glucose.above_pct)}</dd></div>
					<div><dt>Above 250</dt><dd class:high={w.glucose.very_high_pct >= 5}>{pct(w.glucose.very_high_pct)}</dd></div>
				</dl>
				<p class="note">Targets: over 70 % in range, under 4 % below {w.glucose.low}, under 1 % below 54, under 5 % above 250.</p>
			</section>
		</div>

		<div class="cols">
			{#if w.nights.length}
				<section class="sec" aria-labelledby="nights-title">
					<h3 id="nights-title">Nights after training</h3>
					<ul class="nights">
						{#each w.nights as n}
							<li>
								<span>{n.day}</span>
								<span class:low={n.min_below > 0}>
									{n.lowest === null ? 'no readings' : n.min_below ? `${n.min_below} min below ${w.glucose.low}, lowest ${n.lowest}` : `lowest ${n.lowest}`}
								</span>
							</li>
						{/each}
					</ul>
				</section>
			{/if}

			<section class="sec" aria-labelledby="days-title">
				<h3 id="days-title">Days</h3>
				<div class="table-wrap">
					<table>
						<thead>
							<tr><th scope="col">Day</th><th scope="col">Carbs</th><th scope="col">Rapid</th><th scope="col">Basal</th><th scope="col">In range</th></tr>
						</thead>
						<tbody>
							{#each w.days as d}
								<tr class:future={d.future}>
									<td>{d.day}</td>
									<td>{d.future ? '' : d.carbs_g ? `${num(d.carbs_g)} g` : '-'}</td>
									<td>{d.future ? '' : d.rapid_u ? `${num(d.rapid_u)} u` : '-'}</td>
									<td>{d.future ? '' : d.basal_u ? `${num(d.basal_u)} u` : '-'}</td>
									<td>{d.future || d.in_range_pct === null ? '' : `${Math.round(d.in_range_pct)} %`}</td>
								</tr>
							{/each}
						</tbody>
					</table>
				</div>
			</section>
		</div>

		{#key monday}
			<AiReport path={`/api/weeks/${monday}/report`} title="Summary" {onUnauthorized} />
		{/key}
	{/if}
{/if}

<style>
	.flags {
		display: grid;
		gap: var(--s-2);
	}
	.badge.thin {
		border-style: dashed;
	}
	.cols {
		display: grid;
		gap: var(--s-4);
		align-items: start;
	}
	@media (min-width: 900px) {
		.cols {
			grid-template-columns: repeat(2, minmax(0, 1fr));
		}
	}
	.head {
		display: flex;
		align-items: baseline;
		gap: var(--s-3);
	}
	h3 {
		margin: 0;
		font-size: var(--fs-title);
		font-weight: 700;
		line-height: 1.25;
	}
	.muted {
		margin: 0;
		color: var(--muted);
	}
	.note {
		margin: 0;
		color: var(--muted);
		font-size: var(--fs-caption);
	}
	.error {
		margin: 0;
		color: var(--danger);
	}
	.low {
		color: var(--danger);
	}
	.high {
		color: var(--caution);
	}
	.ok {
		color: var(--glucose-good);
	}
	.load,
	.nights {
		list-style: none;
		margin: 0;
		padding: 0;
	}
	.load li,
	.nights li {
		display: flex;
		justify-content: space-between;
		gap: var(--s-4);
		padding: var(--s-2) 0;
	}
	.load li + li,
	.nights li + li {
		border-top: 1px solid var(--border);
	}
	.load li span:last-child {
		font-weight: 700;
	}
	.nights li span:first-child {
		color: var(--muted);
	}
	.bar {
		display: flex;
		gap: 2px;
		height: 14px;
	}
	.bar i {
		flex-basis: 0;
		min-width: 2px;
		border-radius: 2px;
	}
	.legend {
		display: flex;
		flex-wrap: wrap;
		gap: var(--s-1) var(--s-4);
		margin: 0;
		padding: 0;
		list-style: none;
		font-size: var(--fs-small);
	}
	.legend i {
		display: inline-block;
		width: 0.7rem;
		height: 0.7rem;
		margin-right: 0.35rem;
		border-radius: 2px;
	}
	.figures {
		display: grid;
		grid-template-columns: repeat(2, minmax(0, 1fr));
		gap: var(--s-3) var(--s-4);
		margin: 0;
	}
	.figures div {
		display: grid;
		gap: 0.05rem;
	}
	dt {
		color: var(--muted);
		font-size: var(--fs-caption);
	}
	dd {
		margin: 0;
		font-size: var(--fs-title);
		font-weight: 700;
	}
	.sub {
		display: block;
		color: var(--muted);
		font-size: var(--fs-caption);
		font-weight: 400;
	}
	.table-wrap {
		overflow-x: auto;
	}
	table {
		width: 100%;
		border-collapse: collapse;
		font-size: var(--fs-small);
		white-space: nowrap;
	}
	th,
	td {
		padding: var(--s-2) var(--s-3) var(--s-2) 0;
		text-align: right;
		border-bottom: 1px solid var(--border);
	}
	th:first-child,
	td:first-child {
		text-align: left;
	}
	th:last-child,
	td:last-child {
		padding-right: 0;
	}
	th {
		color: var(--muted);
		font-weight: 400;
	}
	tr.future td {
		color: var(--muted);
	}
</style>
