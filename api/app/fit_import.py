"""FIT import: raw archive first, then sessions, laps and records into the activity tables (roadmap step 6).

Used by manual upload (POST /api/activities/fit) and, from the next change, by the Garmin sync; both go
through import_bytes(), so a file is handled the same way whichever way it arrives.

- Accepts a .fit file, or the .zip that Garmin Connect's "Export original" downloads (one .fit inside).
- The bytes as received are archived under <RAW_DIR>/fit/<first 2 hex of sha256>/<sha256>.<fit|zip> before
  parsing (content-addressed: the same file is stored once). activity.fit_path is that path, relative to
  RAW_DIR, so every activity can be rebuilt from it: `python -m app.fit_import --reparse`.
- One activity per FIT session. Laps and records belong to the session whose time span holds their start.
- A file is identified by its file_id (manufacturer, serial number, time created), else by its sha256.
  Importing it again replaces its activities' laps and records and keeps their ids.
- Values are stored as recorded. GPS comes in FIT semicircles and is stored in degrees.
"""

import hashlib
import io
import os
import sys
import zipfile
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path

import fitdecode
import psycopg
from psycopg.rows import dict_row

from . import ai_reports, derive
from .common import conninfo

PARSER_VERSION = "1"
MAX_BYTES = 25 * 1024 * 1024
SEMICIRCLE_DEG = 180 / 2**31


class FitProblem(Exception):
    """The file cannot be imported; the message says why, in words for the user."""


@dataclass
class Parsed:
    key: str
    device: str | None
    sessions: list[dict] = field(default_factory=list)
    laps: list[dict] = field(default_factory=list)
    records: list[dict] = field(default_factory=list)


def raw_root() -> Path:
    return Path(os.environ.get("RAW_DIR", "/raw"))


def unpack(data: bytes) -> bytes:
    """The FIT bytes: the data itself, or the single .fit inside a Garmin Connect export zip."""
    if data[:2] == b"PK":
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as z:
                fits = [n for n in z.namelist() if n.lower().endswith(".fit")]
                if len(fits) != 1:
                    raise FitProblem(f"the zip should hold exactly one .fit file, it holds {len(fits)}")
                info = z.getinfo(fits[0])
                if info.file_size > MAX_BYTES:
                    raise FitProblem("the .fit file inside the zip is too large")
                data = z.read(fits[0])
        except zipfile.BadZipFile as e:
            raise FitProblem("the zip file is damaged") from e
    if len(data) < 14 or data[8:12] != b".FIT":
        raise FitProblem("this is not a FIT file")
    return data


def _value(msg, *names):
    for n in names:
        if msg.has_field(n):
            v = msg.get_value(n)
            if v is not None:
                return v
    return None


def _num(v):
    return None if v is None else float(v)


def _hr(v):
    return v if isinstance(v, int) and 0 < v < 256 else None


