<script>
	// Now (the 2026-10-15 rework, replacing Home): "can I train now, and how did it go last time I started like
	// this?" In order: glucose (Glucose, stale readings shown as stale), the state the calculator also uses
	// (insulin on board, time since carbs and since the last rapid dose, per the log), today's planned sessions
	// with their targets and what was done, the most similar past starts (only with a fresh reading), the
	// glucose chart, and the log.
	// GET /api/compare/now, /api/calendar (today), /api/compare/similar. History, never a prediction or a dose.
	import { onDestroy, onMount } from 'svelte';
	import { api, Unauthorized } from './api.js';
	import { duration, km, pace, sportName } from './activityformat.js';
	import Glucose from './Glucose.svelte';
	import LogList from './LogList.svelte';
	import SimilarStarts from './SimilarStarts.svelte';

	/** @type {{ log: any, logVersion: number, onEdit: (e: any) => void, onOpen: (id: number) => void, onTraining: () => void, onSimilar: () => void, onUnauthorized: () => void }} */
	let { log, logVersion, onEdit, onOpen, onTraining, onSimilar, onUnauthorized } = $props();

	const REFRESH_MS = 60_000;
	const SIMILAR_K = 3;

	/** @type {any} */
	let now = $state(null);
	/** @type {any} */
	let today = $state(null);
	/** @type {any} */
	let similar = $state(null);
	/** The log list: the last 24 h, or the last 7 days after "Earlier". @type {any} */
	let longLog = $state(null);
	let showWeek = $state(false);

	const isoToday = () => {
		const d = new Date();
		return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
	};

	async function load() {
		try {
			const day = isoToday();
			const [n, cal] = await Promise.all([api('/api/compare/now'), api(`/api/calendar?start=${day}&end=${day}`)]);
			now = n;
			today = cal;
			similar = n.glucose === null ? null : await api(`/api/compare/similar?${similarQuery(n)}`);
			if (showWeek) longLog = await api('/api/log?hours=168');
		} catch (e) {
			if (e instanceof Unauthorized) return onUnauthorized();
			// Keep what is on screen; the next refresh tries again.
		}
	}

	/** The starting state as a similar-runs query: what is known, nothing invented. @param {any} n */
	function similarQuery(n) {
		const q = new URLSearchParams({ sport: 'running', k: String(SIMILAR_K), glucose: String(n.glucose) });
		if (n.trend) q.set('trend', String(n.trend));
		if (n.log_covered) {
			if (n.iob !== null) q.set('iob', String(n.iob));
			// No carbs in the window the API looks at: searched as 5 h or more, like Analysis > Similar.
			q.set('since_meal', String(n.min_since_last_meal ?? 300));
		}
		return q.toString();
	}

	async function earlier() {
		showWeek = true;
		try {
			longLog = await api('/api/log?hours=168');
		} catch (e) {
			if (e instanceof Unauthorized) return onUnauthorized();
		}
	}

	$effect(() => {
		logVersion;
		load();
	});
	/** @type {ReturnType<typeof setInterval> | undefined} */
	let timer;
	onMount(() => {
		timer = setInterval(() => document.visibilityState === 'visible' && load(), REFRESH_MS);
	});
	onDestroy(() => clearInterval(timer));

	/** "35 min", "2 h 05 min". @param {number} m */
	// Short enough for a third of a phone's width: "35 min", "2 h 05", "17 h".
	const span = (m) => (m < 60 ? `${m} min` : m < 600 ? `${Math.floor(m / 60)} h ${String(m % 60).padStart(2, '0')}` : `${Math.floor(m / 60)} h`);
	const signed = (/** @type {number} */ v) => (v > 0 ? `+${v}` : `${v}`);
	const mmss = (/** @type {number} */ s) => `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`;

	const STATUS = { done: 'Done', partial: 'Partial', missed: 'Missed', planned: 'Planned' };
	const sessions = $derived(today ? today.days[0].sessions.filter((/** @type {any} */ s) => s.sport !== 'rest') : []);
	const restDay = $derived(today ? today.days[0].sessions.some((/** @type {any} */ s) => s.sport === 'rest') : false);
	const unplanned = $derived(today ? today.days[0].unplanned : []);
</script>

