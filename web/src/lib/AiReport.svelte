<script>
	// An AI report (roadmap step 8), on a run or a week: the newest report, who wrote it and when, whether the
	// data it was written from changed since, and buttons to write it (again) with Claude or Gemini. While a report is being
	// written (by the analysis service, up to a minute) this polls until it is done.
	// The text is the model's, after the dose guard; it is rendered from a small Markdown subset, escaped.
	import { onDestroy } from 'svelte';
	import { api, ApiError, Unauthorized } from './api.js';
	import { markdown } from './markdown.js';

	/** path: the report endpoint, /api/activities/{id}/report or /api/weeks/{monday}/report.
	 * @type {{ path: string, title?: string, onUnauthorized: () => void }} */
	let { path, title = 'Analysis', onUnauthorized } = $props();

	const NAMES = { claude: 'Claude', gemini: 'Gemini' };
	const POLL_MS = 4000;

	/** @type {any} */
	let state = $state(null);
	let error = $state('');
	/** @type {ReturnType<typeof setTimeout> | undefined} */
	let timer;

	const open = $derived(state?.job && state.job.finished_at === null);
	const failed = $derived(state?.job && state.job.ok === false && (!state.report || state.job.requested_at > state.report.created_at));

	async function load() {
		clearTimeout(timer);
		try {
			state = await api(path);
			if (state.job && state.job.finished_at === null) timer = setTimeout(load, POLL_MS);
		} catch (e) {
			if (e instanceof Unauthorized) return onUnauthorized();
			error = 'Could not load the analysis.';
		}
	}

	$effect(() => {
		path;
		state = null;
		load();
	});
	onDestroy(() => clearTimeout(timer));

	/** @param {'claude' | 'gemini'} provider */
	async function write(provider) {
		error = '';
		try {
			await api(path, { method: 'POST', body: { provider } });
			load();
		} catch (e) {
			if (e instanceof Unauthorized) return onUnauthorized();
			error = e instanceof ApiError && e.detail ? `${e.detail[0].toUpperCase()}${e.detail.slice(1)}.` : 'Could not ask for the analysis.';
		}
	}

	/** "13 Oct, 18:02". @param {number} t */
	const at = (t) => new Date(t * 1000).toLocaleString('en-GB', { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit', hour12: false });
	/** A job's error without our "(trying again)" and without a final period, to sit inside a sentence. @param {string} e */
	const plain = (e) => (e ?? '').replace(' (trying again)', '').replace(/\.$/, '');
	/** "08:21". @param {number} t */
	const clock = (t) => new Date(t * 1000).toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit', hour12: false });
	/** "5.1k". @param {number} n */
	const k = (n) => (n >= 1000 ? `${(n / 1000).toFixed(1)}k` : `${n}`);
	const providers = /** @type {('claude' | 'gemini')[]} */ (['claude', 'gemini']);
</script>

<section class="report sec" aria-label={title}>
	<h3>{title}</h3>
	{#if !state}
		{#if error}<p class="error">{error}</p>{:else}<p class="muted">Loading...</p>{/if}
	{:else}
		{#if open}
			{@const who = NAMES[/** @type {'claude' | 'gemini'} */ (state.job.provider ?? state.default_provider)]}
			<p class="muted" role="status">
				{#if state.job.next_try_at && !state.job.started_at}
					{who} did not answer ({plain(state.job.error)}). The analysis service tries again at
					{clock(state.job.next_try_at)}: attempt {state.job.attempts + 1} of {state.max_attempts}.
				{:else if state.job.started_at}
					Writing the analysis with {who}. This takes up to a minute, a little more if {who} is busy.
				{:else}
					Waiting for the analysis service.
				{/if}
			</p>
		{:else if failed}
			<p class="muted">The last attempt failed: {plain(state.job.error)}.</p>
		{/if}

		{#if state.report}
			<p class="meta">
				{NAMES[/** @type {'claude' | 'gemini'} */ (state.report.provider)]} ({state.report.model}), {at(state.report.created_at)}.
				{k(state.report.input_tokens)} tokens in, {k(state.report.output_tokens)} out.
			</p>
			{#if state.report.stale}
				<p class="muted">Written before the latest changes to the data or the log. Write it again to include them.</p>
			{/if}
			{#if state.report.removed}
				<p class="muted">
					{state.report.removed === 1 ? '1 sentence was' : `${state.report.removed} sentences were`} removed because {state.report.removed === 1
						? 'it'
						: 'they'} suggested an insulin change.
				</p>
			{/if}
			<div class="text">{@html markdown(state.report.text)}</div>
		{:else if !open}
			<p class="muted">Nothing written yet.</p>
		{/if}

		{#if !state.service.running}
			<p class="muted small">The analysis service is not running, so nothing can be written now.</p>
		{:else if !open}
			<div class="actions">
				{#each providers as p}
					{#if state.service.configured[p]}
						<button class="link" onclick={() => write(p)}>{state.report ? 'Write again' : 'Write'} with {NAMES[p]}</button>
					{/if}
				{/each}
				{#if !providers.some((p) => state.service.configured[p])}
					<span class="muted small">No API key set: add ANTHROPIC_API_KEY or GEMINI_API_KEY to the server's .env.</span>
				{/if}
			</div>
		{/if}
		{#if error}<p class="error">{error}</p>{/if}
	{/if}
</section>

<style>
	h3 {
		margin: 0;
		font-size: var(--fs-title);
		font-weight: 700;
		line-height: 1.25;
	}
	.meta,
	.muted {
		margin: 0;
		color: var(--muted);
		font-size: var(--fs-small);
	}
	.small {
		font-size: var(--fs-caption);
	}
	.error {
		margin: 0;
		color: var(--danger);
		font-size: var(--fs-small);
	}
	.actions {
		display: flex;
		flex-wrap: wrap;
		gap: 0.25rem 1.25rem;
	}
	/* The report itself: reading text, a comfortable measure, the sections as small headings. */
	.text {
		max-width: 68ch;
		line-height: 1.55;
	}
	.text :global(h4) {
		margin: 1rem 0 0.2rem;
		font-size: var(--fs-body);
		font-weight: 700;
	}
	.text :global(h4:first-child) {
		margin-top: 0.25rem;
	}
	.text :global(p) {
		margin: 0 0 0.5rem;
	}
	.text :global(ul),
	.text :global(ol) {
		margin: 0 0 0.5rem;
		padding-left: 1.2rem;
	}
	.text :global(li) {
		margin-bottom: 0.25rem;
	}
</style>
