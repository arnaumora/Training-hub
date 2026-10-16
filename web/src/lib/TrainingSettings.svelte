<script>
	// Settings > Training: heart-rate zones, pace zones (the targets the calendar shows on planned runs, step 9),
	// the glucose target range and the sensor lag offset.
	// Versioned like Settings > Insulin: every save is a new version, earlier ones are listed and never change.
	// "Zones from LTHR" fills the Friel running zones (85, 90, 95, 100 % of LTHR, rounded down).
	import { api, ApiError, Unauthorized } from './api.js';

	/** @type {{ onUnauthorized: () => void }} */
	let { onUnauthorized } = $props();

	const FRIEL = [0.85, 0.9, 0.95, 1];
	const NAMES = ['Recovery, easy', 'Aerobic endurance', 'Tempo', 'Threshold', 'Above threshold'];

	/** @type {any} */
	let current = $state(null);
	/** @type {any[]} */
	let history = $state([]);
	/** @type {Record<string, string>} */
	let paceNames = $state({});
	/** Pace zones being edited, "m:ss" text: { easy: ['5:55', '6:25'], ... }. @type {Record<string, [string, string]>} */
	let paces = $state({});
	let loadError = $state('');
	let f = $state({ lthr: '', max_hr: '', resting_hr: '', floors: ['', '', '', ''], glucose_low: '', glucose_high: '', lag_min: '', note: '' });
	let error = $state('');
	let saved = $state('');
	let busy = $state(false);

	/** @param {any} c */
	function fill(c) {
		f = {
			lthr: String(c.lthr),
			max_hr: String(c.max_hr),
			resting_hr: String(c.resting_hr),
			floors: c.hr_zone_floors.map(String),
			glucose_low: String(c.glucose_low),
			glucose_high: String(c.glucose_high),
			lag_min: String(c.lag_min),
			note: ''
		};
		paces = Object.fromEntries(
			Object.keys(paceNames).map((k) => [k, c.pace_zones[k] ? [mmss(c.pace_zones[k][0]), mmss(c.pace_zones[k][1])] : ['', '']])
		);
	}

	/** 355 -> "5:55". @param {number} s */
	const mmss = (s) => `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`;
	/** "5:55" -> 355, '' -> undefined, anything else -> null. @param {string} t */
	function secs(t) {
		const v = t.trim();
		if (!v) return undefined;
		const m = /^(\d{1,2}):([0-5]\d)$/.exec(v);
		return m ? Number(m[1]) * 60 + Number(m[2]) : null;
	}

	async function load() {
		try {
			const r = await api('/api/settings/training');
			current = r.current;
			history = r.history;
			paceNames = r.pace_zone_names;
			if (current) fill(current);
		} catch (e) {
			if (e instanceof Unauthorized) return onUnauthorized();
			loadError = 'Could not load the training settings.';
		}
	}

	$effect(() => {
		load();
	});

	const int = (/** @type {string} */ s) => (/^\d{1,3}$/.test(s.trim()) ? Number(s.trim()) : null);
	const floors = $derived(f.floors.map(int));

	function fromLthr() {
		const l = int(f.lthr);
		if (l === null) return;
		f.floors = FRIEL.map((p) => String(Math.floor(l * p)));
	}

	/** "below 162", "162 - 170", "191 and up" for zone i (0-based). @param {number} i */
	function zoneRange(i) {
		const lo = i === 0 ? null : floors[i - 1];
		const hi = i < 4 ? floors[i] : null;
		if (i === 0) return hi === null ? '' : `below ${hi}`;
		if (lo === null) return '';
		return hi === null ? `${lo} and up` : `${lo} - ${hi - 1}`;
	}

	async function save() {
		error = '';
		saved = '';
		const body = {
			lthr: int(f.lthr),
			max_hr: int(f.max_hr),
			resting_hr: int(f.resting_hr),
			hr_zone_floors: floors,
			glucose_low: int(f.glucose_low),
			glucose_high: int(f.glucose_high),
			lag_min: int(f.lag_min),
			note: f.note.trim() || null
		};
		const numbers = [body.lthr, body.max_hr, body.resting_hr, body.glucose_low, body.glucose_high, body.lag_min, ...floors];
		if (numbers.some((v) => v === null)) {
			error = 'Every value is a whole number.';
			return;
		}
		/** @type {Record<string, [number, number]>} */
		const pace_zones = {};
		for (const [k, [a, b]] of Object.entries(paces)) {
			const fast = secs(a);
			const slow = secs(b);
			if (fast === undefined && slow === undefined) continue;
			if (!fast || !slow) {
				error = `${paceNames[k]}: write both paces as minutes:seconds, like 5:55, or leave both empty.`;
				return;
			}
			if (fast >= slow) {
				error = `${paceNames[k]}: the faster pace goes first.`;
				return;
			}
			pace_zones[k] = [fast, slow];
		}
		Object.assign(body, { pace_zones });
		busy = true;
		try {
			const r = await api('/api/settings/training', { method: 'POST', body });
			saved = `Saved as version ${r.version}.`;
			await load();
		} catch (e) {
			if (e instanceof Unauthorized) return onUnauthorized();
			error =
				e instanceof ApiError && e.status === 409
					? 'Nothing changed.'
					: e instanceof ApiError && e.status === 422
						? 'Not saved: check the values (zones must rise, zone 5 at most max HR, resting HR below LTHR, paces between 2:30 and 15:00).'
						: 'Not saved. Try again in a moment.';
		} finally {
			busy = false;
		}
	}

	const date = (/** @type {number} */ t) => new Date(t * 1000).toLocaleDateString('en-GB', { day: 'numeric', month: 'short', year: 'numeric' });
