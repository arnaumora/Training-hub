<script>
	// Food search for the Log form (a meal's foods) and the recipe form (its ingredients).
	// Local API only, debounced, the newest answer wins. With an empty box it lists the foods eaten most often.
	// Used inside a .sheet, whose styles (.search, .results, .muted-line, .list-label) it relies on.
	import { api, Unauthorized } from './api.js';
	import BarcodeScan from './BarcodeScan.svelte';

	/**
	 * @type {{
	 *   onPick: (food: any) => void,
	 *   onUnauthorized: () => void,
	 *   exclude?: number[],
	 *   ingredients?: boolean,
	 *   placeholder?: string,
	 *   onMissing?: ((info: { barcode: string, prefill: any, reason: string }) => void) | null
	 * }}
	 */
	// exclude: ids not listed under "Your foods" (already picked). ingredients: recipes are left out.
	// onMissing: a scanned barcode with no usable food; the parent opens "New food" for it (see BarcodeScan).
	let { onPick, onUnauthorized, exclude = [], ingredients = false, placeholder = 'Search foods', onMissing = null } = $props();
	let scanning = $state(false);

	const DEBOUNCE_MS = 200;

	let query = $state('');
	/** @type {any[]} */
	let results = $state([]);
	/** @type {any[]} */
	let frequent = $state([]);
	let seq = 0;
	/** @type {HTMLInputElement} */
	let input;

	const frequentShown = $derived(frequent.filter((f) => !exclude.includes(f.id) && !(ingredients && f.source === 'recipe')));

	/** Called synchronously from a tap, so iOS opens the keyboard. */
	export function focus() {
		input?.focus();
	}

	/** At most 2 decimals, no trailing zeros. @param {number} v */
	const num = (v) => String(Math.round(v * 100) / 100);

	$effect(() => {
		api('/api/foods/frequent?limit=8')
			.then((r) => (frequent = r))
			.catch((e) => {
				if (e instanceof Unauthorized) onUnauthorized();
			});
	});

	$effect(() => {
		const q = query.trim();
		if (q.length < 2) {
			results = [];
			return;
		}
		const mine = ++seq;
		const timer = setTimeout(async () => {
			try {
				const r = await api(`/api/foods?q=${encodeURIComponent(q)}&limit=8${ingredients ? '&exclude_recipes=true' : ''}`);
				if (mine === seq) results = r;
			} catch (e) {
				if (e instanceof Unauthorized) onUnauthorized();
			}
		}, DEBOUNCE_MS);
		return () => clearTimeout(timer);
	});

	/** @param {any} food */
	function pick(food) {
		query = '';
		results = [];
		onPick(food);
	}
</script>

<div class="search">
	<div class="search-row">
		<input
			bind:this={input}
			bind:value={query}
			type="search"
			enterkeyhint="search"
			autocomplete="off"
			{placeholder}
			aria-label="Search foods"
		/>
		<button type="button" class="link" onclick={() => (scanning = true)}>Scan</button>
	</div>
	{#snippet foodList(/** @type {any[]} */ list)}
		<ul class="results">
			{#each list as f (f.id)}
				<li>
					<button type="button" onclick={() => pick(f)}>
						<span>{f.name}</span>
						<span class="muted-line">
							{f.per100.carbs === null ? 'no carb value' : `${num(f.per100.carbs)} g carbs per 100 g`}{f.source === 'recipe'
								? ', recipe'
								: ''}{f.uses ? `, last time ${f.last_grams} g` : ''}
						</span>
						{#if f.own_name}<span class="muted-line">{f.source_name}</span>{/if}
					</button>
				</li>
			{/each}
		</ul>
	{/snippet}
	{#if results.length}
		{@render foodList(results)}
	{:else if query.trim().length < 2 && frequentShown.length}
		<p class="list-label">Your foods</p>
		{@render foodList(frequentShown)}
	{:else if query.trim().length >= 2}
		<p class="hint">No match. Try fewer words, or add it in Food.</p>
	{/if}
</div>

{#if scanning}
	<BarcodeScan
		onFound={(f) => {
			scanning = false;
			pick(f);
		}}
		onMissing={onMissing
			? (info) => {
					scanning = false;
					onMissing(info);
				}
			: null}
		onClose={() => (scanning = false)}
		{onUnauthorized}
	/>
{/if}
