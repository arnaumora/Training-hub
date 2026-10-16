# Training Hub - Build Spec

The build spec the app was written against, kept up to date as it changed. In this public copy, personal values and the original server layout are replaced with generic ones.

## Purpose and scope

Training Hub is a single-user, self-hosted web app whose first job is to show, run by run, how running affects glucose, and to store enough context around each run to learn from it over time.

In scope:

- Automatic ingest of Garmin activities (twice a day) and Libre 2 glucose (every minute)
- A run detail graph with glucose, HR, pace and elevation on one time axis
- Per-run context: insulin on board, last meal, time of day, starting glucose and trend
- Cross-run comparison to find patterns
- Insulin and carbs log for pens (MDI), later a bolus calculator
- AI written analysis per run and per week, model selectable (Claude or Gemini)
- Calendar of planned vs done sessions

Out of scope: other users, sharing, public access, real-time alarms (the LibreLink app already does this), replacing LibreLink.

Operating assumptions: one user, iPhone plus laptop, and the phone and Garmin watch are carried on every run, so minute-level glucose is available during runs.

## Architecture

One compose stack in the server: a worker that pulls from Garmin and LibreLinkUp, Postgres as the single source of truth, and an API that serves the PWA and calls the AI providers.

```
Garmin Connect  --(twice a day)-->  worker  -->  Postgres
LibreLinkUp     --(every 60 s)--->  worker  -->  raw archive (./data)

Postgres  <-->  api + web (FastAPI, PWA, analysis jobs)  <-->  Claude / Gemini APIs

iPhone / laptop  <-->  Tailscale  <-->  Nginx Proxy Manager  <-->  api + web
```

The worker only writes; the API only reads raw tables and writes the manual log and derived results. Nothing is reachable from outside the tailnet.

Stack: Python 3.12 with FastAPI, fitdecode and pandas for the backend and worker; PostgreSQL 16; a SvelteKit frontend built as a PWA, with uPlot for the time-series panels.

## Interface design

The app should read like a well-made instrument, not a generic AI-generated site: the data is the interface and everything else stays quiet.

