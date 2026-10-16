<script>
	// Settings > Insulin: the only place insulin parameters are entered (spec, "Settings menu").
	// Nothing is pre-filled from code: the form starts from the current saved version, or empty.
	// Saving goes through a review of old against new values; values outside the usual ranges need an
	// extra confirmation. Every save is a new version; earlier versions are listed and never change.
	import { onMount } from 'svelte';
	import { api, ApiError, Unauthorized } from './api.js';
	import { parseClock } from './entry.js';

	/** @type {{ onUnauthorized: () => void, onSaved?: () => void }} */
	let { onUnauthorized, onSaved = () => {} } = $props();

	/** @type {any} */
	let current = $state(null);
	/** @type {any} */
	let usual = $state(null);
	/** @type {any[]} */
	let history = $state([]);
	let loaded = $state(false);
	let loadError = $state('');

	/** @type {'edit' | 'review'} */
	let mode = $state('edit');
	/** @type {{ name: string, start: string, ratio: string }[]} */
	let slots = $state([]);
	let f = $state({
		isf: '',
		correct_above: '',
		correct_to: '',
		action_time_h: '',
		action_time_provisional: false,
		pen_increment: '',
		racion_g: '',
		rapid_insulin: '',
		note: ''
	});
	let confirmUnusual = $state(false);
	/** @type {{ field: string, value: string, usual: string }[]} */
	let serverUnusual = $state([]);
	let busy = $state(false);
	let saveError = $state('');
	let savedMsg = $state('');

	const str = (/** @type {any} */ v) => (v === null || v === undefined ? '' : String(Number(v)));

	/** Fill the form from a saved version, or leave it empty when there is none. */
	function fillFrom(/** @type {any} */ v) {
		slots = v ? v.meal_slots.map((s) => ({ name: s.name, start: s.start, ratio: str(s.ratio) })) : [{ name: '', start: '', ratio: '' }];
		f = {
			isf: str(v?.isf),
			correct_above: str(v?.correct_above),
			correct_to: str(v?.correct_to),
			action_time_h: str(v?.action_time_h),
			action_time_provisional: v?.action_time_provisional ?? false,
			pen_increment: str(v?.pen_increment),
			racion_g: str(v?.racion_g),
			rapid_insulin: v?.rapid_insulin ?? '',
			note: ''
		};
	}

	async function load() {
		try {
			const [s, h] = await Promise.all([api('/api/settings/insulin'), api('/api/settings/insulin/history')]);
			current = s.current;
			usual = s.usual;
			history = h;
			fillFrom(current);
			loaded = true;
		} catch (e) {
			if (e instanceof Unauthorized) return onUnauthorized();
			loadError = 'Could not load the settings. Reload to try again.';
		}
	}
	onMount(load);

	/** Number with at most `dp` decimals ("2,5" works), or null. @param {string} t @param {number} dp */
	function num(t, dp) {
		const s = String(t).trim().replace(',', '.');
		const re = dp === 0 ? /^\d+$/ : new RegExp(`^\\d+(\\.\\d{1,${dp}})?$`);
		return re.test(s) ? Number(s) : null;
	}

	const parsed = $derived({
		slots: slots.map((s) => ({ name: s.name.trim(), start: parseClock(s.start) ?? '', typed: s.start.trim(), ratio: num(s.ratio, 2) })),
		isf: num(f.isf, 1),
		correct_above: num(f.correct_above, 0),
		correct_to: num(f.correct_to, 0),
		action_time_h: num(f.action_time_h, 1),
		pen_increment: f.pen_increment === '' ? null : Number(f.pen_increment),
		racion_g: num(f.racion_g, 1),
		rapid_insulin: f.rapid_insulin.trim()
	});

	/** What still blocks saving, in plain words. */
	const problems = $derived.by(() => {
		const p = [];
		const s = parsed.slots;
		if (s.length === 0) p.push('Add at least one meal slot.');
		s.forEach((x, i) => {
			const label = x.name || `Slot ${i + 1}`;
			if (!x.name) p.push(`${label}: name missing.`);
			if (!x.start) p.push(x.typed ? `${label}: start time as hh:mm (24 h).` : `${label}: start time missing.`);
			if (x.ratio === null || x.ratio <= 0) p.push(`${label}: ratio missing or not a number (up to 2 decimals).`);
		});
		const names = s.map((x) => x.name.toLowerCase()).filter(Boolean);
		if (new Set(names).size !== names.length) p.push('Two meal slots have the same name.');
		const starts = s.map((x) => x.start).filter(Boolean);
		if (new Set(starts).size !== starts.length) p.push('Two meal slots start at the same time.');
		if (parsed.isf === null || parsed.isf <= 0) p.push('Sensitivity missing or not a number.');
		if (parsed.correct_above === null) p.push('"Correct above" missing (whole mg/dL).');
		if (parsed.correct_to === null) p.push('"Correct down to" missing (whole mg/dL).');
		if (parsed.correct_above !== null && parsed.correct_to !== null && parsed.correct_to > parsed.correct_above)
			p.push('"Correct down to" cannot be above "correct above".');
		if (parsed.action_time_h === null || parsed.action_time_h <= 0) p.push('Action time missing or not a number.');
		if (parsed.pen_increment === null) p.push('Choose the pen increment.');
		if (parsed.racion_g === null || parsed.racion_g <= 0) p.push('Racion size missing or not a number.');
		if (!parsed.rapid_insulin) p.push('Rapid insulin name missing.');
		return p;
	});

	/** Fields outside the usual ranges (the API checks the same and answers 409 if not confirmed). */
	const unusual = $derived.by(() => {
		if (!usual) return [];
		const out = [];
		const check = (/** @type {string} */ key, /** @type {string} */ label, /** @type {number | null} */ v) => {
			const u = usual[key];
			if (v !== null && (v < Number(u.min) || v > Number(u.max))) out.push(`${label}: ${v} (usual ${Number(u.min)} to ${Number(u.max)} ${u.unit})`);
		};
		parsed.slots.forEach((s) => check('ratio', `${s.name || 'Slot'} ratio`, s.ratio));
		check('isf', 'Sensitivity', parsed.isf);
		check('correct_above', 'Correct above', parsed.correct_above);
		check('correct_to', 'Correct down to', parsed.correct_to);
		check('action_time_h', 'Action time', parsed.action_time_h);
		check('racion_g', 'Racion size', parsed.racion_g);
		return out;
	});

	const slotText = (/** @type {any} */ s) => `from ${s.start}, ${Number(s.ratio)} u per racion`;

	/** Old against new, one row per value; meal slots matched by name. */
	const review = $derived.by(() => {
		/** @type {{ label: string, old: string, now: string }[]} */
		const rows = [];
		const c = current;
		const newSlots = [...parsed.slots].sort((a, b) => a.start.localeCompare(b.start));
		for (const s of newSlots) {
			const o = c?.meal_slots.find((x) => x.name.toLowerCase() === s.name.toLowerCase());
			rows.push({ label: s.name, old: o ? slotText(o) : '-', now: slotText(s) });
		}
		for (const o of c?.meal_slots ?? []) {
			if (!newSlots.some((s) => s.name.toLowerCase() === o.name.toLowerCase())) rows.push({ label: o.name, old: slotText(o), now: 'removed' });
		}
		const v = (/** @type {any} */ x, /** @type {string} */ unit) => (x === null || x === undefined ? '-' : `${Number(x)} ${unit}`);
		rows.push({ label: 'Sensitivity, 1 u lowers', old: v(c?.isf, 'mg/dL'), now: v(parsed.isf, 'mg/dL') });
		rows.push({ label: 'Correct above', old: v(c?.correct_above, 'mg/dL'), now: v(parsed.correct_above, 'mg/dL') });
		rows.push({ label: 'Correct down to', old: v(c?.correct_to, 'mg/dL'), now: v(parsed.correct_to, 'mg/dL') });
		const at = (/** @type {any} */ h, /** @type {any} */ p) => (h === null || h === undefined ? '-' : `${Number(h)} h${p ? ', provisional' : ''}`);
		rows.push({
			label: 'Action time',
			old: at(c?.action_time_h, c?.action_time_provisional),
			now: at(parsed.action_time_h, f.action_time_provisional)
		});
		rows.push({ label: 'Pen increment', old: v(c?.pen_increment, 'u'), now: v(parsed.pen_increment, 'u') });
		rows.push({ label: 'Racion size', old: v(c?.racion_g, 'g'), now: v(parsed.racion_g, 'g') });
		rows.push({ label: 'Rapid insulin', old: c?.rapid_insulin ?? '-', now: parsed.rapid_insulin });
		return rows;
	});
	const changes = $derived(review.filter((r) => r.old !== r.now).length);

	function addSlot() {
		slots.push({ name: '', start: '', ratio: '' });
	}

	/** @param {number} i */
	function removeSlot(i) {
		slots.splice(i, 1);
	}

	function toReview() {
		if (problems.length) return;
		confirmUnusual = false;
		serverUnusual = [];
		saveError = '';
		mode = 'review';
		window.scrollTo(0, 0);
	}

	async function save() {
		busy = true;
		saveError = '';
		try {
			const saved = await api('/api/settings/insulin', {
				method: 'POST',
				body: {
					meal_slots: parsed.slots.map(({ name, start, ratio }) => ({ name, start, ratio })),
					isf: parsed.isf,
					correct_above: parsed.correct_above,
					correct_to: parsed.correct_to,
					action_time_h: parsed.action_time_h,
					action_time_provisional: f.action_time_provisional,
					pen_increment: parsed.pen_increment,
					racion_g: parsed.racion_g,
					rapid_insulin: parsed.rapid_insulin,
					note: f.note.trim() || null,
					confirm_unusual: confirmUnusual
				}
			});
			current = saved;
			history = [saved, ...history];
			fillFrom(saved);
			mode = 'edit';
			savedMsg = `Saved as version ${saved.version}.`;
			onSaved();
			window.scrollTo(0, 0);
		} catch (e) {
			if (e instanceof Unauthorized) return onUnauthorized();
			if (e instanceof ApiError && e.status === 409) {
				saveError = 'Some values are outside the usual range. Check them and confirm below.';
			} else {
				saveError = 'Not saved. Check the values and try again.';
			}
		} finally {
			busy = false;
		}
	}

	const dateTime = (/** @type {number} */ t) =>
		new Date(t * 1000).toLocaleString('en-GB', { day: 'numeric', month: 'short', year: 'numeric', hour: '2-digit', minute: '2-digit' });
