<script>
	// Settings > AI (roadmap step 8): which provider writes the reports by default, the model of each,
	// whether a report is written by itself after an import, and the state of the analysis service (which
	// alone holds the API keys) with the tokens used in the last 30 days. Saved as a new version, like the
	// other settings.
	import { api, ApiError, Unauthorized } from './api.js';

	/** @type {{ onUnauthorized: () => void }} */
	let { onUnauthorized } = $props();

	const NAMES = { claude: 'Claude', gemini: 'Gemini' };
	/** @type {any} */
	let data = $state(null);
	let f = $state({ default_provider: 'claude', claude_model: '', gemini_model: '', auto_report: true });
	let error = $state('');
	let saved = $state('');
	let busy = $state(false);

	async function load() {
		try {
			data = await api('/api/settings/ai');
			const c = data.current;
			f = { default_provider: c.default_provider, claude_model: c.claude_model, gemini_model: c.gemini_model, auto_report: c.auto_report };
		} catch (e) {
			if (e instanceof Unauthorized) return onUnauthorized();
			error = 'Could not load the AI settings.';
		}
	}
	$effect(() => {
		load();
	});

	async function save() {
		busy = true;
		error = '';
		saved = '';
		try {
			const r = await api('/api/settings/ai', { method: 'POST', body: { ...f, claude_model: f.claude_model.trim(), gemini_model: f.gemini_model.trim() } });
			saved = `Saved as version ${r.version}.`;
			load();
		} catch (e) {
			if (e instanceof Unauthorized) return onUnauthorized();
			error =
				e instanceof ApiError && e.status === 409
					? 'Nothing changed.'
					: e instanceof ApiError && e.status === 422
						? 'A model name has letters, digits, dots, dashes and colons only.'
						: 'Not saved. Try again in a moment.';
		} finally {
			busy = false;
		}
	}

	/** "13 Oct, 18:02". @param {number} t */
	const at = (t) => new Date(t * 1000).toLocaleString('en-GB', { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit', hour12: false });
	const n = (/** @type {number} */ v) => v.toLocaleString('en-GB');
</script>

<section class="ai" aria-labelledby="ai-title">
	<h2 id="ai-title" class="visually-hidden">AI settings</h2>
	{#if !data}
		{#if error}<p class="error">{error}</p>{:else}<p class="muted">Loading...</p>{/if}
	{:else}
		<p class="status">
			{#if data.service.running}
				Analysis service running. API keys: {['claude', 'gemini']
					.filter((p) => data.service.configured[p])
					.map((p) => NAMES[/** @type {'claude' | 'gemini'} */ (p)])
					.join(' and ') || 'none'}.
			{:else if data.service.seen_at}
				<span class="caution">The analysis service has not been seen since {at(data.service.seen_at)}.</span>
			{:else}
				<span class="caution">The analysis service has never run.</span>
			{/if}
			Version {data.current.version}, saved {at(data.current.created_at)}.
		</p>

		<section class="sec" aria-labelledby="reports-title">
			<h3 id="reports-title">Reports</h3>
			<div class="written">
				<span class="label" id="provider-label">Written by</span>
				<div class="seg" role="group" aria-labelledby="provider-label">
					<button aria-pressed={f.default_provider === 'claude'} onclick={() => (f.default_provider = 'claude')}>Claude</button>
					<button aria-pressed={f.default_provider === 'gemini'} onclick={() => (f.default_provider = 'gemini')}>Gemini</button>
				</div>
			</div>
			<label class="check">
				<input type="checkbox" bind:checked={f.auto_report} />
				<span>Write one by itself after a new activity (from the last 48 h)</span>
			</label>
		</section>

		<section class="sec" aria-labelledby="models-title">
			<h3 id="models-title">Models</h3>
			<label class="row"><span>Claude</span><input bind:value={f.claude_model} spellcheck="false" autocapitalize="off" /></label>
			<label class="row"><span>Gemini</span><input bind:value={f.gemini_model} spellcheck="false" autocapitalize="off" /></label>
			<p class="muted small">The model's API name. A wrong name shows up as a failed report.</p>
		</section>

		<button class="primary" onclick={save} disabled={busy}>{busy ? 'Saving...' : 'Save as a new version'}</button>
		{#if error}<p class="error" role="alert">{error}</p>{/if}
		{#if saved}<p class="saved" role="status">{saved}</p>{/if}

		<section class="sec" aria-labelledby="use-title">
		<h3 id="use-title">Use in the last 30 days</h3>
		{#if data.usage_30d.length}
			<table>
				<thead><tr><th scope="col">Provider</th><th scope="col">Calls</th><th scope="col">Failed</th><th scope="col">Tokens in</th><th scope="col">Tokens out</th></tr></thead>
				<tbody>
					{#each data.usage_30d as u}
						<tr>
							<td>{NAMES[/** @type {'claude' | 'gemini'} */ (u.provider)] ?? u.provider}</td>
							<td>{u.calls}</td>
							<td>{u.failed}</td>
							<td>{n(u.input_tokens)}</td>
							<td>{n(u.output_tokens)}</td>
						</tr>
					{/each}
				</tbody>
			</table>
			<p class="muted small">Costs are in each provider's console.</p>
		{:else}
			<p class="muted">No calls yet.</p>
		{/if}
		</section>
	{/if}
</section>

<style>
	.ai {
		display: grid;
		gap: var(--s-4);
		max-width: 44rem;
	}
	.status {
		margin: 0;
		color: var(--muted);
		font-size: var(--fs-small);
	}
	.written {
		display: flex;
		align-items: center;
		gap: var(--s-3);
	}
	.label {
		color: var(--muted);
	}
	.check {
		display: flex;
		gap: var(--s-3);
		align-items: center;
		min-height: var(--tap);
	}
	.check input {
		accent-color: var(--accent);
	}
	.row {
		display: grid;
		grid-template-columns: 5rem minmax(0, 1fr);
		gap: 1rem;
		align-items: baseline;
		color: var(--muted);
	}
	.row input {
		width: 100%;
		min-width: 0;
		padding: 0.35rem 0;
		background: none;
		border: none;
		border-bottom: 1.5px solid var(--border);
		border-radius: 0;
		color: var(--text);
		font: inherit;
	}
	.row input:focus {
		outline: none;
		border-bottom-color: var(--accent);
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
	.muted {
		margin: 0;
		color: var(--muted);
	}
	.small {
		font-size: var(--fs-small);
	}
	.caution {
		color: var(--caution);
	}
	.error {
		margin: 0;
		color: var(--danger);
	}
	.saved {
		margin: 0;
		color: var(--muted);
	}
	table {
		border-collapse: collapse;
		font-size: var(--fs-small);
		font-variant-numeric: tabular-nums;
	}
	th,
	td {
		padding: 0.35rem 1.25rem 0.35rem 0;
		text-align: right;
		border-bottom: 1px solid var(--border);
	}
	th:first-child,
	td:first-child {
		text-align: left;
	}
	th {
		color: var(--muted);
		font-weight: 400;
	}
</style>
