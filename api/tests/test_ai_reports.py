"""Run reports end to end (app/ai_input, ai_reports, ai_worker) against a real Postgres 16, with a fake provider."""

import json
import os

import psycopg
import pytest
from conftest import TEST_PASSWORD
from psycopg.rows import dict_row

from app import ai_input, ai_providers, ai_reports, ai_worker
from app.ai_guard import NOTE

pytestmark = pytest.mark.skipif(not os.environ.get("TEST_PG_HOST"), reason="TEST_PG_HOST not set")

from test_activities import RUN  # noqa: E402

REPORT = "## Summary\nGlucose fell from 160 to 100.\n## Worth noting\n- Consider reducing your bolus before runs.\n- Coverage was full."


def _db(name, **kw):
    return psycopg.connect(host=os.environ["TEST_PG_HOST"], port=os.environ.get("TEST_PG_PORT", "5432"),
                           user=os.environ.get("TEST_PG_USER", "postgres"), password=os.environ.get("TEST_PG_PASSWORD", ""),
                           dbname=name, **kw)


@pytest.fixture()
def client(env, tmp_path, monkeypatch):
    c, name = env
    assert c.post("/api/login", json={"password": TEST_PASSWORD}).status_code == 204
    monkeypatch.setenv("RAW_DIR", str(tmp_path / "raw"))
    for k in ("ANTHROPIC_API_KEY", "GEMINI_API_KEY"):  # set then deleted: monkeypatch restores them after the test
        monkeypatch.setenv(k, "")
        monkeypatch.delenv(k)
    c.dbname = name
    with _db(name, autocommit=True) as conn:
        conn.execute(
            "INSERT INTO glucose_reading (minute, ts, mg_dl, trend, source) "
            "SELECT m, m, greatest(100, 160 - 2 * greatest(0, extract(epoch FROM m - '2026-10-10 07:00Z')::int / 60)), 2, 'llu_live' "
            "FROM generate_series('2026-10-10 06:55Z'::timestamptz, '2026-10-10 09:30Z', interval '1 min') m"
        )
    c.post("/api/entries", json={"ts": "2026-10-08T12:00:00Z", "carbs": {"grams": 10}})  # the log starts before the run
    c.post("/api/entries", json={"ts": "2026-10-10T06:30:00Z", "carbs": {"grams": 30, "description": "toast"}})
    c.aid = c.post("/api/activities/fit", content=RUN).json()["activities"][0]["id"]
    # The fake provider: records what it was asked, answers REPORT.
    c.calls = []

    def fake_complete(provider, model, system, user, max_tokens=8192):
        c.calls.append({"provider": provider, "model": model, "system": system, "user": user})
        if c.fail:
            raise c.fail
        return ai_providers.Completion(REPORT, 5000, 400, model)

    c.fail = None
    monkeypatch.setattr(ai_providers, "complete", fake_complete)
    return c


def _service(client, claude=False, gemini=False):
    """What the analysis service reports about itself (it alone has the keys)."""
    if claude:
        os.environ["ANTHROPIC_API_KEY"] = "x"
    if gemini:
        os.environ["GEMINI_API_KEY"] = "x"
    with _db(client.dbname, row_factory=dict_row) as conn:
        ai_worker.report_status(conn)


def _worker(client):
    with _db(client.dbname, row_factory=dict_row) as conn:
        return ai_worker.run_one(conn)


def test_settings(client):
    s = client.get("/api/settings/ai").json()
    # Version 2 from migration 0016: Gemini by default; Claude's model kept for when a key is added.
    assert s["current"]["version"] == 2 and s["current"]["default_provider"] == "gemini"
    assert s["current"]["claude_model"] == "claude-sonnet-5-5"
    assert s["current"]["gemini_model"] == "gemini-3.8-flash" and s["current"]["auto_report"] is True
    assert s["service"] == {"running": False, "seen_at": None, "configured": {"claude": False, "gemini": False}}
    assert s["usage_30d"] == []
    _service(client, gemini=True)
    s = client.get("/api/settings/ai").json()["service"]
    assert s["running"] is True and s["configured"] == {"claude": False, "gemini": True}
    body = {"default_provider": "claude", "claude_model": "claude-opus-5-5", "gemini_model": "gemini-3.8-flash", "auto_report": False}
    assert client.post("/api/settings/ai", json=body).json()["version"] == 3
    assert client.post("/api/settings/ai", json=body).status_code == 409
    assert client.post("/api/settings/ai", json={**body, "claude_model": "bad model name"}).status_code == 422


