<script>
	import { flushSync, onMount } from 'svelte';
	import { api, clock, Unauthorized } from '../lib/api.js';
	import { describe } from '../lib/events.js';
	import Activities from '../lib/Activities.svelte';
	import AiSettings from '../lib/AiSettings.svelte';
	import Compare from '../lib/Compare.svelte';
	import Calendar from '../lib/Calendar.svelte';
	import Similar from '../lib/Similar.svelte';
	import Run from '../lib/Run.svelte';
	import TrainingSettings from '../lib/TrainingSettings.svelte';
	import PlanSettings from '../lib/PlanSettings.svelte';
	import ThemeSwitch from '../lib/ThemeSwitch.svelte';
	import Foods from '../lib/Foods.svelte';
	import InsulinSettings from '../lib/InsulinSettings.svelte';
	import LogSheet from '../lib/LogSheet.svelte';
	import Now from '../lib/Now.svelte';

	/** @type {'loading' | 'login' | 'app'} */
	let view = $state('loading');
	/** Signed-in screen. Four places (Now, Training, Analysis, Food), Settings, and one activity opened from
	 * any of them (the 2026-10-15 rework).
	 * @type {'now' | 'training' | 'analysis' | 'food' | 'settings' | 'activity'} */
	let screen = $state('now');
	/** @type {number | null} */
	let activityId = $state(null);
	/** Where an opened activity returns to. @type {'now' | 'training' | 'analysis' | 'food' | 'settings'} */
	let returnTo = $state('training');
	/** Training: the calendar (plan against done, with the week's figures under the week view), every activity. @type {'calendar' | 'list'} */
	let trainingTab = $state('calendar');
	/** Analysis: patterns across runs. @type {'compare' | 'similar'} */
	let analysisTab = $state('compare');
	/** Which settings: insulin (calculator), training (zones, glucose target, lag), the plan, or AI. @type {'insulin' | 'training' | 'plan' | 'ai'} */
	let settingsTab = $state('insulin');

	const PLACES = /** @type {const} */ ([
		['now', 'Now'],
		['training', 'Training'],
		['analysis', 'Analysis'],
		['food', 'Food']
	]);
	// Every place and every opened activity is a history entry with its own URL (#training, #activity-12...),
	// so the browser's back and forward (the button, a mouse's back button, a trackpad's two-finger swipe, the
	// phone's back gesture) move between them, and a reload stays where it was. Tabs inside a place are not
	// entries: back leaves the place, as in a phone app.
	const PLACE_KEYS = ['now', 'training', 'analysis', 'food', 'settings'];

	/** @param {'now' | 'training' | 'analysis' | 'food' | 'settings'} p */
	function go(p) {
		if (p !== screen) history.pushState({ screen: p }, '', `#${p}`);
		screen = p;
		scrollTo(0, 0);
	}

	/** Open one activity, as its own history entry. @param {number} id */
	function openActivity(id) {
		if (screen !== 'activity') returnTo = screen;
		activityId = id;
		screen = 'activity';
		history.pushState({ screen: 'activity', activity: id, returnTo }, '', `#activity-${id}`);
		scrollTo(0, 0);
	}

	function closeActivity() {
		// Opened inside the app: back to where it was opened. Loaded from its URL: there is nothing to go back to.
		if (history.state?.activity && !history.state?.first) history.back();
		else go(returnTo);
	}

	/** The entry a URL stands for (typed or pasted, a reload). @returns {any} */
	function fromUrl() {
		const m = /^#activity-(\d+)$/.exec(location.hash);
		const place = location.hash.slice(1);
		return m
			? { screen: 'activity', activity: Number(m[1]), returnTo: 'now', first: true }
			: { screen: PLACE_KEYS.includes(place) ? place : 'now' };
	}

	/** Show what a history entry holds; an entry without state (a URL typed by hand) by its URL. @param {any} st */
	function show(st) {
		if (!st?.screen) {
			st = fromUrl();
			history.replaceState(st, '', st.activity ? location.hash : `#${st.screen}`);
		}
		if (st.activity) {
			activityId = st.activity;
			returnTo = st.returnTo ?? returnTo;
			screen = 'activity';
		} else screen = PLACE_KEYS.includes(st.screen) ? st.screen : 'now';
	}

	onMount(() => {
		// First load or reload: the URL says where (the entry gets its state, so back works from here).
		show(history.state?.screen ? history.state : null);

		const onPop = () => {
			// The phone's back gesture closes the Log sheet first (it has its own history entry).
			if (sheetOpen && !history.state?.sheet) {
				sheetOpen = false;
				editing = null;
				return;
			}
			show(history.state);
		};
		addEventListener('popstate', onPop);
		return () => removeEventListener('popstate', onPop);
	});
	let password = $state('');
	let error = $state('');
	let busy = $state(false);

	// Insulin and carbs log for the last 24 h, plus what the Log form pre-fills.
	/** @type {any} */
	let log = $state(null);
	let sheetOpen = $state(false);
	/** The event being edited, or null for a new entry. @type {{ t: number, carbs: any, dose: any } | null} */
	let editing = $state(null);
	/** @type {any} */
	let sheetRef = $state(null);
	// Bumped after every change to the log, so the chart's markers reload.
	let logVersion = $state(0);
	/** @type {{ text: string, undo?: () => Promise<void> } | null} */
	let notice = $state(null);
	/** @type {ReturnType<typeof setTimeout> | undefined} */
	let noticeTimer;
	const NOTICE_MS = 8000;

	function toLogin() {
		closeSheet();
		view = 'login';
	}

	/** The sheet gets a history entry, so the phone's back gesture closes it instead of leaving the page. */
	function pushSheet() {
		history.pushState({ ...(history.state ?? {}), sheet: true }, '');
	}

	/** Desktop: L opens the Log sheet, unless a field has the keyboard or a sheet is already open. @param {KeyboardEvent} e */
	function shortcut(e) {
		if (view !== 'app' || sheetOpen || e.ctrlKey || e.metaKey || e.altKey || e.key.toLowerCase() !== 'l') return;
		const t = /** @type {HTMLElement} */ (e.target);
		if (t && (t.isContentEditable || ['INPUT', 'TEXTAREA', 'SELECT'].includes(t.tagName))) return;
		e.preventDefault();
		openSheet();
	}

	function closeSheet() {
		sheetOpen = false;
		editing = null;
		if (history.state?.sheet) history.back();
	}

	async function loadLog() {
		try {
			log = await api('/api/log?hours=24');
		} catch (e) {
			if (e instanceof Unauthorized) toLogin();
			// Otherwise keep the old copy; the sheet still works without it (the name is typed).
		}
	}

	function openSheet() {
		editing = null;
		sheetOpen = true;
		pushSheet();
		// Render the sheet now, inside the tap, and focus its amount field: iOS only opens the
		// keyboard for a focus() that happens during the user's tap.
		flushSync();
		sheetRef?.focusAmount();
		loadLog();
	}

	/** After any change to the log: reload it, and tell the chart to reload its markers. */
	function changed() {
		loadLog();
		logVersion += 1;
	}

	/** @param {string} text @param {(() => Promise<void>) | undefined} [undo] */
	function showNotice(text, undo) {
		clearTimeout(noticeTimer);
		notice = { text, undo };
		noticeTimer = setTimeout(() => (notice = null), NOTICE_MS);
	}

	/** Open a logged event in the form to change or delete it (no keyboard: nothing to type yet). @param {{ t: number, carbs: any, dose: any }} e */
	function openEdit(e) {
		editing = e;
		sheetOpen = true;
		pushSheet();
		loadLog();
	}

	/** Close the form and confirm. A new entry gets Undo (soft-deletes everything that tap saved). @param {{ carbs: any, dose: any }} r */
	function logged(r) {
		const wasEdit = editing !== null;
		closeSheet();
		const t = (r.carbs ?? r.dose).t;
		if (wasEdit) {
			showNotice(`Saved: ${describe(r)} at ${clock(t)}.`);
			changed();
			return;
		}
		showNotice(`Logged ${describe(r)} at ${clock(t)}.`, async () => {
			const q = [r.carbs ? `carbs_id=${r.carbs.id}` : '', r.dose ? `dose_id=${r.dose.id}` : ''].filter(Boolean).join('&');
			try {
				await api(`/api/entries?${q}`, { method: 'DELETE' });
				showNotice('Removed.');
			} catch (e) {
				if (e instanceof Unauthorized) return toLogin();
				showNotice('Could not remove it. Try again in a moment.');
			}
			changed();
		});
		changed();
	}

	function deleted() {
		closeSheet();
		showNotice('Entry deleted.');
		changed();
	}

	$effect(() => {
		if (view === 'app') loadLog();
	});

	async function checkSession() {
		try {
			const r = await fetch('/api/me');
			view = r.ok ? 'app' : 'login';
		} catch {
			view = 'login';
			error = 'Server unreachable.';
		}
	}

	/** @param {SubmitEvent} event */
	async function signIn(event) {
		event.preventDefault();
		if (!password) return;
		busy = true;
		error = '';
		try {
			const r = await fetch('/api/login', {
				method: 'POST',
				headers: { 'Content-Type': 'application/json' },
				body: JSON.stringify({ password })
			});
			if (r.status === 204) {
				password = '';
				view = 'app';
			} else if (r.status === 401) {
				error = 'Wrong password.';
			} else if (r.status === 429) {
				error = 'Too many wrong attempts. Try again in 15 minutes.';
			} else {
				error = `Unexpected response (${r.status}).`;
			}
		} catch {
			error = 'Server unreachable.';
		} finally {
			busy = false;
		}
	}

	async function signOut() {
		try {
			await fetch('/api/logout', { method: 'POST' });
		} finally {
			view = 'login';
		}
	}

	onMount(checkSession);
