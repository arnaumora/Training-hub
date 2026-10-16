// Helpers shared by the dose and carbs entry sheets.

// Same typo guards as the API: no more than 5 min ahead, no more than a year back.
const MAX_FUTURE_MS = 5 * 60 * 1000;
const MAX_PAST_MS = 365 * 24 * 3600 * 1000;

/**
 * A typed amount with at most one decimal, in (0, max]. Accepts "4", "4.5" and "4,5"
 * (the Spanish iOS keypad types a comma). Returns null when it is not a valid amount.
 * @param {string} text @param {number} max
 */
export function parseAmount(text, max) {
	const s = text.trim().replace(',', '.');
	if (!/^\d{1,4}(\.\d)?$/.test(s)) return null;
	const v = Number(s);
	return v > 0 && v <= max ? v : null;
}

/** Now, as the value of an <input type="datetime-local"> in the device's time zone. */
export function nowLocalInput() {
	return toLocalInput(new Date());
}

/** A date as the value of an <input type="datetime-local"> in the device's time zone. @param {Date} d */
export function toLocalInput(d) {
	const pad = (/** @type {number} */ n) => String(n).padStart(2, '0');
	return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

/**
 * A typed 24 h clock time as "HH:MM", or null. Accepts "7", "730", "0730", "7:30", "7.30" and "19h30": the
 * phone's number pad has no colon. The app's own time fields are text, not <input type="time">, whose
 * display (12 h or 24 h) follows the browser's language and cannot be set by the page.
 * @param {string} text
 */
export function parseClock(text) {
	const s = String(text ?? '').trim().replace(/[.,h]/, ':');
	let m = /^(\d{1,2}):(\d{2})$/.exec(s) ?? /^(\d{1,2})(\d{2})$/.exec(s);
	const [h, mi] = m ? [Number(m[1]), Number(m[2])] : /^\d{1,2}$/.test(s) ? [Number(s), 0] : [NaN, NaN];
	if (!(h >= 0 && h <= 23 && mi >= 0 && mi <= 59)) return null;
	return `${String(h).padStart(2, '0')}:${String(mi).padStart(2, '0')}`;
}

/**
 * A typed length of time in minutes, or null: "45" or "45 min" (minutes), "2 h", "2h30", "2 h 30", "2:30",
 * "17h 50 min". @param {string} text
 */
export function parseSpan(text) {
	const s = String(text ?? '').trim().toLowerCase();
	let m = /^(\d+)\s*(?:min|m)?$/.exec(s);
	if (m) return Number(m[1]);
	m = /^(\d+)\s*(?:h|:)\s*(?:(\d{1,2})\s*(?:min|m)?)?$/.exec(s);
	if (m && Number(m[2] ?? 0) < 60) return Number(m[1]) * 60 + Number(m[2] ?? 0);
	return null;
}

/** Minutes as typed back into a span field: "45 min", "2 h 05". @param {number} min */
export function spanText(min) {
	return min < 60 ? `${min} min` : `${Math.floor(min / 60)} h ${String(min % 60).padStart(2, '0')}`;
}

/** A local day as "YYYY-MM-DD". @param {Date} d */
export function isoDay(d) {
	return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
}

/** Problem with a custom entry time, or '' when it is fine. @param {Date | null} when */
export function timeProblem(when) {
	if (!when || Number.isNaN(when.getTime())) return 'Time as hh:mm, 24 h.';
	if (when.getTime() > Date.now() + MAX_FUTURE_MS) return 'That time is in the future.';
	if (when.getTime() < Date.now() - MAX_PAST_MS) return 'That time is more than a year ago.';
	return '';
}