def parse(fit: bytes) -> Parsed:
    file_id = None
    sessions, laps, records = [], [], []
    try:
        with fitdecode.FitReader(io.BytesIO(fit), check_crc=fitdecode.CrcCheck.RAISE) as reader:
            for frame in reader:
                if not isinstance(frame, fitdecode.FitDataMessage):
                    continue
                if frame.name == "file_id" and file_id is None:
                    file_id = frame
                elif frame.name == "session":
                    sessions.append(frame)
                elif frame.name == "lap":
                    laps.append(frame)
                elif frame.name == "record":
                    records.append(frame)
    except (fitdecode.FitError, EOFError, ValueError) as e:
        raise FitProblem("the file is incomplete or damaged (it may not have finished downloading)") from e
    if not sessions:
        raise FitProblem("the file has no activity summary (session): it is not a finished activity")

    serial = _value(file_id, "serial_number") if file_id else None
    created = _value(file_id, "time_created") if file_id else None
    maker = _value(file_id, "manufacturer") if file_id else None
    product = _value(file_id, "garmin_product", "product_name", "product") if file_id else None
    key = (
        f"fileid:{maker}:{serial}:{int(created.timestamp())}"
        if serial is not None and isinstance(created, datetime)
        else "sha256:" + hashlib.sha256(fit).hexdigest()
    )
    device = " ".join(str(x) for x in (maker, product) if x is not None) or None

    out = Parsed(key=key, device=device)
    for s in sessions:
        start = _value(s, "start_time")
        elapsed = _num(_value(s, "total_elapsed_time"))
        if not isinstance(start, datetime) or elapsed is None:
            raise FitProblem("a session in the file has no start time or duration")
        out.sessions.append({
            "sport": str(_value(s, "sport") or "generic"),
            "sub_sport": (str(v) if (v := _value(s, "sub_sport")) is not None else None),
            "start_ts": start,
            "end_ts": start + timedelta(seconds=elapsed),
            "elapsed_s": elapsed,
            "moving_s": _num(_value(s, "total_timer_time")),
            "distance_m": _num(_value(s, "total_distance")),
            "avg_hr": _hr(_value(s, "avg_heart_rate")),
            "max_hr": _hr(_value(s, "max_heart_rate")),
            "ascent_m": _num(_value(s, "total_ascent")),
            "descent_m": _num(_value(s, "total_descent")),
            "calories": _value(s, "total_calories"),
            "avg_speed_mps": _num(_value(s, "enhanced_avg_speed", "avg_speed")),
        })
    out.sessions.sort(key=lambda s: s["start_ts"])

    def session_of(ts) -> int | None:
        # The last session that has started by ts and not ended before it: at a transition (a brick), the
        # moment one leg ends belongs to the next leg, which starts then.
        for i in range(len(out.sessions) - 1, -1, -1):
            s = out.sessions[i]
            if s["start_ts"] <= ts <= s["end_ts"]:
                return i
        return None

    for lap in laps:
        start = _value(lap, "start_time")
        if not isinstance(start, datetime) or (i := session_of(start)) is None:
            continue
        out.laps.append({
            "session": i,
            "start_ts": start,
            "elapsed_s": _num(_value(lap, "total_elapsed_time")),
            "moving_s": _num(_value(lap, "total_timer_time")),
            "distance_m": _num(_value(lap, "total_distance")),
            "avg_hr": _hr(_value(lap, "avg_heart_rate")),
            "max_hr": _hr(_value(lap, "max_heart_rate")),
            "avg_speed_mps": _num(_value(lap, "enhanced_avg_speed", "avg_speed")),
        })
    out.laps.sort(key=lambda lap: lap["start_ts"])

    seen = {}
    for r in records:
        ts = _value(r, "timestamp")
        if not isinstance(ts, datetime) or (i := session_of(ts)) is None:
            continue
        lat, lon = _value(r, "position_lat"), _value(r, "position_long")
        seen[(i, ts)] = {  # a repeated timestamp keeps the last record
            "session": i,
            "ts": ts,
            "elapsed_s": int((ts - out.sessions[i]["start_ts"]).total_seconds()),
            "distance_m": _num(_value(r, "distance")),
            "hr": _hr(_value(r, "heart_rate")),
            "speed_mps": _num(_value(r, "enhanced_speed", "speed")),
            "altitude_m": _num(_value(r, "enhanced_altitude", "altitude")),
            "cadence": _value(r, "cadence"),
            "lat": lat * SEMICIRCLE_DEG if isinstance(lat, int) else None,
            "lon": lon * SEMICIRCLE_DEG if isinstance(lon, int) else None,
        }
    out.records = sorted(seen.values(), key=lambda r: r["ts"])
    return out


def archive(data: bytes) -> tuple[str, str]:
    """Store the bytes as received; returns (path relative to RAW_DIR, sha256). Raises OSError."""
    sha = hashlib.sha256(data).hexdigest()
    rel = f"fit/{sha[:2]}/{sha}.{'zip' if data[:2] == b'PK' else 'fit'}"
    path = raw_root() / rel
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".part")
        tmp.write_bytes(data)
        tmp.replace(path)  # never a half-written file under the final name
    return rel, sha


ACTIVITY_COLS = ["sport", "sub_sport", "start_ts", "end_ts", "elapsed_s", "moving_s", "distance_m", "avg_hr", "max_hr",
                 "ascent_m", "descent_m", "calories", "avg_speed_mps"]
LAP_COLS = ["start_ts", "elapsed_s", "moving_s", "distance_m", "avg_hr", "max_hr", "avg_speed_mps"]
RECORD_COLS = ["ts", "elapsed_s", "distance_m", "hr", "speed_mps", "altitude_m", "cadence", "lat", "lon"]


