"""The training week (app/week.py) and its AI summary (app/ai_reports, kind week_summary).

Pure parts always run; the database parts need TEST_PG_HOST, like the other database tests."""

import os
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

import psycopg
import pytest
from conftest import TEST_PASSWORD
from psycopg.rows import dict_row

from app import ai_providers, ai_reports, ai_worker, week

T0 = datetime(2026, 10, 10, 7, 0, tzinfo=UTC)
LOCAL = ZoneInfo("Europe/Madrid")


# --- pure ----------------------------------------------------------------------------------------------


def test_glucose_stats_weights_each_reading_by_the_time_it_stands_for():
    readings = [(T0 + timedelta(minutes=m), 100, True) for m in range(30)]  # 30 live minutes in range
    readings += [(T0 + timedelta(minutes=30), 60, False), (T0 + timedelta(minutes=45), 260, False)]  # 15 min each
    s = week.glucose_stats(readings, T0, T0 + timedelta(minutes=60), low=70, high=180)
    assert s["coverage_pct"] == 100.0 and s["in_range_pct"] == 50.0 and s["below_pct"] == 25.0
    assert s["above_pct"] == 25.0 and s["very_high_pct"] == 25.0 and s["very_low_pct"] == 0.0 and s["mean"] == 130


def test_glucose_stats_with_gaps_and_nothing():
    # One history reading in two hours: 15 of 120 minutes covered.
    s = week.glucose_stats([(T0, 150, False)], T0, T0 + timedelta(hours=2), 70, 180)
    assert s["coverage_pct"] == 12.5 and s["in_range_pct"] == 100.0
    assert week.glucose_stats([], T0, T0 + timedelta(hours=1), 70, 180)["in_range_pct"] is None


def test_bounds_follow_local_midnight_across_the_clock_change():
    start, end = week.bounds(date(2026, 10, 19))  # the week of 25 Oct, when summer time ends
    assert start == datetime(2026, 10, 18, 22, 0, tzinfo=UTC) and end == datetime(2026, 10, 25, 23, 0, tzinfo=UTC)
    assert week.monday_of(date(2026, 10, 11)) == date(2026, 10, 5) and week.ref_of(date(2026, 10, 5)) == 20261005


def test_week_flags():
    g = {"below_pct": 5.2, "very_high_pct": 1.0, "coverage_pct": 55.0}
    sessions = [{"day": "Sat 10 Oct", "nadir": 62, "flags": [{"kind": "safety", "text": "Glucose was above 270 mg/dL (...)"}]}]
    nights = [{"day": "Sat 10 Oct", "min_below": 35, "lowest": 58}]
    days = [{"day": "Mon 05 Oct", "rapid_u": 0, "future": False}, {"day": "Tue 06 Oct", "rapid_u": 4, "future": False}]
    f = week.week_flags(sessions, g, nights, days, 70)
    texts = [x["text"] for x in f]
    assert [x["kind"] for x in f] == ["safety", "safety", "safety", "safety", "log", "data"]
    assert [x["code"] for x in f] == ["ketones", "went_low", "night_low", "time_low", "no_bolus", "coverage"]
    assert texts[0].startswith("1 session with glucose above 270 mg/dL (Sat 10 Oct)")
    assert texts[1] == "Below 70 mg/dL during or in the 2 h after 1 session (Sat 10 Oct)."
    assert texts[2] == "Below 70 mg/dL overnight after training: night after Sat 10 Oct, 35 min, lowest 58."
    assert "5.2 % of the covered time" in texts[3] and "Mon 05 Oct" in texts[4] and "55 %" in texts[5]


