<script>
	// Activities: newest first. The Garmin sync fills it twice a day; "Sync now" queues a run and this screen
	// follows it until it ends. Manual FIT upload (a .fit from the watch, or the .zip from Garmin Connect's
	// "Export original") always works, also when the Garmin login is broken.
	import { duration, km, pace, sportName, when } from './activityformat.js';
	import { api, ApiError, Unauthorized } from './api.js';

	/** @type {{ onUnauthorized: () => void, onNotice: (text: string) => void, onOpen: (id: number) => void }} */
	let { onUnauthorized, onNotice, onOpen } = $props();


	/** @type {any[] | null} */
	let list = $state(null);
	let loadError = $state('');
	let uploadError = $state('');
	let busy = $state(false);
	/** @type {any} */
	let garmin = $state(null);
	let syncing = $state(false);
	let syncError = $state('');
	const POLL_MS = 4000;
	const POLL_MAX_MS = 5 * 60 * 1000;
	/** @type {HTMLInputElement} */
	let fileInput;

	async function load() {
		try {
			list = await api('/api/activities?limit=100');
			loadError = '';
		} catch (e) {
			if (e instanceof Unauthorized) return onUnauthorized();
			loadError = 'Could not load activities. Try again in a moment.';
		}
	}

	async function loadStatus() {
		try {
			garmin = await api('/api/garmin/status');
		} catch (e) {
			if (e instanceof Unauthorized) onUnauthorized();
		}
	}

	$effect(() => {
		load();
		loadStatus();
	});

	/** Queue a run, then follow it until the garmin service has finished it. */
	async function syncNow() {
		syncing = true;
		syncError = '';
		try {
			const { queued } = await api('/api/garmin/sync', { method: 'POST' });
			const until = Date.now() + POLL_MAX_MS;
			while (Date.now() < until) {
				await new Promise((r) => setTimeout(r, POLL_MS));
				await loadStatus();
				const run = garmin?.runs?.find((/** @type {any} */ r) => r.id === queued);
				if (run?.finished_at) {
					if (run.ok) onNotice(run.imported ? `Garmin: ${run.imported} new ${run.imported === 1 ? 'activity' : 'activities'}.` : 'Garmin: nothing new.');
					load();
					return;
				}
			}
			syncError = 'The sync has not finished after 5 minutes. Check again later.';
		} catch (e) {
			if (e instanceof Unauthorized) return onUnauthorized();
			syncError = e instanceof ApiError && e.detail ? `${e.detail[0].toUpperCase()}${e.detail.slice(1)}.` : 'Could not start a sync.';
		} finally {
			syncing = false;
		}
	}

	/** "today 13:02", "yesterday 22:01", "Sat 10 Oct 13:00". @param {number} t */
	function ago(t) {
		const d = new Date(t * 1000);
		const hm = d.toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit', hour12: false });
		const days = Math.round((new Date().setHours(0, 0, 0, 0) - new Date(d).setHours(0, 0, 0, 0)) / 86400000);
		if (days === 0) return `today ${hm}`;
		if (days === 1) return `yesterday ${hm}`;
		return `${d.toLocaleDateString('en-GB', { weekday: 'short', day: 'numeric', month: 'short' })} ${hm}`;
	}

	/** @param {Event} event */
	async function upload(event) {
		const input = /** @type {HTMLInputElement} */ (event.currentTarget);
		const file = input.files?.[0];
		input.value = ''; // the same file can be picked again
		if (!file) return;
		busy = true;
		uploadError = '';
		try {
			const r = await fetch('/api/activities/fit', {
				method: 'POST',
				headers: { 'Content-Type': 'application/octet-stream' },
				body: file
			});
			if (r.status === 401) return onUnauthorized();
			const body = await r.json().catch(() => ({}));
			if (!r.ok) throw new ApiError(r.status, typeof body.detail === 'string' ? body.detail : '');
			const names = body.activities.map((/** @type {any} */ a) => `${sportName(a)} ${km(a.distance_m)}`).join(' + ');
			onNotice(body.duplicate ? `Already imported: ${names}.` : `Imported ${names}.`);
			load();
		} catch (e) {
			uploadError =
				e instanceof ApiError && e.detail
					? `Not imported: ${e.detail}.`
					: e instanceof ApiError && e.status === 413
						? 'Not imported: the file is too large for the server.'
						: 'Not imported. Try again in a moment.';
		} finally {
			busy = false;
		}
	}

