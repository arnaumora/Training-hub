<script>
	// The most similar past starts (GET /api/compare/similar) as a short list: a one-line summary, then one row per
	// run (date, start glucose, change, lowest), each opening its run page. Used on Now, on the run page and,
	// with `details` (a muted second line: how it started and what to know about it) and without the summary
	// line, in Analysis > Similar. History, never a prediction or a dose.
	import { sportName } from './activityformat.js';
	import { spanText } from './entry.js';

	/** similar: the /api/compare/similar answer, or null while loading.
	 * @type {{ similar: any, onOpen: (id: number) => void, noun?: string, summary?: boolean, details?: boolean, sport?: boolean }} */
	let { similar, onOpen, noun = 'run', summary = true, details = false, sport = false } = $props();

	const TREND = { 1: 'falling fast', 2: 'falling', 3: 'stable', 4: 'rising', 5: 'rising fast' };
	/** The second line: sport (when mixed), trend, IOB, last carbs, carbs just before, time low, coverage. @param {any} r */
	const detail = (r) =>
		[
			sport ? sportName(r) : '',
			r.trend_start ? TREND[/** @type {keyof typeof TREND} */ (r.trend_start)] : '',
			r.iob_at_start !== null ? `${r.iob_at_start} u on board` : '',
			r.min_since_last_meal !== null ? `carbs ${spanText(r.min_since_last_meal)} before` : r.log_covered ? 'no carbs in the 24 h before' : '',
			r.pre_run_carbs ? `${r.pre_run_carbs} g from 1 h before` : '',
			r.min_below_low ? `${r.min_below_low} min below ${low}` : '',
			r.low_coverage ? 'under 80 % coverage' : ''
		]
			.filter(Boolean)
			.join(', ');

	const signed = (/** @type {number} */ v) => (v > 0 ? `+${v}` : `${v}`);
	/** "Tue 13 Oct". @param {number} t */
	const day = (t) => new Date(t * 1000).toLocaleDateString('en-GB', { weekday: 'short', day: 'numeric', month: 'short' });
	const low = $derived(similar?.low ?? 70);
	const sum = $derived(similar?.summary);
</script>

{#if !similar}
	<p class="muted">Loading...</p>
{:else if !sum?.n}
	<p class="muted">No earlier {noun} with glucose at its start yet.</p>
{:else}
	{#if summary}
	<p class="sum">
		{sum.n === 1 ? `1 ${noun}` : `${sum.n} ${noun}s`}: median change {sum.median_delta === null ? '-' : `${signed(sum.median_delta)} mg/dL`},
		lowest <span class:low-text={sum.lowest !== null && sum.lowest < low}>{sum.lowest ?? '-'}</span>{sum.went_low
			? `, ${sum.went_low} went below ${low}`
			: ''}.
	</p>
	{/if}
	<ul class="runs" class:details>
		{#each similar.runs as r (r.id)}
			<li>
				<button onclick={() => onOpen(r.id)}>
					<span class="when">{day(r.start)}</span>
					<span>from {r.glucose_start}</span>
					<span class="num">{r.delta === null ? '-' : signed(r.delta)}</span>
					<span class="num" class:low-text={r.nadir !== null && r.nadir < low}>low {r.nadir ?? '-'}</span>
					{#if details}<span class="detail">{detail(r)}</span>{/if}
				</button>
			</li>
		{/each}
	</ul>
{/if}

<style>
	.muted,
	.sum {
		margin: 0;
	}
	.muted {
		color: var(--muted);
	}
	.low-text {
		color: var(--danger);
	}
	.runs {
		list-style: none;
		margin: 0;
		padding: 0;
	}
	.runs li {
		border-top: 1px solid var(--border);
	}
	.runs button {
		display: grid;
		grid-template-columns: minmax(0, 1.2fr) minmax(0, 1fr) 3.2rem 4.6rem;
		align-items: center;
		gap: var(--s-2);
		width: 100%;
		min-height: var(--tap);
		padding: 0;
		background: none;
		border: none;
		color: var(--text);
		font-size: var(--fs-small);
		text-align: left;
		cursor: pointer;
	}
	.runs.details button {
		padding: var(--s-2) 0;
		row-gap: var(--s-1);
	}
	.detail {
		grid-column: 1 / -1;
		color: var(--muted);
		font-size: var(--fs-caption);
	}
	.runs .when {
		color: var(--accent);
	}
	.num {
		text-align: right;
	}
</style>
