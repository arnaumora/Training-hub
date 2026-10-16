<script>
	// One food, full screen, in the same style as the Log form:
	//   own     name and values per 100 g, typed from a label (carbs required, the rest optional)
	//   recipe  name, ingredients with their weights, and the weight when done; values per 100 g previewed here
	//           and computed by the API on save
	//   ciqual, off  read-only values (CIQUAL, or a product from Open Food Facts); only your own name can change
	// Own foods and recipes can be deleted: they disappear from search and lists, past meals keep them.
	// scan: a new own food opened from a scan, with the barcode and whatever Open Food Facts had.
	import { flushSync } from 'svelte';
	import { api, ApiError, Unauthorized } from './api.js';
	import FoodSearch from './FoodSearch.svelte';
	import { LABELS, NUTRIENTS, parseGrams, parseValue, recipePer100, show, UNITS, valuesProblem } from './foodmath.js';

	/**
	 * @type {{
	 *   kind: 'own' | 'recipe' | 'ciqual' | 'off',
	 *   foodId?: number | null,
	 *   scan?: { barcode: string, reason?: string, prefill?: { name: string, per100: Record<string, number | null> } | null } | null,
	 *   onSaved: (food: any, created: boolean) => void,
	 *   onDeleted: (food: any) => void,
	 *   onClose: () => void,
	 *   onUnauthorized: () => void
	 * }}
	 */
	let { kind, foodId = null, scan = null, onSaved, onDeleted, onClose, onUnauthorized } = $props();
	const readOnly = kind === 'ciqual' || kind === 'off';

	const MAX_GRAMS = 20000;
	const MAX_BATCH = 50000;

	/** The food as loaded (null for a new one). @type {any} */
	let food = $state(null);
	let loading = $state(foodId !== null);
	let loadError = $state('');
	let name = $state(scan?.prefill?.name ?? '');
	/** @type {Record<string, string>} */
	let valueText = $state(
		Object.fromEntries(NUTRIENTS.map((n) => [n, scan?.prefill?.per100?.[n] == null ? '' : String(scan.prefill.per100[n])]))
	);
	/** @type {{ food: any, gramsText: string }[]} */
	let items = $state([]);
	let batchText = $state('');
	let ownName = $state('');
	let error = $state('');
	let busy = $state(false);
	let confirmDelete = $state(false);

	/** @type {HTMLInputElement} */
	let nameInput;
	/** @type {HTMLInputElement[]} */
	let gramInputs = $state([]);
	/** @type {any} */
	let foodSearch = $state(null);

	/** Called from the tap that opened the sheet, so iOS opens the keyboard on the name. */
	export function focusFirst() {
		nameInput?.focus();
	}

	$effect(() => {
		if (foodId === null) return;
		api(`/api/foods/${foodId}`)
			.then((f) => {
				food = f;
				name = f.source === 'ciqual' || f.source === 'off' ? f.source_name : f.name;
				ownName = f.own_name ?? '';
				for (const n of NUTRIENTS) valueText[n] = f.per100[n] === null ? '' : String(f.per100[n]);
				if (f.recipe) {
					items = f.recipe.items.map((/** @type {any} */ i) => ({
						food: { id: i.food_id, name: i.name, source: i.source, per100: i.per100 },
						gramsText: String(i.grams)
					}));
					batchText = f.recipe.batch_g === null ? '' : String(f.recipe.batch_g);
				}
			})
			.catch((e) => {
				if (e instanceof Unauthorized) return onUnauthorized();
				loadError = 'Could not load this food. Try again in a moment.';
			})
			.finally(() => (loading = false));
	});

	const nameOk = $derived(name.trim().length > 0 && name.trim().length <= 60);

	// Own food: the typed values.
	const values = $derived(Object.fromEntries(NUTRIENTS.map((n) => [n, parseValue(valueText[n], n === 'kcal' ? 900 : 100)])));
	const badFields = $derived(NUTRIENTS.filter((n) => values[n] === undefined));
	const ownProblem = $derived.by(() => {
		if (badFields.length) return `Check ${badFields.map((n) => LABELS[n].replace('of which ', '')).join(', ')}: a number, at most 2 decimals.`;
		if (values.carbs === null) return '';
		return valuesProblem(/** @type {Record<string, number | null>} */ (values));
	});

	// Recipe: ingredients, weight when done, preview.
	const grams = $derived(items.map((it) => parseGrams(it.gramsText, MAX_GRAMS)));
	const itemsG = $derived(grams.reduce((/** @type {number} */ s, g) => s + (g ?? 0), 0));
	const batch = $derived(parseGrams(batchText, MAX_BATCH));
	const batchBad = $derived(batchText.trim() !== '' && batch === null);
	const recipeReady = $derived(items.length > 0 && grams.every((g) => g !== null) && !batchBad);
	const preview = $derived(
		recipeReady
			? recipePer100(
					items.map((it, i) => ({ grams: /** @type {number} */ (grams[i]), per100: it.food.per100 })),
					batch
				)
			: null
	);
	const recipeProblem = $derived.by(() => {
		if (items.some((it, i) => grams[i] === null && it.gramsText.trim() !== '')) return 'Check the weights: grams, at most one decimal.';
		if (batchBad) return 'Check the weight when done: grams, at most one decimal.';
		if (preview && valuesProblem(preview)) return 'That is more than 100 g of nutrients in 100 g: the weight when done looks too low.';
		return '';
	});
	// Not an error: a pot usually loses or gains some water, but not half its weight.
	const batchOdd = $derived(batch !== null && itemsG > 0 && (batch < itemsG * 0.5 || batch > itemsG * 2));
	const recipeCarbs = $derived(
		recipeReady ? items.reduce((s, it, i) => s + (/** @type {number} */ (grams[i]) * (it.food.per100?.carbs ?? 0)) / 100, 0) : null
	);

	const canSave = $derived.by(() => {
		if (busy || loading) return false;
		if (readOnly) return food !== null && ownName.trim() !== (food.own_name ?? '');
		if (!nameOk) return false;
		if (kind === 'own') return values.carbs !== null && values.carbs !== undefined && !ownProblem;
		return recipeReady && !recipeProblem;
	});

	const title = $derived(
		readOnly ? (food?.source_name ?? 'Food') : food ? name || food.name : kind === 'own' ? 'New food' : 'New recipe'
	);

	/** @param {any} f */
	function addIngredient(f) {
		items.push({ food: f, gramsText: '' });
		flushSync();
		gramInputs[items.length - 1]?.focus();
	}

	/** @param {number} i */
	function removeIngredient(i) {
		items.splice(i, 1);
		gramInputs.splice(i, 1);
	}

	/** @param {SubmitEvent} event */
	async function save(event) {
		event.preventDefault();
		if (!canSave) return;
		busy = true;
		error = '';
		try {
			let saved;
			if (readOnly) {
				saved = await api(`/api/foods/${foodId}/name`, { method: 'PUT', body: { own_name: ownName.trim() || null } });
			} else if (kind === 'own') {
				const per100 = Object.fromEntries(NUTRIENTS.map((n) => [n, values[n]]));
				const body = { name: name.trim(), per100, ...(scan && !food ? { barcode: scan.barcode } : {}) };
				saved = food
					? await api(`/api/own-foods/${food.id}`, { method: 'PUT', body })
					: await api('/api/own-foods', { method: 'POST', body });
			} else {
				const body = {
					name: name.trim(),
					items: items.map((it, i) => ({ food_id: it.food.id, grams: grams[i] })),
					batch_g: batch
				};
				saved = food
					? await api(`/api/recipes/${food.id}`, { method: 'PUT', body })
					: await api('/api/recipes', { method: 'POST', body });
			}
			onSaved(saved, food === null);
		} catch (e) {
			if (e instanceof Unauthorized) return onUnauthorized();
			// The API explains what it refused (for example an ingredient without a carb value).
			error = e instanceof ApiError && e.detail ? `Not saved: ${e.detail}.` : 'Not saved. Try again in a moment.';
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
		try {
			await api(`/api/foods/${food.id}`, { method: 'DELETE' });
			onDeleted(food);
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

<div class="sheet" role="dialog" aria-modal="true" aria-labelledby="food-title">
	<form onsubmit={save} novalidate>
		<header class="top">
			<h2 id="food-title">{title}</h2>
			<button type="button" class="link" onclick={onClose}>Cancel</button>
		</header>

		{#if loadError}
			<p class="error" role="alert">{loadError}</p>
		{:else if loading}
			<p class="hint">Loading...</p>
		{:else if readOnly}
			<label class="field-row">
				<span>Your name</span>
				<input class="field" bind:this={nameInput} bind:value={ownName} maxlength="60" autocomplete="off" placeholder="For example: macarrones" />
			</label>
			<p class="hint">Shown and found first. Empty removes it.</p>
			<section class="block" aria-labelledby="values-label">
				<span class="block-label" id="values-label">Per 100 g</span>
				<dl class="values">
					{#each NUTRIENTS as n}
						<dt class:sub={n === 'sugars'}>{LABELS[n]}</dt>
						<dd>{food.per100[n] === null ? '-' : `${show(food.per100[n])} ${UNITS[n] ?? 'g'}`}</dd>
					{/each}
				</dl>
				<p class="hint">
					{#if food.source === 'off'}
						Open Food Facts, barcode {food.barcode ?? food.source_name}, fetched {food.source_version.replace('off-', '')}. Crowd-sourced:
						check it once against the label.
					{:else}
						CIQUAL (ANSES){food.source_version ? `, release ${food.source_version.replace('ciqual-', '')}` : ''}.
					{/if}
				</p>
			</section>
		{:else}
			<label class="field-row">
				<span>Name</span>
				<input
					class="field"
					bind:this={nameInput}
					bind:value={name}
					maxlength="60"
					autocomplete="off"
					placeholder={kind === 'own' ? 'Tomate frito Mercadona' : 'Lentejas'}
				/>
			</label>

			{#if scan}
				<p class="hint">Barcode {scan.barcode}. {scan.reason ?? ''} Type the values from the label.</p>
			{:else if food?.barcode}
				<p class="hint">Barcode {food.barcode}.</p>
			{/if}

			{#if kind === 'own'}
				<section class="block" aria-labelledby="values-label">
					<span class="block-label" id="values-label">Per 100 g</span>
					<div class="value-fields">
						{#each NUTRIENTS as n}
							<label class="value-row" class:sub={n === 'sugars'}>
								<span>{LABELS[n]}</span>
								<input
									bind:value={valueText[n]}
									inputmode="decimal"
									autocomplete="off"
									aria-invalid={values[n] === undefined}
									aria-required={n === 'carbs'}
								/>
								<span class="unit">{UNITS[n] ?? 'g'}</span>
							</label>
						{/each}
					</div>
					<p class="hint">As on EU labels: carbs without fibre. Leave a value empty if the label does not give it.</p>
					{#if ownProblem}<p class="error">{ownProblem}</p>{/if}
				</section>
			{:else}
				<section class="block" aria-labelledby="ingredients-label">
					<span class="block-label" id="ingredients-label">Ingredients</span>
					{#if items.length}
						<ul class="items">
							{#each items as it, i (i)}
								<li>
									<span class="food-name">{it.food.name}</span>
									<label class="food-grams">
										<span class="visually-hidden">Grams of {it.food.name}</span>
										<input
											bind:this={gramInputs[i]}
											bind:value={it.gramsText}
											inputmode="decimal"
											enterkeyhint="done"
											autocomplete="off"
											aria-invalid={it.gramsText.trim() !== '' && grams[i] === null}
										/>
										<span>g</span>
									</label>
									<span class="food-carbs">
										{#if grams[i] !== null}
											{show(((grams[i] ?? 0) * (it.food.per100?.carbs ?? 0)) / 100)} g carbs
										{:else}
											{show(it.food.per100?.carbs, 2)} g per 100 g
										{/if}
									</span>
									<span class="food-actions">
										<button type="button" class="link" onclick={() => removeIngredient(i)}>Remove</button>
									</span>
								</li>
							{/each}
						</ul>
					{/if}
					<FoodSearch
						bind:this={foodSearch}
						onPick={addIngredient}
						{onUnauthorized}
						ingredients
						exclude={items.map((it) => it.food.id)}
						placeholder={items.length ? 'Add another ingredient' : 'Add an ingredient'}
					/>
				</section>

				<section class="block" aria-labelledby="batch-label">
					<label class="value-row">
						<span id="batch-label">Weight when done</span>
						<input bind:value={batchText} inputmode="decimal" autocomplete="off" aria-invalid={batchBad} placeholder={itemsG ? show(itemsG) : ''} />
						<span class="unit">g</span>
					</label>
					<p class="hint">Weigh the whole pot when done. Empty: the ingredients{itemsG ? `, ${show(itemsG)} g` : ''}.</p>
					{#if batchOdd}
						<p class="caution">That is very different from the ingredients ({show(itemsG)} g). Check it, or add the water as an ingredient.</p>
					{/if}
				</section>

				{#if preview}
					<section class="block" aria-labelledby="preview-label">
						<span class="block-label" id="preview-label">Per 100 g</span>
						<p class="totals">
							<strong>{show(preview.carbs, 2)} g carbs</strong>
							<span class="muted-line">
								{show(preview.kcal, 0)} kcal, protein {show(preview.protein)} g, fat {show(preview.fat)} g, fibre {show(preview.fibre)} g
							</span>
							<span class="muted-line">The whole recipe: {show(recipeCarbs)} g carbs in {show(batch ?? itemsG)} g.</span>
						</p>
					</section>
				{/if}
				{#if recipeProblem}<p class="error">{recipeProblem}</p>{/if}
			{/if}
		{/if}

		{#if !loading && !loadError}
			<button type="submit" class="save" disabled={!canSave}>
				{busy ? 'Saving...' : readOnly ? 'Save name' : kind === 'own' ? 'Save food' : 'Save recipe'}
			</button>
		{/if}
		{#if error}<p class="error" role="alert">{error}</p>{/if}

		{#if food && !readOnly}
			<button type="button" class="delete" onclick={remove} disabled={busy}>
				{confirmDelete ? 'Tap again to delete. Past meals keep it.' : kind === 'own' ? 'Delete food' : 'Delete recipe'}
			</button>
		{/if}
	</form>
</div>
