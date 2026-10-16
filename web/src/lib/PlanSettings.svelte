<script>
	// Settings > Plan (roadmap step 9, change 1): import the training plan's .ics calendars and see what is
	// in the app. Importing a calendar again updates its sessions by UID; a session no longer in the file is
	// marked removed. The calendar screen (change 2) reads these sessions.
	import { api, ApiError, Unauthorized } from './api.js';

	/** @type {{ onUnauthorized: () => void }} */
	let { onUnauthorized } = $props();

	const SPORTS = { running: 'runs', swimming: 'swims', cycling: 'rides', strength: 'gym', rest: 'rest days', other: 'other' };

	/** @type {any[] | null} */
	let cals = $state(null);
	/** @type {{ file: string, text: string, failed: boolean }[]} */
	let results = $state([]);
	let error = $state('');
	let busy = $state(false);
	/** @type {HTMLInputElement} */
	let fileInput;

	async function load() {
		try {
			cals = await api('/api/plan/calendars');
		} catch (e) {
			if (e instanceof Unauthorized) return onUnauthorized();
			error = 'Could not load the plan.';
		}
	}
	$effect(() => {
		load();
	});

	/** "12 added, 3 updated, 87 unchanged, 1 removed", leaving out the zeros. @param {any} r */
	function changes(r) {
		const parts = [['added', r.added], ['updated', r.updated], ['unchanged', r.unchanged], ['removed', r.removed]]
			.filter(([, n]) => n > 0)
			.map(([w, n]) => `${n} ${w}`);
		return parts.length ? parts.join(', ') : 'no sessions';
	}

	/** @param {Event} event */
	async function upload(event) {
		const input = /** @type {HTMLInputElement} */ (event.currentTarget);
		const files = [...(input.files ?? [])];
		input.value = ''; // the same files can be picked again
		if (!files.length) return;
		busy = true;
		results = [];
		for (const file of files) {
			try {
				const r = await fetch(`/api/plan/ics?name=${encodeURIComponent(file.name)}`, {
					method: 'POST',
					headers: { 'Content-Type': 'text/calendar' },
					body: file
				});
				if (r.status === 401) return onUnauthorized();
				const body = await r.json().catch(() => ({}));
				if (!r.ok) throw new ApiError(r.status, typeof body.detail === 'string' ? body.detail : '');
				results.push({ file: file.name, text: `${body.calendar}: ${changes(body)}.`, failed: false });
			} catch (e) {
				const why =
					e instanceof ApiError && e.detail
						? e.detail
						: e instanceof ApiError && e.status === 413
							? 'the file is too large'
							: 'try again in a moment';
				results.push({ file: file.name, text: `Not imported: ${why}.`, failed: true });
			}
		}
		busy = false;
		load();
	}

	/** "Tue 13 Oct 2026". @param {string} d YYYY-MM-DD */
	const day = (d) =>
		new Date(`${d}T12:00:00`).toLocaleDateString('en-GB', { weekday: 'short', day: 'numeric', month: 'short', year: 'numeric' });
	/** "13 Oct, 18:02". @param {number} t */
	const at = (t) => new Date(t * 1000).toLocaleString('en-GB', { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit', hour12: false });
	/** "88 runs, 14 rest days". @param {Record<string, number>} by */
	const kinds = (by) =>
		Object.entries(by)
			.sort((a, b) => b[1] - a[1])
			.map(([s, n]) => `${n} ${SPORTS[/** @type {keyof typeof SPORTS} */ (s)] ?? s}`)
			.join(', ');
</script>

<section class="plan" aria-labelledby="plan-title">
	<h2 id="plan-title" class="visually-hidden">Training plan</h2>
	<section class="sec" aria-labelledby="import-title">
	<h3 id="import-title">Import</h3>
	<p class="muted">The plan's .ics calendars. Importing one again updates it; sessions gone from the file are marked removed.</p>
	<div>
		<button class="primary" onclick={() => fileInput.click()} disabled={busy}>{busy ? 'Importing...' : 'Import .ics files'}</button>
		<input
			bind:this={fileInput}
			type="file"
			accept=".ics,text/calendar"
			multiple
			class="visually-hidden"
			tabindex="-1"
			aria-hidden="true"
			onchange={upload}
		/>
	</div>
	{#if results.length}
		<ul class="results" role="status">
			{#each results as r}
				<li class:error={r.failed}><span class="file">{r.file}</span> {r.text}</li>
			{/each}
		</ul>
	{/if}
	</section>

	<section class="sec" aria-labelledby="cals-title">
	<h3 id="cals-title">Calendars</h3>
	{#if cals === null}
		{#if error}<p class="error">{error}</p>{:else}<p class="muted">Loading...</p>{/if}
	{:else if cals.length === 0}
		<p class="muted">No plan yet.</p>
	{:else}
		{#each cals as c}
			<div class="cal">
				<div class="name">{c.calendar}</div>
				{#if c.sessions}
					<div>{c.sessions} sessions{Object.keys(c.by_sport).length > 1 ? `: ${kinds(c.by_sport)}` : ''}</div>
					<div class="muted small">{day(c.first_day)} to {day(c.last_day)}</div>
				{:else}
					<div class="muted">No sessions left in this calendar.</div>
				{/if}
				{#if c.last_import}
					<div class="muted small">
						Last import {at(c.last_import.at)}: {changes(c.last_import)}.{#if c.removed}
							{' '}{c.removed} removed in total.{/if}
					</div>
				{/if}
			</div>
		{/each}
	{/if}
	</section>
</section>

<style>
	.plan {
		display: grid;
		gap: var(--s-4);
		max-width: 44rem;
	}
	.primary {
		min-height: var(--tap);
		padding: 0 var(--s-5);
		background: var(--accent);
		color: var(--accent-text);
		border: none;
		border-radius: var(--radius);
		font-weight: 700;
		cursor: pointer;
	}
	.results {
		margin: 0;
		padding: 0;
		list-style: none;
		display: grid;
		gap: var(--s-1);
		font-size: var(--fs-small);
	}
	.file {
		color: var(--muted);
	}
	.cal {
		display: grid;
		gap: 0.15rem;
		padding: var(--s-3) 0;
		border-top: 1px solid var(--border);
		font-variant-numeric: tabular-nums;
	}
	.name {
		font-weight: 700;
	}
	.muted {
		margin: 0;
		color: var(--muted);
	}
	.small {
		font-size: var(--fs-small);
	}
	h3 + .cal {
		border-top: none;
		padding-top: 0;
	}
	.error {
		margin: 0;
		color: var(--danger);
	}
</style>
