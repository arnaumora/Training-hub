<script module>
	// Kept while the app is open, so coming back from a run shows the same search.
	const saved = { sport: 'running', glucose: '', trend: '', iob: '', meal: '' };
</script>

<script>
	// Similar runs (roadmap step 7, change 4): give a starting state (glucose, trend, IOB, time since the last
	// carbs), or take it from now, and see the past runs that started closest to it and what happened in them.
	// History only: no prediction and nothing about doses. GET /api/compare/similar and /api/compare/now.
	import { onMount } from 'svelte';
	import { api, Unauthorized } from './api.js';
	import { parseSpan, spanText } from './entry.js';
	import SimilarStarts from './SimilarStarts.svelte';

	/** @type {{ onOpen: (id: number) => void, onUnauthorized: () => void }} */
	let { onOpen, onUnauthorized } = $props();

	const TRENDS = [
		['', 'Any'],
		['1', 'Falling fast'],
		['2', 'Falling'],
		['3', 'Stable'],
		['4', 'Rising'],
		['5', 'Rising fast']
	];
	const LABEL = { glucose_start: 'glucose', trend_start: 'trend', iob_at_start: 'IOB', min_since_last_meal: 'time since the last carbs' };

	let sport = $state(saved.sport);
	let glucose = $state(saved.glucose);
	let trend = $state(saved.trend);
	let iob = $state(saved.iob);
	let meal = $state(saved.meal);
	$effect(() => {
		Object.assign(saved, { sport, glucose, trend, iob, meal });
	});

	/** @type {any} */
	let result = $state(null);
	let error = $state('');
	let nowNote = $state('');
	let loadingNow = $state(false);

	const signed = (/** @type {number} */ v) => (v > 0 ? `+${v}` : `${v}`);
	/** "35 min" or "2 h 05 min". @param {number} m */
	const ago = (m) => (m < 60 ? `${m} min` : `${Math.floor(m / 60)} h ${String(m % 60).padStart(2, '0')} min`);
	const num = (/** @type {string} */ s) => (s.trim() === '' ? null : Number(s.replace(',', '.')));

	/** The query, or a reason it cannot be asked yet. */
	const query = $derived.by(() => {
		const g = num(glucose);
		const i = num(iob);
		const m = meal.trim() === '' ? null : parseSpan(meal);
		if (g !== null && !(g >= 40 && g <= 500)) return { error: 'Glucose between 40 and 500 mg/dL.' };
		if (i !== null && !(i >= 0 && i <= 50)) return { error: 'IOB between 0 and 50 u.' };
		if (meal.trim() !== '' && (m === null || m > 10080)) return { error: 'Time since the last carbs as 45 min or 2 h 30, up to 7 days.' };
		if (g === null && i === null && m === null && !trend) return { error: '' };
		const q = new URLSearchParams();
		if (sport !== 'all') q.set('sport', sport);
		if (g !== null) q.set('glucose', String(Math.round(g)));
		if (trend) q.set('trend', trend);
		if (i !== null) q.set('iob', String(i));
		if (m !== null) q.set('since_meal', String(Math.round(m)));
		return { q: q.toString() };
	});

	// Search as you type, a moment after the last change.
	$effect(() => {
		const { q } = query;
		if (!q) {
			result = null;
			return;
		}
		const timer = setTimeout(() => {
			api(`/api/compare/similar?${q}`)
				.then((r) => {
					result = r;
					error = '';
				})
				.catch((e) => {
					if (e instanceof Unauthorized) return onUnauthorized();
					error = 'Could not search. Try again in a moment.';
				});
		}, 250);
		return () => clearTimeout(timer);
	});

	/** Fill the form from now: the latest reading, IOB and the last carbs. Says what it could not fill. */
	async function useNow() {
		loadingNow = true;
		try {
			const n = await api('/api/compare/now');
			const missing = [];
			if (n.glucose !== null) {
				glucose = String(n.glucose);
				trend = n.trend ? String(n.trend) : '';
			} else {
				glucose = '';
				trend = '';
				missing.push(n.glucose_age_min === null ? 'no glucose reading' : `the last reading is ${ago(n.glucose_age_min)} old`);
			}
			if (!n.log_covered) {
				iob = '';
				meal = '';
				missing.push('the log has nothing yet, so IOB and the last carbs are unknown');
			} else {
				if (n.iob === null) missing.push('no IOB without Settings > Insulin');
				iob = n.iob === null ? '' : String(n.iob);
				meal = spanText(n.min_since_last_meal ?? 300);
				if (n.min_since_last_meal === null) missing.push('no carbs in the last 24 h (searched as 5 h or more)');
			}
			nowNote = missing.length ? `Filled from now; ${missing.join('; ')}.` : 'Filled from now.';
		} catch (e) {
			if (e instanceof Unauthorized) return onUnauthorized();
			nowNote = 'Could not read the current state.';
		} finally {
			loadingNow = false;
		}
	}

	// Opened with nothing filled in: start from now (the question is usually "what about right now?").
	onMount(() => {
		if (!glucose && !trend && !iob && !meal) useNow();
	});

	const low = $derived(result?.low ?? 70);
	const s = $derived(result?.summary);
</script>