</script>

<svelte:window onkeydown={shortcut} />

{#if view === 'loading'}
	<p class="loading">Loading...</p>
{:else if view === 'login'}
	<main class="login">
		<h1>Training Hub</h1>
		<form onsubmit={signIn}>
			<label for="password">Password</label>
			<input
				id="password"
				type="password"
				autocomplete="current-password"
				bind:value={password}
				disabled={busy}
				required
			/>
			<button type="submit" disabled={busy || !password}>
				{busy ? 'Signing in...' : 'Sign in'}
			</button>
			{#if error}
				<p class="error" role="alert">{error}</p>
			{/if}
		</form>
	</main>
{:else}
	<header>
		<div class="bar">
			<button class="name" onclick={() => go('now')}>Training Hub</button>
			<nav class="places top" aria-label="Places">
				{#each PLACES as [p, label] (p)}
					<button class:active={screen === p || (screen === 'activity' && returnTo === p)} aria-current={screen === p ? 'page' : undefined} onclick={() => go(p)}>
						{label}
					</button>
				{/each}
			</nav>
			<div class="end">
				<button class="settings" class:active={screen === 'settings'} aria-current={screen === 'settings' ? 'page' : undefined} onclick={() => go('settings')}>
					Settings
				</button>
				<button class="primary log-top" onclick={openSheet} title="Log (L)" aria-keyshortcuts="L">Log</button>
			</div>
		</div>
	</header>
	<main class="app">
		{#if screen === 'settings'}
			<ThemeSwitch />
			<div class="tabs page-tabs" role="group" aria-label="Settings">
				<button class:active={settingsTab === 'insulin'} aria-pressed={settingsTab === 'insulin'} onclick={() => (settingsTab = 'insulin')}>Insulin</button>
				<button class:active={settingsTab === 'training'} aria-pressed={settingsTab === 'training'} onclick={() => (settingsTab = 'training')}>Training</button>
				<button class:active={settingsTab === 'plan'} aria-pressed={settingsTab === 'plan'} onclick={() => (settingsTab = 'plan')}>Plan</button>
				<button class:active={settingsTab === 'ai'} aria-pressed={settingsTab === 'ai'} onclick={() => (settingsTab = 'ai')}>AI</button>
			</div>
			{#if settingsTab === 'insulin'}
				<InsulinSettings onUnauthorized={toLogin} onSaved={loadLog} />
			{:else if settingsTab === 'training'}
				<TrainingSettings onUnauthorized={toLogin} />
			{:else if settingsTab === 'plan'}
				<PlanSettings onUnauthorized={toLogin} />
			{:else}
				<AiSettings onUnauthorized={toLogin} />
			{/if}
			<p class="signout"><button class="link" onclick={signOut}>Sign out</button></p>
		{:else if screen === 'training'}
			<div class="tabs page-tabs" role="group" aria-label="Training">
				<button class:active={trainingTab === 'calendar'} aria-pressed={trainingTab === 'calendar'} onclick={() => (trainingTab = 'calendar')}>Calendar</button>
				<button class:active={trainingTab === 'list'} aria-pressed={trainingTab === 'list'} onclick={() => (trainingTab = 'list')}>Activities</button>
			</div>
			{#if trainingTab === 'calendar'}
				<Calendar onUnauthorized={toLogin} onOpen={openActivity} />
			{:else}
				<Activities onUnauthorized={toLogin} onNotice={(text) => showNotice(text)} onOpen={openActivity} />
			{/if}
		{:else if screen === 'analysis'}
			<div class="tabs page-tabs" role="group" aria-label="Analysis">
				<button class:active={analysisTab === 'compare'} aria-pressed={analysisTab === 'compare'} onclick={() => (analysisTab = 'compare')}>Compare</button>
				<button class:active={analysisTab === 'similar'} aria-pressed={analysisTab === 'similar'} onclick={() => (analysisTab = 'similar')}>Similar</button>
			</div>
			{#if analysisTab === 'compare'}
				<Compare onUnauthorized={toLogin} onOpen={openActivity} />
			{:else}
				<Similar onUnauthorized={toLogin} onOpen={openActivity} />
			{/if}
		{:else if screen === 'activity' && activityId !== null}
			<Run id={activityId} backLabel={returnTo === 'settings' ? 'Settings' : Object.fromEntries(PLACES)[returnTo]} onBack={closeActivity} onOpen={openActivity} onUnauthorized={toLogin} />
		{:else if screen === 'food'}
			<Foods onUnauthorized={toLogin} onNotice={(text) => showNotice(text)} />
		{:else}
			<Now
				{log}
				{logVersion}
				onEdit={openEdit}
				onOpen={openActivity}
				onTraining={() => {
					trainingTab = 'calendar';
					go('training');
				}}
				onSimilar={() => {
					analysisTab = 'similar';
					go('analysis');
				}}
				onUnauthorized={toLogin}
			/>
		{/if}
	</main>

	<!-- Phone: the places in thumb reach, Log in the middle. -->
	<nav class="bottom" aria-label="Places">
		{#each PLACES.slice(0, 2) as [p, label] (p)}
			<button class:active={screen === p || (screen === 'activity' && returnTo === p)} aria-current={screen === p ? 'page' : undefined} onclick={() => go(p)}>{label}</button>
		{/each}
		<button class="primary log-bottom" onclick={openSheet}>Log</button>
		{#each PLACES.slice(2) as [p, label] (p)}
			<button class:active={screen === p || (screen === 'activity' && returnTo === p)} aria-current={screen === p ? 'page' : undefined} onclick={() => go(p)}>{label}</button>
		{/each}
	</nav>

	{#if notice}
		<div class="notice" role="status">
			<span>{notice.text}</span>
			{#if notice.undo}
				<button class="link" onclick={notice.undo}>Undo</button>
			{/if}
		</div>
	{/if}

	{#if sheetOpen}
		{#key editing}
			<LogSheet
				bind:this={sheetRef}
				info={log}
				edit={editing}
				onSaved={logged}
				onDeleted={deleted}
				onClose={closeSheet}
				onUnauthorized={toLogin}
			/>
		{/key}
	{/if}
{/if}

<style>
	.loading {
		margin: 2rem 1rem;
		color: var(--muted);
	}

	/* Login */
	.login {
		max-width: 320px;
		margin: 18vh auto 0;
		padding: 0 1rem;
	}
	h1 {
		margin: 0 0 1.75rem;
		font-size: 1.15rem;
		font-weight: 700;
	}
	form {
		display: grid;
		gap: 0.4rem;
	}
	label {
		color: var(--muted);
	}
	input {
		width: 100%;
		padding: 0.45rem 0;
		background: none;
		border: none;
		border-bottom: 2px solid var(--border);
		border-radius: 0;
	}
	input:focus {
		outline: none;
		border-bottom-color: var(--accent);
	}
	button[type='submit'] {
		margin-top: 1.25rem;
		padding: 0.75rem;
		background: var(--accent);
		color: var(--accent-text);
		border: none;
		border-radius: var(--radius);
		font-weight: 700;
		cursor: pointer;
	}
	button:disabled {
		opacity: 0.4;
		cursor: default;
	}
	.error {
		margin: 0.25rem 0 0;
		color: var(--danger);
		font-size: 0.9rem;
	}

	/* Signed in. Header and content share one centred column. */
	header {
		border-bottom: 1px solid var(--border);
	}
	.bar {
		display: flex;
		align-items: center;
		gap: var(--s-5);
		max-width: 1120px;
		margin: 0 auto;
		padding: var(--s-1) var(--s-4);
	}
	.name {
		min-height: var(--tap);
		background: none;
		border: none;
		padding: 0;
		font-weight: 700;
		cursor: pointer;
	}
	.places.top {
		display: none;
		gap: var(--s-5);
	}
	.end {
		display: flex;
		align-items: center;
		gap: var(--s-4);
		margin-left: auto;
	}
	/* Place links and Settings: muted, the current one in ink with the accent underline, like tabs. */
	.places.top button,
	.settings {
		min-height: var(--tap);
		padding: 0;
		background: none;
		border: none;
		border-bottom: 2px solid transparent;
		color: var(--muted);
		cursor: pointer;
	}
	.places.top button.active,
	.settings.active {
		color: var(--text);
		border-bottom-color: var(--accent);
	}
	@media (hover: hover) {
		.places.top button:hover,
		.settings:hover {
			color: var(--text);
		}
	}
	.page-tabs {
		margin-bottom: var(--s-5);
		overflow-x: auto;
		scrollbar-width: none;
	}
	.signout {
		margin: var(--s-7) 0 0;
	}
	.signout .link {
		color: var(--muted);
	}
	.app {
		max-width: 1120px;
		margin: 0 auto;
		/* Room for the bottom bar on the phone. */
		padding: var(--s-5) var(--s-4) calc(5.5rem + env(safe-area-inset-bottom));
	}

	.primary {
		min-height: var(--tap);
		padding: 0 var(--s-5);
		background: var(--accent);
		color: var(--accent-text);
		border: none;
		border-radius: var(--radius);
		font-weight: 700;
		cursor: pointer;
	}
	.log-top {
		display: none;
	}

	/* Phone: the bottom bar. Four places and Log, every target 44 px or more. */
	.bottom {
		position: fixed;
		left: 0;
		right: 0;
		bottom: 0;
		z-index: 10;
		display: grid;
		grid-template-columns: 1fr 1fr 1.15fr 1fr 1fr;
		align-items: center;
		gap: var(--s-1);
		padding: var(--s-2) var(--s-2) calc(var(--s-2) + env(safe-area-inset-bottom));
		background: var(--bg);
		border-top: 1px solid var(--border);
	}
	.bottom > button:not(.primary) {
		min-height: var(--tap);
		padding: 0;
		background: none;
		border: none;
		border-top: 2px solid transparent;
		color: var(--muted);
		font-size: var(--fs-small);
		cursor: pointer;
	}
	.bottom > button.active {
		color: var(--text);
		border-top-color: var(--accent);
	}
	.log-bottom {
		padding: 0;
		font-size: var(--fs-body);
	}

	/* Desktop: the places move to the top bar with Log, and the bottom bar goes. */
	@media (min-width: 720px) {
		.places.top,
		.log-top {
			display: flex;
		}
		.log-top {
			align-items: center;
		}
		.bottom {
			display: none;
		}
		.app {
			padding-bottom: var(--s-7);
		}
	}

	.notice {
		position: fixed;
		left: 1rem;
		right: 1rem;
		bottom: calc(4.75rem + env(safe-area-inset-bottom));
		z-index: 11;
		display: flex;
		align-items: baseline;
		justify-content: space-between;
		gap: 1rem;
		max-width: 1088px;
		margin: 0 auto;
		padding: 0.6rem 0.8rem;
		background: var(--text);
		color: var(--bg);
		border-radius: var(--radius);
		font-size: var(--fs-small);
	}
	@media (min-width: 720px) {
		.notice {
			bottom: calc(1rem + env(safe-area-inset-bottom));
		}
	}
	.notice .link {
		color: var(--bg);
		font-weight: 700;
		text-decoration: underline;
	}
</style>
