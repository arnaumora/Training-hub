"""Insulin and carbs log API against a real Postgres 16 (skipped unless TEST_PG_HOST is set)."""

import os
from datetime import datetime, timedelta, timezone

import psycopg
import pytest
from conftest import TEST_PASSWORD

pytestmark = pytest.mark.skipif(not os.environ.get("TEST_PG_HOST"), reason="TEST_PG_HOST not set")


def _db(name):
    return psycopg.connect(
        host=os.environ["TEST_PG_HOST"],
        port=os.environ.get("TEST_PG_PORT", "5432"),
        user=os.environ.get("TEST_PG_USER", "postgres"),
        password=os.environ.get("TEST_PG_PASSWORD", ""),
        dbname=name,
        autocommit=True,
    )


def _audit(name):
    with _db(name) as c:
        return c.execute(
            "SELECT table_name, row_id, action, old_row, new_row FROM log_audit ORDER BY id"
        ).fetchall()


# Test values only (a 10 g racion so the arithmetic below is easy to check by eye).
TEST_SETTINGS = {
    "meal_slots": [{"name": "Breakfast", "start": "07:00", "ratio": 2}, {"name": "Lunch", "start": "12:00", "ratio": 1.5}],
    "isf": 40,
    "correct_above": 160,
    "correct_to": 160,
    "action_time_h": 5,
    "action_time_provisional": True,
    "pen_increment": 1,
    "racion_g": 10,
    "rapid_insulin": "Humalog",
}


@pytest.fixture()
def bare_client(env):
    """Logged in, Settings > Insulin never saved."""
    c, name = env
    assert c.post("/api/login", json={"password": TEST_PASSWORD}).status_code == 204
    c.dbname = name
    return c


@pytest.fixture()
def client(bare_client):
    """Logged in, with a saved Settings > Insulin version (needed for raciones)."""
    assert bare_client.post("/api/settings/insulin", json=TEST_SETTINGS).status_code == 201
    return bare_client


def _iso(minutes_ago):
    return (datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)).isoformat()


# --- access -----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "method,path",
    [
        ("get", "/api/log"),
        ("post", "/api/entries"),
        ("put", "/api/entries"),
        ("delete", "/api/entries?dose_id=1"),
        ("post", "/api/doses"),
        ("patch", "/api/doses/1"),
        ("delete", "/api/doses/1"),
        ("post", "/api/carbs"),
        ("patch", "/api/carbs/1"),
        ("delete", "/api/carbs/1"),
    ],
)
def test_every_endpoint_requires_login(env, method, path):
    c, _ = env
    assert getattr(c, method)(path).status_code == 401


# --- doses ------------------------------------------------------------------------------------


def test_add_dose_defaults_to_now_and_appears_in_log(client):
    before = datetime.now(timezone.utc).timestamp()
    r = client.post("/api/doses", json={"units": 4, "kind": "rapid", "insulin_name": " Humalog "})
    assert r.status_code == 201
    dose = r.json()
    assert dose["units"] == 4 and dose["kind"] == "rapid" and dose["insulin_name"] == "Humalog"
    assert dose["note"] is None
    assert before - 5 <= dose["t"] <= before + 5

    body = client.get("/api/log").json()
    assert [d["id"] for d in body["doses"]] == [dose["id"]]
    assert body["last_insulin_name"] == {"rapid": "Humalog", "basal": None}
    assert body["last_carb_entry"] is None and body["racion_g"] == 10


def test_add_dose_with_explicit_time(client):
    when = datetime.now(timezone.utc).replace(microsecond=0) - timedelta(hours=2)
    r = client.post(
        "/api/doses",
        json={"ts": when.isoformat(), "units": 20, "kind": "basal", "insulin_name": "Lantus", "note": "dinner"},
    )
    assert r.status_code == 201
    assert r.json()["t"] == int(when.timestamp()) and r.json()["note"] == "dinner"