</script>

<section class="activities sec" aria-labelledby="activities-title">
	<div class="head">
		<h2 id="activities-title">Activities</h2>
		<div class="actions">
			<button class="link" onclick={syncNow} disabled={syncing}>{syncing ? 'Syncing...' : 'Sync now'}</button>
			<button class="link" onclick={() => fileInput.click()} disabled={busy}>{busy ? 'Importing...' : 'Upload FIT'}</button>
		</div>
		<input bind:this={fileInput} type="file" class="visually-hidden" tabindex="-1" aria-hidden="true" onchange={upload} />
	</div>
	{#if garmin}
		{#if garmin.login_needed}
			<p class="caution">Garmin needs a login: run the login command on the server. Upload still works.</p>
		{:else if garmin.last_error}
			<p class="caution">
				Garmin: the last sync failed ({garmin.last_error}).{garmin.last_success ? ` Last good sync ${ago(garmin.last_success)}.` : ''}
			</p>
		{:else if garmin.last_success}
			<p class="muted small">Synced {ago(garmin.last_success)}.</p>
		{:else}
			<p class="muted small">Garmin: not synced yet.</p>
		{/if}
	{/if}
	{#if syncError}<p class="error" role="alert">{syncError}</p>{/if}
	{#if uploadError}<p class="error" role="alert">{uploadError}</p>{/if}

	{#if loadError}
		<p class="error" role="alert">{loadError}</p>
	{:else if list === null}
		<p class="muted">Loading...</p>
	{:else if list.length === 0}
		<p class="muted">
			No activities yet. They arrive with the Garmin sync, or upload a .fit file, or the .zip from Garmin Connect's "Export original".
		</p>
	{:else}
		<ul>
			{#each list as a (a.id)}
				<li>
					<button onclick={() => onOpen(a.id)}>
						<span class="when">{when(a.start)}</span>
						<span class="what">{sportName(a)}</span>
						<span class="figures">
							{#each [km(a.distance_m), duration(a.moving_s ?? a.elapsed_s), pace(a), a.avg_hr ? `${a.avg_hr} bpm` : ''].filter(Boolean) as f}
								<span>{f}</span>
							{/each}
						</span>
					</button>
				</li>
			{/each}
		</ul>
	{/if}
</section>

<style>

	.head {
		display: flex;
		align-items: baseline;
		justify-content: space-between;
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
	.error {
		margin: 0;
		color: var(--danger);
	}
	.caution {
		margin: 0;
		color: var(--caution);
		font-size: var(--fs-small);
	}
	.small {
		font-size: var(--fs-small);
	}
	.actions {
		display: flex;
		gap: 1.1rem;
	}
	ul {
		list-style: none;
		margin: 0;
		padding: 0;
	}
	li button {
		display: grid;
		grid-template-columns: minmax(0, 1fr) auto;
		grid-template-areas: 'what when' 'figures figures';
		gap: 0.15rem 1rem;
		width: 100%;
		padding: 0.65rem 0;
		background: none;
		border: none;
		text-align: left;
		cursor: pointer;
	}
	@media (hover: hover) {
		li button:hover .what {
			color: var(--accent);
		}
	}
	li + li {
		border-top: 1px solid var(--border);
	}
	.what {
		grid-area: what;
		font-weight: 700;
	}
	.when {
		grid-area: when;
		color: var(--muted);
		font-size: var(--fs-small);
	}
	.figures {
		grid-area: figures;
		display: flex;
		flex-wrap: wrap;
		gap: 0 1.1rem;
	}
</style>
