<script>
	// The last 24 h of the log, newest first. One row per Log tap (a meal and its dose share a row).
	// Tapping a row opens it in the Log form to change or delete it.
	import { clock } from './api.js';
	import { describe, groupEvents } from './events.js';

	/** @type {{ log: any, onEdit: (event: { t: number, carbs: any, dose: any }) => void, title?: string }} */
	let { log, onEdit, title = 'Last 24 hours' } = $props();
	// Rows older than today carry their day.
	const startOfToday = new Date().setHours(0, 0, 0, 0) / 1000;
	/** "Tue 13" @param {number} t */
	const dayOf = (t) => new Date(t * 1000).toLocaleDateString('en-GB', { weekday: 'short', day: 'numeric' });

	const events = $derived(groupEvents(log));
</script>

<section aria-labelledby="log-list-title">
	<h2 id="log-list-title">{title}</h2>
	{#if !log}
		<p class="muted">Loading...</p>
	{:else if events.length === 0}
		<p class="muted">Nothing logged in this period.</p>
	{:else}
		<ul>
			{#each events as e (e.key)}
				<li>
					<button onclick={() => onEdit(e)}>
						<span class="time">{e.t < startOfToday ? `${dayOf(e.t)}, ` : ''}{clock(e.t)}</span>
						<span class="what">
							{describe(e)}
							{#if e.carbs?.description || e.dose?.note}
								<span class="note">{e.carbs?.description ?? e.dose?.note}</span>
							{/if}
						</span>
					</button>
				</li>
			{/each}
		</ul>
	{/if}
</section>

<style>
	section {
		display: grid;
		gap: 0.4rem;
		margin-top: var(--s-6);
	}
	h2 {
		margin: 0;
		font-size: var(--fs-title);
		font-weight: 700;
	}
	.muted {
		margin: 0;
		color: var(--muted);
	}
	ul {
		list-style: none;
		margin: 0;
		padding: 0;
	}
	li + li {
		border-top: 1px solid var(--border);
	}
	button {
		display: grid;
		grid-template-columns: max-content minmax(0, 1fr);
		align-items: baseline;
		gap: 0.75rem;
		width: 100%;
		padding: 0.65rem 0;
		background: none;
		border: none;
		text-align: left;
		cursor: pointer;
	}
	@media (hover: hover) {
		button:hover .what {
			color: var(--accent);
		}
	}
	.time {
		color: var(--muted);
	}
	.what {
		display: grid;
		gap: 0.1rem;
	}
	.note {
		color: var(--muted);
		font-size: 0.85rem;
	}
</style>