def test_input(client):
    with _db(client.dbname, row_factory=dict_row) as conn:
        data, sha = ai_input.build_run(conn, client.aid)
    assert data["activity"]["start_local"] == "2026-10-10 09:00 (Sat)" and data["activity"]["avg_pace_min_km"] == "5:34"  # as the run page shows it
    assert data["glucose_metrics"]["glucose_start"] == 160 and data["context_before"]["trend_start"] == "falling"
    assert data["context_before"]["pre_run_carbs"] == 30
    assert data["log_6h_before_to_2h_after"] == [{"min": -30.0, "carbs_g": 30.0, "what": "toast"}]
    assert data["series_30s"]["columns"] == ["min", "hr", "pace_s_km", "alt_m"] and len(data["series_30s"]["rows"]) == 60
    assert data["glucose_series"]["rows"][0] == [-5.0, 160, 1] and len(data["glucose_series"]["rows"]) == 156
    assert data["training_metrics"]["time_in_zone_min"]["z1"] > 0
    assert data["similar_past_runs"]["n"] == 0  # nothing before it
    # Decided by code, and the model must mention each: here only that no rapid insulin was logged.
    assert data["flags_must_mention"] == ["No rapid insulin logged in the 24 h before: insulin on board is 0 only if no dose went unlogged."]
    assert len(sha) == 64


def test_request_needs_the_service_and_a_key(client):
    r = client.post(f"/api/activities/{client.aid}/report", json={})
    assert r.status_code == 503 and "not running" in r.json()["detail"]
    _service(client)
    r = client.post(f"/api/activities/{client.aid}/report", json={})
    assert r.status_code == 400 and "GEMINI_API_KEY" in r.json()["detail"]


def test_a_report_is_written_guarded_and_stored(client, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "x")
    _service(client)
    assert client.post(f"/api/activities/{client.aid}/report", json={}).status_code == 202
    assert client.post(f"/api/activities/{client.aid}/report", json={}).status_code == 409  # one at a time
    assert client.get(f"/api/activities/{client.aid}/report").json()["job"]["finished_at"] is None
    assert _worker(client) is True and _worker(client) is False
    call = client.calls[0]
    assert call["provider"] == "gemini" and call["model"] == "gemini-3.8-flash"
    assert "Never write an insulin dose" in call["system"] and "Riddell" in call["system"]  # rules and the reference
    sent = json.loads(call["user"].split("```json\n")[1].split("\n```")[0])
    assert sent["glucose_metrics"]["glucose_start"] == 160
    out = client.get(f"/api/activities/{client.aid}/report").json()
    rep = out["report"]
    assert rep["removed"] == 1 and NOTE in rep["text"] and "reducing your bolus" not in rep["text"]
    assert "Coverage was full." in rep["text"]
    assert (rep["provider"], rep["input_tokens"], rep["output_tokens"], rep["stale"]) == ("gemini", 5000, 400, False)
    assert out["job"]["ok"] is True
    usage = client.get("/api/settings/ai").json()["usage_30d"]
    assert usage == [{"provider": "gemini", "calls": 1, "failed": 0, "input_tokens": 5000, "output_tokens": 400}]
    # A log change the run's numbers depend on makes the report out of date.
    client.post("/api/entries", json={"ts": "2026-10-10T06:45:00Z", "carbs": {"grams": 15}})
    assert client.get(f"/api/activities/{client.aid}/report").json()["report"]["stale"] is True