@pytest.mark.parametrize(
    "body",
    [
        {"units": 0, "kind": "rapid", "insulin_name": "Humalog"},
        {"units": -1, "kind": "rapid", "insulin_name": "Humalog"},
        {"units": 101, "kind": "rapid", "insulin_name": "Humalog"},
        {"units": 1.25, "kind": "rapid", "insulin_name": "Humalog"},  # more than one decimal: a typo
        {"units": 4, "kind": "bolus", "insulin_name": "Humalog"},
        {"units": 4, "kind": "rapid", "insulin_name": "   "},
        {"units": 4, "kind": "rapid"},
        {"units": 4, "kind": "rapid", "insulin_name": "x" * 41},
        {"units": 4, "kind": "rapid", "insulin_name": "Humalog", "ts": "2026-10-12T10:00:00"},  # no offset
        {"units": 4, "kind": "rapid", "insulin_name": "Humalog", "ts": "FUTURE"},
        {"units": 4, "kind": "rapid", "insulin_name": "Humalog", "ts": "2020-01-01T10:00:00+00:00"},
    ],
)
def test_add_dose_rejects_bad_input(client, body):
    if body.get("ts") == "FUTURE":
        body = {**body, "ts": _iso(-10)}
    assert client.post("/api/doses", json=body).status_code == 422


def test_last_insulin_name_is_latest_per_kind_ignoring_deleted(client):
    client.post("/api/doses", json={"ts": _iso(300), "units": 3, "kind": "rapid", "insulin_name": "Humalog"})
    client.post("/api/doses", json={"ts": _iso(200), "units": 3, "kind": "rapid", "insulin_name": "Humalog"})
    newest = client.post(
        "/api/doses", json={"ts": _iso(100), "units": 3, "kind": "rapid", "insulin_name": "Typo"}
    ).json()
    client.post("/api/doses", json={"ts": _iso(150), "units": 20, "kind": "basal", "insulin_name": "Lantus"})
    assert client.delete(f"/api/doses/{newest['id']}").status_code == 204
    assert client.get("/api/log").json()["last_insulin_name"] == {"rapid": "Humalog", "basal": "Lantus"}


def test_edit_dose_is_audited(client):
    dose = client.post("/api/doses", json={"units": 4, "kind": "rapid", "insulin_name": "Humalog"}).json()
    r = client.patch(f"/api/doses/{dose['id']}", json={"units": 5, "note": "corrected"})
    assert r.status_code == 200
    assert r.json()["units"] == 5 and r.json()["note"] == "corrected"

    [(table, row_id, action, old, new)] = _audit(client.dbname)
    assert (table, row_id, action) == ("insulin_dose", dose["id"], "update")
    assert float(old["units"]) == 4 and float(new["units"]) == 5
    assert new["updated_at"] > old["updated_at"]


def test_edit_dose_can_clear_note_but_not_required_fields(client):
    dose = client.post(
        "/api/doses", json={"units": 4, "kind": "rapid", "insulin_name": "Humalog", "note": "x"}
    ).json()
    assert client.patch(f"/api/doses/{dose['id']}", json={"note": None}).json()["note"] is None
    for field in ("units", "kind", "insulin_name", "ts"):
        assert client.patch(f"/api/doses/{dose['id']}", json={field: None}).status_code == 422


def test_delete_dose_is_soft_and_audited(client):
    dose = client.post("/api/doses", json={"units": 4, "kind": "rapid", "insulin_name": "Humalog"}).json()
    assert client.delete(f"/api/doses/{dose['id']}").status_code == 204
    assert client.get("/api/log").json()["doses"] == []
    # Gone for the API: a second delete or an edit is a 404.
    assert client.delete(f"/api/doses/{dose['id']}").status_code == 404
    assert client.patch(f"/api/doses/{dose['id']}", json={"units": 5}).status_code == 404
    # Still in the table, and the delete is in the audit trail.
    with _db(client.dbname) as c:
        assert c.execute("SELECT deleted_at IS NOT NULL FROM insulin_dose").fetchone()[0] is True
    [(_, _, action, old, new)] = _audit(client.dbname)
    assert action == "delete" and old["deleted_at"] is None and new["deleted_at"] is not None


def test_unknown_dose_is_404(client):
    assert client.patch("/api/doses/999", json={"units": 5}).status_code == 404
    assert client.delete("/api/doses/999").status_code == 404


# --- carbs ------------------------------------------------------------------------------------


def test_add_carbs_in_grams(client):
    r = client.post("/api/carbs", json={"grams": 45.5, "description": "  toast  "})
    assert r.status_code == 201
    c = r.json()
    assert c["grams"] == 45.5 and c["entry"] == "grams" and c["racion_g"] is None and c["raciones"] is None
    assert c["description"] == "toast"


