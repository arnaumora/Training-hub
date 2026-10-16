<script module>
	// What was shown, kept while the app is open (coming back from a run returns to it).
	const saved = { view: /** @type {'week' | 'month'} */ ('week'), anchor: /** @type {string | null} */ (null) };
</script>

<script>
	// Planned vs done (roadmap step 9, change 2), from GET /api/calendar: the plan's sessions next to what was
	// done, with each day's status, and the glucose change of every activity. Week view: every session with its
	// activity; tapping a session shows its text and lets you link an activity by hand, mark it done (gym) or
	// not done. Month view: one mark per session; tapping a day opens its week. Planned runs show their target
	// pace from Settings > Training (change 3), never the paces written in the plan files. Matching and statuses are
	// decided by the API (app/plan_calendar.py).
	import { api, ApiError, Unauthorized } from './api.js';
	import Week from './Week.svelte';
	import { duration, km, pace, sportName } from './activityformat.js';

	/** @type {{ onOpen: (id: number) => void, onUnauthorized: () => void }} */
	let { onOpen, onUnauthorized } = $props();

	/** @param {Date} d */
	const iso = (d) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
	/** @param {string} s */
	const parse = (s) => {
		const [y, m, d] = s.split('-').map(Number);
		return new Date(y, m - 1, d);
	};
	/** @param {string} s @param {number} n */
	const addDays = (s, n) => {
		const d = parse(s);
		d.setDate(d.getDate() + n);
		return iso(d);
	};
	/** Monday of the week holding s. @param {string} s */
	const mondayOf = (s) => addDays(s, -((parse(s).getDay() + 6) % 7));
	const firstOfMonth = (/** @type {string} */ s) => `${s.slice(0, 8)}01`;
	const today = iso(new Date());

	let view = $state(saved.view);
	let anchor = $state(saved.anchor ?? mondayOf(today)); // a Monday (week) or the 1st (month)
	$effect(() => {
		saved.view = view;
		saved.anchor = anchor;
	});

	const range = $derived.by(() => {
		if (view === 'week') return { start: anchor, end: addDays(anchor, 6) };
		const first = parse(anchor);
		const last = iso(new Date(first.getFullYear(), first.getMonth() + 1, 0));
		return { start: mondayOf(anchor), end: addDays(mondayOf(last), 6) };
	});

	/** @type {any} */
	let cal = $state(null);
	let error = $state('');
	let version = $state(0); // bumped to reload after a change
	$effect(() => {
		const { start, end } = range;
		version;
		error = '';
		api(`/api/calendar?start=${start}&end=${end}`)
			.then((r) => {
				if (r.start === range.start && r.end === range.end) cal = r;
			})
			.catch((e) => {
				if (e instanceof Unauthorized) return onUnauthorized();
				error = 'Could not load the calendar. Try again in a moment.';
			});
	});
	const shown = $derived(cal && cal.start === range.start && cal.end === range.end ? cal : null);

	function move(/** @type {number} */ n) {
		open = null;
		if (view === 'week') anchor = addDays(anchor, 7 * n);
		else {
			const d = parse(anchor);
			anchor = iso(new Date(d.getFullYear(), d.getMonth() + n, 1));
		}
	}
	function setView(/** @type {'week' | 'month'} */ v) {
		if (v === view) return;
		open = null;
		anchor = v === 'week' ? mondayOf(view === 'month' && anchor.slice(0, 7) === today.slice(0, 7) ? today : anchor) : firstOfMonth(anchor);
		view = v;
	}
	function goToday() {
		open = null;
		anchor = view === 'week' ? mondayOf(today) : firstOfMonth(today);
	}
	const atToday = $derived(view === 'week' ? anchor === mondayOf(today) : anchor === firstOfMonth(today));

	const title = $derived.by(() => {
		if (view === 'month') return parse(anchor).toLocaleDateString('en-GB', { month: 'long', year: 'numeric' });
		const a = parse(range.start);
		const b = parse(range.end);
		const sameMonth = a.getMonth() === b.getMonth();
		return `${a.getDate()}${sameMonth ? '' : ` ${a.toLocaleDateString('en-GB', { month: 'short' })}`} to ${b.toLocaleDateString('en-GB', { day: 'numeric', month: 'short', year: 'numeric' })}`;
	});

	/** A day with one session and nothing unplanned: its status is that session's, shown once (on the session). @param {any} d */
	const sameAsOnlySession = (d) => {
		const real = d.sessions.filter((/** @type {any} */ s) => s.sport !== 'rest');
		return real.length === 1 && !d.unplanned.length && real[0].status === d.status;
	};
	const STATUS = { done: 'Done', partial: 'Partial', missed: 'Missed', planned: 'Planned', rest: 'Rest', unplanned: 'Unplanned' };
	const PLAN_SPORT = { running: 'running', swimming: 'swimming', cycling: 'cycling', training: 'strength', fitness_equipment: 'strength' };
	/** "Tue 13 Oct". @param {string} s */
	const dayLabel = (s) => parse(s).toLocaleDateString('en-GB', { weekday: 'short', day: 'numeric', month: 'short' });
	/** "6 km", "1150 m". @param {number} m @param {string} sport */
	const planned = (m, sport) => (sport === 'swimming' ? `${m} m` : `${+(m / 1000).toFixed(1)} km`);
	const signed = (/** @type {number} */ v) => (v > 0 ? `+${v}` : `${v}`);
	/** 355 -> "5:55". @param {number} s */
	const mmss = (s) => `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`;
	/** The session's text without its PACES block: target paces come from Settings > Training. @param {string} t */
	const withoutPaces = (t) => t.replace(/\n*PACES[^\n]*\n(?:[ \t]+[^\n]*\n?)*/g, '\n').trim();
	/** The plan files wrap their text at about 66 characters. Lines cut by that wrap are joined again (same
	 * indent as the line before, which was long), so the phone wraps the text itself; the indents of the
	 * session's own structure (WARM-UP, MAIN SET...) stay. @param {string} t */
	function reflow(t) {
		const out = [];
		let prevIndent = -1;
		let prevLen = 0;
		for (const line of t.split('\n')) {
			const indent = line.length - line.trimStart().length;
			if (out.length && line.trim() && indent === prevIndent && prevLen >= 50) out[out.length - 1] += ` ${line.trim()}`;
			else out.push(line);
			prevIndent = line.trim() ? indent : -1;
			prevLen = line.length;
		}
		return out.join('\n');
	}

	/** Planned and done km (m for swims) per sport over the week shown. */
	const totals = $derived.by(() => {
		if (!shown || view !== 'week') return [];
		/** @type {Record<string, { planned: number, done: number, sessions: number, ok: number }>} */
		const t = {};
		const row = (/** @type {string} */ s) => (t[s] ??= { planned: 0, done: 0, sessions: 0, ok: 0 });
		for (const d of shown.days) {
			for (const s of d.sessions) {
				if (s.sport === 'rest') continue;
				const r = row(s.sport);
				r.sessions += 1;
				r.planned += s.planned_m ?? 0;
				if (s.status === 'done') r.ok += 1;
				if (s.activity_id) r.done += shown.activities[s.activity_id].distance_m ?? 0;
			}
			for (const id of d.unplanned) {
				const a = shown.activities[id];
				const sp = PLAN_SPORT[/** @type {keyof typeof PLAN_SPORT} */ (a.sport)] ?? a.sport;
				row(sp).done += a.distance_m ?? 0;
			}
		}
		return Object.entries(t).sort((a, b) => b[1].sessions - a[1].sessions);
	});
	const SPORT_LABEL = { running: 'Running', swimming: 'Swimming', cycling: 'Cycling', strength: 'Gym', other: 'Other' };
	/** @param {string} sport @param {number} m */
	const amount = (sport, m) => (sport === 'swimming' ? `${Math.round(m)} m` : `${+(m / 1000).toFixed(1)} km`);

	// The session whose details are open, and its link actions.
	/** @type {number | null} */
	let open = $state(null);
	/** @type {any[] | null} */
	let candidates = $state(null);
	let busy = $state(false);
	let actionError = $state('');
	function toggle(/** @type {number} */ id) {
		open = open === id ? null : id;
		candidates = null;
		actionError = '';
	}
	async function showCandidates(/** @type {number} */ id) {
		try {
			candidates = await api(`/api/calendar/sessions/${id}/candidates`);
		} catch (e) {
			if (e instanceof Unauthorized) return onUnauthorized();
			actionError = 'Could not load the activities.';
		}
	}
	/** @param {number} id @param {{ kind: string, activity_id?: number } | null} link */
	async function setLink(id, link) {
		busy = true;
		actionError = '';
		try {
			await api(`/api/calendar/sessions/${id}/link`, link ? { method: 'PUT', body: link } : { method: 'DELETE' });
			candidates = null;
			version += 1;
		} catch (e) {
			if (e instanceof Unauthorized) return onUnauthorized();
			actionError = e instanceof ApiError && e.detail ? `Not saved: ${e.detail}.` : 'Not saved. Try again in a moment.';
		} finally {
			busy = false;
		}
	}
	const LINK = { auto: 'matched by day and sport', week: 'matched within the week, done on another day', activity: 'linked by hand', done: 'marked done by hand', none: 'marked not done by hand' };