- Charts are the main element: dense, readable time series at full width, not big hero cards with one number each.
- Neutral greys plus one accent colour. Other colours only where they carry meaning: glucose range (low, in range, high) and HR zones.
- Palette "warm paper" (chosen 2026-10-15): warm neutrals, a soft ink and a teal accent. The current glucose number has its own three colours: green in range, ochre above it up to 250, red below the low or above 250, with the same word under it ("in range", "above range", "above 250", "below range") so colour is never the only signal.
- No gradients, glows, glass effects, emoji, oversized rounded cards or decorative illustrations. Sections of a page sit on flat panels (lighter surface, hairline border, 4 px radius, no shadow), never nested (added 2026-10-15, so sections are told apart).
- One sans-serif typeface, tabular numbers, tight but consistent spacing, units always shown.
- Light and dark themes, both designed, following the phone setting, with a manual light/dark switch in the app (added 2026-10-15).
- Structure (visual rework, 2026-10-15): four places, Now (current glucose, state, today's sessions, similar starts, the log), Training (calendar, week summary, activities), Analysis (compare, similar) and Food (the foods library), plus Settings in the header. On the phone a bottom bar with Log in the middle; on desktop the same in the top bar. A reading older than 15 minutes is shown as stale, never as current.
- Phone first for the run summary and the dose log, which must work one-handed right after a run. On a touch screen every control is at least 44 px tall; the Log sheet keeps Log above the keyboard and closes with the phone's back gesture (rework step 5, 2026-10-15); on a keyboard, L opens it (rework step 8, 2026-10-16). Comparison views can be desktop-first.
- A step only counts as done after screenshots of its screens are checked against these rules.

## Data sources

None of the automatic sources has an official personal API, so every route is treated as replaceable and every raw payload is archived locally.

| Source | Route | Frequency | Resolution | Main risk |
| --- | --- | --- | --- | --- |
| Libre 2 glucose | LibreLinkUp follower account, unofficial API | Poll every 60 s; each poll also backfills missing history | 1 min while phone is in range; 15 min sensor history otherwise | Abbott changes the API or headers |
| Garmin activities | python-garminconnect 0.3 or later (unofficial login), original FIT download | Twice a day plus a manual "sync now" | 1 s records | Login breaks after Garmin changes (it did in March 2026); ToS risk |
| Garmin fallback | Manual FIT upload in the UI or a watched folder | On demand | 1 s records | None |
| Training plan | Import of the existing .ics plan calendars | On import | Per session | None |
| Insulin and carbs | Manual entry in the PWA | When it happens | Exact time | Forgetting to log |
| Food composition (step 5) | CIQUAL 2025 table imported into Postgres (generic foods); Open Food Facts by barcode (packaged products), cached locally | Import once; barcode lookup on first scan | Per 100 g | CIQUAL has French and English names only; OFF label data is crowd-sourced |

Rejected routes:

- intervals.icu: Garmin changed its partner terms in October 2026 and Intervals expects to block Garmin-sourced data in its API ([forum thread](https://forum.intervals.icu/t/garmin-intervals-bridge-your-complete-garmin-fit-files-and-wellness-data-in-intervals-icu-with-or-without-the-official-garmin-integration/132863)).
- Nightscout: its strengths are sharing and watch display, neither needed for a single user. Polling LibreLinkUp directly means fewer services.
- USDA FoodData Central: US carbohydrate values include fibre, while EU sources (CIQUAL, EU labels and so Open Food Facts) exclude it. Mixing them would overcount carbs on fibrous foods.
- BEDCA: last version from 2010, personal use only, no download or API.

Raw archive: every FIT file and every LibreLinkUp response is stored under `./data/raw/`, so a broken pipe costs convenience, not data.

## Data model

PostgreSQL, all timestamps stored in UTC and shown in Europe/Madrid. Raw tables are written by ingest; derived tables are recomputed by idempotent jobs and can be dropped and rebuilt at any time.

| Table | Kind | Key fields |
| --- | --- | --- |
| glucose_reading | Raw | minute (PK, UTC), ts (exact sensor time), mg_dl, trend, source (llu_live, llu_graph, libreview_csv) |
| collector_status | Ops | name, last_attempt_at, last_success_at, last_error_at, last_error, consecutive_failures |
| activity | Derived from raw FIT (step 6) | fit_key and session_index (one row per FIT session), source (upload, garmin), garmin_activity_id, sport, sub_sport, start_ts, end_ts, elapsed_s, moving_s, distance_m, avg_hr, max_hr, ascent_m, descent_m, calories, avg_speed_mps, device, fit_path, fit_sha256, parser_version |
| activity_record | Derived from raw FIT | activity_id, ts, elapsed_s, distance_m, hr, speed_mps, altitude_m, cadence, lat, lon (degrees) |
| activity_lap | Derived from raw FIT | activity_id, idx, start_ts, elapsed_s, moving_s, distance_m, avg_hr, max_hr, avg_speed_mps |
| garmin_sync_run | Ops (step 6) | trigger (schedule, request, startup, retry), requested_at, started_at, finished_at, ok, login_needed, checked, imported, skipped, error. History of sync runs and the queue for "Sync now" |
| garmin_activity_seen | Ops (step 6) | garmin_activity_id, status (imported, skipped), detail, seen_at |
| insulin_dose | Manual | id, ts, units, kind (rapid, basal), insulin_name, note, deleted_at. Step 4 adds settings_version for doses from the calculator |
| carb_intake | Manual | id, ts, grams, entry (grams, raciones; meal from step 5), racion_g (racion size used, for raciones entries), description, deleted_at |
| log_audit | Audit | table_name, row_id, action (update, delete), old_row, new_row, at. Written by a database trigger on every change to the log tables |
| food | Reference (step 5) | source (ciqual, off, own, recipe), source_id (CIQUAL code, or the barcode for off), name, own name or alias, per 100 g: kcal, carbs, sugars, fibre, protein, fat, salt; barcode (off and own foods; at most one live food per barcode); batch_g (recipes: weight when done), archived_at (own foods and recipes: hidden, never deleted) |
| recipe_item | Manual (step 5) | recipe_id, position, food_id, grams. A recipe's ingredients; its values per 100 g are computed from them by the API |
| meal, meal_item | Manual (step 5) | a meal's time and note; each item's food, grams and the per-100 g values copied at logging time, so later food edits never change past meals |
| planned_session | Import (step 9) | calendar (X-WR-CALNAME), uid (unique), day (local), start_ts, end_ts (empty for all-day), sport (running, swimming, cycling, strength, rest, other), title, description, planned_m (distance read from the text), removed_at (no longer in the calendar's newest file) |
| plan_import | Import (step 9) | calendar, imported_at, raw_path (the .ics as received, under the raw archive), sha256, added, updated, unchanged, removed |
| plan_link | Manual (step 9) | planned_session_id (PK), kind (activity: this activity did it, any day; done: done without an activity; none: not done, no automatic match), activity_id (unique). Overrides the automatic match for one session |
| run_context | Derived (step 7) | activity_id, computed_at, insulin_settings_version, log_covered, iob_at_start, min_since_last_bolus, last_bolus_units, last_meal_carbs, min_since_last_meal, pre_run_carbs, glucose_start, trend_start, h_since_basal, local_time |
| run_glucose_metrics | Derived (step 7) | activity_id, computed_at, training_settings_version, glucose_start, glucose_end, nadir, nadir_minute, delta, rate_per_10min, min_below_low, coverage_pct, low_coverage, readings |
| run_training_metrics | Derived (step 7) | activity_id, computed_at, training_settings_version, time_in_zone (JSON, seconds per zone), hr_drift_pct, avg_pace_s, grade_adjusted_pace_s |
| activity_note | Manual (step 7) | activity_id, note (up to 2000 characters), updated_at. Kept when the derived tables are rebuilt |
| ai_report | Derived (step 8) | scope (activity, or week with ref_id the Monday as YYYYMMDD), ref_id, created_at, provider, model, prompt_version, input_sha, input_tokens, output_tokens, removed (sentences taken out by the dose guard), text. Every report is kept |
| ai_settings | Config (step 8) | version, created_at, default_provider, claude_model, gemini_model, auto_report. Append-only |
| ai_job | Ops (step 8) | kind (run_report, week_summary), ref_id, provider, trigger (import, request, schedule), requested_at, started_at, finished_at, attempts, next_try_at, ok, error. The analysis service's queue |
| ai_call | Ops (step 8) | at, provider, model, purpose, ref_id, ok, input_tokens, output_tokens, ms, error. One row per provider call: the cost record |
| ai_service | Ops (step 8) | one row: seen_at, and whether each provider has a key. Written by the analysis service, read by the API |
| api_token | Config (step 8) | name, token_sha256, created_at, last_used_at, revoked_at. Read-only tokens for the Claude desktop connector |
| insulin_settings | Config (step 4) | version, created_at, note, meal_slots (name, start time, units per racion), isf, correct_above, correct_to, action_time_h, action_time_provisional, pen_increment, racion_g, rapid_insulin. Append-only: every save is a new version |
| training_settings | Config (step 7) | version, created_at, note, lthr, max_hr, resting_hr, hr_zone_floors (lowest bpm of zones 2 to 5), glucose_low, glucose_high, lag_min, pace_zones (step 9: easy, long, steady, tempo, threshold, race; seconds per km, fastest first). Append-only like insulin_settings; version 1 holds the run log's values, version 2 adds the run log's pace zones (revised 2026-10-11) |

Starting settings values come from the project run log: LTHR 191, max HR 214, resting HR 49, the five Friel HR zones and the revised pace zones.

## Glucose collector

The collector runs permanently and polls every minute, because LibreLinkUp only keeps a short, coarse history: a minute not captured live is lost at full resolution.

1. Every 60 s: one request to the LibreLinkUp graph endpoint. It returns the current reading and about 12 h of 15-minute history. Insert the current reading with source `llu_live` and any missing history points with source `llu_graph`. (Changed 2026-10-12: this replaces a separate daily refetch, because every response already carries the history, so gaps fill within a minute of LibreLink uploading them.)
2. One row per minute; an existing row always wins, so a coarse point never overwrites a live one.
3. Cache the session, re-login on 401, wait as asked on 429, back off exponentially (up to 15 min) on other errors. HTTP calls time out after 20 s.
4. Store each raw response in the archive before parsing (one gzip JSON-lines file per UTC day, auth ticket removed).
5. Record every attempt in `collector_status` (last success, last error, consecutive failures). `/health/glucose` uses it to tell "collector failing" (an outage, alert) apart from "no new readings because LibreLink is closed on the phone" (expected, no alert).
6. Provide a LibreView CSV import for historic backfill and for days the collector was down.

Measured with the probe on 2026-10-12: the graph history is 15 minutes apart; live readings arrive once a minute, about 15 s old, but only while LibreLink is running on the phone (swiped away = no minute data, only 15-minute backfill on reopen). Usage decided 2026-10-12: LibreLink is kept open during runs; outside runs 15-minute data is enough.

Client: a maintained Python LibreLinkUp library (for example pylibrelinkup) pinned to a version, or a thin client written against the documented community API. Region EU. Credentials are those of the dedicated follower account, never the main LibreLink account.

Verify in the first week: the real resolution of the graph endpoint for a Libre 2, and how readings taken without mobile data arrive once the phone reconnects.

## Garmin sync and activity pipeline

A new run should appear fully analysed within 12 hours with no action, and every step after the download must be re-runnable on its own.

Sync:

- Scheduled twice a day (13:00 and 22:00 Europe/Madrid, daylight saving handled) plus a "Sync now" button in Training > Activities, plus one run at start-up when nothing succeeded in the last 12 hours (so a restart or deploy catches up). A run that failed for a temporary reason (network, Garmin down, rate limiting) is retried once 30 minutes later; a run that needs a login is never retried automatically.
- Runs in its own container (`traininghub-garmin`, same image as the API, 256 MB). "Sync now" queues a row in `garmin_sync_run`, which the service checks every 30 s; the button allows one run every 2 minutes (a run that stopped for a missing login does not count, it never contacted Garmin).
- Login through python-garminconnect. Tokens persist in a volume with mode 0600. The first login (with 2FA if enabled) is done once through a CLI command inside the container.
  - Background (checked 2026-10-13): Garmin's login protection started rejecting the library in March 2026; `garth`, the auth library it used, was deprecated, and garminconnect 0.3.0 (April 2026) rewrote the login to look like Garmin's own apps. It had 8 more releases in August and September 2026, so breakage is expected now and then and usually fixed upstream within days or weeks.
  - So the password is never stored: only the tokens, which the library refreshes on its own. When they stop working, the sync stops, the alert fires, and the login command is run again. A failing login is never retried automatically, so it cannot get the account rate-limited or locked.
  - The service creates the library client without credentials, so a missing or rejected token raises instead of attempting a password login, and the token file is left untouched (checked in the garminconnect 0.3.17 source; the version is pinned exactly so an upgrade is always a deliberate, tested change).
  - Login command, on the server: `docker exec -it traininghub-garmin python -m app.garmin_login` (add `--fresh` to forget the saved tokens first). It asks for the e-mail, the password (hidden) and the 2FA code if Garmin asks, saves only the tokens, and queues a sync.
- For each activity newer than the last sync: download the original FIT, archive it raw, then hand it to the parser.
  - Window: the first run fetches `GARMIN_BACKFILL_DAYS` (default 30); later runs start 2 days before the newest synced activity. Each Garmin activity id is remembered in `garmin_activity_seen` (imported or skipped, for example an activity typed in by hand on Garmin Connect, which has no file), so nothing is downloaded twice. Downloads are 2 s apart, one transaction per activity.
  - A file already uploaded by hand is linked to its Garmin id, not duplicated, and does not count as new.
- `/health/garmin` reports the last successful sync. It fails after 36 hours without one, and at once when the last run needed a login. A host watcher (hourly, from cron) alerts after 2 failed checks in a row and once on recovery.

Parser: fitdecode reads sessions, laps and records into `activity`, `activity_lap` and `activity_record`. Manual upload and the watched folder call the same parser.

- Manual upload (Training > Activities, built first in step 6) takes a .fit or the .zip from Garmin Connect's "Export original". The bytes are archived as received under `raw/fit/<2 hex>/<sha256>.<fit|zip>` before parsing; a file that cannot be parsed is still kept.
- One activity per FIT session, so a multisport file (a brick, a triathlon) gives one activity per leg; at a transition, the shared second belongs to the leg that starts.
- A file is identified by its file_id (manufacturer, serial number, time created), else its SHA-256. The same file arriving twice (upload and sync, or a .fit and its .zip) is imported once; importing again replaces its laps and records and keeps the activity ids.
- `python -m app.fit_import --reparse` rebuilds every activity from the raw archive after a parser change.

Post-processing, in order, each step idempotent:

1. Join glucose from 60 min before start to 120 min after end.
2. Compute `run_context` from the insulin and carbs log.
3. Compute `run_glucose_metrics`, including `coverage_pct` (share of minutes with a reading). Runs under 80% coverage are flagged.
4. Compute `run_training_metrics`: time in zone from the settings zones, HR drift, grade-adjusted pace.
5. Queue the AI report.

Steps 2 to 4 are built (step 7, `app/derive.py` calling the pure functions in `app/run_metrics.py`); step 1 happens when the graph is read. The three derived rows of an activity are replaced whole, and are recomputed:

- right after the activity is imported (upload or sync);
- right after a Log entry is added, edited or deleted, for every activity whose context it can change (the entry is in the 48 h before the start, or during the activity; an edit counts both the old and the new time);
- when a run page is opened and its rows are missing, from an older Settings > Training version, or the activity ended under 14 h ago (LibreLinkUp backfills up to 12 h of history, so its glucose may still be arriving);
- every 10 minutes in the garmin service, for the same stale rows;
- by hand: `python -m app.derive --all` rebuilds every activity (after a formula change), `--stale` only the stale ones.

Swims and gym sessions go through the same path. Their graph shows glucose and HR only, since wrist HR in the pool is unreliable and pace has no meaning in the gym.

## Run detail graph and per-run context

The activity page is the core screen: stacked panels on one shared time axis, so a single hover shows glucose, HR and pace at the same moment.

Panels, top to bottom:

1. Glucose (mg/dL): sensor line (since 2026-10-16 the same as the home chart: one smooth line through live and 15-min history readings, broken only where readings are more than 25 min apart, coloured by range like the current number), target band 70 to 180, optional lag-corrected line shifted earlier by a configurable offset (default 8 min), markers for insulin and carb events. The window runs from 60 min before to 120 min after the run, with the run itself shaded, because post-run drops matter as much as the run.
2. Heart rate with the five zone bands from settings.
3. Pace on an inverted axis.
4. Elevation.

Behaviour: synced crosshair and tooltip across all panels, x-axis switchable between time and distance, laps marked as vertical rules. Charting library: uPlot, chosen for dense time series and synced cursors.

Built in step 6 (2026-10-13), from `GET /api/activities/{id}/graph` (summary, laps, 1 s records as columns, glucose in the window, doses and carbs in the window):

- Three views: the glucose window (1 h before, 2 h after, run shaded), the run only, and by distance (the run only; glucose placed at the distance covered at its time). One cursor across all panels; on a phone a horizontal drag moves it and a vertical one scrolls. A line above the panels reads time, time from the start, distance and lap at the cursor; each panel shows its value there, or the activity's average when the cursor is away.
- Pace (runs, walks, hikes) on an inverted axis, its range from the 2nd to 98th percentile; speed in km/h for rides. Both smoothed over 15 s for display only; the stored records are untouched. No pace below 1 m/s (a stop).
- Swims and gym sessions show glucose and heart rate only.
- The lag-corrected glucose line is a toggle, offset 8 min, until the settings table holds the offset.
- Below the graph: the laps table and what was logged from 1 h before to 2 h after.
- Not in step 6: the heart-rate zone bands (they need the zones in Settings, built with time in zone in step 7) and the header tiles and context panel (step 7).
- Step 7, change 1 (2026-10-13): Settings > Training (zones, glucose target range, lag offset; a helper fills the Friel zones from LTHR). The heart-rate panel draws the five zones as bands in an ordered single-hue ramp, slate blue since 2026-10-15 (violet before; light to dark as intensity rises, reversed in dark mode, validated against both backgrounds), each zone floor as a thin line, and the readout names the zone. The target band (both charts) and the lag line follow the settings.

Header tiles: glucose start, end, nadir, change, rate per 10 min, IOB at start, minutes since last meal, coverage.

Context panel beside the graph: all `run_context` fields, a free-text note, and a quick "add pre-run carbs" action for logging after the fact, which recomputes the derived tables.

Built in step 7, change 2 (2026-10-13). On a phone there is no room beside the graph, so the page is one column: the glucose figures above the graph (plain figures, not tiles, per "Interface design"), and below it time in zone, the training figures, the context ("Before the run", "Before the ride"...), "Add pre-run carbs" and the note. An unknown value says why ("before the log started", "none in the 24 h before") instead of showing 0.

Page order since the visual rework (step 4, 2026-10-15): the activity and its figures; the safety flags, first, each on a tint of its colour (going below the low in red, the others in amber); "Glucose" with the coverage as a quiet badge (dashed under 80 %); the curves at the full content width, taller on a wide screen; the glucose figures; the log and data flags as quiet lines; the earlier runs of the same sport that started most like this one (the same search the AI report gets: only runs that started before it); then time in zone, the context, the note, the AI analysis, the laps and the log. Text keeps a reading width. Since 2026-10-16 the context leaves out what the header and the glucose figures already show (start time, start glucose, and IOB unless it is unknown), and the laps and the log show their first 5 rows with "Show all" when longer.

Definitions:

- Glucose start and end: the reading nearest the start (or end), if one is within 10 min; otherwise unknown. Change is end minus start; rate per 10 min is that change over the time between the two readings (at least 5 min apart).
- Lowest (nadir) and its minute from the start: the lowest reading from the start to 2 h after the end, so a post-run drop counts.
- Minutes below the low (Settings > Training, 70): over the same span; a live (1-min) reading counts 1 min, a history (15-min) reading 15 min.
- Coverage: the share of the activity's minutes that have a reading of their own. Under 80 % is flagged: the curve is mostly 15-min history (the phone was not connected to the sensor over Bluetooth).
- The log covers a run only if the first log entry (dose or carbs) is before its start. Otherwise IOB, last dose, last meal, pre-run carbs and last basal are unknown, never 0.
- IOB at start: the calculator's insulin_on_board (linear decay) over the rapid doses before the start, with the action time from the Settings > Insulin version in force at the start (before the first version, the first one). Without Settings > Insulin there is no IOB.
- Last rapid dose and last carbs: the latest before the start, within 24 h. Pre-run carbs: everything from 1 h before the start to the end, gels during included. Last basal: within 48 h.
- Time in zone: seconds per Settings > Training zone; a gap between records counts at most 5 s (pauses do not count).
- HR drift (Pa:HR decoupling): speed per heartbeat in the first half against the second half of the moving time (speed at least 1 m/s, 20 min moving minimum), in percent; positive means the heart worked harder for the same speed.
- Grade-adjusted pace (runs, walks, hikes): each 20 m segment's pace scaled by Minetti's energy cost of running at its grade (2002) against flat ground, altitude smoothed over 15 s, grades held to +-45 %. Shown next to the moving pace.

Flags (added 2026-10-13, `app/run_metrics.flags`): what a reader must not miss, decided by code, shown on the run page (safety ones first, the others under the glucose figures) and given to the AI report, which must mention each. From the exercise consensus (Riddell et al., Lancet Diabetes Endocrinol 2017): glucose above 270 mg/dL at the start or during (check ketones when unexplained; no exercise with blood ketones of 1.5 mmol/L or more), above 250, a start under 90, going below the low, an afternoon or evening session of 45 min or more (delayed low overnight); no rapid insulin logged in the 24 h before (IOB is 0 only if nothing went unlogged), a start before the log; no reading near the start, coverage under 80 % (live readings need the phone connected to the sensor over Bluetooth). Flags describe; they never suggest a dose.

## Cross-run comparison

Learning comes from comparing runs, not from any single graph, so this view gets equal weight to the run page.

- Overlay: every run in a filter (sport, date range, distance) drawn as glucose change from start against minutes, coloured by one chosen context variable (IOB at start, start glucose, time of day, minutes since meal).
- Scatter: rate per 10 min against a selectable variable, one dot per run, click through to the run.
- Similar runs: given a starting state (glucose, trend, IOB, minutes since meal), list the closest past runs and what happened.
- Every chart shows its n. At 2 to 3 runs a week, expect about two months (20 to 25 runs) before patterns are worth trusting.

Built in step 7, change 3 (2026-10-13): Activities > Compare (Analysis > Compare since the rework), from `GET /api/compare` (filters: sport, days back, distance range; every activity in the filter with its stored numbers and its glucose as change from the start, to 2 h after the end).

- Filters in one row: sport (with counts), period (all time, last 30 or 90 days, last 12 months), distance (any, under 5 km, 5 to 10, 10 to 21, over 21). The choices are kept while the app is open, so going back from a run returns to the same view.
- Overlay: each activity's glucose change from its start against minutes, starting at 0 by definition. Colour by IOB at start, glucose at start, time of day, time since last meal or carbs from 1 h before (added to the four above: the pre-run carbs are the main lever before a run). Five equal-width steps of one hue (an ordinal ramp in the glucose cyan, light = low in the light theme, brighter = higher in the dark one, both validated against the page background); an unknown value (before the log started) in grey; under 80 % coverage dashed; the end of each activity a dot on its line.
- Scatter: change per 10 min against any of those variables, or duration, average heart rate or distance. One dot per activity, hollow under 80 % coverage.
- Hover a line or dot to read it, click to open the run; on a phone, tap to read it and "Open run" to open it.
- A table of every activity in the filter (also those without a reading at the start, which the charts cannot draw) with its numbers and the colour variable: the readable form of both charts.
- Each chart states its n, and how many in the filter it could not draw.

Built in step 7, change 4 (2026-10-13): Activities > Similar (Analysis > Similar since the rework), from `GET /api/compare/similar` and `GET /api/compare/now`.

- A starting state of up to four parts: glucose, trend, IOB, time since the last carbs (typed as 45 min, 2 h 30 or 2:30, shown in hours and minutes since 2026-10-16). Empty parts do not count. "Use now" (done by itself when the screen opens with nothing filled in, since rework step 6) fills them from the latest reading (if under 15 min old) and the log, with the same definitions as the per-run context, and says what it could not fill.
- Closeness: the square root of the summed squared differences in units of 30 mg/dL, one trend step, 1 u of IOB and 60 min since the last carbs (both sides held to 5 h; a logged run with no carbs in the 24 h before counts as 5 h). A run is a candidate only with a glucose at its start and a value for every part given; the rest are counted as left out, never guessed. Ties go to the more recent run.
- Shows the 5 closest (the sport is a filter). Since 2026-10-16 they use the same compact rows as Now and the run page (date, start glucose, change, lowest), each with one quiet line: how it started (trend, IOB, last carbs, carbs from 1 h before) and what to know (minutes below the low, coverage under 80 %). The rate per 10 min and when the lowest came are on the run page. Above them: the median change, the lowest reading and how many went below the low, with n and how many were left out.
- Worded as history, not a prediction, and it never mentions a dose.

Later: the post-run window (delayed lows in the evening or overnight after hard or long sessions).

## Insulin and carbs log, and calculator safety rules

The log ships early (roadmap step 3) because every run without context is a run you can barely learn from. The calculator follows right after it (roadmap step 4, moved forward on 2026-10-12), and since 2026-10-16 (the owner's decision) it is usable from the first day: for a new rapid dose logged now, the suggestion (carbs part plus correction, or the correction alone when no carbs are entered) fills the insulin field and is recomputed as carbs, raciones or a meal's foods are entered; the field follows it until you type in it, and Log saves the meal and whatever the field shows together. Logging carbs without insulin means clearing the field. Until 14 days after the first logged rapid dose, a suggestion with a correction warns that IOB counts logged doses only, so the correction may be too high (before 2026-10-16 the suggestion was marked not active for those 14 days and only a tap on "Use" filled the field).

Log:

- One Log button opens one form with a carbs field and an insulin field (units, rapid or basal); fill either or both. Both are saved in one transaction with one time (defaults to now, editable) and one note, and one Undo removes both. Target: under 10 seconds per dose. In step 4 the calculator fills the insulin field of this same form (decided 2026-10-12).
- The insulin name is pre-filled from the last logged dose of the same kind, never from a value in code; the first dose of each kind is typed once.
- Carbs in grams or raciones. Stored as grams; a raciones entry also stores the racion size used. The racion size comes from Settings > Insulin (step 4); until it is saved, carbs can only be entered in grams.
- Deletes are soft (the row is kept with deleted_at), and a database trigger copies every edit and delete into log_audit.
- Saved meals come with food entry in step 5.

Food entry (roadmap step 5, decided 2026-10-12):

- Three ways to enter carbs: raciones, grams, or a meal built from foods with their weights.
- Foods come from the CIQUAL 2025 table (generic foods; the official ANSES XML release on Zenodo, record 17550133, archived under `raw/ciqual/<release>/` and imported by `python -m app.ciqual_import`, which matches the constituents by name and reports what it used; searched locally), Open Food Facts by barcode (packaged products, fetched once and kept locally; the API allows 15 product reads per minute and no search-as-you-type, so search is always local), and own foods and recipes (ingredients plus batch weight give values per 100 g).
- All carb values follow the EU convention: available carbohydrate, fibre excluded. Fibre is shown separately.
- Any food can get an own name or alias (for example a Spanish or Catalan name, since CIQUAL has none); own names rank first in search.
- A meal shows its totals: carbs, raciones, kcal, protein, fat, fibre. Logging it writes one carb_intake row linked to the meal. Saving and repeating meals is deferred to the layout and organization pass after the last step (decided 2026-10-13); step 5's acceptance check does not need it.
- A Foods screen (header link, decided 2026-10-13) lists your foods (own foods, recipes, and foods you named or ate), finds any other food, and adds own foods and recipes:
  - Own food: a name and values per 100 g typed from a label. Carbs are required; any other value may stay empty. Typo guards: every value 0 to 100 g (energy 0 to 900 kcal), sugars not above carbs, carbs + protein + fat + fibre at most 102 g per 100 g (room for label rounding).
  - Recipe: ingredients with weights and, optionally, the weight when done (the whole pot weighed after cooking; empty means the sum of the ingredients). The API computes each value per 100 g as sum(grams x value / 100) / weight x 100, rounded half up; the screen only previews it. A value is empty when any ingredient has none. An ingredient without a carb value is refused, and recipes are never ingredients.
  - Editing an own food recomputes every recipe that uses it, and a CIQUAL import recomputes all recipes, in the same transaction. Past meals never change.
  - Deleting an own food or recipe hides it from search and lists; past meals and recipes that use it keep working.
  - The names you give foods (own foods, recipes, own names for CIQUAL and Open Food Facts foods) are unique, case and accents aside, so two foods can always be told apart in a search or a meal (added 2026-10-16; a deleted food frees its name). Duplicates saved before that stay until one is renamed or deleted.
- Barcodes (decided 2026-10-13): a Scan button next to every food search (Log form, recipe ingredients) and on the Foods screen reads EAN-13, EAN-8 and UPC-A with the phone camera (ZXing-C++ as WebAssembly, bundled and served by the API, loaded only when scanning; Safari has no built-in barcode reader), or takes the number typed. The check digit is verified first; a wrong one is a misread and is never looked up.
  - Lookup order: your own food with that barcode, then a product fetched before, then Open Food Facts once (custom User-Agent with the contact from `OFF_CONTACT`; at most 12 reads a minute, under OFF's 15). Every OFF answer is archived raw under `raw/off/` before parsing.
  - An OFF product is stored only if it has carbs per 100 g and passes the own-food typo guards. Otherwise, or when OFF does not know the code, the app opens New food with the barcode and whatever usable values OFF had; that own food then takes the barcode, so your values win from the next scan on.
- No insulin number appears anywhere before step 4.

Calculator, with the parameters of the current Settings > Insulin version and the meal slot at the current local time (`api/app/calculator.py`):

```
carbs part  = R * r_slot
correction  = (G - G_to) / ISF   if G > G_above, else 0
net corr.   = max(0, correction - IOB)
D           = carbs part + net corr., rounded down to the pen increment
```

where R is raciones, r_slot is units per racion for that meal slot, G is the newest glucose reading, G_above and G_to are "correct above" and "correct down to", ISF is the sensitivity and IOB is insulin on board from logged rapid doses, each counting fully when injected and fading linearly to zero over the action time.

IOB only offsets the correction, never the carbs part (decided 2026-10-12, at the owner's request not to be too conservative): insulin from an earlier meal is mostly covering that meal's carbs, still being absorbed, so subtracting it from a new meal would count it twice; subtracting it from a correction prevents stacking corrections on a high that earlier insulin is already bringing down. No dose is suggested below 70 mg/dL, and no correction is computed from a reading older than 30 minutes. Every dose logged from a new entry stores the suggestion that was on screen (used or not) and its settings version.

Non-negotiable rules:

1. Parameters (ratio per meal slot, ISF, target, insulin action time, insulin name) come from the endocrinologist and are entered by hand. Neither the app nor the AI ever changes them.
2. The dose is computed by deterministic, unit-tested code. The AI never outputs a dose.
3. Every result shows each term of the formula and is rounded down to the pen increment. Logging it requires a confirm tap: Log, whose label names the units, with the amount in plain view and editable.
4. IOB counts logged doses only. The screen says so, and warns when no rapid dose has been logged in a long time.
5. Warnings shown, not hidden: exercise in the last or next few hours, glucose falling fast, sensor reading older than 10 minutes.
6. The AI may suggest carbs from a description or photo, always labelled as an estimate and always editable before use.

### Settings menu

Settings > Insulin is the only place these values change.

- One editable row per meal slot: name, start time (set by you), ratio in units per racion. Each slot runs from its start time until the next slot starts, wrapping past midnight, so every minute of the day belongs to exactly one slot. Slots can be added or removed.
- Fields for ISF, correct above, correct down to, action time, pen increment, racion size in grams and insulin names.
- Saving shows old and new values side by side and needs a confirmation. Values outside a plausible range (set in config) need a second confirmation.
- Every save creates a new dated version with an optional note, for example "endo visit". Old versions are kept, and each logged dose stores the version it was calculated with, so any past calculation can be reproduced.
- Analysis and AI can flag "review suggested" with the data behind it (for example lunch ending high on most days). Only a manual save changes a value.

## AI integration

Code computes every number; the model only interprets and compares. Its answers get better as the database grows, because more relevant history goes into each request, not because the model itself learns.

- Provider adapter with Claude and Gemini behind one interface, selectable per request, default in settings. API keys in the env file. API usage is billed per token, separately from any chat subscription; every call logs its tokens.
- Input per run: derived metrics, run context, zones, the 5 most similar past runs and their outcomes, and downsampled series (glucose at 1 min, HR and pace at 30 s). Never raw 1 s streams.
- Outputs: a stored report per run, a weekly summary, and a chat that can call read-only database tools to answer open questions.
- Optional: a read-only MCP server over the same tools, so the data can be queried from the Claude app on the phone.
- Decided 2026-10-13 (hybrid, to keep the running cost near zero): the per-run report and the weekly summary run on the Gemini API (`gemini-3.8-flash` by default); the Claude API stays supported but its key is optional. The open questions go to Claude in the Claude desktop app on the PC, on the subscription, through a local MCP connector that reads the Training Hub over Tailscale (it replaces the in-app chat). Claude.ai web and phone connectors are called from Anthropic's cloud and would need the app on the public internet, so they are not used. On the Gemini free tier Google uses the content to improve its products (the paid tier does not); the tier is set on the Google project, not in the app.
- Privacy: health data goes to the provider. A setting sends only derived numbers, without timestamps or notes, for anyone who prefers that.
- The local Ollama on the server is not used for analysis (an 8 GB GPU is too small for this quality), only possibly for cheap tasks later.

Built in step 8, change 1 (2026-10-13): run reports.

- Providers: Claude (Messages API) and Gemini (generateContent) behind one function, plain HTTPS without SDKs (`app/ai_providers.py`). Settings > AI (append-only versions) holds the default provider (Gemini since migration 0016), the model of each (defaults `gemini-3.8-flash` and `claude-sonnet-5-5`) and whether reports are written by themselves. A report written by itself goes to the default provider, or to the other one when only it has a key.
- The analysis service (`traininghub-analysis`, same image, 128 MB) works through a job queue (`ai_job`): one job at a time; a busy answer (5xx, overloaded) is first tried again within the call after 5 and 20 s; temporary failures (timeouts, 429, 5xx) are then tried again after 2, 4 and 8 min (the run page says when), lasting ones (no key, bad request, refusal) end the job with their error. It alone holds `ANTHROPIC_API_KEY` and `GEMINI_API_KEY`; every minute it writes a status row (`ai_service`: alive, which keys are set) that the API reads.
- Jobs come from an import (only activities that ended in the last 48 h, so the first sync's 30-day backfill does not pay for a dozen reports) and from the run page ("Write with Claude", "Write again with Gemini").
- Input (`app/ai_input.py`): the activity summary and laps, Settings > Training zones and target, the stored glucose, context and training numbers, the note, what was logged from 6 h before to 2 h after, glucose as recorded from 60 min before to 120 min after, heart rate, pace (or speed) and altitude in 30 s means, and the 5 most similar past runs (app.similar) with their outcomes. Times as minutes from the start. A hash of everything except the similar runs is stored with the report, so the page can say when the run's own data changed since.
- The prompt (version run-5) asks for five sections (Summary, Glucose, Training, Compared with similar runs, Worth noting), under 300 words, explaining rather than reciting numbers, mentioning every code-decided flag, and saying "per the log" for insulin and carbs. It forbids any insulin dose or change to insulin settings; "Review suggested:" with the data is the only allowed form. A reference text, `app/ai_guidelines.md`, is part of it: the athlete's devices (how Libre 2 live readings depend on Bluetooth, sensor lag, what the log can and cannot show) and exercise physiology from the consensus statement. Changing it means raising the prompt version.
- The dose guard (`app/ai_guard.py`) does not trust the prompt: a sentence that mentions insulin (or an amount like "2 u") and advises, instructs or puts a change verb before the insulin is replaced by a visible note before the report is stored, and the report records how many were removed. It is deliberately broad.
- Every call is recorded in `ai_call` (provider, model, tokens, time, error). Settings > AI shows the calls and tokens of the last 30 days per provider.
- Every report is kept; the newest is shown under "Analysis" on the run page, with who wrote it, when, its tokens, and notes when it is out of date or when the guard removed something.
- Not built: the setting that sends only derived numbers. The open question below settled on full data.
- Next: the weekly summary (change 2), then the Claude desktop connector (change 3), possibly with one write tool that saves Claude's analysis as a report on a run, through the same dose guard.

Built in step 8, change 2 (2026-10-13): the week.

- The week's figures, under Training > Calendar's week view since rework step 6 (2026-10-15; a separate Week summary screen before, Activities > Week before that), with the week's flags above the calendar's days (lows in red) and the sessions list dropped (the calendar shows each one), from `GET /api/weeks/{monday}` (`app/week.py`): Monday to Sunday in local time, earlier and later weeks, the current one marked as still running. It shows, all computed by code: the flags; the load per sport and minutes per heart-rate zone; each session (start glucose, change, lowest, a pointer to its notes when it has a safety flag); glucose for the week against the international time-in-range consensus (Battelino et al., Diabetes Care 2019: over 70 % in 70-180, under 4 % below 70, under 1 % below 54, under 5 % above 250, read with at least 70 % coverage), with the week before; the night after each training day (22:00 to 07:00) and its minutes below the low; each day's carbs, rapid and basal insulin as logged, and time in range.
- Glucose shares are time-weighted: each reading stands for the time until the next one, at most 1 min (live) or 15 min (history).
- Week flags: sessions above 270, lows during or after sessions, lows overnight after training, time below 70 at or above 4 %, time above 250 at or above 5 %, days with no rapid insulin logged, coverage under 70 %. Time before the app collected glucose or kept the log is not a gap: a week before either started says so once, a week in which one started counts from that day (days before the log are not listed as missing insulin, coverage counts from the first reading).
- The weekly summary: same pipeline as the run reports (provider, queue, dose guard, kept versions, out-of-date note), its own prompt (week-2: patterns across sessions and days, with how many sessions each rests on; under three is "something to watch"), the same reference text, which now also holds the time-in-range targets and calls HR drift cardiac drift. The analysis service queues it by itself on Monday from 06:00 for the week that ended, once, when reports are written by themselves and the week has glucose readings; any week can be written on request.
- Prompts now run-5 and week-2: the reference text says that missing data from before the app collected it is not the athlete's doing.

Built in step 8, change 3 (2026-10-13): the Claude desktop connector.

- `connector/traininghub_mcp.py`: a local MCP server (stdio) that Claude Desktop on the PC starts with uv; it reads the Training Hub over HTTPS on the LAN or Tailscale and runs on the Claude subscription. Tools: list_activities, get_activity, get_week, similar_runs, glucose_between, log_between, current_state, training_settings. Its instructions carry the same reference text as the reports, the "per the log" and n rules, and the app's rule: no insulin doses or setting changes, "Review suggested" instead. Insulin settings are not offered. Set-up in `connector/README.md`.
- Read-only API tokens (`api_token`, `python -m app.api_tokens create | list | revoke`): only the SHA-256 is stored; `Authorization: Bearer` counts as signed in for GET and HEAD only, never for the barcode lookup (it stores products); anything that changes data still needs the password session.
- Connector endpoints (`app/connector_api.py`): `/api/connector/reference`, `/api/connector/activity/{id}` (the run report's input plus the newest report), `/api/connector/glucose` (up to 14 days, 15-min buckets over 12 h, with figures), `/api/connector/log` (up to 62 days); times out in local time.
- Not built: a tool that saves Claude's analysis into the app (would need a write scope; left for later).

## Calendar: planned vs done

The calendar shows each day's planned session next to what was actually done, with the run's glucose change as a small badge.

- Import the existing .ics plan calendars (runs, swims, gym). Re-imports update sessions by their UID.
  - Settings > Plan imports one or more files at once. Each file is archived under `raw/plan/` before it is read (`app/ics.py`, a small RFC 5545 reader: no repeating events). A calendar is known by its name; a session no longer in its newest file is marked removed, not deleted. `python -m app.plan --reimport` rebuilds the sessions from the newest raw file of each calendar.
  - Sport comes from the event's category (RUN, RACE, SWIM, GYM, REST...), else from words in the title. The planned distance comes from the text: "About 9 km" in the description, else "6 km" or "5K" in the title; swims "Total: about 1150 m". Gym, rest and sessions planned only by time have none.
- Status rules (agreed 2026-10-13): done when the matched activity covers at least 80% of the planned distance, partial below that; with no planned distance any matching activity is done. Matching works within each week, Monday to Sunday (changed 2026-10-14, was same day only): sessions swapped or moved inside a week are matched the way they were run; a session done in another week is linked by hand. Gym is not recorded on the watch, so gym days get a "mark done" instead of matching.
- Auto-match activities to planned sessions by date and sport, with manual override.
  - Computed on every read (`app/plan_calendar.py`, `GET /api/calendar`), so it follows new activities and re-imported plans. Per week and sport, sessions and activities are paired at the lowest total cost: distance misfit (relative to the planned distance, capped at 100%) plus 25% per day moved. A pair on another day must fit the planned distance within 50%; a session without an activity costs more than any allowed pair. A swapped long and easy run are matched as run; two sessions of similar distance stay on their days. Without planned distances, the longest activity first. A link by hand (`plan_link`) wins: link an activity (3 days either side, any sport), mark done, mark not done, or back to automatic.
- Status per day: done, partial, missed, unplanned, rest.
  - Per session: done, partial, missed (day over, nothing done), planned (today or later). The day: done when every session is done, missed when every one is missed, partial otherwise; unplanned when only activities nothing planned asked for; rest when nothing was planned or done.
- Week and month views. Tapping a day opens the matching activity page.
  - Training > Calendar. Week view: totals per sport (sessions done, distance done of planned), each day with its sessions and activities and each activity's glucose badge (start and change, amber above 250 at the start, red when it went below the low); tapping an activity opens its run page, tapping a session shows its text (without the PACES block) and the link actions. On a wide screen each day's date and status sit in a left column with its sessions beside it, at a reading width (2026-10-16). Month view: one mark per session (done, partial, missed, unplanned, planned, told apart by shape, not colour); tapping a day opens its week.
- Target paces and zones come from the app settings, not from text in the .ics files: the calendars still carry the old 7:00 to 7:30 easy pace.
  - Pace zones live in Settings > Training. A planned run's zone comes from its title (threshold, tempo, steady, race pace or half marathon, long, easy or shakeout, in that order); sessions run by effort (hill sprints, reps, VO2, strides, time trials, the 10K) have no pace target. The calendar shows "Target easy 5:55-6:25 /km" under the session and hides the PACES block of the file's text.

## Security, backups, operations

The app holds health data and an insulin calculator, so it is never exposed to the public internet.

- Access: only over the home LAN and a VPN (Tailscale), through a reverse proxy with an access list, no port forwarding. The API publishes no ports; the proxy reaches it over a shared Docker network. An app login on top (single user, long session) as a second layer.
- Secrets: `.env` and `secrets/` outside git. Garmin tokens and LibreLinkUp credentials live in a volume or secret files, never in the repo or the logs.
- Backups: a nightly `pg_dump` and the raw archive, copied to another disk by a job on the host. One restore test before relying on it.
- Monitoring: host cron jobs check `/health/glucose` and `/health/garmin` and send an alert after repeated failures.
- Budget: under 1.5 GB RAM for the whole stack.
- Development in Claude Code, with notes in the repo holding the architecture and the conventions, so each session starts with context.

## Phased roadmap

The glucose collector comes first because it starts accumulating data that cannot be recovered later. Each phase ends only when its acceptance check passes.

1. Foundations: repo, compose stack, Postgres, Tailscale-only access, app login, backups, an empty page on the subdomain.
   - Done when: the page loads on the phone over Tailscale and a nightly dump exists in `the backup disk`.
2. Glucose collector.
   - Done when: over 7 consecutive days every 15-minute slot has at least one reading (live or history; sensor changes excluded), at least one session of 30 min or more with LibreLink kept open (for example a run) has 1 min data with under 2% gaps, and the health alert fires when the collector is stopped.
   - (Changed 2026-10-12: was "7 days of continuous 1 min data with under 2% gaps". Minute data only exists while LibreLink runs on the phone, and it is only needed during runs; the rest of the time 15-minute data is enough.)
3. Insulin and carbs log.
   - Done when: logging a dose on the phone takes under 10 seconds.
   - Built while step 2's 7-day observation runs (agreed 2026-10-12).
4. Insulin calculator (moved forward from step 8 on 2026-10-12): Settings > Insulin first, then the calculator in the Log form.
   - Done when: the endo parameters are entered and the calculator matches hand calculations on 20 test cases, including edge cases (no recent dose, high glucose, stale reading).
5. Food entry (moved forward on 2026-10-12).
   - Done when: a meal with three foods (one CIQUAL, one scanned packaged product, one own food) is logged on the phone in under a minute, and meal totals match hand calculations on 10 test meals.
6. Garmin sync and the run graph.
   - Done when: a new run appears within 12 hours with no action, and its distance, time and average HR match Garmin Connect.
7. Per-run context and cross-run comparison.
   - Done when: every run since step 6 has a filled context panel, and the overlay view works with a colour variable.
   - Change 1: Settings > Training and the zone bands. Change 2: the per-run numbers, the context panel, the note and "add pre-run carbs". Change 3: the comparison screen (overlay, scatter, table). Change 4: similar runs. All four built 2026-10-13; the step is done when its acceptance check passes on real runs.
8. AI analysis.
   - Done when: a report is generated automatically after each sync, and switching between Claude and Gemini works.
   - Change 1: run reports (providers, queue and analysis service, dose guard, run page, Settings > AI). Change 2: weekly summary. Change 3: the Claude desktop connector (local MCP), replacing the in-app chat. All three built 2026-10-13.
   - With only a Gemini key (decided 2026-10-13), the Claude side of the switch is checked by the tests against a mocked API; on the deployed stack it is checked when a Claude key is added.
9. Calendar.
   - Done when: the plan is imported and past weeks show done, partial and missed correctly.
   - Change 1: import (Settings > Plan), deployed 2026-10-13. Change 2 (deployed 2026-10-14, matching within the week from the same day): the calendar (week and month, matching, statuses, glucose badge, manual link, gym "mark done"). Change 3: pace zones in Settings > Training, shown as the planned runs' targets (built 2026-10-14).

## Open questions and known risks

Open questions:

- [x] Glucose units: mg/dL.
- [x] Insulin parameters: recorded in the calculator section.
- [x] LibreLinkUp follower account created and accepted.
- [x] Frontend framework: SvelteKit, with the rules in Interface design.
- [x] AI providers receive full data, no stripping.
- [ ] Confirm the provisional 5 h action time with the endocrinologist.
- [x] Pen dose increment: 1 unit.

Known risks:

| Risk | Effect | Mitigation |
| --- | --- | --- |
| Garmin changes its login | Automatic sync stops | Library update; manual FIT upload; health alert after 36 h |
| Abbott changes the LibreLinkUp API | Glucose capture stops | Daily refetch, raw archive, LibreView CSV import, alert after 20 min |
| Mobile data off during a run | Live minutes missing | Daily refetch fills at coarser resolution; coverage flag on the run |
| Sensor lag of several minutes | Lows look later than they are | Configurable lag offset on the graph |
| Unlogged doses | IOB too low, calculator over-doses | Calculator last; explicit IOB warning; confirm tap |
| Too few runs | Patterns not trustworthy | n shown on every comparison chart |

Sources: [Garmin-intervals-bridge thread](https://forum.intervals.icu/t/garmin-intervals-bridge-your-complete-garmin-fit-files-and-wellness-data-in-intervals-icu-with-or-without-the-official-garmin-integration/132863), [python-garminconnect issue 332](https://github.com/cyberjunky/python-garminconnect/issues/332), [FreeStyle LibreLink app](https://pro.freestyle.abbott/fi-fi/home/freestyle-portfolio/digital-health-solutions/freestyle-libre-link.html), [Nightscout LibreLinkUp uploader](https://elements.heroku.com/buttons/timoschlueter/nightscout-librelink-up).