{#snippet activityLine(/** @type {any} */ a)}
	<button class="activity" onclick={() => onOpen(a.id)}>
		<span>{sportName(a)} {[km(a.distance_m), duration(a.moving_s ?? a.elapsed_s), pace(a)].filter(Boolean).join(', ')}</span>
		{#if a.glucose}
			<span class="badge g" class:low={a.glucose.went_low} class:caution={!a.glucose.went_low && a.glucose.high_start}
				>{a.glucose.start}{a.glucose.delta !== null ? ` ${signed(a.glucose.delta)}` : ''}</span
			>
		{/if}
	</button>
{/snippet}

<div class="now">
	<Glucose {onUnauthorized} {logVersion}>
		{#if now}
			<dl class="state" aria-label="Now, per the log">
				<div>
					<dt>Insulin on board</dt>
					<dd>{!now.log_covered ? '-' : now.iob === null ? 'no settings' : `${now.iob} u`}</dd>
				</div>
				<div>
					<dt>Since carbs</dt>
					<dd>{!now.log_covered ? '-' : now.min_since_last_meal === null ? 'over 24 h' : span(now.min_since_last_meal)}</dd>
				</div>
				<div>
					<dt>Since rapid dose</dt>
					<dd>{!now.log_covered ? '-' : now.min_since_last_bolus === null ? 'over 24 h' : span(now.min_since_last_bolus)}</dd>
				</div>
			</dl>
			{#if !now.log_covered}<p class="per-log">The log does not reach back far enough yet.</p>{/if}
		{/if}

		<div class="cols">
		<section class="block sec" aria-labelledby="today-title">
			<div class="head">
				<h2 id="today-title">Today</h2>
				<button class="link" onclick={onTraining}>Week</button>
			</div>
			{#if !today}
				<p class="muted">Loading...</p>
			{:else}
				{#each sessions as s (s.id)}
					<div class="session">
						<div class="title">
							<i class="mark {s.status}" aria-hidden="true"></i>
							<span class="name">{s.title}</span>
							<span class="what">{STATUS[/** @type {keyof typeof STATUS} */ (s.status)]}{s.status === 'planned' && s.time ? `, ${s.time}` : ''}</span>
						</div>
						{#if s.target}<p class="sub">Target {s.target.name.toLowerCase()} {mmss(s.target.from_s)}-{mmss(s.target.to_s)} /km</p>{/if}
						{#if s.activity_id}{@render activityLine(today.activities[s.activity_id])}{/if}
					</div>
				{/each}
				{#each unplanned as id (id)}
					<div class="session">
						<div class="title"><i class="mark unplanned" aria-hidden="true"></i><span class="name">Unplanned</span></div>
						{@render activityLine(today.activities[id])}
					</div>
				{/each}
				{#if !sessions.length && !unplanned.length}
					<p class="muted">{restDay ? 'Rest day.' : 'Nothing planned today.'}</p>
				{/if}
			{/if}
		</section>

		<section class="block sec" aria-labelledby="similar-title">
			<div class="head">
				<h2 id="similar-title">Last time you started like this</h2>
				<button class="link" onclick={onSimilar}>All</button>
			</div>
			{#if now && now.glucose === null}
				<p class="muted">Needs a current reading.</p>
			{:else}
				<SimilarStarts {similar} {onOpen} />
			{/if}
		</section>
		</div>
	</Glucose>

	<div class="sec log">
		{#if showWeek}
			<LogList log={longLog} {onEdit} title="Last 7 days" />
		{:else}
			<LogList {log} {onEdit} />
			<p class="earlier"><button class="link" onclick={earlier}>Earlier</button></p>
		{/if}
	</div>
</div>

<style>
	.now {
		display: grid;
	}
	.muted {
		margin: 0;
		color: var(--muted);
	}

	/* The state line: three figures, labels above, the values the calculator also uses. */
	.state {
		display: grid;
		grid-template-columns: repeat(3, minmax(0, 1fr));
		gap: var(--s-3);
		margin: var(--s-5) 0 0;
	}
	.state dt {
		color: var(--muted);
		font-size: var(--fs-caption);
	}
	.state dd {
		margin: 0.1rem 0 0;
		font-size: var(--fs-title);
		font-weight: 700;
	}
	.per-log {
		margin: var(--s-1) 0 0;
		color: var(--muted);
		font-size: var(--fs-caption);
	}

	/* On a wide screen the figures and lists keep a reading width; the chart below takes the full width. */
	.state,
	.per-log {
		max-width: 44rem;
	}
	.block {
		display: grid;
		gap: var(--s-2);
	}
	/* Today and the similar starts: one column on the phone, side by side on a wide screen. */
	.cols {
		display: grid;
		gap: var(--s-4);
		align-items: start;
		margin-top: var(--s-5);
	}
	@media (min-width: 900px) {
		.cols {
			grid-template-columns: repeat(2, minmax(0, 1fr));
		}
	}
	.log {
		margin-top: var(--s-4);
	}
	.head {
		display: flex;
		align-items: baseline;
		justify-content: space-between;
		gap: var(--s-4);
	}

	.session {
		display: grid;
		gap: 0.15rem;
		padding: var(--s-2) 0;
		border-top: 1px solid var(--border);
	}
	.title {
		display: flex;
		align-items: center;
		gap: 0.55rem;
	}
	.name {
		flex: 1;
		min-width: 0;
	}
	.what {
		color: var(--muted);
		font-size: var(--fs-small);
		white-space: nowrap;
	}
	.sub {
		margin: 0 0 0 1.17rem;
		color: var(--muted);
		font-size: var(--fs-caption);
	}
	.activity {
		display: flex;
		justify-content: space-between;
		align-items: center;
		gap: var(--s-3);
		min-height: var(--tap);
		margin-left: 1.17rem;
		padding: 0;
		background: none;
		border: none;
		color: var(--accent);
		font-size: var(--fs-small);
		text-align: left;
		cursor: pointer;
	}
	.badge.g {
		color: var(--text);
	}
	.badge.g.caution {
		color: var(--caution);
		border-color: var(--caution);
	}
	.badge.g.low {
		color: var(--danger);
		border-color: var(--danger);
	}

	/* Status marks: the same shapes as the calendar. */
	.mark {
		flex: none;
		width: 0.62rem;
		height: 0.62rem;
		border: 1.5px solid var(--text);
		border-radius: 1px;
	}
	.mark.done {
		background: var(--text);
	}
	.mark.partial {
		box-shadow: inset 0 -0.3rem 0 var(--text);
	}
	.mark.missed {
		border-color: var(--muted);
		border-style: dashed;
	}
	.mark.unplanned {
		background: var(--muted);
		border-color: var(--muted);
		border-radius: 50%;
	}
	.mark.planned {
		border-color: var(--muted);
	}

	.earlier {
		margin: 0;
	}
</style>
