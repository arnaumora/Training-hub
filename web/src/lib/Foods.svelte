<script>
	// The Foods screen: your foods (own foods, recipes, foods you named or ate), a box that filters them and
	// also searches every other food, and the two ways to add one. Tapping a food opens it in FoodSheet.
	import { flushSync } from 'svelte';
	import { api, Unauthorized } from './api.js';
	import BarcodeScan from './BarcodeScan.svelte';
	import FoodSheet from './FoodSheet.svelte';
	import { normalize, show } from './foodmath.js';

	/** @type {{ onUnauthorized: () => void, onNotice: (text: string) => void }} */
	let { onUnauthorized, onNotice } = $props();

	const SEARCH_DEBOUNCE_MS = 200;
	const KIND = { own: 'own food', recipe: 'recipe', off: 'packaged' };

	/** @type {any[] | null} */
	let mine = $state(null);
	let loadError = $state('');
	let query = $state('');
	/** Matches outside your foods. @type {any[]} */
	let others = $state([]);
	let seq = 0;
	/** The open sheet: what kind of food, which one (null: a new one), and a scan it came from. @type {{ kind: 'own' | 'recipe' | 'ciqual' | 'off', id: number | null, scan?: any } | null} */
	let sheet = $state(null);
	let scanning = $state(false);
	/** @type {any} */
	let sheetRef = $state(null);

	async function load() {
		try {
			mine = await api('/api/foods/mine');
			loadError = '';
		} catch (e) {
			if (e instanceof Unauthorized) return onUnauthorized();
			loadError = 'Could not load your foods. Try again in a moment.';
		}
	}

	$effect(() => {
		load();
	});

	const q = $derived(normalize(query));
	const shown = $derived(
		(mine ?? []).filter((f) => {
			if (!q) return true;
			const hay = normalize(`${f.name} ${f.source_name ?? ''}`);
			return q.split(' ').every((w) => hay.includes(w));
		})
	);

	$effect(() => {
		const text = query.trim();
		if (text.length < 2) {
			others = [];
			return;
		}
		const mineSeq = ++seq;
		const timer = setTimeout(async () => {
			try {
				const r = await api(`/api/foods?q=${encodeURIComponent(text)}&limit=12`);
				if (mineSeq !== seq) return;
				const ids = new Set((mine ?? []).map((f) => f.id));
				others = r.filter((/** @type {any} */ f) => !ids.has(f.id));
			} catch (e) {
				if (e instanceof Unauthorized) onUnauthorized();
			}
		}, SEARCH_DEBOUNCE_MS);
		return () => clearTimeout(timer);
	});

	/** Open a sheet inside the tap, so iOS opens the keyboard on its first field. @param {'own' | 'recipe' | 'ciqual' | 'off'} kind @param {number | null} id */
	function open(kind, id) {
		sheet = { kind, id };
		if (id === null) {
			flushSync();
			sheetRef?.focusFirst();
		}
	}

	/** @param {any} food @param {boolean} created */
	function saved(food, created) {
		sheet = null;
		onNotice(created ? `Added ${food.name}.` : `Saved ${food.name}.`);
		load();
	}

	/** @param {any} food */
	function deleted(food) {
		sheet = null;
		onNotice(`Deleted ${food.name}.`);
		load();
	}
</script>

