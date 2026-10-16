// How activities are written: sport names, distance, duration, pace. Shared by the list and the run page.

const SPORT = { running: 'Run', cycling: 'Ride', swimming: 'Swim', training: 'Strength', walking: 'Walk', hiking: 'Hike' };

/** @param {{ sport: string, sub_sport: string | null }} a */
export function sportName(a) {
	const base =
		SPORT[/** @type {keyof typeof SPORT} */ (a.sport)] ?? a.sport.replace(/_/g, ' ').replace(/^./, (/** @type {string} */ c) => c.toUpperCase());
	if (a.sub_sport === 'treadmill' || a.sub_sport === 'indoor_cycling') return `${base}, indoor`;
	if (a.sub_sport === 'trail') return 'Trail run';
	return base;
}

/** "7.72 km", or '' without a distance. @param {number | null} m */
export function km(m) {
	return m === null || m === 0 ? '' : `${(m / 1000).toFixed(2)} km`;
}

/** "45:31" or "1:02:03". @param {number | null} s */
export function duration(s) {
	if (s === null) return '';
	const t = Math.round(s);
	const h = Math.floor(t / 3600);
	const m = Math.floor((t % 3600) / 60);
	const sec = String(t % 60).padStart(2, '0');
	return h ? `${h}:${String(m).padStart(2, '0')}:${sec}` : `${m}:${sec}`;
}

/** Pace for runs and walks, speed for rides, pace per 100 m for swims.
 * @param {{ sport: string, distance_m: number | null, moving_s?: number | null, elapsed_s?: number | null }} a */
export function pace(a) {
	const s = a.moving_s ?? a.elapsed_s;
	if (!a.distance_m || !s) return '';
	if (a.sport === 'cycling') return `${((a.distance_m / s) * 3.6).toFixed(1)} km/h`;
	const per = a.sport === 'swimming' ? 100 : 1000;
	const p = Math.round((s / a.distance_m) * per);
	return `${Math.floor(p / 60)}:${String(p % 60).padStart(2, '0')} /${a.sport === 'swimming' ? '100 m' : 'km'}`;
}

/** "Sat 10 Oct, 09:00". @param {number} t */
export const when = (t) =>
	new Date(t * 1000).toLocaleString('en-GB', { weekday: 'short', day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit', hour12: false });
