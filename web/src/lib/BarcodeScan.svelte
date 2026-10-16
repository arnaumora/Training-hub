<script>
	// Full-screen barcode scanner: the back camera, about 6 reads a second, and the number can always be typed
	// instead. A read with a wrong check digit is ignored (a misread). A good one is looked up on our API:
	//   found     -> onFound(food)
	//   not found or no usable carbs -> onMissing({ barcode, prefill, reason }) when the parent can open
	//             "New food"; otherwise it is explained here.
	// The camera is stopped as soon as a code is read, and whenever the scanner closes.
	import { onDestroy, onMount } from 'svelte';
	import { api, ApiError, Unauthorized } from './api.js';

	/**
	 * @type {{
	 *   onFound: (food: any) => void,
	 *   onMissing?: ((info: { barcode: string, prefill: any, reason: string }) => void) | null,
	 *   onClose: () => void,
	 *   onUnauthorized: () => void
	 * }}
	 */
	let { onFound, onMissing = null, onClose, onUnauthorized } = $props();

	const READ_EVERY_MS = 160;

	/** @type {'starting' | 'scanning' | 'looking' | 'message'} */
	let phase = $state('starting');
	let message = $state('');
	let typing = $state(false);
	let typed = $state('');
	/** @type {HTMLVideoElement} */
	let video;
	/** @type {MediaStream | null} */
	let stream = null;
	/** @type {any} */
	let detector = null;
	/** @type {((code: string) => boolean) | null} */
	let checkDigitOk = null;
	/** @type {ReturnType<typeof setTimeout> | undefined} */
	let timer;
	let closed = false;

	function stopCamera() {
		clearTimeout(timer);
		stream?.getTracks().forEach((t) => t.stop());
		stream = null;
	}

	async function startCamera() {
		message = '';
		phase = 'starting';
		try {
			const mod = await import('./scanner.js');
			detector = mod.createDetector();
			checkDigitOk = mod.checkDigitOk;
			if (!navigator.mediaDevices?.getUserMedia) throw new Error('no camera API');
			stream = await navigator.mediaDevices.getUserMedia({
				video: { facingMode: { ideal: 'environment' }, width: { ideal: 1280 }, height: { ideal: 720 } },
				audio: false
			});
			if (closed) return stopCamera();
			video.srcObject = stream;
			await video.play();
			phase = 'scanning';
			read();
		} catch (e) {
			stopCamera();
			phase = 'message';
			typing = true;
			message =
				e instanceof DOMException && e.name === 'NotAllowedError'
					? 'The camera is not allowed for this site. Allow it in the browser settings, or type the number.'
					: 'The camera could not start. Type the number instead.';
		}
	}

	async function read() {
		if (phase !== 'scanning' || closed) return;
		try {
			if (video.readyState >= 2) {
				const codes = await detector.detect(video);
				const good = codes.map((/** @type {any} */ c) => c.rawValue).find((/** @type {string} */ v) => checkDigitOk?.(v));
				if (good) return lookup(good);
			}
		} catch {
			// A frame that cannot be read: try the next one.
		}
		timer = setTimeout(read, READ_EVERY_MS);
	}

	/** @param {string} code */
	async function lookup(code) {
		stopCamera();
		phase = 'looking';
		message = `Looking up ${code}...`;
		try {
			const r = await api(`/api/barcode/${encodeURIComponent(code)}`);
			if (closed) return;
			if (r.status === 'found') return onFound(r.food);
			const reason = r.status === 'not_found' ? 'It is not in Open Food Facts.' : `Open Food Facts: ${r.reason}.`;
			if (onMissing) return onMissing({ barcode: r.barcode, prefill: r.prefill ?? null, reason });
			phase = 'message';
			message = `${reason} Add it under Foods > New food with its barcode (Scan there), then use it here.`;
		} catch (e) {
			if (e instanceof Unauthorized) return onUnauthorized();
			phase = 'message';
			message =
				e instanceof ApiError && e.detail ? `${e.detail[0].toUpperCase()}${e.detail.slice(1)}.` : 'The lookup failed. Try again in a moment.';
		}
	}

	/** @param {SubmitEvent} event */
	function submitTyped(event) {
		event.preventDefault();
		const code = typed.replace(/\s/g, '');
		if (code) lookup(code);
	}

	function close() {
		closed = true;
		stopCamera();
		onClose();
	}

	onMount(startCamera);
	onDestroy(() => {
		closed = true;
		stopCamera();
	});