def test_week_flags_before_the_app_had_data():
    g = {"below_pct": None, "very_high_pct": None, "coverage_pct": 0.0}
    days = [{"day": "Mon 05 Oct", "rapid_u": 0, "future": False, "before_log": True}]
    f = week.week_flags([], g, [], days, 70, glucose_from="never", log_from="never")
    assert [x["text"] for x in f] == [
        "The insulin and carbs log had not started yet this week: nothing is known about insulin or carbs.",
        "Glucose collection had not started yet this week: there are no glucose figures.",
    ]
    # Started mid-week: said once, and the days before it are not listed as gaps.
    days += [{"day": "Tue 06 Oct", "rapid_u": 0, "future": False, "before_log": False}]
    f = week.week_flags([], {**g, "coverage_pct": 90.0}, [], days, 70, glucose_from="Sat 10 Oct", log_from="Tue 06 Oct")
    assert [x["text"] for x in f] == [
        "The insulin and carbs log started on Tue 06 Oct: insulin and carbs before it are unknown.",
        "No rapid insulin logged on Tue 06 Oct: insulin figures for those days are incomplete if doses went unlogged.",
        "Glucose collection started on Sat 10 Oct: the glucose figures count from then.",
    ]


# --- database ------------------------------------------------------------------------------------------

db = pytest.mark.skipif(not os.environ.get("TEST_PG_HOST"), reason="TEST_PG_HOST not set")


def _db(name, **kw):
    return psycopg.connect(host=os.environ["TEST_PG_HOST"], port=os.environ.get("TEST_PG_PORT", "5432"),
                           user=os.environ.get("TEST_PG_USER", "postgres"), password=os.environ.get("TEST_PG_PASSWORD", ""),
                           dbname=name, **kw)


@pytest.fixture()
def client(env, tmp_path, monkeypatch):
    from test_activities import RUN

    c, name = env
    assert c.post("/api/login", json={"password": TEST_PASSWORD}).status_code == 204
    monkeypatch.setenv("RAW_DIR", str(tmp_path / "raw"))
    for k in ("ANTHROPIC_API_KEY", "GEMINI_API_KEY"):
        monkeypatch.setenv(k, "")
        monkeypatch.delenv(k)
    c.dbname = name
    with _db(name, autocommit=True) as conn:
        # Saturday 10 Oct: the run 07:00-07:30 UTC with live readings 160 falling to 100 by 07:30.
        conn.execute(
            "INSERT INTO glucose_reading (minute, ts, mg_dl, trend, source) "
            "SELECT m, m, greatest(100, 160 - 2 * greatest(0, extract(epoch FROM m - '2026-10-10 07:00Z')::int / 60)), 3, 'llu_live' "
            "FROM generate_series('2026-10-10 06:55Z'::timestamptz, '2026-10-10 09:30Z', interval '1 min') m"
        )
        # The night after (Madrid 22:00 Sat to 07:00 Sun = 20:00 to 05:00 UTC): 60 mg/dL at 02:00 local, 15 min.
        conn.execute("INSERT INTO glucose_reading (minute, ts, mg_dl, source) VALUES ('2026-10-11 00:00Z', '2026-10-11 00:00Z', 60, 'llu_graph')")
    c.post("/api/entries", json={"ts": "2026-10-06T07:00:00Z", "carbs": {"grams": 50}, "dose": {"units": 5, "kind": "rapid", "insulin_name": "Humalog"}})
    c.post("/api/entries", json={"ts": "2026-10-06T21:00:00Z", "dose": {"units": 18, "kind": "basal", "insulin_name": "basal"}})
    c.aid = c.post("/api/activities/fit", content=RUN).json()["activities"][0]["id"]
    c.calls = []

    def fake_complete(provider, model, system, user, max_tokens=8192):
        c.calls.append({"provider": provider, "system": system, "user": user})
        return ai_providers.Completion("## The week\nOne run.", 3000, 300, model)

    monkeypatch.setattr(ai_providers, "complete", fake_complete)
    return c


