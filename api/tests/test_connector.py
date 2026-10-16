"""Read-only API tokens (app/api_tokens, app/common.require_auth), the connector endpoints (app/connector_api)
and the Claude desktop connector itself (connector/traininghub_mcp.py), against a real Postgres 16."""

import importlib.util
import json
import os
from pathlib import Path

import psycopg
import pytest
from conftest import TEST_PASSWORD
from fastapi.testclient import TestClient

pytestmark = pytest.mark.skipif(not os.environ.get("TEST_PG_HOST"), reason="TEST_PG_HOST not set")

CONNECTOR = Path(__file__).resolve().parents[2] / "connector" / "traininghub_mcp.py"


def _db(name):
    return psycopg.connect(host=os.environ["TEST_PG_HOST"], port=os.environ.get("TEST_PG_PORT", "5432"),
                           user=os.environ.get("TEST_PG_USER", "postgres"), password=os.environ.get("TEST_PG_PASSWORD", ""),
                           dbname=name)


@pytest.fixture()
def setup(env, tmp_path, monkeypatch):
    from app import api_tokens
    from test_activities import RUN

    c, name = env
    monkeypatch.setenv("RAW_DIR", str(tmp_path / "raw"))
    assert c.post("/api/login", json={"password": TEST_PASSWORD}).status_code == 204
    with _db(name) as conn:
        conn.execute(
            "INSERT INTO glucose_reading (minute, ts, mg_dl, trend, source) "
            "SELECT m, m, 150, 3, 'llu_live' FROM generate_series('2026-10-10 06:55Z'::timestamptz, '2026-10-10 09:30Z', interval '1 min') m"
        )
        token = api_tokens.create(conn, "test")
        conn.commit()
    c.post("/api/entries", json={"ts": "2026-10-10T06:30:00Z", "carbs": {"grams": 30, "description": "toast"}})
    aid = c.post("/api/activities/fit", content=RUN).json()["activities"][0]["id"]
    from app import main

    t = TestClient(main.app, base_url="https://testserver", headers={"Authorization": f"Bearer {token}"})
    return {"session": c, "token_client": t, "token": token, "aid": aid, "db": name, "app": main.app}


def test_a_token_can_read_and_nothing_else(setup):
    t = setup["token_client"]
    assert t.get("/api/activities").status_code == 200
    assert t.get(f"/api/activities/{setup['aid']}/graph").status_code == 200
    assert t.post("/api/entries", json={"ts": "2026-10-10T08:00:00Z", "carbs": {"grams": 5}}).status_code == 403
    assert t.put(f"/api/activities/{setup['aid']}/note", json={"note": "x"}).status_code == 403
    assert t.get("/api/barcode/8410000000000").status_code == 403  # a GET that stores products: not for tokens
    from app import main

    bad = TestClient(main.app, base_url="https://testserver", headers={"Authorization": "Bearer th_wrong"})
    assert bad.get("/api/activities").status_code == 401
    with _db(setup["db"]) as conn:
        assert conn.execute("SELECT last_used_at IS NOT NULL FROM api_token").fetchone() == (True,)
        conn.execute("UPDATE api_token SET revoked_at = now()")
        conn.commit()
    assert t.get("/api/activities").status_code == 401


def test_connector_endpoints(setup):
    t = setup["token_client"]
    a = t.get(f"/api/connector/activity/{setup['aid']}").json()
    assert a["id"] == setup["aid"] and a["glucose_metrics"]["glucose_start"] == 150 and a["report"] is None
    # Readings from 06:55 UTC = 08:55 local; times without a zone are local.
    g = t.get("/api/connector/glucose", params={"start": "2026-10-10T09:00", "end": "2026-10-10T09:10"}).json()
    assert g["start"] == "2026-10-10 09:00" and len(g["readings"]) == 10 and g["readings"][0] == ["2026-10-10 09:00", 150, 1]
    assert g["figures"]["in_range_pct"] == 100.0
    day = t.get("/api/connector/glucose", params={"start": "2026-10-10", "end": "2026-10-11"}).json()
    assert day["columns"][0] == "time (15-min bucket)" and day["readings"][0][1:] == [150, 150, 150, 5]
    lg = t.get("/api/connector/log", params={"start": "2026-10-10", "end": "2026-10-11"}).json()
    assert lg["entries"] == [{"time": "2026-10-10 08:30", "carbs_g": 30.0, "what": "toast"}]
    assert t.get("/api/connector/glucose", params={"start": "2026-10-01", "end": "2026-10-30"}).status_code == 422
    assert t.get("/api/connector/log", params={"start": "2026-10-11", "end": "2026-10-10"}).status_code == 422
    assert "Riddell" in t.get("/api/connector/reference").json()["text"]


def test_the_connector_tools(setup, monkeypatch):
    pytest.importorskip("mcp")
    spec = importlib.util.spec_from_file_location("traininghub_mcp", CONNECTOR)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # at import it tries the real URL for the reference text and carries on without it
    monkeypatch.setattr(mod, "client", setup["token_client"])

    listed = json.loads(mod.list_activities(days=3650))
    runs = listed["activities"]
    assert [r["id"] for r in runs] == [setup["aid"]] and "curve" not in runs[0]
    assert listed["count"] == 1 and "sports" not in listed  # no all-time counts to mistake for the window's
    assert isinstance(runs[0]["start"], str) and runs[0]["start"][4:8] == "2026"  # "Sat 2026-10-10 08:..", local
    act = json.loads(mod.get_activity(setup["aid"]))
    assert act["context_before"]["pre_run_carbs"] == 30
    week = json.loads(mod.get_week("2026-10-10"))
    assert week["monday"] == "2026-10-05" and week["summary"] is None
    assert json.loads(mod.similar_runs(glucose=150))["summary"]["n"] == 1
    assert json.loads(mod.glucose_between("2026-10-10T09:00", "2026-10-10T09:10"))["figures"]["mean"] == 150
    assert json.loads(mod.log_between("2026-10-10", "2026-10-11"))["entries"][0]["what"] == "toast"
    assert "glucose" in json.loads(mod.current_state())
    assert json.loads(mod.training_settings())["lthr"] == 191
    with pytest.raises(mod.Problem, match="at most 14 days"):
        mod.glucose_between("2026-10-01", "2026-10-30")
    # Every tool the connector offers, with its description, is registered for Claude.
    names = {t.name for t in mod.mcp._tool_manager.list_tools()}
    assert names == {"list_activities", "get_activity", "get_week", "similar_runs", "glucose_between", "log_between",
                     "current_state", "training_settings"}