<section class="similar" aria-labelledby="similar-title">
	<h2 id="similar-title" class="visually-hidden">Similar runs</h2>

	<form class="state sec" onsubmit={(e) => e.preventDefault()} aria-labelledby="start-title">
		<h3 id="start-title">Starting like this</h3>
		<label class="row"><span>Sport</span>
			<select bind:value={sport}>
				<option value="running">Run</option>
				<option value="cycling">Ride</option>
				<option value="swimming">Swim</option>
				<option value="all">Any sport</option>
			</select>
		</label>
		<label class="row"><span>Glucose</span><span class="field"><input bind:value={glucose} inputmode="numeric" placeholder="-" /><em>mg/dL</em></span></label>
		<label class="row"><span>Trend</span>
			<select bind:value={trend}>
				{#each TRENDS as t}<option value={t[0]}>{t[1]}</option>{/each}
			</select>
		</label>
		<label class="row"><span>Insulin on board</span><span class="field"><input bind:value={iob} inputmode="decimal" placeholder="-" /><em>u</em></span></label>
		<label class="row"><span>Since the last carbs</span><span class="field"><input bind:value={meal} placeholder="2 h 30" /><em></em></span></label>
		<div class="actions">
			<button type="button" class="link" onclick={useNow} disabled={loadingNow}>{loadingNow ? 'Reading...' : 'Use now'}</button>
			<button type="button" class="link muted" onclick={() => { glucose = ''; trend = ''; iob = ''; meal = ''; nowNote = ''; }}>Clear</button>
		</div>
		{#if nowNote}<p class="note">{nowNote}</p>{/if}
		{#if query.error}<p class="error">{query.error}</p>{/if}
	</form>

	<section class="sec" aria-labelledby="closest-title">
	{#if error}
		<p class="error" role="alert">{error}</p>
	{:else if !query.q}
		<h3 id="closest-title">Closest runs</h3>
		<p class="muted">Fill in at least one value.</p>
	{:else if result}
		<h3 id="closest-title">Closest {s.n === 1 ? 'run' : `${s.n} runs`}</h3>
		<p class="n">
			On {result.used.map((/** @type {string} */ k) => LABEL[/** @type {keyof typeof LABEL} */ (k)]).join(', ')}, of {result.candidates}{result.skipped
				? `; ${result.skipped} left out, missing one of these`
				: ''}.
		</p>
		{#if !s.n}
			<p class="muted">No past run with glucose at its start yet.</p>
		{:else}
			<dl class="summary">
				<div><dt>Median change</dt><dd>{s.median_delta === null ? '-' : `${signed(s.median_delta)} mg/dL`}</dd></div>
				<div><dt>Lowest reading</dt><dd class:low={s.lowest !== null && s.lowest < low}>{s.lowest ?? '-'}</dd></div>
				<div><dt>Went below {low}</dt><dd class:low={s.went_low > 0}>{s.went_low} of {s.n}</dd></div>
			</dl>
			<SimilarStarts similar={result} {onOpen} summary={false} details sport={sport === 'all'} />
		{/if}
	{:else}
		<h3 id="closest-title">Closest runs</h3>
		<p class="muted">Loading...</p>
	{/if}
	</section>
</section>

<style>
	/* The starting state and the closest runs: one column on the phone, side by side on a wide screen. */
	.similar {
		display: grid;
		gap: var(--s-4);
		align-items: start;
	}
	@media (min-width: 900px) {
		.similar {
			grid-template-columns: minmax(0, 2fr) minmax(0, 3fr);
		}
	}
	.state {
		gap: var(--s-1);
	}
	.row {
		display: grid;
		grid-template-columns: minmax(0, 1fr) 9.5rem;
		align-items: baseline;
		gap: 1rem;
		padding: 0.2rem 0;
		color: var(--muted);
	}
	.field {
		display: flex;
		align-items: baseline;
		gap: 0.4rem;
	}
	.field em {
		font-style: normal;
		color: var(--muted);
		font-size: var(--fs-caption);
		min-width: 2.6rem;
	}
	input,
	select {
		width: 100%;
		min-width: 0;
		padding: 0.3rem 0;
		background: none;
		border: none;
		border-bottom: 1.5px solid var(--border);
		border-radius: 0;
		color: var(--text);
		font: inherit;
		font-size: max(16px, 1rem);
		font-variant-numeric: tabular-nums;
	}
	input:focus,
	select:focus {
		outline: none;
		border-bottom-color: var(--accent);
	}
	select option {
		background: var(--surface);
		color: var(--text);
	}
	.actions {
		display: flex;
		gap: 1.25rem;
		margin-top: 0.5rem;
	}
	.link.muted {
		color: var(--muted);
	}
	.note,
	.n {
		margin: 0.25rem 0 0;
		color: var(--muted);
		font-size: var(--fs-caption);
	}
	.error {
		margin: 0.25rem 0 0;
		color: var(--danger);
		font-size: var(--fs-small);
	}
	.muted {
		margin: 0;
		color: var(--muted);
	}
	h3 {
		margin: 0;
		font-size: var(--fs-title);
		font-weight: 700;
		line-height: 1.25;
	}
	.summary {
		display: grid;
		grid-template-columns: repeat(3, minmax(0, 1fr));
		gap: 1rem;
		margin: 0.5rem 0;
	}
	.summary div {
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
		font-variant-numeric: tabular-nums;
	}
	.low {
		color: var(--danger);
	}
</style>
