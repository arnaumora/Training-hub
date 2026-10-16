<script>
	// One activity (rework step 4, 2026-10-15), in reading order: what it was; the safety flags, first and
	// plain; the glucose figures with their coverage; the curves at full width (RunGraph); the earlier runs that
	// started like this one; time in zone and the context before it (RunNumbers); the AI analysis (AiReport);
	// its laps; and what was logged around it.
	import { duration, km, pace, sportName, when } from './activityformat.js';
	import { api, Unauthorized } from './api.js';
	import { describe, groupEvents } from './events.js';
	import RunGraph from './RunGraph.svelte';
	import RunNumbers from './RunNumbers.svelte';
	import AiReport from './AiReport.svelte';
	import SimilarStarts from './SimilarStarts.svelte';

	/** @type {{ id: number, backLabel?: string, onBack: () => void, onOpen: (id: number) => void, onUnauthorized: () => void }} */
	let { id, backLabel = 'Back', onBack, onOpen, onUnauthorized } = $props();

	const SIMILAR_K = 3;
	const FOLD = 5; // longer laps and log lists show their first rows and "Show all"

	/** @type {any} */
	let data = $state(null);
	let error = $state('');
	/** @type {any} */
	let similar = $state(null);
	let allLaps = $state(false);
	let allLogged = $state(false);

	/** Load (or, after "add pre-run carbs", reload in place: the page keeps showing the old data meanwhile). */
	function load(/** @type {number} */ which) {
		api(`/api/activities/${which}/graph`)
			.then((d) => {
				if (which !== id) return;
				data = d;
				loadSimilar(d);
			})
			.catch((e) => {
				if (e instanceof Unauthorized) return onUnauthorized();
				error = 'Could not load this activity. Try again in a moment.';
			});
	}

	/** Earlier runs of the same sport that started like this one: the same search the AI report gets. @param {any} d */
	function loadSimilar(d) {
		const c = d.metrics?.context;
		if (!c || c.glucose_start === null || c.glucose_start === undefined) return (similar = null);
		const q = new URLSearchParams({ sport: d.sport, k: String(SIMILAR_K), before: String(d.start), glucose: String(c.glucose_start) });
		if (c.trend_start) q.set('trend', String(c.trend_start));
		if (c.log_covered) {
			if (c.iob_at_start !== null) q.set('iob', String(c.iob_at_start));
			q.set('since_meal', String(c.min_since_last_meal ?? 300));
		}
		api(`/api/compare/similar?${q}`)
			.then((s) => {
				if (d.id === id) similar = s;
			})
			.catch((e) => {
				if (e instanceof Unauthorized) onUnauthorized();
			});
	}

	$effect(() => {
		data = null;
		similar = null;
		allLaps = false;
		allLogged = false;
		error = '';
		load(id);
	});

	const clock = (/** @type {number} */ t) =>
		new Date(t * 1000).toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit', hour12: false });

	/** "40 min before" / "during" / "1 h 05 min after", for the log entries. @param {number} t */
	function rel(t) {
		const m = Math.round((t - data.start) / 60);
		const end = Math.round((t - data.end) / 60);
		if (t < data.start) return `${Math.abs(m)} min before`;
		if (t > data.end) return `${end} min after`;
		return 'during';
	}

	const figures = $derived(
		data
			? [
					km(data.distance_m),
					duration(data.moving_s ?? data.elapsed_s),
					pace(data),
					data.avg_hr ? `${data.avg_hr} bpm avg` : '',
					data.ascent_m ? `+${Math.round(data.ascent_m)} m` : '',
					data.calories ? `${data.calories} kcal` : ''
				].filter(Boolean)
			: []
	);
	/** Decided by code (app.run_metrics.flags), never by the AI. Safety ones lead the page. */
	const safety = $derived((data?.flags ?? []).filter((/** @type {any} */ f) => f.kind === 'safety'));
	const noun = $derived(
		data ? ({ running: 'run', cycling: 'ride', swimming: 'swim', walking: 'walk', hiking: 'hike' }[/** @type {string} */ (data.sport)] ?? 'activity') : 'activity'
	);
	const cover = $derived(data?.metrics?.glucose);
	const logged = $derived(data ? groupEvents(data.log).sort((a, b) => a.t - b.t) : []);
</script>

