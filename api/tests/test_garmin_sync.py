"""Garmin sync: schedule, token-only login, the sync run, status, "Sync now" and /health/garmin.

Garmin is never contacted: run_sync gets a fake client and a fake downloader that answer like
garminconnect 0.3.17 (activity list dicts with activityId; the original download is a zip with the .fit).
"""

import io
import os
import zipfile
from datetime import UTC, datetime, timedelta
from pathlib import Path

import psycopg
import pytest
from conftest import TEST_PASSWORD

from app import garmin_sync as gs

DATA = Path(__file__).parent / "data"
RUN = (DATA / "run.fit").read_bytes()
BRICK = (DATA / "brick.fit").read_bytes()
TRUNCATED = (DATA / "truncated.fit").read_bytes()


def _zip(name: str, data: bytes) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr(name, data)
    return buf.getvalue()


# --- schedule (no database) ------------------------------------------------------------------------


@pytest.mark.parametrize(
    "now,expected",
    [
        ("2026-10-13T10:00:00+00:00", "2026-10-13T11:00:00+00:00"),  # 12:00 in Madrid -> 13:00 (CEST, UTC+2)
        ("2026-10-13T11:00:00+00:00", "2026-10-13T20:00:00+00:00"),  # exactly 13:00 -> 22:00
        ("2026-10-13T20:30:00+00:00", "2026-10-14T11:00:00+00:00"),  # 22:30 -> tomorrow 13:00
        ("2026-10-24T20:30:00+00:00", "2026-10-25T12:00:00+00:00"),  # clocks go back on 25 Oct: 13:00 CET is 12:00 UTC
        ("2027-03-27T21:30:00+00:00", "2027-03-28T11:00:00+00:00"),  # clocks go forward on 28 Mar 2027
    ],
)
def test_next_sync_time(now, expected):
    assert gs.next_sync_time(datetime.fromisoformat(now)).isoformat() == expected


def test_connect_without_tokens_never_tries_a_password_login(tmp_path, monkeypatch):
    monkeypatch.setenv("GARMIN_TOKENS", str(tmp_path))
    with pytest.raises(gs.LoginNeeded, match="no Garmin tokens"):
        gs.connect()
    # A broken token file: the library has no credentials, so it must refuse rather than log in,
    # and it must leave the file where it is.
    bad = tmp_path / gs.TOKEN_FILE
    bad.write_text('{"not": "tokens"}')
    with pytest.raises(gs.LoginNeeded, match="rejected the saved tokens"):
        gs.connect()
    assert bad.read_text() == '{"not": "tokens"}'


# --- sync runs (database) --------------------------------------------------------------------------

db = pytest.mark.skipif(not os.environ.get("TEST_PG_HOST"), reason="TEST_PG_HOST not set")


class FakeGarmin:
    def __init__(self, listed):
        self.listed = listed
        self.asked = []

    def get_activities_by_date(self, start, end, sortorder=None):
        self.asked.append((start, end))
        return self.listed


class NotFound(Exception):
    pass


@pytest.fixture()
def client(env, tmp_path, monkeypatch):
    c, name = env
    assert c.post("/api/login", json={"password": TEST_PASSWORD}).status_code == 204
    monkeypatch.setenv("RAW_DIR", str(tmp_path / "raw"))
    monkeypatch.setenv("GARMIN_TOKENS", str(tmp_path / "tokens"))
    monkeypatch.setattr(gs, "_is_not_found", lambda e: isinstance(e, NotFound))
    c.dbname = name
    c.files = {111: _zip("111_ACTIVITY.fit", RUN), 222: _zip("222_ACTIVITY.fit", BRICK), 444: _zip("444.fit", TRUNCATED)}
    c.downloads = []

    def downloader(_client, aid):
        c.downloads.append(aid)
        if aid not in c.files:
            raise NotFound(aid)
        return c.files[aid]

    c.downloader = downloader
    return c


def _sync(client, ids):
    fake = FakeGarmin([{"activityId": i} for i in ids])
    return gs.run_sync(client_factory=lambda: fake, downloader=client.downloader, pause_s=0), fake


def _db(name):
    return psycopg.connect(host=os.environ["TEST_PG_HOST"], port=os.environ.get("TEST_PG_PORT", "5432"),
                           user=os.environ.get("TEST_PG_USER", "postgres"), password=os.environ.get("TEST_PG_PASSWORD", ""),
                           dbname=name, autocommit=True)