def test_switching_to_claude(client, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "x")
    _service(client)
    assert client.post(f"/api/activities/{client.aid}/report", json={"provider": "gemini"}).status_code == 400
    assert client.post(f"/api/activities/{client.aid}/report", json={"provider": "claude"}).status_code == 202
    _worker(client)
    assert client.calls[0]["provider"] == "claude" and client.calls[0]["model"] == "claude-sonnet-5-5"
    assert client.get(f"/api/activities/{client.aid}/report").json()["report"]["provider"] == "claude"


def test_a_report_after_an_import_uses_the_provider_that_has_a_key(client, monkeypatch):
    # Default Gemini, but only a Claude key: the job (provider left to the service) goes to Claude.
    monkeypatch.setenv("ANTHROPIC_API_KEY", "x")
    with _db(client.dbname, row_factory=dict_row) as conn:
        ai_reports.queue(conn, client.aid, None, "import")
        conn.commit()
    _worker(client)
    assert client.calls[0]["provider"] == "claude"


def test_temporary_failures_are_retried_then_given_up(client, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "x")
    _service(client)
    client.post(f"/api/activities/{client.aid}/report", json={})
    client.fail = ai_providers.Temporary("HTTP 529")
    for attempt in range(1, ai_worker.MAX_ATTEMPTS + 1):
        assert _worker(client) is True
        with _db(client.dbname, autocommit=True) as conn:
            job = conn.execute("SELECT finished_at, next_try_at, attempts, ok, error FROM ai_job").fetchone()
            assert job[2] == attempt
            if attempt < ai_worker.MAX_ATTEMPTS:
                assert job[0] is None and job[1] is not None and "trying again" in job[4]
                assert _worker(client) is False  # not due yet
                conn.execute("UPDATE ai_job SET next_try_at = now()")
            else:
                assert job[0] is not None and job[3] is False and job[4] == "HTTP 529"
    with _db(client.dbname, autocommit=True) as conn:
        assert conn.execute("SELECT count(*), bool_or(ok) FROM ai_call").fetchone() == (4, False)
        assert conn.execute("SELECT count(*) FROM ai_report").fetchone() == (0,)


def test_lasting_failures_end_the_job(client, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "x")
    _service(client)
    client.post(f"/api/activities/{client.aid}/report", json={})
    client.fail = ai_providers.Failed("HTTP 400: model: not found")
    _worker(client)
    job = client.get(f"/api/activities/{client.aid}/report").json()["job"]
    assert job["ok"] is False and job["error"] == "HTTP 400: model: not found" and job["attempts"] == 1


def test_queued_after_an_import_only_when_recent_and_asked_for(client):
    # The fixture's run ended days ago: an import queues nothing.
    with _db(client.dbname, autocommit=True) as conn:
        assert conn.execute("SELECT count(*) FROM ai_job").fetchone() == (0,)
        recent = conn.execute(
            """INSERT INTO activity (fit_key, session_index, source, sport, start_ts, end_ts, elapsed_s, fit_path, fit_sha256, parser_version)
               VALUES ('r', 0, 'garmin', 'running', now() - interval '2 h', now() - interval '1 h', 3600, 'x', 'x', 't') RETURNING id"""
        ).fetchone()[0]
    with _db(client.dbname, row_factory=dict_row) as conn:
        assert ai_reports.queue_after_import(conn, [{"id": client.aid}, {"id": recent}]) == 1
        conn.execute("INSERT INTO ai_settings (default_provider, claude_model, gemini_model, auto_report) VALUES ('claude', 'm', 'g', false)")
        assert ai_reports.queue_after_import(conn, [{"id": recent}]) == 0
        conn.commit()
    with _db(client.dbname, autocommit=True) as conn:
        assert conn.execute("SELECT ref_id, provider, trigger FROM ai_job").fetchall() == [(recent, None, "import")]


def test_needs_login(env):
    c, _ = env
    assert c.get("/api/settings/ai").status_code == 401
    assert c.get("/api/activities/1/report").status_code == 401
    assert c.post("/api/activities/1/report", json={}).status_code == 401
