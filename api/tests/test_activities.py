"""FIT parsing (always) and upload/list (needs TEST_PG_HOST), on the synthetic files in tests/data
(made by tests/data/make_fit_fixtures.py; the expected values below are what that script wrote)."""

import io
import os
import zipfile
from pathlib import Path

import psycopg
import pytest
from conftest import TEST_PASSWORD

from app import fit_import as fi

DATA = Path(__file__).parent / "data"
RUN = (DATA / "run.fit").read_bytes()
BRICK = (DATA / "brick.fit").read_bytes()
TRUNCATED = (DATA / "truncated.fit").read_bytes()


def _zip(**files) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for name, data in files.items():
            z.writestr(name, data)
    return buf.getvalue()


# --- parsing ---------------------------------------------------------------------------------------


def test_run():
    p = fi.parse(fi.unpack(RUN))
    assert p.key == "fileid:garmin:3999000001:1791615600" and p.device == "garmin fr945"
    (s,) = p.sessions
    assert (s["sport"], s["sub_sport"], s["elapsed_s"], s["moving_s"], s["distance_m"]) == ("running", "generic", 1800, 1800, 5397)
    assert (s["avg_hr"], s["max_hr"], s["ascent_m"], s["descent_m"], s["calories"]) == (149, 165, 42, 40, 380)
    assert s["start_ts"].isoformat() == "2026-10-10T07:00:00+00:00" and s["end_ts"].isoformat() == "2026-10-10T07:30:00+00:00"
    assert [(lap["elapsed_s"], lap["distance_m"], lap["avg_hr"]) for lap in p.laps] == [(900, 2697, 140), (900, 2700, 158)]
    assert len(p.records) == 1800
    first, last = p.records[0], p.records[-1]
    assert (first["elapsed_s"], first["hr"], first["speed_mps"], first["cadence"]) == (0, 130, 3.0, 85)
    assert last["elapsed_s"] == 1799 and last["hr"] == 165
    assert abs(first["lat"] - 48.85) < 1e-6 and abs(first["lon"] - 2.35) < 1e-6  # semicircles -> degrees


def test_brick_gives_one_activity_per_leg():
    p = fi.parse(fi.unpack(BRICK))
    assert [(s["sport"], s["elapsed_s"]) for s in p.sessions] == [("cycling", 600), ("running", 300)]
    assert [sum(1 for r in p.records if r["session"] == i) for i in (0, 1)] == [600, 300]
    # The transition second (08:10:00) is the run's first record, not the ride's last.
    run_first = next(r for r in p.records if r["session"] == 1)
    assert run_first["elapsed_s"] == 0 and run_first["ts"].isoformat() == "2026-10-10T08:10:00+00:00"
    assert [lap["session"] for lap in p.laps] == [0, 1]


@pytest.mark.parametrize(
    "data,why",
    [
        (TRUNCATED, "incomplete or damaged"),
        (b"hello, not a fit file", "not a FIT file"),
        (b"", "not a FIT file"),
        (_zip(**{"a.fit": RUN, "b.fit": BRICK}), "exactly one .fit"),
        (_zip(**{"readme.txt": b"x"}), "exactly one .fit"),
        (b"PK\x03\x04broken", "zip file is damaged"),
    ],
)
def test_bad_files(data, why):
    with pytest.raises(fi.FitProblem, match=why):
        fi.parse(fi.unpack(data))


def test_garmin_export_zip():
    assert fi.unpack(_zip(**{"12345678901_ACTIVITY.fit": RUN})) == RUN


# --- upload and list -------------------------------------------------------------------------------

db = pytest.mark.skipif(not os.environ.get("TEST_PG_HOST"), reason="TEST_PG_HOST not set")


@pytest.fixture()
def client(env, tmp_path, monkeypatch):
    c, name = env
    assert c.post("/api/login", json={"password": TEST_PASSWORD}).status_code == 204
    monkeypatch.setenv("RAW_DIR", str(tmp_path / "raw"))
    c.raw = tmp_path / "raw"
    c.dbname = name
    return c


def _up(client, data):
    return client.post("/api/activities/fit", content=data, headers={"Content-Type": "application/octet-stream"})


def _db(name):
    return psycopg.connect(host=os.environ["TEST_PG_HOST"], port=os.environ.get("TEST_PG_PORT", "5432"),
                           user=os.environ.get("TEST_PG_USER", "postgres"), password=os.environ.get("TEST_PG_PASSWORD", ""),
                           dbname=name, autocommit=True)


@db
def test_upload_stores_archives_and_lists(client):
    r = _up(client, RUN)
    assert r.status_code == 201, r.text
    body = r.json()
    (a,) = body["activities"]
    assert body["duplicate"] is False and a["source"] == "upload" and a["sport"] == "running"
    assert (a["distance_m"], a["elapsed_s"], a["avg_hr"], a["max_hr"]) == (5397, 1800, 149, 165)
    assert a["start"] == 1791615600 and a["end"] == 1791617400
    files = list(client.raw.glob("fit/*/*.fit"))
    assert len(files) == 1 and files[0].read_bytes() == RUN
    assert [x["id"] for x in client.get("/api/activities").json()] == [a["id"]]
    detail = client.get(f"/api/activities/{a['id']}").json()
    assert detail["records"] == 1800 and [lap["avg_hr"] for lap in detail["laps"]] == [140, 158]
    assert client.get("/api/activities/999999").status_code == 404


