// Small JSON helper for /api calls. A 401 becomes Unauthorized so callers can return to the login screen.

export class Unauthorized extends Error {}

export class ApiError extends Error {
	/** @param {number} status @param {string} detail */
	constructor(status, detail) {
		super(`HTTP ${status}${detail ? `: ${detail}` : ''}`);
		this.status = status;
		this.detail = detail;
	}
}

/**
 * @param {string} path
 * @param {{ method?: string, body?: unknown }} [options]
 */
export async function api(path, { method = 'GET', body } = {}) {
	const r = await fetch(path, {
		method,
		headers: body === undefined ? undefined : { 'Content-Type': 'application/json' },
		body: body === undefined ? undefined : JSON.stringify(body)
	});
	if (r.status === 401) throw new Unauthorized();
	if (!r.ok) {
		let detail = '';
		try {
			const j = await r.json();
			detail = typeof j.detail === 'string' ? j.detail : '';
		} catch {
			// not JSON
		}
		throw new ApiError(r.status, detail);
	}
	return r.status === 204 ? null : r.json();
}

/** "18:42" in the device's time zone (Europe/Madrid on the owner's devices). @param {number} t epoch seconds */
export const clock = (t) =>
	new Date(t * 1000).toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit', hour12: false });