</script>

<section class="training" aria-labelledby="training-title">
	<h2 id="training-title" class="visually-hidden">Training settings</h2>
	{#if loadError}
		<p class="error">{loadError}</p>
	{:else if current}
		<p class="status">Version {current.version}, saved {date(current.created_at)}{current.note ? ` (${current.note})` : ''}.</p>

		<fieldset class="sec">
			<legend>Heart rate</legend>
			<label class="row"><span>Lactate threshold (LTHR)</span><input bind:value={f.lthr} inputmode="numeric" /><span class="unit">bpm</span></label>
			<label class="row"><span>Max</span><input bind:value={f.max_hr} inputmode="numeric" /><span class="unit">bpm</span></label>
			<label class="row"><span>Resting</span><input bind:value={f.resting_hr} inputmode="numeric" /><span class="unit">bpm</span></label>
		</fieldset>

		<fieldset class="sec">
			<legend>Zones</legend>
			<div class="zones">
				{#each NAMES as name, i}
					<div class="zone">
						<i class="swatch" style="background: var(--zone-{i + 1})"></i>
						<span class="zname">Zone {i + 1} <span class="muted">{name}</span></span>
						{#if i > 0}
							<label class="from">
								<span class="visually-hidden">Zone {i + 1} starts at</span>
								<span class="muted">from</span>
								<input bind:value={f.floors[i - 1]} inputmode="numeric" />
							</label>
						{:else}
							<span></span>
						{/if}
						<span class="range muted">{zoneRange(i)}</span>
					</div>
				{/each}
			</div>
			<button type="button" class="link" onclick={fromLthr}>Zones from LTHR (Friel: 85, 90, 95, 100 %)</button>
		</fieldset>

		<fieldset class="sec">
			<legend>Pace zones</legend>
			<p class="muted small">Targets for planned runs, per km.</p>
			<div class="paces">
				{#each Object.entries(paceNames) as [k, name] (k)}
					<span class="pname">{name}</span>
					<label><span class="visually-hidden">{name} from</span><input bind:value={paces[k][0]} inputmode="numeric" placeholder="m:ss" /></label>
					<span class="muted">to</span>
					<label><span class="visually-hidden">{name} to</span><input bind:value={paces[k][1]} inputmode="numeric" placeholder="m:ss" /></label>
					<span class="unit">/km</span>
				{/each}
			</div>
		</fieldset>

		<fieldset class="sec">
			<legend>Glucose</legend>
			<label class="row"><span>Target from</span><input bind:value={f.glucose_low} inputmode="numeric" /><span class="unit">mg/dL</span></label>
			<label class="row"><span>Target to</span><input bind:value={f.glucose_high} inputmode="numeric" /><span class="unit">mg/dL</span></label>
			<label class="row"><span>Sensor lag (graph line)</span><input bind:value={f.lag_min} inputmode="numeric" /><span class="unit">min</span></label>
		</fieldset>

		<label class="row note"><span>Note for this version</span><input bind:value={f.note} maxlength="200" placeholder="For example: after the 5K" /></label>
		<button class="primary" onclick={save} disabled={busy}>{busy ? 'Saving...' : 'Save as a new version'}</button>
		{#if error}<p class="error" role="alert">{error}</p>{/if}
		{#if saved}<p class="saved" role="status">{saved}</p>{/if}

		{#if history.length > 1}
			<details class="history">
				<summary>Earlier versions ({history.length - 1})</summary>
				<ul>
					{#each history.slice(1) as h (h.version)}
						<li>
							Version {h.version}, {date(h.created_at)}{h.note ? ` (${h.note})` : ''}:
							<span class="muted"
								>LTHR {h.lthr}, zones {h.hr_zone_floors.join(' / ')}, target {h.glucose_low}-{h.glucose_high}, lag {h.lag_min} min{Object.keys(h.pace_zones).length
									? `, easy ${h.pace_zones.easy ? `${mmss(h.pace_zones.easy[0])}-${mmss(h.pace_zones.easy[1])}` : '-'} /km`
									: ''}</span
							>
						</li>
					{/each}
				</ul>
			</details>
		{/if}
	{:else}
		<p class="muted">Loading...</p>
	{/if}
</section>

<style>
	.paces {
		display: grid;
		grid-template-columns: minmax(0, 1fr) 4.2rem auto 4.2rem auto;
		gap: 0.45rem 0.6rem;
		align-items: baseline;
	}
	.paces input {
		width: 100%;
		padding: 0.3rem 0;
		background: none;
		border: none;
		border-bottom: 1.5px solid var(--border);
		border-radius: 0;
		color: var(--text);
		font: inherit;
		font-variant-numeric: tabular-nums;
		text-align: right;
	}
	.paces input:focus {
		outline: none;
		border-bottom-color: var(--accent);
	}
	.small {
		font-size: var(--fs-small);
	}
	.training {
		display: grid;
		gap: var(--s-4);
		max-width: 44rem;
	}
	.status,
	.muted {
		margin: 0;
		color: var(--muted);
	}
	.row {
		display: grid;
		grid-template-columns: minmax(0, 1fr) 5rem 3.5rem;
		gap: 0.75rem;
		align-items: baseline;
	}
	.row.note {
		grid-template-columns: minmax(0, 1fr) minmax(0, 1.4fr);
	}
	input {
		width: 100%;
		min-width: 0;
		padding: 0.35rem 0;
		background: none;
		border: none;
		border-bottom: 1.5px solid var(--border);
		border-radius: 0;
		color: var(--text);
	}
	input:focus {
		outline: none;
		border-bottom-color: var(--accent);
	}
	.unit {
		color: var(--muted);
		font-size: var(--fs-small);
	}
	.zones {
		display: grid;
		gap: 0.35rem;
	}
	.zone {
		display: grid;
		grid-template-columns: 0.9rem minmax(0, 1fr) 6.5rem 6.5rem;
		gap: 0.6rem;
		align-items: baseline;
	}
	.swatch {
		width: 0.9rem;
		height: 0.9rem;
		border-radius: 2px;
		align-self: center;
	}
	.zname .muted {
		font-size: var(--fs-small);
	}
	.from {
		display: grid;
		grid-template-columns: auto minmax(0, 1fr);
		gap: 0.4rem;
		align-items: baseline;
	}
	.range {
		text-align: right;
		font-size: var(--fs-small);
	}
	.link {
		justify-self: start;
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
	}
	.error {
		margin: 0;
		color: var(--danger);
	}
	.saved {
		margin: 0;
		color: var(--accent);
	}
	.history summary {
		display: flex;
		align-items: center;
		min-height: var(--tap);
		cursor: pointer;
		color: var(--muted);
	}
	.history ul {
		margin: 0.5rem 0 0;
		padding: 0;
		list-style: none;
		display: grid;
		gap: var(--s-2);
		font-size: var(--fs-small);
	}
	@media (max-width: 420px) {
		.zone {
			grid-template-columns: 0.9rem minmax(0, 1fr) 5.5rem;
		}
		.range {
			grid-column: 2;
			text-align: left;
		}
	}
</style>