</script>

<section class="settings" aria-labelledby="insulin-settings-title">
	<h2 id="insulin-settings-title" class="visually-hidden">Insulin settings</h2>

	{#if loadError}
		<p class="error" role="alert">{loadError}</p>
	{:else if !loaded}
		<p class="muted">Loading...</p>
	{:else}
		<p class="status">
			{#if current}
				Version {current.version}, saved {dateTime(current.saved_at)}{current.note ? ` (${current.note})` : ''}.
			{:else}
				Nothing saved yet. Until every field is saved, raciones can't be converted and the calculator stays off.
			{/if}
		</p>
		{#if savedMsg}
			<p class="saved" role="status">{savedMsg}</p>
		{/if}

		{#if mode === 'edit'}
			<form
				onsubmit={(e) => {
					e.preventDefault();
					toReview();
				}}
				novalidate
			>
				<fieldset class="sec">
					<legend>Meal slots</legend>
					<p class="help">A slot lasts until the next one starts.</p>
					<!-- Each slot on two lines: the name across the full width (never cut off on a phone), then its
					     start time, its ratio and Remove. -->
					<div class="slots">
						{#each slots as s, i}
							<div class="slot">
								<div class="slot-top">
									<input class="slot-name" aria-label="Slot name" bind:value={s.name} maxlength="30" autocomplete="off" oninput={() => (savedMsg = '')} />
									<button type="button" class="link danger" onclick={() => removeSlot(i)} disabled={slots.length === 1} aria-label="Remove slot {s.name}">
										Remove
									</button>
								</div>
								<div class="slot-line">
									<label class="inline"><span>from</span><input
											aria-label="Starts at, 24 h"
											class="clock"
											bind:value={s.start}
											inputmode="numeric"
											maxlength="5"
											placeholder="hh:mm"
											autocomplete="off"
											oninput={() => (savedMsg = '')}
											onblur={() => (s.start = parseClock(s.start) ?? s.start)}
										/></label>
									<label class="inline"><input aria-label="Units per racion" bind:value={s.ratio} inputmode="decimal" autocomplete="off" oninput={() => (savedMsg = '')} /><span>u per racion</span></label>
								</div>
							</div>
						{/each}
					</div>
					<button type="button" class="link" onclick={addSlot} disabled={slots.length >= 8}>Add slot</button>
				</fieldset>

				<fieldset class="sec">
					<legend>Correction</legend>
					<label class="row">
						<span>Sensitivity: 1 u lowers</span>
						<input bind:value={f.isf} inputmode="decimal" autocomplete="off" />
						<span class="unit">mg/dL</span>
					</label>
					<label class="row">
						<span>Correct above</span>
						<input bind:value={f.correct_above} inputmode="numeric" autocomplete="off" />
						<span class="unit">mg/dL</span>
					</label>
					<label class="row">
						<span>Correct down to</span>
						<input bind:value={f.correct_to} inputmode="numeric" autocomplete="off" />
						<span class="unit">mg/dL</span>
					</label>
				</fieldset>

				<fieldset class="sec">
					<legend>Insulin</legend>
					<label class="row">
						<span>Action time (for insulin still active)</span>
						<input bind:value={f.action_time_h} inputmode="decimal" autocomplete="off" />
						<span class="unit">h</span>
					</label>
					<label class="check">
						<input type="checkbox" bind:checked={f.action_time_provisional} />
						<span>Provisional: not yet confirmed by the endocrinologist</span>
					</label>
					<div class="row">
						<span id="pen-label">Pen increment</span>
						<div class="seg" role="group" aria-labelledby="pen-label">
							{#each ['0.5', '1'] as p}
								<button type="button" aria-pressed={f.pen_increment === p} onclick={() => (f.pen_increment = p)}>
									{p} u
								</button>
							{/each}
						</div>
					</div>
					<label class="row">
						<span>Rapid insulin</span>
						<input bind:value={f.rapid_insulin} maxlength="40" autocomplete="off" />
						<span></span>
					</label>
				</fieldset>

				<fieldset class="sec">
					<legend>Carbs</legend>
					<label class="row">
						<span>1 racion is</span>
						<input bind:value={f.racion_g} inputmode="decimal" autocomplete="off" />
						<span class="unit">g carbs</span>
					</label>
				</fieldset>

				<label class="row note">
					<span>Note for this version</span>
					<input bind:value={f.note} maxlength="200" autocomplete="off" placeholder="For example: endo visit" />
					<span></span>
				</label>

				{#if problems.length}
					<ul class="problems">
						{#each problems as p}<li>{p}</li>{/each}
					</ul>
				{/if}
				<button type="submit" class="primary" disabled={problems.length > 0}>Review changes</button>
			</form>
		{:else}
			<div class="review sec">
				<h3>{current ? `Check the changes (${changes})` : 'Check the values'}</h3>
				<table>
					<thead>
						<tr><th scope="col"></th><th scope="col">Now</th><th scope="col">New</th></tr>
					</thead>
					<tbody>
						{#each review as r}
							<tr class:changed={r.old !== r.now}>
								<th scope="row">{r.label}</th>
								<td>{r.old}</td>
								<td>{r.now}</td>
							</tr>
						{/each}
					</tbody>
				</table>
				{#if f.note.trim()}<p class="muted">Note: {f.note.trim()}</p>{/if}

				{#if unusual.length}
					<div class="warn" role="alert">
						<p>Outside the usual range:</p>
						<ul>
							{#each unusual as u}<li>{u}</li>{/each}
						</ul>
						<label class="check">
							<input type="checkbox" bind:checked={confirmUnusual} />
							<span>I have checked these values and they are right</span>
						</label>
					</div>
				{/if}
				{#if saveError}<p class="error" role="alert">{saveError}</p>{/if}

				<div class="actions-row">
					<button class="link" onclick={() => (mode = 'edit')} disabled={busy}>Back to edit</button>
					<button class="primary" onclick={save} disabled={busy || changes === 0 || (unusual.length > 0 && !confirmUnusual)}>
						{busy ? 'Saving...' : changes === 0 ? 'Nothing changed' : `Save as version ${(current?.version ?? 0) + 1}`}
					</button>
				</div>
			</div>
		{/if}

		{#if history.length > 1}
			<details class="history">
				<summary>Earlier versions ({history.length - 1})</summary>
				<ul>
					{#each history.slice(1) as h (h.version)}
						<li>
							<span>Version {h.version}, {dateTime(h.saved_at)}{h.note ? ` (${h.note})` : ''}</span>
							<span class="muted">
								{h.meal_slots.map((s) => `${s.name} ${Number(s.ratio)}`).join(', ')}; ISF {Number(h.isf)}; correct {h.correct_above} to
								{h.correct_to}; {Number(h.action_time_h)} h; racion {Number(h.racion_g)} g
							</span>
						</li>
					{/each}
				</ul>
			</details>
		{/if}
	{/if}
</section>

<style>
	.settings {
		display: grid;
		gap: var(--s-4);
		max-width: 44rem;
	}
	h3 {
		margin: 0;
		font-size: var(--fs-title);
		font-weight: 700;
	}
	.status,
	.muted,
	.help {
		margin: 0;
		color: var(--muted);
	}
	.help {
		font-size: var(--fs-small);
	}
	.saved {
		margin: 0;
		color: var(--accent);
	}
	.error {
		margin: 0;
		color: var(--danger);
	}
	form {
		display: grid;
		gap: var(--s-4);
	}
	input:not([type='checkbox']) {
		width: 100%;
		min-width: 0;
		padding: 0.35rem 0;
		background: none;
		border: none;
		border-bottom: 1.5px solid var(--border);
		border-radius: 0;
	}
	input:not([type='checkbox']):focus {
		outline: none;
		border-bottom-color: var(--accent);
	}
	input[type='checkbox'] {
		accent-color: var(--accent);
	}
	/* Meal slots: the name on its own line, then "from [time] [ratio] u per racion   Remove". */
	.slots {
		display: grid;
	}
	.slot {
		display: grid;
		gap: var(--s-1);
		padding: var(--s-3) 0;
		border-top: 1px solid var(--border);
	}
	.slot:first-child {
		border-top: none;
		padding-top: 0;
	}
	.slot-name {
		font-weight: 700;
	}
	.slot-line {
		display: flex;
		flex-wrap: wrap;
		align-items: center;
		gap: var(--s-2) var(--s-4);
	}
	.inline {
		display: flex;
		align-items: center;
		gap: var(--s-2);
		color: var(--muted);
		font-size: var(--fs-small);
	}
	.inline input {
		width: 3.5rem;
	}
	.inline input.clock {
		width: 4.5rem;
	}
	.slot-top {
		display: flex;
		align-items: center;
		gap: var(--s-4);
	}
	.slot-top .slot-name {
		flex: 1;
	}
	/* The pen increment's two choices sit in the field and unit columns, on one line. */
	.row .seg {
		grid-column: 2 / 4;
		justify-self: start;
	}
	.row .seg button {
		white-space: nowrap;
	}
	.row {
		display: grid;
		grid-template-columns: minmax(0, 1fr) 6rem 4.5rem;
		gap: var(--s-3);
		align-items: center;
	}
	.row.note {
		grid-template-columns: minmax(0, 1fr) minmax(0, 1.4fr) 0;
	}
	.unit {
		color: var(--muted);
		font-size: var(--fs-small);
	}
	.check {
		display: flex;
		gap: var(--s-3);
		align-items: center;
		min-height: var(--tap);
		font-size: var(--fs-small);
	}
	.link {
		justify-self: start;
	}
	.link.danger {
		color: var(--muted);
		font-size: var(--fs-small);
	}
	.problems {
		margin: 0;
		padding-left: 1.1rem;
		color: var(--muted);
		font-size: var(--fs-small);
	}
	.primary {
		justify-self: start;
		min-height: var(--tap);
		padding: 0 var(--s-5);
		background: var(--accent);
		color: var(--accent-text);
		border: none;
		border-radius: var(--radius);
		font-weight: 700;
		cursor: pointer;
	}
	.primary:disabled {
		opacity: 0.4;
		cursor: default;
	}
	table {
		width: 100%;
		border-collapse: collapse;
		font-size: var(--fs-small);
	}
	th,
	td {
		text-align: left;
		padding: var(--s-2) var(--s-2) var(--s-2) 0;
		border-bottom: 1px solid var(--border);
		vertical-align: top;
	}
	thead th {
		color: var(--muted);
		font-weight: 400;
		font-size: var(--fs-caption);
	}
	tbody th {
		font-weight: 400;
		color: var(--muted);
	}
	tr.changed td:last-child {
		font-weight: 700;
		color: var(--accent);
	}
	tr.changed th {
		color: var(--text);
	}
	/* Values outside the usual range: a caution block (tint, no coloured edge), checked before saving. */
	.warn {
		display: grid;
		gap: var(--s-2);
		padding: var(--s-3);
		border-radius: var(--radius);
		background: var(--caution-bg);
		color: var(--caution);
	}
	.warn .check {
		color: var(--text);
	}
	.warn p,
	.warn ul {
		margin: 0;
	}
	.warn ul {
		padding-left: 1.1rem;
	}
	.actions-row {
		display: flex;
		justify-content: space-between;
		align-items: center;
		gap: var(--s-4);
	}
	.actions-row .primary {
		justify-self: auto;
	}
	.history summary {
		min-height: var(--tap);
		display: flex;
		align-items: center;
		cursor: pointer;
		color: var(--muted);
	}
	.history ul {
		list-style: none;
		margin: var(--s-2) 0 0;
		padding: 0;
		display: grid;
		gap: var(--s-3);
	}
	.history li {
		display: grid;
		gap: 0.1rem;
		font-size: var(--fs-small);
	}
</style>
