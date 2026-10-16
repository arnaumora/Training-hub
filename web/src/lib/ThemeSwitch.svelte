<script>
	// Light, dark, or the phone's setting. Kept on this device only (src/lib/theme.js).
	import { getTheme, setTheme } from './theme.js';

	const OPTIONS = /** @type {const} */ ([
		['system', 'Phone setting'],
		['light', 'Light'],
		['dark', 'Dark']
	]);
	let theme = $state(getTheme());
</script>

<div class="theme">
	<span id="theme-label">Theme</span>
	<div class="seg" role="group" aria-labelledby="theme-label">
		{#each OPTIONS as [value, label] (value)}
			<button
				type="button"
				aria-pressed={theme === value}
				onclick={() => {
					theme = value;
					setTheme(value);
				}}>{label}</button
			>
		{/each}
	</div>
</div>

<style>
	.theme {
		display: flex;
		align-items: center;
		justify-content: space-between;
		flex-wrap: wrap;
		gap: var(--s-2) var(--s-4);
		max-width: 44rem; /* the width of the settings below it */
		margin-bottom: var(--s-5);
	}
	.theme > span {
		color: var(--muted);
	}
</style>
