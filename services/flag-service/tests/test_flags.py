from datetime import datetime
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from main import app, get_db

client = TestClient(app)

# A fake DB row that matches the SELECT column order used throughout main.py:
# (id, name, description, enabled, rollout_percentage, created_at, updated_at)
NOW = datetime(2024, 1, 15, 10, 0, 0)
FLAG_ROW = (1, "dark_mode", "Dark mode UI", True, 50, NOW, NOW)


def make_db_override(fetchone=None, fetchall=None):
    """Returns a dependency override that yields a mock connection."""
    def override():
        conn = MagicMock()
        cur = conn.cursor.return_value
        cur.fetchone.return_value = fetchone
        cur.fetchall.return_value = fetchall or []
        yield conn
    return override


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------

def test_health():
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "service": "flag-service"}


# ---------------------------------------------------------------------------
# Create
# ---------------------------------------------------------------------------

def test_create_flag_returns_201():
    app.dependency_overrides[get_db] = make_db_override(fetchone=FLAG_ROW)
    response = client.post(
        "/flags",
        json={"name": "dark_mode", "description": "Dark mode UI", "enabled": True, "rollout_percentage": 50},
    )
    app.dependency_overrides.clear()

    assert response.status_code == 201
    data = response.json()
    assert data["name"] == "dark_mode"
    assert data["enabled"] is True
    assert data["rollout_percentage"] == 50


def test_create_flag_defaults():
    row = (2, "beta_nav", "", False, 0, NOW, NOW)
    app.dependency_overrides[get_db] = make_db_override(fetchone=row)
    response = client.post("/flags", json={"name": "beta_nav"})
    app.dependency_overrides.clear()

    assert response.status_code == 201
    assert response.json()["enabled"] is False
    assert response.json()["rollout_percentage"] == 0


# ---------------------------------------------------------------------------
# List
# ---------------------------------------------------------------------------

def test_list_flags_returns_all():
    app.dependency_overrides[get_db] = make_db_override(fetchall=[FLAG_ROW])
    response = client.get("/flags")
    app.dependency_overrides.clear()

    assert response.status_code == 200
    flags = response.json()
    assert len(flags) == 1
    assert flags[0]["name"] == "dark_mode"


def test_list_flags_empty():
    app.dependency_overrides[get_db] = make_db_override(fetchall=[])
    response = client.get("/flags")
    app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json() == []


# ---------------------------------------------------------------------------
# Get one
# ---------------------------------------------------------------------------

def test_get_flag():
    app.dependency_overrides[get_db] = make_db_override(fetchone=FLAG_ROW)
    response = client.get("/flags/1")
    app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json()["id"] == 1


def test_get_flag_not_found():
    app.dependency_overrides[get_db] = make_db_override(fetchone=None)
    response = client.get("/flags/999")
    app.dependency_overrides.clear()

    assert response.status_code == 404


# ---------------------------------------------------------------------------
# Update
# ---------------------------------------------------------------------------

def test_update_flag():
    updated_row = (1, "dark_mode", "Dark mode UI", False, 50, NOW, NOW)
    app.dependency_overrides[get_db] = make_db_override(fetchone=updated_row)
    response = client.patch("/flags/1", json={"enabled": False})
    app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json()["enabled"] is False


def test_update_flag_not_found():
    app.dependency_overrides[get_db] = make_db_override(fetchone=None)
    response = client.patch("/flags/999", json={"enabled": False})
    app.dependency_overrides.clear()

    assert response.status_code == 404


def test_update_flag_no_fields_returns_400():
    app.dependency_overrides[get_db] = make_db_override()
    response = client.patch("/flags/1", json={})
    app.dependency_overrides.clear()

    assert response.status_code == 400


# ---------------------------------------------------------------------------
# Delete
# ---------------------------------------------------------------------------

def test_delete_flag():
    conn = MagicMock()
    conn.cursor.return_value.fetchone.return_value = ("dark_mode",)

    def override():
        yield conn

    app.dependency_overrides[get_db] = override
    response = client.delete("/flags/1")
    app.dependency_overrides.clear()

    assert response.status_code == 204


def test_delete_flag_not_found():
    app.dependency_overrides[get_db] = make_db_override(fetchone=None)
    response = client.delete("/flags/999")
    app.dependency_overrides.clear()

    assert response.status_code == 404
