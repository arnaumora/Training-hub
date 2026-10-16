<script>
	// The one Log form: carbs, the dose that was injected, or both, saved together as one event.
	// Built for one hand: the carbs field has focus on open (tap the insulin field for a dose only),
	// Log sits under both fields, so it stays above the keyboard; everything else is pre-filled and sits
	// in a row of quiet buttons under it. The phone's back gesture closes the sheet (+page.svelte).
	// For a new rapid dose logged "now", the API's suggestion is recomputed as carbs are typed and, since
	// 2026-10-16, fills the insulin field (carbs plus correction, or a correction alone): the field follows it
	// until you type in it,
	// and Log saves what the field shows (Log, with the amount on its label, is the confirm tap). One line
	// under the field repeats the suggestion; "Why" opens every term of the formula; "Use" puts it back after
	// you changed the field. Nothing here computes a dose: the numbers come from the tested calculator module
	// on the server. The suggestion is stored with the dose, so suggested and injected can be compared later.
	// The racion size comes from the API; nothing in this file knows how many grams a racion is.
	import { flushSync } from 'svelte';
	import { api, ApiError, clock, Unauthorized } from './api.js';
	import { isoDay, parseAmount, parseClock, timeProblem } from './entry.js';
	import FoodSearch from './FoodSearch.svelte';
	import FoodSheet from './FoodSheet.svelte';

	/**
	 * @type {{
	 *   info: { last_insulin_name: { rapid: string | null, basal: string | null }, last_carb_entry: 'raciones' | 'grams' | null,
	 *           racion_g: number | null, doses: { t: number, units: number, kind: string }[] } | null,
	 *   edit?: { t: number, carbs: any, dose: any } | null,
	 *   onSaved: (result: { carbs: any, dose: any }) => void,
	 *   onDeleted?: () => void,
	 *   onClose: () => void,
	 *   onUnauthorized: () => void
	 * }}
	 */
	// `edit` set: the form opens on one logged event (its carbs and/or dose) to change or delete it.
	let { info, edit = null, onSaved, onDeleted = () => {}, onClose, onUnauthorized } = $props();
	const initial = edit; // the event as it was when the form opened

	// A dose of the same kind this close to the new one is probably logged twice.
	const DUPLICATE_WINDOW_MIN = 15;
	// Typo guards, same as the API.
	const MAX_CARBS = { raciones: 100, grams: 1000 };
	const MAX_FOOD_GRAMS = 5000;
	const MAX_UNITS = 100;

	const c0 = initial?.carbs ?? null;
	const d0 = initial?.dose ?? null;
	const note0 = c0?.description ?? d0?.note ?? '';

	/** @type {'raciones' | 'grams' | 'meal'} */
	let carbMode = $state(c0?.entry ?? info?.last_carb_entry ?? 'raciones');
	let carbText = $state(c0 && c0.entry !== 'meal' ? String(Number(c0.entry === 'raciones' ? c0.raciones : c0.grams)) : '');

	// Foods mode: a meal built from foods. Each item: the food (name, values per 100 g) and its typed weight.
	/** @type {{ food: { id: number, name: string, per100: Record<string, number | null> }, gramsText: string }[]} */
	let items = $state(
		c0?.meal
			? c0.meal.items.map((/** @type {any} */ i) => ({
					food: { id: i.food_id, name: i.name, source: i.source, per100: i.per100 },
					gramsText: String(i.grams)
				}))
			: []
	);
	// Naming a food from a meal item: which item, and the name being typed.
	let renaming = $state(-1);
	let renameText = $state('');
	/** @type {any} */
	let foodSearch = $state(null);
	// A scanned barcode with no usable food: "New food" opens on top, with the barcode and OFF's values.
	/** @type {any} */
	let scanned = $state(null);
	/** @type {HTMLInputElement[]} */
	let gramInputs = $state([]);
	/** @type {'rapid' | 'basal'} */
	let kind = $state(d0?.kind ?? 'rapid');
	let unitsText = $state(d0 ? String(Number(d0.units)) : '');
	let nameEdited = $state(d0 !== null);
	let nameText = $state(d0?.insulin_name ?? '');
	let customTime = $state(initial !== null);
	// The time as a day (from a short list) and a 24 h clock typed as text: <input type="datetime-local">
	// shows 12 h and month-first dates in an English browser, whatever the page asks for.
	const t0 = initial ? new Date(initial.t * 1000) : null;
	let dayText = $state(t0 ? isoDay(t0) : '');
	let clockText = $state(t0 ? `${String(t0.getHours()).padStart(2, '0')}:${String(t0.getMinutes()).padStart(2, '0')}` : '');
	/** The last 7 days, and the entry's own day when it is older. */
	const dayOptions = (() => {
		const out = [];
		for (let i = 0; i < 7; i++) {
			const d = new Date();
			d.setDate(d.getDate() - i);
			out.push({
				value: isoDay(d),
				label: i === 0 ? 'Today' : i === 1 ? 'Yesterday' : d.toLocaleDateString('en-GB', { weekday: 'short', day: 'numeric', month: 'short' })
			});
		}
		if (t0 && !out.some((o) => o.value === isoDay(t0)))
			out.push({ value: isoDay(t0), label: t0.toLocaleDateString('en-GB', { day: 'numeric', month: 'short', year: 'numeric' }) });
		return out;
	})();
	let noteOpen = $state(note0 !== '');
	let note = $state(note0);
	let error = $state('');
	let busy = $state(false);
	let confirmDuplicate = $state(false);
	let confirmDelete = $state(false);

	// Calculator: the latest suggestion and the carbs it was computed for.
	/** @type {any} */
	let calc = $state(null);
	let calcFor = $state('');
	let calcNote = $state('');
	let calcSeq = 0;
	let calcPending = $state(false);
	const CALC_DEBOUNCE_MS = 300;
	// The insulin field follows the suggestion until you type in it (a new entry only; an edit keeps its dose).
	let unitsAuto = $state(!initial);
	// The breakdown behind the suggestion: closed by default, every term one tap away.
	let whyOpen = $state(false);

	/** @type {HTMLInputElement} */
	let carbInput;
	/** @type {HTMLInputElement} */
	let unitsInput;

	/** Called synchronously from the tap that opened the sheet, so iOS shows the keyboard. */
	export function focusAmount() {
		if (carbMode === 'meal') foodSearch?.focus();
		else carbInput?.focus();
	}

	// Meal: every item needs a valid weight and a known carb value (unknown carbs never count as 0).
	const mealRows = $derived(
		items.map((it) => {
			const g = parseAmount(it.gramsText, MAX_FOOD_GRAMS);
			const per = it.food.per100?.carbs;
			return { g, carbs: g !== null && per !== null && per !== undefined ? (g * per) / 100 : null };
		})
	);
	const mealProblem = $derived.by(() => {
		if (carbMode !== 'meal') return '';
		const noValue = items.find((it) => it.food.per100?.carbs === null || it.food.per100?.carbs === undefined);
		if (noValue) return `${noValue.food.name} has no carb value: remove it, or enter the carbs in grams.`;
		if (mealRows.some((r, i) => r.g === null && items[i].gramsText.trim() !== '')) return 'Check the weights: grams, at most one decimal.';
		return '';
	});
	const mealReady = $derived(carbMode === 'meal' && items.length > 0 && !mealProblem && mealRows.every((r) => r.g !== null));
	const mealTotals = $derived.by(() => {
		/** @type {Record<string, number>} */
		const t = {};
		/** @type {Set<string>} */
		const missing = new Set();
		for (const n of ['carbs', 'kcal', 'protein', 'fat', 'fibre']) {
			t[n] = 0;
			items.forEach((it, i) => {
				const per = it.food.per100?.[n];
				const g = mealRows[i]?.g;
				if (per === null || per === undefined) missing.add(n);
				else if (g !== null && g !== undefined) t[n] += (g * per) / 100;
			});
		}
		return { t, missing };
	});

	// Carbs of this entry in its own unit (raciones or grams); for a meal, its total grams of carbs.
	const carbs = $derived(
		carbMode === 'meal' ? (mealReady ? Math.round(mealTotals.t.carbs * 10) / 10 : null) : parseAmount(carbText, MAX_CARBS[carbMode])
	);
	const units = $derived(parseAmount(unitsText, MAX_UNITS));
	const carbBad = $derived(carbMode === 'meal' ? mealProblem !== '' : carbText.trim() !== '' && carbs === null);
	const unitsBad = $derived(unitsText.trim() !== '' && units === null);

	const lastName = $derived(info?.last_insulin_name?.[kind] ?? null);
	const name = $derived((nameEdited ? nameText : (lastName ?? nameText)).trim());
	const showNameInput = $derived(nameEdited || !lastName);
	const needName = $derived(units !== null && name.length === 0);

	const when = $derived(customTime && parseClock(clockText) ? new Date(`${dayText}T${parseClock(clockText)}`) : null);
	const timeIssue = $derived(customTime ? timeProblem(when) : '');

	/** At most 2 decimals, no trailing zeros. @param {number} v */
	const num = (v) => String(Math.round(v * 100) / 100);
	/** One decimal, for meal totals. @param {number} v */
	const one = (v) => String(Math.round(v * 10) / 10);
	const racionG = $derived(info?.racion_g ?? null);
	// Raciones need the racion size from Settings > Insulin. The info is loaded: none saved yet.
	const noRacion = $derived(info !== null && !racionG && carbMode === 'raciones' && carbs !== null);
	const conversion = $derived.by(() => {
		if (carbs === null || !racionG || carbMode === 'meal') return '';
		return carbMode === 'raciones' ? `= ${num(carbs * racionG)} g carbs` : `= ${num(carbs / racionG)} raciones`;
	});

	const duplicate = $derived.by(() => {
		if (units === null) return null;
		const t = (when ?? new Date()).getTime() / 1000;
		return (
			(info?.doses ?? []).find(
				(d) => d.id !== d0?.id && d.kind === kind && Math.abs(d.t - t) <= DUPLICATE_WINDOW_MIN * 60
			) ?? null
		);
	});

	// Only for a new entry, a rapid dose, logged now (a suggestion for a past moment would mislead).
	const showCalc = $derived(!initial && kind === 'rapid' && !customTime);
	const carbsKey = $derived(carbs === null ? 'none' : `${carbMode}:${carbs}`);
	const calcFresh = $derived(calc !== null && calcFor === carbsKey);

	$effect(() => {
		if (!showCalc || carbBad || noRacion) {
			calcPending = false;
			return;
		}
		const key = carbsKey;
		// A meal goes to the calculator as its grams of carbs.
		const body = carbs === null || carbs === 0 ? {} : { [carbMode === 'meal' ? 'grams' : carbMode]: carbs };
		const seq = ++calcSeq;
		calcPending = true;
		const timer = setTimeout(async () => {
			try {
				const r = await api('/api/calculate', { method: 'POST', body });
				if (seq !== calcSeq) return; // a newer request is on its way
				calc = r.available ? r.result : null;
				calcNote = r.available ? '' : r.reason;
				calcFor = key;
				calcPending = false;
			} catch (e) {
				if (e instanceof Unauthorized) return onUnauthorized();
				if (seq === calcSeq) {
					calc = null;
					calcNote = 'Calculator unavailable right now.';
					calcPending = false;
				}
			}
		}, CALC_DEBOUNCE_MS);
		return () => clearTimeout(timer);
	});

	// Fill the insulin field with the suggestion for what is on screen: the carbs part plus the correction, or
	// the correction alone with no carbs (2026-10-16). A low or nothing to cover (no dose suggested),
	// basal or a past time empty the field while it is still following.
	$effect(() => {
		if (!unitsAuto) return;
		if (!showCalc || carbBad || noRacion) {
			unitsText = '';
			return;
		}
		if (calcFresh) unitsText = calc.suggested_units > 0 ? String(calc.suggested_units) : '';
		else if (!calcPending && !calc) unitsText = ''; // calculator unavailable: nothing to fill
	});

	const fmt = (/** @type {number} */ v) => String(Math.round(v * 100) / 100);
	const TREND = { 1: 'falling fast', 2: 'falling', 3: 'stable', 4: 'rising', 5: 'rising fast' };

	function useSuggestion() {
		if (!calc || calc.suggested_units === null) return;
		unitsAuto = true; // follows the suggestion again
		unitsText = String(calc.suggested_units);
		confirmDuplicate = false;
	}

	// The quiet row under Log: what is already filled in, each a button that opens it to change.
	const metaParts = $derived([
		...(units !== null && !showNameInput ? [{ text: lastName, open: editName, aria: `Insulin ${lastName}, change` }] : []),
		...(!customTime ? [{ text: 'Now', open: openTime, aria: 'Time: now, change' }] : []),
		...(!noteOpen ? [{ text: 'Note', open: () => (noteOpen = true), aria: 'Add a note' }] : [])
	]);

	const canSave = $derived(
		(carbs !== null || units !== null) && !carbBad && !noRacion && !unitsBad && !needName && !timeIssue && !busy &&
			// while the field follows the calculator, wait for the suggestion for these carbs
			!(unitsAuto && showCalc && calcPending)
	);

	const label = $derived.by(() => {
		if (busy) return 'Saving...';
		if (confirmDuplicate) return initial ? 'Save anyway' : 'Log anyway';
		const parts = [];
		if (carbs !== null)
			parts.push(
				carbMode === 'meal'
					? `meal (${num(carbs)} g carbs)`
					: carbMode === 'raciones'
						? `${carbs} ${carbs === 1 ? 'racion' : 'raciones'}`
						: `${carbs} g`
			);
		if (units !== null) parts.push(`${units} u${kind === 'basal' ? ' basal' : ''}`);
		const verb = initial ? 'Save' : 'Log';
		return parts.length ? `${verb} ${parts.join(' + ')}` : verb;
	});

	/** @param {'raciones' | 'grams' | 'meal'} m */
	function pickMode(m) {
		carbMode = m; // keep what was typed: switching usually means the wrong mode was picked first
		flushSync();
		focusAmount();
	}

	/** Add a food and put the cursor in its grams field (inside the tap, so iOS opens the keypad). @param {any} food */
	function addFood(food) {
		items.push({ food, gramsText: '' });
		flushSync();
		gramInputs[items.length - 1]?.focus();
	}

	/** @param {number} i */
	function removeItem(i) {
		items.splice(i, 1);
		gramInputs.splice(i, 1);
		renaming = -1;
	}

	/** @param {number} i */
	function startRename(i) {
		renaming = i;
		renameText = items[i].food.own_name ?? '';
	}

	/** Save (or clear, if empty) the food's own name; it is used from now on, past meals keep theirs. */
	async function saveRename() {
		const i = renaming;
		if (i < 0) return;
		try {
			const f = await api(`/api/foods/${items[i].food.id}/name`, { method: 'PUT', body: { own_name: renameText.trim() || null } });
			items[i].food = { ...items[i].food, name: f.name, own_name: f.own_name };
			renaming = -1;
		} catch (e) {
			if (e instanceof Unauthorized) return onUnauthorized();
			error = e instanceof ApiError && e.detail ? `Name not saved: ${e.detail}.` : 'Name not saved. Try again.';
		}
	}

	/** @param {'rapid' | 'basal'} k */
	function pickKind(k) {
		kind = k;
		confirmDuplicate = false;
		unitsInput?.focus();
	}

	function editName() {
		nameText = lastName ?? '';
		nameEdited = true;
	}

	function openTime() {
		const n = new Date();
		dayText = isoDay(n);
		clockText = `${String(n.getHours()).padStart(2, '0')}:${String(n.getMinutes()).padStart(2, '0')}`;
		customTime = true;
	}

	/** @param {SubmitEvent} event */
	async function save(event) {
		event.preventDefault();
		if (!canSave) return;
		if (duplicate && !confirmDuplicate) {
			confirmDuplicate = true;
			return;
		}
		busy = true;
		error = '';
		const text = note.trim() || null;
		const parts = {
			...(carbs !== null
				? {
						carbs:
							carbMode === 'meal'
								? { meal: { items: items.map((it, i) => ({ food_id: it.food.id, grams: mealRows[i].g })) }, description: text }
								: { [carbMode]: carbs, description: text }
					}
				: {}),
			...(units !== null
				? {
						dose: {
							units,
							kind,
							insulin_name: name,
							note: text,
							// Keep the suggestion that was on screen for these carbs, used or not.
							...(showCalc && calcFresh ? { calc } : {})
						}
					}
				: {})
		};
		try {
			const result = initial
				? await api('/api/entries', {
						method: 'PUT',
						// Ids of what the event had: a part left empty is removed, a new one is added.
						body: { ts: when?.toISOString(), carbs_id: c0?.id ?? null, dose_id: d0?.id ?? null, ...parts }
					})
				: await api('/api/entries', { method: 'POST', body: { ...(when ? { ts: when.toISOString() } : {}), ...parts } });
			onSaved(result);
		} catch (e) {
			if (e instanceof Unauthorized) return onUnauthorized();
			error = 'Nothing saved. Check the values and try again.';
		} finally {
			busy = false;
		}
	}

	async function remove() {
		if (!confirmDelete) {
			confirmDelete = true;
			return;
		}
		busy = true;
		error = '';
		const q = [c0 ? `carbs_id=${c0.id}` : '', d0 ? `dose_id=${d0.id}` : ''].filter(Boolean).join('&');
		try {
			await api(`/api/entries?${q}`, { method: 'DELETE' });
			onDeleted();
		} catch (e) {
			if (e instanceof Unauthorized) return onUnauthorized();
			error = 'Not deleted. Try again in a moment.';
		} finally {
			busy = false;
		}
	}

	/** @param {KeyboardEvent} event */
	function onKey(event) {
		if (event.key === 'Escape') onClose();
	}