</script>

<div class="scan" role="dialog" aria-modal="true" aria-label="Scan a barcode">
	<!-- svelte-ignore a11y_media_has_caption -->
	<video bind:this={video} playsinline muted class:hidden={phase !== 'scanning'}></video>
	{#if phase === 'scanning'}<div class="guide" aria-hidden="true"></div>{/if}

	<div class="panel">
		<p class="status" role="status">
			{#if phase === 'starting'}
				Starting the camera...
			{:else if phase === 'scanning'}
				Hold the barcode inside the frame.
			{:else}
				{message}
			{/if}
		</p>

		{#if typing}
			<form class="typed" onsubmit={submitTyped}>
				<input bind:value={typed} inputmode="numeric" autocomplete="off" placeholder="Barcode number" aria-label="Barcode number" />
				<button type="submit" class="look" disabled={typed.replace(/\s/g, '').length < 8 || phase === 'looking'}>Look up</button>
			</form>
		{/if}

		<div class="actions">
			{#if phase === 'message'}
				<button class="link" onclick={startCamera}>Scan again</button>
			{:else if !typing}
				<button class="link" onclick={() => (typing = true)}>Type the number</button>
			{/if}
			<button class="link" onclick={close}>Cancel</button>
		</div>
	</div>
</div>

<style>
	/* The camera view is black whatever the theme (easier to aim); the panel under it follows the theme. */
	.scan {
		position: fixed;
		inset: 0;
		z-index: 40;
		display: grid;
		grid-template-rows: minmax(0, 1fr) auto;
		background: #000;
	}
	video {
		grid-row: 1;
		grid-column: 1;
		width: 100%;
		height: 100%;
		object-fit: cover;
	}
	video.hidden {
		visibility: hidden;
	}
	/* The frame is drawn over live video, so it is white rather than a theme colour. */
	.guide {
		grid-row: 1;
		grid-column: 1;
		align-self: center;
		justify-self: center;
		width: min(78vw, 360px);
		height: min(36vw, 170px);
		border: 2px solid rgb(255 255 255 / 0.9);
		border-radius: var(--radius);
		box-shadow: 0 0 0 100vmax rgb(0 0 0 / 0.45);
		pointer-events: none;
	}
	.panel {
		display: grid;
		gap: var(--s-3);
		padding: var(--s-4) var(--s-4) calc(var(--s-4) + env(safe-area-inset-bottom));
		background: var(--bg);
		color: var(--text);
	}
	.status {
		margin: 0;
		min-height: 1.45em;
	}
	.typed {
		display: grid;
		grid-template-columns: minmax(0, 1fr) auto;
		gap: var(--s-3);
		align-items: center;
	}
	.typed input {
		width: 100%;
		padding: 0.45rem 0;
		background: none;
		border: none;
		border-bottom: 2px solid var(--border);
		border-radius: 0;
		color: var(--text);
		font-weight: 700;
		letter-spacing: 0.04em;
	}
	.typed input:focus {
		outline: none;
		border-bottom-color: var(--accent);
	}
	.look {
		min-height: var(--tap);
		padding: 0 var(--s-4);
		background: var(--accent);
		color: var(--accent-text);
		border: none;
		border-radius: var(--radius);
		font-weight: 700;
	}
	.look:disabled {
		opacity: 0.4;
	}
	.actions {
		display: flex;
		justify-content: space-between;
	}
</style>