@db
def test_sync_imports_new_activities_once(client):
    counts, fake = _sync(client, [111, 222, 333])  # 333: typed in by hand, no original file
    assert counts == {"checked": 3, "imported": 2, "skipped": 1}
    start, _ = fake.asked[0]
    assert start == (datetime.now(gs.LOCAL).date() - timedelta(days=30)).isoformat()  # first run: the backfill
    acts = client.get("/api/activities").json()
    assert sorted((a["sport"], a["garmin_activity_id"], a["source"]) for a in acts) == [
        ("cycling", 222, "garmin"), ("running", 111, "garmin"), ("running", 222, "garmin")]
    assert len(list((Path(os.environ["RAW_DIR"]) / "fit").glob("*/*.zip"))) == 2
    counts, fake = _sync(client, [111, 222, 333])
    assert counts == {"checked": 3, "imported": 0, "skipped": 0} and client.downloads == [111, 222, 333]
    # Later runs start from the newest synced activity minus the overlap.
    assert fake.asked[0][0] == (datetime(2026, 10, 10, tzinfo=UTC).date() - timedelta(days=gs.OVERLAP_DAYS)).isoformat()


@db
def test_a_run_uploaded_by_hand_is_linked_not_duplicated(client):
    up = client.post("/api/activities/fit", content=RUN).json()["activities"][0]
    counts, _ = _sync(client, [111])
    assert counts["imported"] == 0  # linked, not new
    (a,) = client.get("/api/activities").json()
    assert a["id"] == up["id"] and a["garmin_activity_id"] == 111 and a["source"] == "upload"


@db
def test_a_broken_file_is_skipped_and_remembered(client):
    counts, _ = _sync(client, [444])
    assert counts == {"checked": 1, "imported": 0, "skipped": 1}
    with _db(client.dbname) as conn:
        assert conn.execute("SELECT status, detail FROM garmin_activity_seen WHERE garmin_activity_id = 444").fetchone()[0] == "skipped"
    _sync(client, [444])
    assert client.downloads == [444]


@db
def test_login_needed_is_recorded_reported_and_cleared(client):
    run = gs.claim("startup")
    assert gs.execute(run) is None  # no tokens in GARMIN_TOKENS: the real connect() refuses
    st = client.get("/api/garmin/status").json()
    assert st["login_needed"] is True and "login needed" in st["last_error"] and st["runs"][0]["login_needed"] is True
    h = client.get("/health/garmin")
    assert h.status_code == 503 and h.json()["status"] == "login_needed"
    fake = FakeGarmin([{"activityId": 111}])
    assert gs.execute(gs.claim("request") or gs.claim("retry"), client_factory=lambda: fake, downloader=client.downloader, pause_s=0) is True
    st = client.get("/api/garmin/status").json()
    assert st["login_needed"] is False and st["last_error"] is None and st["activities"] == 1
    assert client.get("/health/garmin").json()["status"] == "ok"


@db
def test_temporary_problems_ask_for_a_retry(client):
    class Down:
        def get_activities_by_date(self, *a, **k):
            raise ConnectionError("garmin is down")

    assert gs.execute(gs.claim("schedule"), client_factory=Down, pause_s=0) is False
    st = client.get("/api/garmin/status").json()
    assert "could not list activities" in st["last_error"] and st["login_needed"] is False and st["consecutive_failures"] == 1


@db
def test_sync_now_queue(client):
    first = client.post("/api/garmin/sync")
    assert first.status_code == 202
    assert client.post("/api/garmin/sync").json() == first.json()  # a pending request is reused
    assert client.get("/api/garmin/status").json()["busy"] is True
    assert gs.has_pending() and gs.claim("request") == [first.json()["queued"]] and not gs.has_pending()
    r = client.post("/api/garmin/sync")
    assert r.status_code == 429 and "running" in r.json()["detail"]


@db
def test_a_login_needed_run_does_not_block_sync_now(client):
    gs.execute(gs.claim("startup"))  # no tokens: login needed, Garmin never contacted
    assert client.post("/api/garmin/sync").status_code == 202


@db
def test_health_never_and_stale(client):
    assert client.get("/health/garmin").json()["status"] == "never_succeeded"
    with _db(client.dbname) as conn:
        conn.execute("INSERT INTO collector_status (name, last_attempt_at, last_success_at, consecutive_failures) "
                     "VALUES ('garmin', now(), now() - interval '40 hours', 0)")
    h = client.get("/health/garmin")
    assert h.status_code == 503 and h.json()["status"] == "stale"


@db
def test_startup_run_only_without_a_recent_success(client):
    assert gs.recent_success() is False
    with _db(client.dbname) as conn:
        conn.execute("INSERT INTO collector_status (name, last_attempt_at, last_success_at, consecutive_failures) "
                     "VALUES ('garmin', now(), now() - interval '2 hours', 0)")
    assert gs.recent_success() is True


@db
def test_needs_login(env):
    c, _ = env
    assert c.get("/api/garmin/status").status_code == 401
    assert c.post("/api/garmin/sync").status_code == 401