<section class="foods" aria-labelledby="foods-title">
	<h2 id="foods-title" class="visually-hidden">Foods</h2>
	<div class="btn-row">
		<button class="btn-quiet" onclick={() => (scanning = true)}>Scan</button>
		<button class="btn-quiet" onclick={() => open('own', null)}>New food</button>
		<button class="btn-quiet" onclick={() => open('recipe', null)}>New recipe</button>
	</div>

	<input class="find" bind:value={query} type="search" autocomplete="off" placeholder="Find a food" aria-label="Find a food" />

	{#if loadError}
		<p class="error" role="alert">{loadError}</p>
	{:else if mine === null}
		<p class="muted">Loading...</p>
	{:else}
		{#if mine.length === 0 && !q}
			<p class="muted">Nothing here yet. Foods you add, name or eat show up here.</p>
		{:else if shown.length === 0 && q}
			<p class="muted">None of your foods match.</p>
		{/if}

		{#snippet list(/** @type {any[]} */ foods)}
			<ul>
				{#each foods as f (f.id)}
					<li>
						<button onclick={() => open(f.source, f.id)}>
							<span class="name">
								{f.name}
								{#if f.own_name}<span class="sub">{f.source_name}</span>{/if}
							</span>
							<span class="kind">{KIND[f.source] ?? ''}</span>
							<span class="carbs">{f.per100.carbs === null ? '-' : `${show(f.per100.carbs)} g`}</span>
						</button>
					</li>
				{/each}
			</ul>
		{/snippet}

		{#if shown.length}
			<section class="sec" aria-labelledby="mine-title">
				<div class="list-head"><h3 id="mine-title">Your foods</h3><span>carbs / 100 g</span></div>
				{@render list(shown)}
			</section>
		{/if}
		{#if others.length}
			<section class="sec" aria-labelledby="others-title">
				<div class="list-head"><h3 id="others-title">Other foods</h3><span>carbs / 100 g</span></div>
				{@render list(others)}
			</section>
		{/if}
	{/if}
</section>

{#if scanning}
	<BarcodeScan
		onFound={(f) => {
			scanning = false;
			sheet = { kind: f.source, id: f.id };
			load();
		}}
		onMissing={(info) => {
			scanning = false;
			sheet = { kind: 'own', id: null, scan: info };
		}}
		onClose={() => (scanning = false)}
		{onUnauthorized}
	/>
{/if}

{#if sheet}
	{#key sheet}
		<FoodSheet
			bind:this={sheetRef}
			kind={sheet.kind}
			foodId={sheet.id}
			scan={sheet.scan ?? null}
			onSaved={saved}
			onDeleted={deleted}
			onClose={() => (sheet = null)}
			{onUnauthorized}
		/>
	{/key}
{/if}

<style>
	.foods {
		display: grid;
		gap: var(--s-4);
		max-width: 44rem;
	}
	h3 {
		margin: 0;
		font-size: var(--fs-title);
		font-weight: 700;
		color: var(--text);
	}
	.find {
		width: 100%;
		margin: 0;
		padding: 0.5rem 0;
		background: none;
		border: none;
		border-bottom: 2px solid var(--border);
		border-radius: 0;
		-webkit-appearance: none;
		appearance: none;
	}
	.find:focus {
		outline: none;
		border-bottom-color: var(--accent);
	}
	.find::-webkit-search-cancel-button {
		display: none;
	}
	.muted {
		margin: 0;
		color: var(--muted);
	}
	.error {
		margin: 0;
		color: var(--danger);
	}
	.list-head {
		display: flex;
		align-items: baseline;
		justify-content: space-between;
		gap: var(--s-3);
		color: var(--muted);
		font-size: var(--fs-caption);
	}
	ul {
		list-style: none;
		margin: 0;
		padding: 0;
	}
	li + li {
		border-top: 1px solid var(--border);
	}
	li button {
		display: grid;
		grid-template-columns: minmax(0, 1fr) auto 4.5rem;
		align-items: baseline;
		gap: 0.75rem;
		width: 100%;
		padding: 0.6rem 0;
		background: none;
		border: none;
		text-align: left;
		cursor: pointer;
	}
	@media (hover: hover) {
		li button:hover .name {
			color: var(--accent);
		}
	}
	.name {
		display: grid;
	}
	.sub {
		color: var(--muted);
		font-size: var(--fs-caption);
	}
	.kind {
		color: var(--muted);
		font-size: var(--fs-caption);
	}
	.carbs {
		text-align: right;
	}
</style>
