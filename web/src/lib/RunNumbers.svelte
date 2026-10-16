<script>
	// The per-run numbers (app/derive.py) on the run page:
	//   part 'glucose'  the glucose figures under the graph, and what the log and the readings cannot tell
	//   part 'details'  time in zone and training figures, the context before the run, "add pre-run carbs",
	//                   and the note
	// Unknown values say why ("before the log started", "no reading near the end") instead of showing 0.
	import { untrack } from 'svelte';
	import { api, Unauthorized } from './api.js';
	import { duration } from './activityformat.js';

	/** @type {{ data: any, part: 'glucose' | 'details', onChanged?: () => void, onUnauthorized?: () => void }} */
	let { data, part, onChanged = () => {}, onUnauthorized = () => {} } = $props();

	const TREND = { 1: 'falling fast', 2: 'falling', 3: 'stable', 4: 'rising', 5: 'rising fast' };
	const m = $derived(data.metrics ?? {});
	const g = $derived(m.glucose);
	const c = $derived(m.context);
	const t = $derived(m.training);
	const low = $derived(data.settings?.glucose_low ?? 70);
	/** "run", "ride", "swim"...: the context panel names the activity it is about. */
	const noun = $derived({ running: 'run', cycling: 'ride', swimming: 'swim', walking: 'walk', hiking: 'hike' }[/** @type {string} */ (data.sport)] ?? 'activity');

	const signed = (/** @type {number} */ v) => (v > 0 ? `+${v}` : `${v}`);
	const mmss = (/** @type {number} */ s) => `${Math.floor(s / 60)}:${String(Math.round(s % 60)).padStart(2, '0')}`;
	/** "35 min" or "2 h 05 min". @param {number} min */
	const ago = (min) => (min < 60 ? `${min} min` : `${Math.floor(min / 60)} h ${String(min % 60).padStart(2, '0')} min`);

	// --- time in zone: one stacked bar, a legend with minutes per zone -------------------------------
	const zones = $derived(t?.time_in_zone ?? null);
	const zoneTotal = $derived(zones ? zones.reduce((/** @type {number} */ a, /** @type {number} */ b) => a + b, 0) : 0);

	// --- add pre-run carbs ---------------------------------------------------------------------------
	let adding = $state(false);
	let gramsText = $state('');
	let beforeText = $state('15');
	let addError = $state('');
	let busy = $state(false);

	async function addCarbs() {
		const grams = Number(gramsText.replace(',', '.'));
		const before = Number(beforeText);
		if (!(grams > 0 && grams <= 300) || !(before >= 0 && before <= 60)) {
			addError = 'Grams between 1 and 300, minutes before the start between 0 and 60.';
			return;
		}
		busy = true;
		addError = '';
		try {
			const ts = new Date((data.start - before * 60) * 1000).toISOString();
			await api('/api/entries', { method: 'POST', body: { ts, carbs: { grams, description: 'pre-run, added from the run page' } } });
			adding = false;
			gramsText = '';
			onChanged();
		} catch (e) {
			if (e instanceof Unauthorized) return onUnauthorized();
			addError = 'Not saved. Try again in a moment.';
		} finally {
			busy = false;
		}
	}

	// --- note ----------------------------------------------------------------------------------------
	// The saved note, read once: Run.svelte remounts this part when another run opens, and a reload after
	// "add pre-run carbs" must not throw away a note being typed.
	let noteText = $state(untrack(() => data.metrics?.note ?? ''));
	let noteState = $state('');

	async function saveNote() {
		noteState = 'Saving...';
		try {
			await api(`/api/activities/${data.id}/note`, { method: 'PUT', body: { note: noteText } });
			noteState = 'Saved.';
		} catch (e) {
			if (e instanceof Unauthorized) return onUnauthorized();
			noteState = 'Not saved. Try again in a moment.';
		}
	}
</script>