@db
def test_same_file_again_or_zipped_is_not_duplicated(client):
    first = _up(client, RUN).json()["activities"][0]["id"]
    again = _up(client, RUN).json()
    zipped = _up(client, _zip(**{"export.fit": RUN})).json()
    assert again["duplicate"] is True and zipped["duplicate"] is True
    assert again["activities"][0]["id"] == first == zipped["activities"][0]["id"]
    assert len(client.get("/api/activities").json()) == 1
    assert client.get(f"/api/activities/{first}").json()["records"] == 1800
    assert len(list(client.raw.glob("fit/*/*"))) == 2  # the .fit and the .zip, each stored once


@db
def test_brick_upload(client):
    acts = _up(client, BRICK).json()["activities"]
    assert [(a["sport"], a["distance_m"]) for a in acts] == [("cycling", 4792), ("running", 986.7)]
    assert [a["sport"] for a in client.get("/api/activities").json()] == ["running", "cycling"]  # newest first


@db
def test_bad_file_is_refused_but_kept_raw(client):
    r = _up(client, TRUNCATED)
    assert r.status_code == 422 and "incomplete" in r.json()["detail"]
    assert len(list(client.raw.glob("fit/*/*.fit"))) == 1  # archived before parsing
    assert client.get("/api/activities").json() == []


@db
def test_archive_not_writable(client):
    client.raw.parent.mkdir(parents=True, exist_ok=True)
    client.raw.write_text("a file where the archive folder should be")
    assert _up(client, RUN).status_code == 503
    assert client.get("/api/activities").json() == []


@db
def test_too_large(client):
    r = client.post("/api/activities/fit", content=b"x", headers={"Content-Length": str(fi.MAX_BYTES + 1)})
    assert r.status_code in (413, 422)


@db
def test_reparse_rebuilds_from_the_raw_file(client):
    aid = _up(client, RUN).json()["activities"][0]["id"]
    with _db(client.dbname) as conn:
        conn.execute("DELETE FROM activity_record WHERE activity_id = %s", (aid,))
        conn.execute("UPDATE activity SET distance_m = 1 WHERE id = %s", (aid,))
    assert fi.reparse_all() == 1
    d = client.get(f"/api/activities/{aid}").json()
    assert d["records"] == 1800 and d["distance_m"] == 5397


@db
def test_needs_login(env):
    c, _ = env
    assert c.get("/api/activities").status_code == 401
    assert c.post("/api/activities/fit", content=RUN).status_code == 401


@db
def test_graph_has_records_glucose_and_log_in_the_window(client):
    aid = _up(client, RUN).json()["activities"][0]["id"]  # 07:00 to 07:30 UTC on 10 Oct 2026
    with _db(client.dbname) as conn:
        # One reading every 5 min from 05:00 to 11:00 UTC; the window is 06:00 to 09:30.
        conn.execute(
            "INSERT INTO glucose_reading (minute, ts, mg_dl, trend, source) "
            "SELECT m, m, 100 + (extract(epoch FROM m - '2026-10-10 05:00Z')::int / 300), 3, 'llu_graph' "
            "FROM generate_series('2026-10-10 05:00Z'::timestamptz, '2026-10-10 11:00Z', interval '5 min') m"
        )
    ok = {"ts": "2026-10-10T06:40:00Z", "carbs": {"grams": 30}, "dose": {"units": 3, "kind": "rapid", "insulin_name": "Humalog"}}
    assert client.post("/api/entries", json=ok).status_code == 201
    assert client.post("/api/entries", json={"ts": "2026-10-10T05:30:00Z", "carbs": {"grams": 15}}).status_code == 201
    g = client.get(f"/api/activities/{aid}/graph").json()
    assert g["window"] == {"from": 1791615600 - 3600, "to": 1791617400 + 7200}
    rec = g["records"]
    assert len(rec["t"]) == 1800 and rec["t"][0] == 1791615600 and rec["hr"][0] == 130 and rec["speed_mps"][0] == 3
    assert set(rec) == {"t", "distance_m", "hr", "speed_mps", "altitude_m", "cadence"}
    times = [p[0] for p in g["glucose"]]
    assert times[0] == 1791615600 - 3600 and times[-1] == 1791617400 + 7200 and len(times) == 43  # 06:00 .. 09:30
    assert [c["grams"] for c in g["log"]["carbs"]] == [30] and [d["units"] for d in g["log"]["doses"]] == [3]
    assert [lap["avg_hr"] for lap in g["laps"]] == [140, 158]
    assert client.get("/api/activities/999999/graph").status_code == 404