</script>

{#snippet activityLine(/** @type {any} */ a)}
	<button class="activity" onclick={() => onOpen(a.id)}>
		<span>{sportName(a)} {[km(a.distance_m), duration(a.moving_s ?? a.elapsed_s), pace(a)].filter(Boolean).join(', ')}</span>
		{#if a.glucose}
			<span class="badge" class:low={a.glucose.went_low} class:caution={!a.glucose.went_low && a.glucose.high_start}
				title="Glucose at the start and the change over the activity, mg/dL"
				>{a.glucose.start}{a.glucose.delta !== null ? ` ${signed(a.glucose.delta)}` : ''}</span
			>
		{/if}
	</button>
{/snippet}

<section class="calendar" aria-labelledby="calendar-title">
	<div class="nav">
		<button class="link" onclick={() => move(-1)} aria-label={view === 'week' ? 'Previous week' : 'Previous month'}>&lt; Earlier</button>
		<h2 id="calendar-title">{title}</h2>
		<button class="link" onclick={() => move(1)} aria-label={view === 'week' ? 'Next week' : 'Next month'}>Later &gt;</button>
	</div>
	<div class="bar">
		<div class="seg" role="group" aria-label="View">
			<button aria-pressed={view === 'week'} onclick={() => setView('week')}>Week</button>
			<button aria-pressed={view === 'month'} onclick={() => setView('month')}>Month</button>
		</div>
		{#if !atToday}<button class="link" onclick={goToday}>Today</button>{/if}
	</div>

	{#if error}
		<p class="error" role="alert">{error}</p>
	{:else if !shown}
		<p class="muted">Loading...</p>
	{:else if view === 'week'}
		<!-- The week's figures (Week) wrap the plan: its flags first, then the days, then load, glucose, nights. -->
		<Week monday={anchor} {onUnauthorized}>
			<section class="sec" aria-label="Plan and done">
				{#if totals.length}
					<ul class="totals">
						{#each totals as [sport, t]}
							<li>
								<span>{SPORT_LABEL[/** @type {keyof typeof SPORT_LABEL} */ (sport)] ?? sport}</span>
								<span>{#if t.sessions}{t.ok} of {t.sessions} done{:else}Not planned{/if}{#if t.planned}, {amount(sport, t.done)} of {amount(sport, t.planned)}{:else if t.done}, {amount(sport, t.done)}{/if}</span>
							</li>
						{/each}
					</ul>
				{/if}
				<ol class="days">
					{#each shown.days as d (d.day)}
						<li class="day" class:today={d.day === shown.today}>
							<div class="day-head">
								<span class="date">{dayLabel(d.day)}{d.day === shown.today ? ', today' : ''}</span>
								{#if d.status !== 'rest' && d.status !== 'planned' && !sameAsOnlySession(d)}<span class="status {d.status}">{STATUS[/** @type {keyof typeof STATUS} */ (d.status)]}</span>{/if}
							</div>
							<div class="day-body">
							{#each d.sessions as s (s.id)}
								{#if s.sport === 'rest'}
									<p class="rest">{s.title}</p>
								{:else}
									<div class="session">
										<button class="title" onclick={() => toggle(s.id)} aria-expanded={open === s.id}>
											<i class="mark {s.status}" aria-hidden="true"></i>
											<span class="name" class:struck={s.status === 'missed'}>{s.title}</span>
											<span class="what">
												{#if s.status === 'partial' && s.activity_id}
													{amount(s.sport, shown.activities[s.activity_id].distance_m ?? 0)} of {planned(s.planned_m, s.sport)}
												{:else}
													{STATUS[/** @type {keyof typeof STATUS} */ (s.status)]}{#if s.status === 'planned' && s.time}, {s.time}{/if}
												{/if}
											</span>
										</button>
										{#if s.target}
											<p class="target">Target {s.target.name.toLowerCase()} {mmss(s.target.from_s)}-{mmss(s.target.to_s)} /km</p>
										{/if}
										{#if s.activity_id}
											{#if s.activity_day}<p class="moved">Done on {dayLabel(s.activity_day)}</p>{/if}
											{@render activityLine(shown.activities[s.activity_id])}
										{/if}
										{#if open === s.id}
											<div class="details">
												<p class="muted small">
													{s.calendar}{s.planned_m ? `; planned ${planned(s.planned_m, s.sport)}` : ''}{s.link ? `; ${LINK[/** @type {keyof typeof LINK} */ (s.link)]}` : ''}.
												</p>
												{#if s.description}<p class="text">{reflow(withoutPaces(s.description))}</p>{/if}
												<div class="actions">
													{#if s.status !== 'done'}
														<button class="link" disabled={busy} onclick={() => setLink(s.id, { kind: 'done' })}>Mark done</button>
													{/if}
													{#if s.link !== 'none'}
														<button class="link" disabled={busy} onclick={() => setLink(s.id, { kind: 'none' })}>Mark not done</button>
													{/if}
													<button class="link" disabled={busy} onclick={() => showCandidates(s.id)}>Link an activity</button>
													{#if s.link && s.link !== 'auto' && s.link !== 'week'}
														<button class="link" disabled={busy} onclick={() => setLink(s.id, null)}>Match automatically</button>
													{/if}
												</div>
												{#if candidates}
													{#if candidates.length}
														<ul class="candidates">
															{#each candidates as a (a.id)}
																<li>
																	<button disabled={busy || a.id === s.activity_id} onclick={() => setLink(s.id, { kind: 'activity', activity_id: a.id })}>
																		{dayLabel(a.day)}: {sportName(a)} {km(a.distance_m)}{a.id === s.activity_id ? ' (this one)' : ''}
																	</button>
																</li>
															{/each}
														</ul>
													{:else}
														<p class="muted small">No activities within 3 days of this session.</p>
													{/if}
												{/if}
												{#if actionError}<p class="error" role="alert">{actionError}</p>{/if}
											</div>
										{/if}
									</div>
								{/if}
							{/each}
							{#each d.unplanned as id (id)}
								<div class="session">
									<p class="title static"><i class="mark unplanned" aria-hidden="true"></i><span class="name">Unplanned</span></p>
									{@render activityLine(shown.activities[id])}
								</div>
							{/each}
							</div>
						</li>
					{/each}
				</ol>
			</section>
		</Week>
	{:else}
		<section class="sec">
			<div class="month" role="grid" aria-label={title}>
				{#each ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'] as w}
					<div class="dow" role="columnheader">{w}</div>
				{/each}
				{#each shown.days as d (d.day)}
					<button
						class="cell"
						class:other={d.day.slice(0, 7) !== anchor.slice(0, 7)}
						class:today={d.day === shown.today}
						role="gridcell"
						aria-label="{dayLabel(d.day)}: {STATUS[/** @type {keyof typeof STATUS} */ (d.status)]}"
						onclick={() => {
							view = 'week';
							anchor = mondayOf(d.day);
						}}
					>
						<span class="n">{parse(d.day).getDate()}</span>
						<span class="marks">
							{#each d.sessions.filter((/** @type {any} */ s) => s.sport !== 'rest') as s (s.id)}<i class="mark {s.status}"></i>{/each}
							{#each d.unplanned as id (id)}<i class="mark unplanned"></i>{/each}
						</span>
					</button>
				{/each}
			</div>
			<ul class="key">
				<li><i class="mark done"></i>Done</li>
				<li><i class="mark partial"></i>Partial</li>
				<li><i class="mark missed"></i>Missed</li>
				<li><i class="mark unplanned"></i>Unplanned</li>
				<li><i class="mark planned"></i>Planned</li>
			</ul>
		</section>
	{/if}
</section>

<style>
	.calendar {
		display: grid;
		gap: var(--s-4);
	}
	.nav {
		display: grid;
		grid-template-columns: auto minmax(0, 1fr) auto;
		align-items: baseline;
		gap: 0.75rem;
	}
	h2 {
		margin: 0;
		text-align: center;
		font-size: var(--fs-title);
		font-weight: 700;
	}
	.bar {
		display: flex;
		justify-content: space-between;
		align-items: center;
	}
	.small {
		font-size: 0.82rem;
	}
	.muted {
		margin: 0;
		color: var(--muted);
	}
	.error {
		margin: 0;
		color: var(--danger);
	}

	/* Status marks: shape and fill, in greys; no colour needed to tell them apart. */
	.mark {
		display: inline-block;
		flex: none;
		width: 0.62rem;
		height: 0.62rem;
		box-sizing: border-box;
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
		border-color: var(--border);
	}

	.totals {
		margin: 0;
		padding: 0;
		list-style: none;
		display: grid;
		gap: 0.15rem;
		font-size: 0.9rem;
		font-variant-numeric: tabular-nums;
	}
	.totals li {
		display: flex;
		justify-content: space-between;
		gap: 1rem;
	}
	.totals li span:first-child {
		color: var(--muted);
	}

	.days {
		margin: 0;
		padding: 0;
		list-style: none;
	}
	.days > .day:first-child {
		border-top: none;
		padding-top: 0;
	}
	.totals + .days > .day:first-child {
		border-top: 1px solid var(--border);
		padding-top: var(--s-3);
	}
	.days > .day:last-child {
		padding-bottom: 0;
	}
	.day {
		padding: var(--s-3) 0;
		border-top: 1px solid var(--border);
		display: grid;
		gap: 0.3rem;
	}
	.day-head {
		display: flex;
		justify-content: space-between;
		align-items: baseline;
	}
	.day-body {
		display: grid;
		gap: 0.3rem;
		min-width: 0;
	}
	/* A wide screen: the day (date, status) in its own column and its sessions beside it, at a reading width,
	   so a session's title and its status stay close. */
	@media (min-width: 900px) {
		.totals,
		.days {
			max-width: 52rem;
		}
		.day {
			grid-template-columns: 9.5rem minmax(0, 1fr);
			column-gap: var(--s-4);
			align-items: start;
		}
		.day-head {
			flex-direction: column;
			gap: 0.15rem;
		}
	}
	.date {
		font-weight: 700;
		font-size: 0.92rem;
	}
	.day.today .date {
		color: var(--accent);
	}
	.status {
		font-size: 0.82rem;
		color: var(--muted);
	}
	.status.done {
		color: var(--text);
	}
	.rest {
		margin: 0;
		color: var(--muted);
		font-size: 0.9rem;
	}
	.session {
		display: grid;
		gap: 0.15rem;
	}
	.title {
		display: flex;
		align-items: center;
		gap: 0.55rem;
		width: 100%;
		margin: 0;
		padding: 0.15rem 0;
		background: none;
		border: none;
		color: var(--text);
		font: inherit;
		text-align: left;
		cursor: pointer;
	}
	.title.static {
		cursor: default;
	}
	.name {
		flex: 1;
		min-width: 0;
	}
	.struck {
		color: var(--muted);
		text-decoration: line-through;
	}
	.what {
		color: var(--muted);
		font-size: 0.85rem;
		font-variant-numeric: tabular-nums;
		white-space: nowrap;
	}
	.target {
		margin: 0 0 0 1.17rem;
		color: var(--muted);
		font-size: 0.82rem;
		font-variant-numeric: tabular-nums;
	}
	.moved {
		margin: 0 0 0 1.17rem;
		color: var(--muted);
		font-size: 0.82rem;
	}
	.activity {
		display: flex;
		justify-content: space-between;
		align-items: baseline;
		gap: 0.75rem;
		margin-left: 1.17rem;
		padding: 0.1rem 0;
		background: none;
		border: none;
		color: var(--accent);
		font: inherit;
		font-size: 0.9rem;
		font-variant-numeric: tabular-nums;
		text-align: left;
		cursor: pointer;
	}
	.badge {
		flex: none;
		padding: 0 0.35rem;
		border: 1px solid var(--border);
		border-radius: var(--radius);
		color: var(--text);
		font-size: 0.82rem;
	}
	.badge.caution {
		color: var(--caution);
		border-color: var(--caution);
	}
	.badge.low {
		color: var(--danger);
		border-color: var(--danger);
	}
	.details {
		margin-left: 1.17rem;
		display: grid;
		gap: 0.45rem;
		padding: 0.35rem 0 0.25rem;
	}
	.text {
		margin: 0;
		white-space: pre-wrap;
		font-size: 0.85rem;
		line-height: 1.45;
		color: var(--muted);
	}
	.actions {
		display: flex;
		flex-wrap: wrap;
		gap: 0.4rem 1.1rem;
		font-size: 0.9rem;
	}
	.candidates {
		margin: 0;
		padding: 0;
		list-style: none;
		display: grid;
		gap: 0.1rem;
	}
	.candidates button {
		padding: 0.3rem 0;
		background: none;
		border: none;
		color: var(--accent);
		font: inherit;
		font-size: 0.9rem;
		text-align: left;
		cursor: pointer;
	}
	.candidates button:disabled {
		color: var(--muted);
		cursor: default;
	}

	.month {
		display: grid;
		grid-template-columns: repeat(7, minmax(0, 1fr));
		border-top: 1px solid var(--border);
		border-left: 1px solid var(--border);
	}
	.dow {
		padding: 0.25rem 0.3rem;
		color: var(--muted);
		font-size: 0.75rem;
		border-right: 1px solid var(--border);
		border-bottom: 1px solid var(--border);
	}
	.cell {
		display: grid;
		align-content: start;
		gap: 0.3rem;
		min-height: 3.6rem;
		padding: 0.25rem 0.3rem;
		background: none;
		border: none;
		border-right: 1px solid var(--border);
		border-bottom: 1px solid var(--border);
		color: var(--text);
		font: inherit;
		text-align: left;
		cursor: pointer;
	}
	.cell.other .n {
		color: var(--muted);
	}
	.cell.today .n {
		color: var(--accent);
		font-weight: 700;
	}
	.n {
		font-size: 0.85rem;
		font-variant-numeric: tabular-nums;
	}
	.marks {
		display: flex;
		flex-wrap: wrap;
		gap: 0.2rem;
	}
	.key {
		margin: 0;
		padding: 0;
		list-style: none;
		display: flex;
		flex-wrap: wrap;
		gap: 0.3rem 1rem;
		color: var(--muted);
		font-size: 0.82rem;
	}
	.key li {
		display: flex;
		align-items: center;
		gap: 0.4rem;
	}
</style>
