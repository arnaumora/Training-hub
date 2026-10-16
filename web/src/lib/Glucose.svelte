<script>
	// Glucose section: latest reading (hero figure), window picker, chart. A reading older than
	// STALE_MIN never looks current: the headline becomes its age, the number goes muted and smaller, and after
	// GONE_MIN the number leaves the headline altogether. `children` (the Now screen's state, today and similar
	// starts) is drawn between the reading and the chart.
	import { onDestroy, onMount } from 'svelte';
	import { groupEvents } from './events.js';
	import GlucoseChart from './GlucoseChart.svelte';

	// logVersion goes up whenever the log changes, so the chart's markers follow right away.
	/** @type {{ onUnauthorized: () => void, logVersion?: number, children?: import('svelte').Snippet }} */
	let { onUnauthorized, logVersion = 0, children } = $props();
	const STALE_MIN = 15; // the same limit as "now" in the API (compare.NOW_MAX_AGE_S)
	const GONE_MIN = 180;

	const WINDOWS = [
		{ hours: 3, label: '3 h' },
		{ hours: 6, label: '6 h' },
		{ hours: 24, label: '24 h' },
		{ hours: 72, label: '3 days' }
	];
	const TREND = { 1: 'falling fast', 2: 'falling', 3: 'stable', 4: 'rising', 5: 'rising fast' };
	// The trend arrow points right for stable and turns 45 degrees per step, like the Libre app.
	const ARROW_DEG = { 1: 90, 2: 45, 3: 0, 4: -45, 5: -90 };
	const REFRESH_MS = 60_000;

	let hours = $state(6); // the default window (24 h until 2026-10-16)
	/** @type {any} */
	let data = $state(null);
	/** @type {{ t: number, carbs: any, dose: any }[]} */
	let events = $state([]);
	let error = $state('');
	/** @type {ReturnType<typeof setInterval> | undefined} */
	let timer;

	async function load() {
		try {
			const [r, l] = await Promise.all([fetch(`/api/glucose?hours=${hours}`), fetch(`/api/log?hours=${hours}`)]);
			if (r.status === 401 || l.status === 401) return onUnauthorized();
			if (!r.ok || !l.ok) throw new Error(`${r.status} ${l.status}`);
			const [g, log] = await Promise.all([r.json(), l.json()]);
			data = g;
			events = groupEvents(log);
			error = '';
		} catch {
			error = 'Could not load glucose data. Retrying every minute.';
		}
	}

	/** @param {number} h */
	function pick(h) {
		hours = h; // the $effect below reloads, since load() reads `hours`
	}

	const latest = $derived(data?.latest ?? null);
	const ageMin = $derived(latest ? Math.max(0, Math.round((data.to - latest.t) / 60)) : null);
	/** "12 min", "3 h 05 min", "43 h". @param {number} m */
	const span = (m) => (m < 60 ? `${m} min` : m < 600 ? `${Math.floor(m / 60)} h ${String(m % 60).padStart(2, '0')} min` : `${Math.round(m / 60)} h`);
	const ageText = $derived(ageMin === null ? '' : ageMin === 0 ? 'just now' : `${span(ageMin)} ago`);
	/** @type {'fresh' | 'stale' | 'gone'} */
	const freshness = $derived(ageMin === null || ageMin <= STALE_MIN ? 'fresh' : ageMin <= GONE_MIN ? 'stale' : 'gone');
	const readAt = $derived(
		latest
			? new Date(latest.t * 1000).toLocaleString('en-GB', { weekday: 'short', day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit', hour12: false })
			: ''
	);
	// Above this a high is level 2 (time-in-range consensus, Battelino et al. 2019; app.run_metrics.HIGH_MG_DL).
	const VERY_HIGH = 250;
	/** @type {'low' | 'high' | 'veryhigh' | 'in'} */
	// Below and above the target range from Settings > Training (served with the readings).
	const range = $derived(
		!latest
			? 'in'
			: latest.mg_dl < (data.target?.low ?? 70)
				? 'low'
				: latest.mg_dl > VERY_HIGH
					? 'veryhigh'
					: latest.mg_dl > (data.target?.high ?? 180)
						? 'high'
						: 'in'
	);

	// Runs on mount and again whenever logVersion or the window (`hours`, read inside load) changes.
	$effect(() => {
		logVersion;
		load();
	});

	onMount(() => {
		timer = setInterval(() => document.visibilityState === 'visible' && load(), REFRESH_MS);
	});
	onDestroy(() => clearInterval(timer));
</script>

<section aria-label="Glucose">
	{#if error}
		<p class="error" role="alert">{error}</p>
	{/if}

	{#if data && !latest}
		<p class="muted">No readings yet. The collector stores them once LibreLink uploads.</p>
	{:else if data}
		{#if freshness === 'fresh'}
			<div class="reading">
				<span class="value {range}">{latest.mg_dl}</span>
				{#if latest.live && ARROW_DEG[latest.trend] !== undefined}
					<svg class="arrow {range}" viewBox="0 0 24 24" aria-hidden="true" style="transform: rotate({ARROW_DEG[latest.trend]}deg)">
						<path d="M3 12h15M12 5l7 7-7 7" fill="none" stroke="currentColor" stroke-width="2.6" stroke-linecap="square" />
					</svg>
				{/if}
				<span class="unit">mg/dL</span>
			</div>
			<p class="meta">
				{ageText}{latest.live && TREND[latest.trend] ? `, ${TREND[latest.trend]}` : ''}{latest.live ? '' : ', from 15-min history'}
			</p>
		{:else}
			<p class="stale-head">No reading for {span(ageMin ?? 0)}</p>
			{#if freshness === 'stale'}
				<div class="reading old">
					<span class="value">{latest.mg_dl}</span>
					<span class="unit">mg/dL then</span>
				</div>
			{:else}
				<p class="meta">Last {latest.mg_dl} mg/dL, {readAt}.</p>
			{/if}
			<p class="meta">Open LibreLink on the phone, or scan the sensor.</p>
		{/if}

		{#if children}{@render children()}{/if}

		<div class="sec chart">
		<div class="controls">
			<div class="seg" role="group" aria-label="Time window">
				{#each WINDOWS as w (w.hours)}
					<button aria-pressed={w.hours === hours} onclick={() => pick(w.hours)}>
						{w.label}
					</button>
				{/each}
			</div>
		</div>

		<GlucoseChart {data} {events} />
		</div>
	{:else if !error}
		<p class="muted">Loading...</p>
	{/if}
</section>

<style>
	section {
		display: grid;
	}
	.muted {
		margin: 0;
		color: var(--muted);
	}
	.error {
		margin: 0 0 0.75rem;
		color: var(--danger);
	}

	/* The reading: the one big thing on the page, in its own three colours (not the accent): green in range,
	   ochre above it, red below the low or above 250. The colour is the range; no word repeats it. */
	.reading {
		display: flex;
		align-items: center;
		gap: 0.5rem;
	}
	.value {
		font-size: var(--fs-hero);
		font-weight: 700;
		line-height: 1;
		letter-spacing: -0.02em;
	}
	.in {
		color: var(--glucose-good);
	}
	.high {
		color: var(--glucose-high);
	}
	.low,
	.veryhigh {
		color: var(--glucose-bad);
	}
	.arrow {
		width: 40px;
		height: 40px;
	}
	.unit {
		align-self: flex-end;
		padding-bottom: 0.5rem;
		color: var(--muted);
	}
	.meta {
		margin: 0.35rem 0 0;
		color: var(--muted);
	}
	/* A stale reading: the age is the headline, the number muted and smaller. */
	.stale-head {
		margin: 0;
		font-size: var(--fs-heading);
		font-weight: 700;
	}
	.reading.old {
		margin-top: var(--s-2);
		align-items: baseline;
	}
	.reading.old .value {
		font-size: var(--fs-display);
		color: var(--muted);
	}
	.reading.old .unit {
		padding-bottom: 0;
	}

	/* The chart and its window: one section (a panel), under the reading and whatever Now puts between. */
	.chart {
		margin-top: var(--s-4);
	}
	.controls {
		display: flex;
		flex-wrap: wrap;
		align-items: baseline;
		justify-content: space-between;
		gap: 0.25rem 1rem;
		margin: 0;
	}

	@media (prefers-reduced-motion: no-preference) {
		.arrow {
			transition: transform 0.3s;
		}
	}
</style>