def test_add_carbs_in_raciones_stores_grams_and_racion_size(client):
    c = client.post("/api/carbs", json={"raciones": 2.5}).json()
    assert c["grams"] == 25 and c["entry"] == "raciones" and c["racion_g"] == 10 and c["raciones"] == 2.5


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"grams": 30, "raciones": 3},
        {"grams": 0},
        {"grams": 1001},
        {"grams": 12.34},
        {"raciones": 0},
        {"raciones": 101},
        {"grams": 30, "description": "x" * 201},
    ],
)
def test_add_carbs_rejects_bad_input(client, body):
    assert client.post("/api/carbs", json=body).status_code == 422


def test_edit_carbs_switches_entry_mode(client):
    c = client.post("/api/carbs", json={"raciones": 3}).json()
    r = client.patch(f"/api/carbs/{c['id']}", json={"grams": 32})
    assert r.status_code == 200
    assert r.json()["grams"] == 32 and r.json()["entry"] == "grams" and r.json()["racion_g"] is None
    r = client.patch(f"/api/carbs/{c['id']}", json={"raciones": 1.5})
    assert r.json()["grams"] == 15 and r.json()["entry"] == "raciones" and r.json()["raciones"] == 1.5
    assert client.patch(f"/api/carbs/{c['id']}", json={"grams": 10, "raciones": 1}).status_code == 422
    assert [a[2] for a in _audit(client.dbname)] == ["update", "update"]


def test_edit_carbs_description_only_keeps_amount(client):
    c = client.post("/api/carbs", json={"raciones": 3}).json()
    r = client.patch(f"/api/carbs/{c['id']}", json={"description": "pasta"}).json()
    assert r["description"] == "pasta" and r["grams"] == 30 and r["entry"] == "raciones"


def test_delete_carbs(client):
    c = client.post("/api/carbs", json={"grams": 30}).json()
    assert client.delete(f"/api/carbs/{c['id']}").status_code == 204
    assert client.get("/api/log").json()["carbs"] == []
    assert [a[2] for a in _audit(client.dbname)] == ["delete"]


# --- log window and audit integrity -----------------------------------------------------------


def test_log_window_and_order(client):
    old = client.post("/api/carbs", json={"grams": 10, "ts": _iso(30 * 60)}).json()
    a = client.post("/api/carbs", json={"grams": 20, "ts": _iso(60)}).json()
    b = client.post("/api/carbs", json={"grams": 30, "ts": _iso(10)}).json()
    assert [c["id"] for c in client.get("/api/log?hours=24").json()["carbs"]] == [b["id"], a["id"]]
    assert [c["id"] for c in client.get("/api/log?hours=48").json()["carbs"]] == [b["id"], a["id"], old["id"]]
    assert client.get("/api/log?hours=0").status_code == 422
    assert client.get("/api/log?hours=169").status_code == 422


def test_hard_delete_from_sql_is_audited_and_audit_is_append_only(client):
    dose = client.post("/api/doses", json={"units": 4, "kind": "rapid", "insulin_name": "Humalog"}).json()
    with _db(client.dbname) as c:
        c.execute("DELETE FROM insulin_dose WHERE id = %s", (dose["id"],))
    [(_, row_id, action, old, new)] = _audit(client.dbname)
    assert (row_id, action, new) == (dose["id"], "delete", None) and float(old["units"]) == 4
    with _db(client.dbname) as c:
        with pytest.raises(psycopg.errors.RaiseException):
            c.execute("UPDATE log_audit SET action = 'update'")
        with pytest.raises(psycopg.errors.RaiseException):
            c.execute("DELETE FROM log_audit")


def test_last_carb_entry_follows_latest_non_deleted_entry(client):
    client.post("/api/carbs", json={"grams": 40, "ts": _iso(120)})
    client.post("/api/carbs", json={"raciones": 2, "ts": _iso(60)})
    assert client.get("/api/log").json()["last_carb_entry"] == "raciones"
    newest = client.post("/api/carbs", json={"grams": 15, "ts": _iso(5)}).json()
    assert client.get("/api/log").json()["last_carb_entry"] == "grams"
    client.delete(f"/api/carbs/{newest['id']}")
    assert client.get("/api/log").json()["last_carb_entry"] == "raciones"


# --- one Log tap: carbs and dose together -------------------------------------------------


