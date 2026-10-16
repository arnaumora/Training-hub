"""Plan import (app/plan.py) against a real Postgres 16: archive, insert, re-import by UID, removal, the
calendars list, refusals."""

import os

import psycopg
import pytest
from conftest import TEST_PASSWORD

pytestmark = pytest.mark.skipif(not os.environ.get("TEST_PG_HOST"), reason="TEST_PG_HOST not set")


def ev(uid, start, summary, extra=""):
    return f"BEGIN:VEVENT\nUID:{uid}\nDTSTART;TZID=Europe/Madrid:{start}\nSUMMARY:{summary}\n{extra}END:VEVENT\n"


def cal(*events, name="Half M - Running"):
    return f"BEGIN:VCALENDAR\nVERSION:2.0\nX-WR-CALNAME:{name}\n{''.join(events)}END:VCALENDAR\n".encode()


V1 = cal(
    ev("r1", "20261013T080000", "Easy 6 km + strides", "CATEGORIES:RUN\n"),
    ev("r2", "20261015T080000", "Easy 5 km", "CATEGORIES:RUN\n"),
    ev("r3", "20261018T100000", "Long run 8 km", "CATEGORIES:RUN\n"),
)
# r1 unchanged, r2 moved a day, r3 gone, r4 new.
V2 = cal(
    ev("r1", "20261013T080000", "Easy 6 km + strides", "CATEGORIES:RUN\n"),
    ev("r2", "20261016T080000", "Easy 5 km", "CATEGORIES:RUN\n"),
    ev("r4", "20261017T080000", "Easy 4 km", "CATEGORIES:RUN\n"),
)


@pytest.fixture()
def client(env, tmp_path, monkeypatch):
    c, name = env
    monkeypatch.setenv("RAW_DIR", str(tmp_path / "raw"))
    assert c.post("/api/login", json={"password": TEST_PASSWORD}).status_code == 204
    return c, name, tmp_path / "raw"


def _db(name):
    return psycopg.connect(host=os.environ["TEST_PG_HOST"], port=os.environ.get("TEST_PG_PORT", "5432"),
                           user=os.environ.get("TEST_PG_USER", "postgres"), password=os.environ.get("TEST_PG_PASSWORD", ""),
                           dbname=name)


def test_import_and_reimport(client):
    c, name, raw = client
    r = c.post("/api/plan/ics", params={"name": "training-running.ics"}, content=V1)
    assert r.status_code == 201, r.text
    assert r.json() == {"calendar": "Half M - Running", "sessions": 3, "added": 3, "updated": 0, "unchanged": 0, "removed": 0}
    assert len(list(raw.glob("plan/*/*.ics"))) == 1  # archived as received

    again = c.post("/api/plan/ics", content=V1).json()
    assert (again["added"], again["updated"], again["unchanged"], again["removed"]) == (0, 0, 3, 0)

    r = c.post("/api/plan/ics", content=V2).json()
    assert (r["added"], r["updated"], r["unchanged"], r["removed"]) == (1, 1, 1, 1)
    with _db(name) as conn:
        rows = {u: (d, gone) for u, d, gone in conn.execute("SELECT uid, day::text, removed_at IS NOT NULL FROM planned_session")}
    assert rows == {"r1": ("2026-10-13", False), "r2": ("2026-10-16", False), "r3": ("2026-10-18", True), "r4": ("2026-10-17", False)}

    # A session that comes back is live again.
    r = c.post("/api/plan/ics", content=V1).json()
    assert (r["added"], r["updated"], r["removed"]) == (0, 2, 1)  # r2 moved back, r3 back; r4 gone

    cals = c.get("/api/plan/calendars").json()
    assert len(cals) == 1
    k = cals[0]
    assert (k["calendar"], k["sessions"], k["removed"], k["first_day"], k["last_day"]) == ("Half M - Running", 3, 1, "2026-10-13", "2026-10-18")
    assert k["by_sport"] == {"running": 3} and k["last_import"]["updated"] == 2 and k["last_import"]["at"] > 0


def test_calendars_are_separate(client):
    c, name, _ = client
    c.post("/api/plan/ics", content=V1)
    c.post("/api/plan/ics", content=cal(ev("g1", "20261012T080000", "Gym A", "CATEGORIES:GYM\n"), name="Half M - Gym"))
    # Importing the gym calendar removed nothing from the running one.
    cals = {k["calendar"]: k for k in c.get("/api/plan/calendars").json()}
    assert cals["Half M - Running"]["sessions"] == 3 and cals["Half M - Gym"]["by_sport"] == {"strength": 1}


def test_name_from_the_file_when_the_calendar_has_none(client):
    c, _, _ = client
    body = b"BEGIN:VCALENDAR\nBEGIN:VEVENT\nUID:s1\nDTSTART:20261016T080000\nSUMMARY:Swim 8x100\nEND:VEVENT\nEND:VCALENDAR\n"
    assert c.post("/api/plan/ics", params={"name": "training-swim.ics"}, content=body).json()["calendar"] == "training-swim"
    r = c.post("/api/plan/ics", content=body)
    assert r.status_code == 422 and "no name" in r.json()["detail"]


def test_refusals(client):
    c, name, _ = client
    r = c.post("/api/plan/ics", content=b"not a calendar")
    assert r.status_code == 422 and "not an .ics" in r.json()["detail"]
    assert c.post("/api/plan/ics", content=b"x" * (2 * 1024 * 1024 + 1)).status_code == 413
    with _db(name) as conn:
        assert conn.execute("SELECT count(*) FROM planned_session").fetchone() == (0,)


def test_reimport_from_raw(client, monkeypatch):
    c, name, _ = client
    c.post("/api/plan/ics", content=V2)
    monkeypatch.setenv("POSTGRES_DB", name)
    from app import plan

    out = plan.reimport()
    assert out == [{"calendar": "Half M - Running", "sessions": 3, "added": 0, "updated": 0, "unchanged": 3, "removed": 0}]