def store(conn, parsed: Parsed, *, source: str, fit_path: str, sha: str, garmin_activity_id: int | None = None) -> list[dict]:
    """Insert or refresh the file's activities with their laps and records. Returns [{id, created}] per session."""
    out = []
    for i, s in enumerate(parsed.sessions):
        row = conn.execute(
            f"""INSERT INTO activity (fit_key, session_index, source, garmin_activity_id, device, fit_path, fit_sha256,
                                      parser_version, {", ".join(ACTIVITY_COLS)})
                VALUES (%(key)s, %(i)s, %(source)s, %(garmin)s, %(device)s, %(path)s, %(sha)s, %(pv)s,
                        {", ".join(f"%({c})s" for c in ACTIVITY_COLS)})
                ON CONFLICT (fit_key, session_index) DO UPDATE SET
                    garmin_activity_id = coalesce(EXCLUDED.garmin_activity_id, activity.garmin_activity_id),
                    device = EXCLUDED.device, fit_path = EXCLUDED.fit_path, fit_sha256 = EXCLUDED.fit_sha256,
                    parser_version = EXCLUDED.parser_version, imported_at = now(),
                    {", ".join(f"{c} = EXCLUDED.{c}" for c in ACTIVITY_COLS)}
                RETURNING id, (xmax = 0) AS created""",
            {**s, "key": parsed.key, "i": i, "source": source, "garmin": garmin_activity_id, "device": parsed.device,
             "path": fit_path, "sha": sha, "pv": PARSER_VERSION},
        ).fetchone()
        aid = row["id"]
        conn.execute("DELETE FROM activity_lap WHERE activity_id = %s", (aid,))
        conn.execute("DELETE FROM activity_record WHERE activity_id = %s", (aid,))
        with conn.cursor() as cur:
            cur.executemany(
                f"INSERT INTO activity_lap (activity_id, idx, {', '.join(LAP_COLS)}) VALUES (%s, %s, {', '.join(['%s'] * len(LAP_COLS))})",
                [(aid, n, *[lap[c] for c in LAP_COLS]) for n, lap in enumerate(x for x in parsed.laps if x["session"] == i)],
            )
            with cur.copy(f"COPY activity_record (activity_id, {', '.join(RECORD_COLS)}) FROM STDIN") as copy:
                for r in parsed.records:
                    if r["session"] == i:
                        copy.write_row((aid, *[r[c] for c in RECORD_COLS]))
        out.append({"id": aid, "created": row["created"]})
    return out


def import_bytes(conn, data: bytes, *, source: str, garmin_activity_id: int | None = None) -> dict:
    """Archive, parse and store one file. Raises FitProblem (bad file) or OSError (archive not writable)."""
    if len(data) > MAX_BYTES:
        raise FitProblem("the file is larger than 25 MB")
    if not data:
        raise FitProblem("the file is empty")
    rel, sha = archive(data)  # before parsing: a file the parser cannot read today is kept for a better parser
    parsed = parse(unpack(data))
    stored = store(conn, parsed, source=source, fit_path=rel, sha=sha, garmin_activity_id=garmin_activity_id)
    derive.recompute(conn, [x["id"] for x in stored])  # the per-run numbers, same transaction
    ai_reports.queue_after_import(conn, [x for x in stored if x["created"]])  # the AI report, if asked for
    return {"ids": [s["id"] for s in stored], "duplicate": not any(s["created"] for s in stored)}


def reparse_all() -> int:
    """Rebuild every activity from its raw file (after a parser change). Returns the number of files."""
    with psycopg.connect(conninfo(), row_factory=dict_row) as conn:
        files = conn.execute(
            "SELECT DISTINCT ON (fit_path) fit_path, fit_sha256, source, garmin_activity_id FROM activity ORDER BY fit_path"
        ).fetchall()
        for f in files:
            data = (raw_root() / f["fit_path"]).read_bytes()
            parsed = parse(unpack(data))
            stored = store(conn, parsed, source=f["source"], fit_path=f["fit_path"], sha=f["fit_sha256"],
                           garmin_activity_id=f["garmin_activity_id"])
            derive.recompute(conn, [x["id"] for x in stored])
    return len(files)


if __name__ == "__main__":
    if sys.argv[1:] != ["--reparse"]:
        print("usage: python -m app.fit_import --reparse   (rebuild all activities from the raw FIT archive)")
        sys.exit(2)
    print(f"re-parsed {reparse_all()} files at {datetime.now(UTC):%Y-%m-%d %H:%M} UTC")