</script>

<svelte:window onkeydown={onKey} />

<div class="sheet" role="dialog" aria-modal="true" aria-labelledby="log-title">
	<form onsubmit={save} novalidate>
		<header class="top">
			<h2 id="log-title">{initial ? 'Edit entry' : 'Log'}</h2>
			<button type="button" class="link" onclick={onClose}>Cancel</button>
		</header>

		<section class="block" aria-labelledby="carbs-label">
			<div class="block-head">
				<span class="block-label" id="carbs-label">Carbs</span>
				<div class="seg" role="group" aria-label="Carbs as">
					<button type="button" aria-pressed={carbMode === 'raciones'} onclick={() => pickMode('raciones')}>Raciones</button>
					<button type="button" aria-pressed={carbMode === 'grams'} onclick={() => pickMode('grams')}>Grams</button>
					<button type="button" aria-pressed={carbMode === 'meal'} onclick={() => pickMode('meal')}>Foods</button>
				</div>
			</div>

			{#if carbMode === 'meal'}
				{#if items.length}
					<ul class="items">
						{#each items as it, i (i)}
							<li>
								{#if renaming === i}
									<span class="rename">
										<input
											bind:value={renameText}
											maxlength="60"
											autocomplete="off"
											placeholder="Your name for it"
											aria-label="Your name for {it.food.source_name ?? it.food.name}"
											onkeydown={(e) => {
												if (e.key === 'Enter') {
													e.preventDefault();
													saveRename();
												}
											}}
										/>
										<button type="button" class="link" onclick={saveRename}>Save</button>
										<button type="button" class="link" onclick={() => (renaming = -1)}>Cancel</button>
									</span>
								{:else}
									<span class="food-name">{it.food.name}</span>
								{/if}
								<label class="food-grams">
									<span class="visually-hidden">Grams of {it.food.name}</span>
									<input
										bind:this={gramInputs[i]}
										bind:value={it.gramsText}
										inputmode="decimal"
										enterkeyhint="done"
										autocomplete="off"
										aria-invalid={it.gramsText.trim() !== '' && mealRows[i]?.g === null}
										placeholder={it.food.last_grams ? String(it.food.last_grams) : ''}
									/>
									<span>g</span>
								</label>
								<span class="food-carbs">
									{#if it.food.per100?.carbs === null || it.food.per100?.carbs === undefined}
										no carb value
									{:else if mealRows[i]?.carbs !== null}
										{one(mealRows[i].carbs)} g carbs
									{:else}
										{num(it.food.per100.carbs)} g per 100 g
									{/if}
								</span>
								{#if renaming !== i}
									<span class="food-actions">
										{#if !it.food.source || it.food.source === 'ciqual'}
											<button type="button" class="link" onclick={() => startRename(i)}>{it.food.own_name ? 'Rename' : 'Name it'}</button>
										{/if}
										<button type="button" class="link" onclick={() => removeItem(i)}>Remove</button>
									</span>
								{/if}
							</li>
						{/each}
					</ul>
				{/if}

				<FoodSearch
					bind:this={foodSearch}
					onPick={addFood}
					{onUnauthorized}
					exclude={items.map((it) => it.food.id)}
					placeholder={items.length ? 'Add another food' : 'Search foods'}
					onMissing={(info) => (scanned = info)}
				/>

				{#if items.length}
					<p class="totals">
						<strong>{one(mealTotals.t.carbs)} g carbs</strong>{racionG ? ` = ${num(mealTotals.t.carbs / racionG)} raciones` : ''}
						<span class="muted-line">
							{Math.round(mealTotals.t.kcal)}{mealTotals.missing.has('kcal') ? '+' : ''} kcal,
							protein {one(mealTotals.t.protein)}{mealTotals.missing.has('protein') ? '+' : ''} g,
							fat {one(mealTotals.t.fat)}{mealTotals.missing.has('fat') ? '+' : ''} g,
							fibre {one(mealTotals.t.fibre)}{mealTotals.missing.has('fibre') ? '+' : ''} g
						</span>
						{#if ['kcal', 'protein', 'fat', 'fibre'].some((n) => mealTotals.missing.has(n))}
							<span class="muted-line">+ at least: some foods lack that value</span>
						{/if}
					</p>
				{/if}
				{#if mealProblem}
					<p class="error">{mealProblem}</p>
				{/if}
			{:else}
				<label class="amount">
					<span class="visually-hidden">{carbMode === 'raciones' ? 'Raciones' : 'Grams of carbs'}</span>
					<input
						bind:this={carbInput}
						bind:value={carbText}
						inputmode="decimal"
						enterkeyhint="next"
						autocomplete="off"
						aria-invalid={carbBad}
					/>
					<span class="unit">{carbMode === 'raciones' ? 'raciones' : 'g'}</span>
				</label>
				{#if noRacion}
					<p class="error">Set the racion size in Settings first, or switch to grams.</p>
				{:else if conversion}
					<p class="hint">{conversion}</p>
				{/if}
			{/if}
		</section>

		<section class="block" aria-labelledby="insulin-label">
			<div class="block-head">
				<span class="block-label" id="insulin-label">Insulin</span>
				<div class="seg" role="group" aria-label="Insulin kind">
					<button type="button" aria-pressed={kind === 'rapid'} onclick={() => pickKind('rapid')}>Rapid</button>
					<button type="button" aria-pressed={kind === 'basal'} onclick={() => pickKind('basal')}>Basal</button>
				</div>
			</div>
			<label class="amount">
				<span class="visually-hidden">Units injected</span>
				<input
					bind:this={unitsInput}
					bind:value={unitsText}
					oninput={() => {
						confirmDuplicate = false;
						unitsAuto = false; // typed by hand: the field is yours now
					}}
					inputmode="decimal"
					enterkeyhint="done"
					autocomplete="off"
					aria-invalid={unitsBad}
				/>
				<span class="unit">u</span>
			</label>

			{#if showCalc}
				{#if !calc && calcNote}
					<p class="hint">{calcNote}</p>
				{:else if calc}
					<div class="suggest" class:stale={!calcFresh} aria-live="polite">
						<span>
							{#if calc.suggested_units === null}
								No dose suggested
							{:else}
								Suggested <strong>{fmt(calc.suggested_units)} u</strong>
							{/if}
						</span>
						{#if calc.suggested_units !== null && calc.suggested_units > 0 && calcFresh && units !== calc.suggested_units}
							<button type="button" class="use" onclick={useSuggestion}>Use</button>
						{/if}
						<button type="button" class="link why" aria-expanded={whyOpen} onclick={() => (whyOpen = !whyOpen)}>
							{whyOpen ? 'Hide' : 'Why'}
						</button>
					</div>
					{#each calc.warnings as w}
						<p class="caution">{w}</p>
					{/each}
					{#if whyOpen}
						<div class="breakdown">
							<div class="terms">
								<span>Carbs</span>
								<span class="f">{calc.raciones === null ? 'none' : `${fmt(calc.raciones)} raciones x ${fmt(calc.ratio)} u`}</span>
								<span class="v">{fmt(calc.carbs_units)} u</span>

								<span>Correction</span>
								<span class="f">
									{#if calc.correction_units === null}
										no recent reading
									{:else if calc.glucose.mg_dl > calc.correct_above}
										({calc.glucose.mg_dl} - {calc.correct_to}) / {fmt(calc.isf)}
									{:else}
										not above {calc.correct_above}
									{/if}
								</span>
								<span class="v">{calc.correction_units === null ? '-' : `${fmt(calc.correction_units)} u`}</span>

								<span>Active insulin</span>
								<span class="f">{fmt(calc.iob_units)} u, only off the correction</span>
								<span class="v">{calc.iob_used > 0 ? `-${fmt(calc.iob_used)}` : '0'} u</span>

								<span class="sum">Total</span>
								<span class="f sum">{fmt(calc.total_units)} u, down to {fmt(calc.pen_increment)} u steps</span>
								<span class="v sum">{calc.suggested_units === null ? '-' : `${fmt(calc.suggested_units)} u`}</span>
							</div>
							<p class="hint">
								{#if calc.glucose}
									Glucose {calc.glucose.mg_dl} mg/dL, {calc.glucose.age_min} min ago{calc.glucose.trend ? `, ${TREND[calc.glucose.trend]}` : ''}.
								{/if}
								{calc.slot} ratio, settings v{calc.settings_version}.
							</p>
							{#if calc.notes?.length}
								<ul>
									{#each calc.notes as n}<li>{n}</li>{/each}
								</ul>
							{/if}
						</div>
					{/if}
				{/if}
			{/if}
		</section>

		{#if confirmDuplicate && duplicate}
			<p class="duplicate" role="alert">
				{duplicate.units} u {duplicate.kind} already logged at {clock(duplicate.t)}. Tap again to log this one too.
			</p>
		{/if}

		<button type="submit" class="save" disabled={!canSave}>{label}</button>

		{#if error}
			<p class="error" role="alert">{error}</p>
		{/if}

		{#if metaParts.length}
			<div class="meta-line">
				{#each metaParts as part}<button type="button" class="btn-quiet" onclick={part.open} aria-label={part.aria}>{part.text}</button>{/each}
			</div>
		{/if}

		{#if (showNameInput && (units !== null || nameEdited)) || customTime || noteOpen}
			<div class="fields">
				{#if showNameInput && (units !== null || nameEdited)}
					<label class="field-row">
						<span>Insulin</span>
						<input class="field" bind:value={nameText} placeholder="The pen's name" maxlength="40" autocomplete="off" aria-invalid={needName} />
					</label>
				{/if}
				{#if customTime}
					<label class="field-row">
						<span>Time</span>
						<span class="when-fields">
							<select class="field" bind:value={dayText} aria-label="Day">
								{#each dayOptions as o (o.value)}<option value={o.value}>{o.label}</option>{/each}
							</select>
							<input
								class="field clock"
								bind:value={clockText}
								inputmode="numeric"
								maxlength="5"
								placeholder="hh:mm"
								autocomplete="off"
								aria-label="Time, 24 h"
								aria-invalid={timeIssue !== ''}
								onblur={() => (clockText = parseClock(clockText) ?? clockText)}
							/>
						</span>
					</label>
				{/if}
				{#if noteOpen}
					<label class="field-row">
						<span>Note</span>
						<input class="field" bind:value={note} maxlength="200" autocomplete="off" placeholder="For example: pasta, correction" />
					</label>
				{/if}
			</div>
		{/if}
		{#if needName}
			<p class="error" role="alert">Type the insulin name once; next time it is filled in.</p>
		{/if}
		{#if timeIssue}
			<p class="error" role="alert">{timeIssue}</p>
		{/if}

		{#if initial}
			<button type="button" class="delete" onclick={remove} disabled={busy}>
				{confirmDelete ? 'Tap again to delete this entry' : 'Delete entry'}
			</button>
		{/if}
	</form>
</div>

{#if scanned}
	<FoodSheet
		kind="own"
		scan={scanned}
		onSaved={(food) => {
			scanned = null;
			addFood(food);
		}}
		onDeleted={() => (scanned = null)}
		onClose={() => (scanned = null)}
		{onUnauthorized}
	/>
{/if}
