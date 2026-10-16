"""Shared fixtures for database tests against a real Postgres 16.

The `env` fixture creates a throwaway database, migrates it to head, and yields a TestClient for a
freshly imported app plus the database name. Tests using it are skipped unless TEST_PG_HOST is set:
  TEST_PG_HOST=127.0.0.1 TEST_PG_PORT=55432 TEST_PG_USER=th pytest
"""

import importlib
import os
import uuid
from pathlib import Path

import psycopg
import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient

API_DIR = Path(__file__).resolve().parent.parent
TEST_PASSWORD = "irrelevant-password"


def _admin(dbname="postgres"):
    return psycopg.connect(
        host=os.environ["TEST_PG_HOST"],
        port=os.environ.get("TEST_PG_PORT", "5432"),
        user=os.environ.get("TEST_PG_USER", "postgres"),
        password=os.environ.get("TEST_PG_PASSWORD", ""),
        dbname=dbname,
        autocommit=True,
    )


@pytest.fixture()
def env(tmp_path, monkeypatch):
    if not os.environ.get("TEST_PG_HOST"):
        pytest.skip("TEST_PG_HOST not set")
    name = f"th_test_{uuid.uuid4().hex[:8]}"
    with _admin() as c:
        c.execute(f'CREATE DATABASE "{name}"')
    monkeypatch.setenv("POSTGRES_HOST", os.environ["TEST_PG_HOST"])
    monkeypatch.setenv("POSTGRES_PORT", os.environ.get("TEST_PG_PORT", "5432"))
    monkeypatch.setenv("POSTGRES_USER", os.environ.get("TEST_PG_USER", "postgres"))
    monkeypatch.setenv("POSTGRES_PASSWORD", os.environ.get("TEST_PG_PASSWORD", ""))
    monkeypatch.setenv("POSTGRES_DB", name)
    cfg = Config(str(API_DIR / "alembic.ini"))
    cfg.set_main_option("script_location", str(API_DIR / "migrations"))
    command.upgrade(cfg, "head")

    from app import auth

    hash_file = tmp_path / "hash"
    hash_file.write_text(auth.hash_password(TEST_PASSWORD) + "\n")
    monkeypatch.setenv("APP_PASSWORD_HASH_FILE", str(hash_file))
    monkeypatch.setenv("SESSION_SECRET", "x" * 64)
    importlib.reload(auth)
    from app import main

    importlib.reload(main)
    yield TestClient(main.app, base_url="https://testserver"), name
    with _admin() as c:
        c.execute(f'DROP DATABASE "{name}" WITH (FORCE)')