@db
def test_the_week_numbers(client):
    w = client.get("/api/weeks/2026-10-07").json()  # a Wednesday: the week starts on Monday 5 Oct
    assert w["monday"] == "2026-10-05" and w["complete"] is True and w["label"] == "Week of Mon 05 Oct 2026"
    assert w["by_sport"] == [{"sport": "running", "n": 1, "minutes": 30, "km": 5.4}]
    (s,) = w["sessions"]
    assert (s["day"], s["local_time"], s["glucose_start"], s["delta"]) == ("Sat 10 Oct", "09:00", 160, -60)
    assert sum(w["zones_min"]) == 30
    tue = w["days"][1]
    assert (tue["day"], tue["carbs_g"], tue["rapid_u"], tue["basal_u"], tue["entries"]) == ("Tue 06 Oct", 50, 5, 18, 3)
    (night,) = w["nights"]
    assert (night["day"], night["lowest"], night["min_below"]) == ("Sat 10 Oct", 60, 15)
    texts = [f["text"] for f in w["flags"]]
    assert "Below 70 mg/dL overnight after training: night after Sat 10 Oct, 15 min, lowest 60." in texts
    # The log started on Tuesday (Monday is not a gap); glucose collection on Saturday (figures from then).
    assert (w["log_from"], w["glucose_from"]) == ("Tue 06 Oct", "Sat 10 Oct")
    assert "The insulin and carbs log started on Tue 06 Oct: insulin and carbs before it are unknown." in texts
    no_rapid = next(t for t in texts if t.startswith("No rapid insulin logged on"))
    assert "Mon 05 Oct" not in no_rapid and "Tue 06 Oct" not in no_rapid and "Wed 07 Oct" in no_rapid
    assert "Glucose collection started on Sat 10 Oct: the glucose figures count from then." in texts
    assert client.get("/api/weeks/2099-01-05").status_code == 404


@db
def test_the_weekly_summary_on_request(client, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "x")
    with _db(client.dbname, row_factory=dict_row) as conn:
        ai_worker.report_status(conn)
    assert client.post("/api/weeks/2026-10-05/report", json={}).status_code == 202
    assert client.post("/api/weeks/2026-10-05/report", json={}).status_code == 409
    with _db(client.dbname, row_factory=dict_row) as conn:
        assert ai_worker.run_one(conn) is True
    call = client.calls[0]
    assert "summary of one training week" in call["system"] and "Battelino" in call["system"]
    assert '"week":"Week of Mon 05 Oct 2026"' in call["user"] and "flags_must_mention" in call["user"]
    out = client.get("/api/weeks/2026-10-05/report").json()
    assert out["report"]["text"] == "## The week\nOne run." and out["report"]["prompt_version"] == "week-2"
    assert out["report"]["stale"] is False and out["job"]["ok"] is True


@db
def test_the_weekly_summary_is_queued_on_monday_once(client):
    monday_7 = datetime(2026, 10, 12, 7, 0, tzinfo=LOCAL)
    with _db(client.dbname, row_factory=dict_row) as conn:
        assert ai_reports.queue_weekly(conn, datetime(2026, 10, 12, 5, 30, tzinfo=LOCAL)) is None  # before 06:00
        assert ai_reports.queue_weekly(conn, monday_7) is not None
        assert ai_reports.queue_weekly(conn, monday_7 + timedelta(hours=3)) is None  # once
        # A week without glucose gets no summary by itself.
        assert ai_reports.queue_weekly(conn, datetime(2026, 11, 2, 7, 0, tzinfo=LOCAL)) is None
        job = conn.execute("SELECT kind, ref_id, trigger, provider FROM ai_job").fetchall()
    assert job == [{"kind": "week_summary", "ref_id": 20261005, "trigger": "schedule", "provider": None}]


@db
def test_no_weekly_summary_when_reports_are_off(client):
    with _db(client.dbname, row_factory=dict_row) as conn:
        conn.execute("INSERT INTO ai_settings (default_provider, claude_model, gemini_model, auto_report) VALUES ('gemini', 'c', 'g', false)")
        conn.commit()
        assert ai_reports.queue_weekly(conn, datetime(2026, 10, 12, 7, 0, tzinfo=LOCAL)) is None