def test_entry_with_carbs_and_dose_shares_one_time(client):
    r = client.post(
        "/api/entries",
        json={
            "carbs": {"raciones": 3, "description": "pasta"},
            "dose": {"units": 5, "kind": "rapid", "insulin_name": "Humalog", "note": None},
        },
    )
    assert r.status_code == 201
    body = r.json()
    assert body["carbs"]["grams"] == 30 and body["carbs"]["description"] == "pasta"
    assert body["dose"]["units"] == 5 and body["dose"]["insulin_name"] == "Humalog"
    assert body["carbs"]["t"] == body["dose"]["t"]
    log = client.get("/api/log").json()
    assert len(log["carbs"]) == 1 and len(log["doses"]) == 1


def test_entry_with_explicit_time_and_only_one_part(client):
    when = datetime.now(timezone.utc).replace(microsecond=0) - timedelta(minutes=30)
    r = client.post("/api/entries", json={"ts": when.isoformat(), "dose": {"units": 20, "kind": "basal", "insulin_name": "Lantus"}})
    assert r.status_code == 201 and r.json()["carbs"] is None and r.json()["dose"]["t"] == int(when.timestamp())
    r = client.post("/api/entries", json={"carbs": {"grams": 12}})
    assert r.status_code == 201 and r.json()["dose"] is None and r.json()["carbs"]["entry"] == "grams"


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"carbs": None, "dose": None},
        {"carbs": {}},
        {"carbs": {"grams": 10, "raciones": 1}},
        {"carbs": {"grams": 30}, "dose": {"units": 0, "kind": "rapid", "insulin_name": "Humalog"}},
        {"carbs": {"grams": 30}, "dose": {"units": 4, "kind": "rapid"}},
        {"carbs": {"grams": 30}, "ts": "FUTURE"},
    ],
)
def test_entry_rejects_bad_input_and_saves_nothing(client, body):
    if body.get("ts") == "FUTURE":
        body = {**body, "ts": _iso(-10)}
    assert client.post("/api/entries", json=body).status_code == 422
    log = client.get("/api/log").json()
    assert log["carbs"] == [] and log["doses"] == []


def test_entry_is_all_or_nothing(client):
    # Make the dose insert fail inside the database (after the carbs insert) and check nothing stays.
    with _db(client.dbname) as c:
        c.execute("ALTER TABLE insulin_dose ADD CONSTRAINT test_no_7 CHECK (units <> 7)")
    # The test client re-raises server errors; in production this is a 500.
    with pytest.raises(psycopg.errors.CheckViolation):
        client.post(
            "/api/entries",
            json={"carbs": {"grams": 40}, "dose": {"units": 7, "kind": "rapid", "insulin_name": "Humalog"}},
        )
    with _db(client.dbname) as c:
        assert c.execute("SELECT count(*) FROM carb_intake").fetchone()[0] == 0


# --- editing and deleting a whole event ----------------------------------------------------


def _meal(client, minutes_ago=20):
    return client.post(
        "/api/entries",
        json={
            "ts": _iso(minutes_ago),
            "carbs": {"raciones": 3, "description": "pasta"},
            "dose": {"units": 5, "kind": "rapid", "insulin_name": "Humalog"},
        },
    ).json()


def test_edit_entry_updates_both_parts_and_time(client):
    e = _meal(client)
    new_ts = _iso(40)
    r = client.put(
        "/api/entries",
        json={
            "ts": new_ts,
            "carbs_id": e["carbs"]["id"],
            "dose_id": e["dose"]["id"],
            "carbs": {"grams": 35, "description": "pasta, big plate"},
            "dose": {"units": 6, "kind": "rapid", "insulin_name": "Humalog", "note": None},
        },
    )
    assert r.status_code == 200
    body = r.json()
    assert body["carbs"]["id"] == e["carbs"]["id"] and body["carbs"]["grams"] == 35 and body["carbs"]["entry"] == "grams"
    assert body["dose"]["id"] == e["dose"]["id"] and body["dose"]["units"] == 6
    assert body["carbs"]["t"] == body["dose"]["t"] == int(datetime.fromisoformat(new_ts).timestamp())
    assert sorted(a[2] for a in _audit(client.dbname)) == ["update", "update"]