{#if part === 'glucose'}
	{#if g && g.readings > 0}
		<dl class="figures">
			<div><dt>Start</dt><dd>{g.glucose_start ?? '-'}{#if c?.trend_start}<span class="sub">{TREND[c.trend_start]}</span>{/if}</dd></div>
			<div><dt>End</dt><dd>{g.glucose_end ?? '-'}</dd></div>
			<div><dt>Change</dt><dd>{g.delta === null ? '-' : signed(g.delta)}</dd></div>
			<div><dt>Per 10 min</dt><dd>{g.rate_per_10min === null ? '-' : signed(g.rate_per_10min)}</dd></div>
			<div>
				<dt>Lowest</dt>
				<dd class:low={g.nadir !== null && g.nadir < low}>{g.nadir ?? '-'}{#if g.nadir_minute !== null}<span class="sub">at {g.nadir_minute} min</span>{/if}</dd>
			</div>
			<div><dt>Below {low}</dt><dd class:low={g.min_below_low > 0}>{g.min_below_low} min</dd></div>
			<div><dt>IOB at start</dt><dd>{c?.iob_at_start === null || c?.iob_at_start === undefined ? '-' : `${c.iob_at_start} u`}</dd></div>
			<div><dt>Since last meal</dt><dd>{c?.min_since_last_meal == null ? '-' : ago(c.min_since_last_meal)}</dd></div>
		</dl>
	{:else}
		<p class="flag-quality">No glucose readings around this activity.</p>
	{/if}
	<!-- What the log and the readings can and cannot tell (app.run_metrics.flags, kinds "log" and "data");
	     the safety flags lead the page (Run.svelte). -->
	{#each (data.flags ?? []).filter((/** @type {any} */ f) => f.kind !== 'safety' && f.code !== 'coverage') as f}
		<p class="flag-quality">{f.text}</p>
	{/each}
{:else}
	{#if (zones && zoneTotal > 0) || (t && (t.hr_drift_pct !== null || t.grade_adjusted_pace_s !== null))}
	<section class="sec" aria-label="Effort">
		<h3>Effort</h3>
		{#if zones && zoneTotal > 0}
			<div class="bar" role="img" aria-label="Time in zones 1 to 5: {zones.map((/** @type {number} */ s) => Math.round(s / 60)).join(', ')} minutes">
				{#each zones as s, i}
					{#if s > 0}<i style="flex-grow: {s}; background: var(--zone-{i + 1})"></i>{/if}
				{/each}
			</div>
			<ul class="legend">
				{#each zones as s, i}
					<li><i style="background: var(--zone-{i + 1})"></i>Zone {i + 1} <strong>{duration(s)}</strong> <span class="sub">{Math.round((100 * s) / zoneTotal)} %</span></li>
				{/each}
			</ul>
		{/if}
		{#if t && (t.hr_drift_pct !== null || t.grade_adjusted_pace_s !== null)}
			<dl class="figures wide">
				{#if t.hr_drift_pct !== null}
					<div><dt>HR drift</dt><dd>{signed(t.hr_drift_pct)} %</dd></div>
				{/if}
				{#if t.grade_adjusted_pace_s !== null}
					<div><dt>Grade-adjusted pace</dt><dd>{mmss(t.grade_adjusted_pace_s)} /km<span class="sub">moving pace {mmss(t.avg_pace_s)} /km</span></dd></div>
				{/if}
			</dl>
		{/if}
	</section>
	{/if}

	<section class="sec" aria-label="Before the {noun}">
		<h3>Before the {noun}</h3>
		{#if c && c.log_covered}
			<!-- Start time, glucose at start and IOB are in the header and the glucose figures; IOB only comes back
			     here to say why it is missing. -->
			<dl class="context">
				{#if c.iob_at_start === null}<dt>Insulin on board</dt><dd>unknown: no Settings &gt; Insulin saved</dd>{/if}
				<dt>Last rapid dose</dt><dd>{c.last_bolus_units === null ? 'none in the 24 h before' : `${c.last_bolus_units} u, ${ago(c.min_since_last_bolus)} before`}</dd>
				<dt>Last carbs</dt><dd>{c.last_meal_carbs === null ? 'none in the 24 h before' : `${c.last_meal_carbs} g, ${ago(c.min_since_last_meal)} before`}</dd>
				<dt>Carbs from 1 h before to the end</dt><dd>{c.pre_run_carbs} g</dd>
				<dt>Last basal</dt><dd>{c.h_since_basal === null ? 'none in the 48 h before' : `${c.h_since_basal} h before`}</dd>
			</dl>
		{:else if c}
			<p class="flag-quality">Started at {c.local_time}, before the log did: what was eaten or injected is not known.</p>
		{/if}
		{#if adding}
			<form class="add" onsubmit={(e) => { e.preventDefault(); addCarbs(); }}>
				<label><span>Grams of carbs</span><input bind:value={gramsText} inputmode="decimal" /></label>
				<label><span>Minutes before the start</span><input bind:value={beforeText} inputmode="numeric" /></label>
				<div class="actions">
					<button type="submit" class="primary" disabled={busy}>{busy ? 'Saving...' : 'Add to the log'}</button>
					<button type="button" class="link" onclick={() => (adding = false)}>Cancel</button>
				</div>
				{#if addError}<p class="error">{addError}</p>{/if}
			</form>
		{:else}
			<button class="link" onclick={() => (adding = true)}>Add pre-{noun} carbs</button>
		{/if}
	</section>

	<section class="sec" aria-label="Note">
		<h3>Note</h3>
		<textarea bind:value={noteText} rows="3" maxlength="2000" placeholder="How it felt, a gel at 30 min, what you would change" oninput={() => (noteState = '')}></textarea>
		<div class="actions">
			<button class="link" onclick={saveNote}>Save note</button>
			{#if noteState}<span class="sub">{noteState}</span>{/if}
		</div>
	</section>
{/if}

<style>
	.figures {
		display: grid;
		grid-template-columns: repeat(auto-fill, minmax(7.5rem, 1fr));
		gap: var(--s-3) var(--s-4);
		margin: 0;
	}
	/* The eight glucose figures: two rows of four on a wide screen, four rows of two on a phone. */
	.figures:not(.wide) {
		grid-template-columns: repeat(2, minmax(0, 1fr));
	}
	@media (min-width: 560px) {
		.figures:not(.wide) {
			grid-template-columns: repeat(4, minmax(0, 1fr));
		}
	}
	.figures.wide {
		grid-template-columns: repeat(auto-fill, minmax(12rem, 1fr));
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
	}
	.figures dd {
		font-size: var(--fs-title);
		font-weight: 700;
	}
	.figures dd.low {
		color: var(--danger);
	}
	.sub {
		display: block;
		color: var(--muted);
		font-size: var(--fs-caption);
		font-weight: 400;
	}
	.legend .sub,
	.actions .sub {
		display: inline;
	}
	.flag-quality + .flag-quality,
	.figures + .flag-quality {
		margin-top: var(--s-2);
	}

	h3 {
		margin: 0;
		font-size: var(--fs-title);
		font-weight: 700;
		line-height: 1.25;
	}
	/* One stacked bar: segments separated by a 2px gap in the page colour. */
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
		margin: var(--s-2) 0 0;
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
	.context {
		display: grid;
		grid-template-columns: minmax(0, 1fr) auto;
		gap: 0.35rem 1rem;
		margin: 0 0 var(--s-3);
	}
	.context dd {
		text-align: right;
	}
	.add {
		display: grid;
		gap: 0.5rem;
		max-width: 22rem;
	}
	.add label {
		display: grid;
		grid-template-columns: minmax(0, 1fr) 5rem;
		gap: 0.75rem;
		align-items: baseline;
		color: var(--muted);
	}
	input,
	textarea {
		width: 100%;
		min-width: 0;
		padding: 0.35rem 0;
		background: none;
		border: none;
		border-bottom: 1.5px solid var(--border);
		border-radius: 0;
		color: var(--text);
		font: inherit;
	}
	textarea {
		resize: vertical;
		font-size: max(16px, 1em);
	}
	input:focus,
	textarea:focus {
		outline: none;
		border-bottom-color: var(--accent);
	}
	.actions {
		display: flex;
		gap: 1rem;
		align-items: baseline;
		margin-top: 0.25rem;
	}
	.link {
		justify-self: start;
	}
	.primary {
		min-height: var(--tap);
		padding: 0 var(--s-4);
		background: var(--accent);
		color: var(--accent-text);
		border: none;
		border-radius: var(--radius);
		font-weight: 700;
		cursor: pointer;
	}
	.error {
		margin: 0;
		color: var(--danger);
		font-size: var(--fs-small);
	}
</style>