<section class="run" aria-labelledby="run-title">
	<button class="link back" onclick={onBack}>{backLabel}</button>
	{#if error}
		<p class="error" role="alert">{error}</p>
	{:else if !data}
		<p class="muted">Loading...</p>
	{:else}
		<header class="text">
			<h2 id="run-title">{sportName(data)}</h2>
			<p class="when">{when(data.start)}</p>
			<p class="figures">
				{#each figures as f}<span>{f}</span>{/each}
			</p>
		</header>

		{#if safety.length}
			<div class="flags text" role="note" aria-label="Safety">
				{#each safety as f}
					<p class={f.code === 'went_low' ? 'flag-low' : 'flag-safety'}>{f.text}</p>
				{/each}
			</div>
		{/if}

		<!-- Sections (.sec, app.css): Glucose at the full content width, the rest in two columns on a wide screen. -->
		<section class="sec glucose" aria-labelledby="glucose-title">
			<div class="g-head">
				<h3 id="glucose-title">Glucose</h3>
				{#if cover && cover.readings > 0}
					<span class="badge" class:thin={cover.low_coverage} title={cover.low_coverage
							? 'Mostly 15-min history: live readings only arrive while LibreLink on the phone is connected to the sensor.'
							: "Share of the activity's minutes with a reading"}>{cover.coverage_pct} % coverage</span>
				{/if}
			</div>
			{#key data}
				<RunGraph {data} />
			{/key}
			<RunNumbers {data} part="glucose" />
		</section>

		<div class="cols">
			{#if similar}
				<section class="sec" aria-labelledby="similar-title">
					<h3 id="similar-title">Earlier {noun}s that started like this</h3>
					<SimilarStarts {similar} {onOpen} {noun} />
				</section>
			{/if}
			{#key data.id}
				<RunNumbers {data} part="details" onChanged={() => load(data.id)} {onUnauthorized} />
			{/key}
		</div>

		{#key data.id}
			<AiReport path={`/api/activities/${data.id}/report`} {onUnauthorized} />
		{/key}

		<div class="cols">
			{#if data.laps.length > 1}
				<section class="sec" aria-labelledby="laps-title">
					<h3 id="laps-title">Laps</h3>
					<table>
						<thead>
							<tr><th scope="col">Lap</th><th scope="col">Distance</th><th scope="col">Time</th><th scope="col">{data.sport === 'cycling' ? 'Speed' : 'Pace'}</th><th scope="col">HR</th></tr>
						</thead>
						<tbody>
							{#each allLaps ? data.laps : data.laps.slice(0, FOLD) as lap}
								<tr>
									<td>{lap.idx + 1}</td>
									<td>{km(lap.distance_m)}</td>
									<td>{duration(lap.moving_s ?? lap.elapsed_s)}</td>
									<td>{pace({ sport: data.sport, distance_m: lap.distance_m, moving_s: lap.moving_s, elapsed_s: lap.elapsed_s })}</td>
									<td>{lap.avg_hr ?? ''}</td>
								</tr>
							{/each}
						</tbody>
					</table>
					{#if !allLaps && data.laps.length > FOLD + 1}
						<button class="link more" onclick={() => (allLaps = true)}>Show all {data.laps.length} laps</button>
					{/if}
				</section>
			{/if}

			<section class="sec" aria-labelledby="logged-title">
				<h3 id="logged-title">Logged around it</h3>
				{#if logged.length}
					<ul class="logged">
						{#each allLogged ? logged : logged.slice(0, FOLD) as e (e.key)}
							<li><span class="t">{clock(e.t)}</span><span>{describe(e)}</span><span class="rel">{rel(e.t)}</span></li>
						{/each}
					</ul>
					{#if !allLogged && logged.length > FOLD + 1}
						<button class="link more" onclick={() => (allLogged = true)}>Show all {logged.length}</button>
					{/if}
				{:else}
					<p class="muted">Nothing logged.</p>
				{/if}
			</section>
		</div>
	{/if}
</section>

<style>
	/* The page: the activity, the safety flags, then its sections (.sec in app.css), spaced evenly. */
	.run {
		display: grid;
		gap: var(--s-4);
	}
	.text {
		max-width: 44rem;
	}
	.back {
		justify-self: start;
		color: var(--muted);
	}
	.back::before {
		content: '< ';
	}
	h2 {
		margin: 0;
		font-size: var(--fs-heading);
		font-weight: 700;
		line-height: 1.25;
	}
	.when {
		margin: var(--s-1) 0 0;
		color: var(--muted);
	}
	.figures {
		display: flex;
		flex-wrap: wrap;
		gap: 0 var(--s-4);
		margin: var(--s-2) 0 0;
		font-weight: 700;
	}
	.flags {
		display: grid;
		gap: var(--s-2);
	}
	.g-head {
		display: flex;
		align-items: baseline;
		gap: var(--s-3);
	}
	/* Under 80 %: the same quiet badge with a dashed border, like the dashed low-coverage lines in Compare. */
	.badge.thin {
		border-style: dashed;
	}
	h3 {
		margin: 0;
		font-size: var(--fs-title);
		font-weight: 700;
		line-height: 1.25;
	}
	/* Secondary sections: one column on the phone, two side by side on a wide screen. */
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
	.muted {
		margin: 0;
		color: var(--muted);
	}
	.error {
		margin: 0;
		color: var(--danger);
	}
	table {
		width: 100%;
		border-collapse: collapse;
		font-size: var(--fs-small);
	}
	th,
	td {
		padding: var(--s-2) var(--s-2) var(--s-2) 0;
		text-align: right;
		border-bottom: 1px solid var(--border);
	}
	th:first-child,
	td:first-child {
		text-align: left;
	}
	th {
		color: var(--muted);
		font-weight: 400;
	}
	.logged {
		list-style: none;
		margin: 0;
		padding: 0;
	}
	.logged li {
		display: grid;
		grid-template-columns: 3.4rem minmax(0, 1fr) auto;
		gap: var(--s-3);
		padding: var(--s-2) 0;
		border-bottom: 1px solid var(--border);
		font-size: var(--fs-small);
	}
	.more {
		justify-self: start;
	}
	.t,
	.rel {
		color: var(--muted);
	}
</style>