def test_edit_entry_can_drop_or_add_a_part(client):
    e = _meal(client)
    # Drop the dose, keep the carbs.
    r = client.put(
        "/api/entries",
        json={"ts": _iso(20), "carbs_id": e["carbs"]["id"], "dose_id": e["dose"]["id"], "carbs": {"raciones": 3}},
    )
    assert r.status_code == 200 and r.json()["dose"] is None
    assert client.get("/api/log").json()["doses"] == []
    # Add a dose to the carbs-only event.
    r = client.put(
        "/api/entries",
        json={
            "ts": _iso(20),
            "carbs_id": e["carbs"]["id"],
            "carbs": {"raciones": 3},
            "dose": {"units": 4, "kind": "rapid", "insulin_name": "Humalog"},
        },
    )
    assert r.status_code == 200 and r.json()["dose"]["id"] != e["dose"]["id"]
    assert len(client.get("/api/log").json()["doses"]) == 1


def test_edit_entry_is_all_or_nothing(client):
    e = _meal(client)
    other = _meal(client, minutes_ago=90)
    client.delete(f"/api/doses/{other['dose']['id']}")
    # The carbs update would succeed, but the dose id is deleted: the whole edit must roll back.
    r = client.put(
        "/api/entries",
        json={
            "ts": _iso(20),
            "carbs_id": e["carbs"]["id"],
            "dose_id": other["dose"]["id"],
            "carbs": {"grams": 99},
            "dose": {"units": 4, "kind": "rapid", "insulin_name": "Humalog"},
        },
    )
    assert r.status_code == 404
    carbs = {c["id"]: c for c in client.get("/api/log").json()["carbs"]}
    assert carbs[e["carbs"]["id"]]["grams"] == 30


def test_edit_entry_rejects_removing_everything_and_future_time(client):
    e = _meal(client)
    ids = {"carbs_id": e["carbs"]["id"], "dose_id": e["dose"]["id"]}
    assert client.put("/api/entries", json={"ts": _iso(20), **ids}).status_code == 422
    assert client.put("/api/entries", json={"ts": _iso(-10), **ids, "carbs": {"grams": 10}}).status_code == 422
    assert client.put("/api/entries", json={**ids, "carbs": {"grams": 10}}).status_code == 422  # ts required


def test_delete_entry_removes_both_in_one_go(client):
    e = _meal(client)
    r = client.delete(f"/api/entries?carbs_id={e['carbs']['id']}&dose_id={e['dose']['id']}")
    assert r.status_code == 204
    log = client.get("/api/log").json()
    assert log["carbs"] == [] and log["doses"] == []
    assert client.delete("/api/entries").status_code == 422
    # A second delete finds nothing, and nothing changes.
    assert client.delete(f"/api/entries?carbs_id={e['carbs']['id']}").status_code == 404


# --- racion size comes from Settings > Insulin --------------------------------------------


def test_without_settings_raciones_are_refused_and_grams_work(bare_client):
    c = bare_client
    assert c.get("/api/log").json()["racion_g"] is None
    r = c.post("/api/carbs", json={"raciones": 2})
    assert r.status_code == 422 and "racion size not set" in r.json()["detail"]
    assert c.post("/api/entries", json={"carbs": {"raciones": 2}}).status_code == 422
    assert c.post("/api/carbs", json={"grams": 20}).status_code == 201


def test_raciones_use_the_current_racion_size_and_keep_it(client):
    old = client.post("/api/carbs", json={"raciones": 2}).json()
    assert old["grams"] == 20 and old["racion_g"] == 10
    assert client.post("/api/settings/insulin", json={**TEST_SETTINGS, "racion_g": 12}).status_code == 201
    new = client.post("/api/carbs", json={"raciones": 2}).json()
    assert new["grams"] == 24 and new["racion_g"] == 12 and new["raciones"] == 2
    # The earlier entry keeps the size it was logged with.
    carbs = {c["id"]: c for c in client.get("/api/log").json()["carbs"]}
    assert carbs[old["id"]]["grams"] == 20 and carbs[old["id"]]["raciones"] == 2
    assert client.get("/api/log").json()["racion_g"] == 12


def test_raciones_over_1000_g_is_a_clear_422(client):
    assert client.post("/api/settings/insulin", json={**TEST_SETTINGS, "racion_g": 20, "confirm_unusual": True}).status_code == 201
    assert client.post("/api/carbs", json={"raciones": 60}).status_code == 422
