import base64
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from service.api import create_app
from service.config import Settings
from service.engine import Engine

ADMIN_KEY = base64.b64encode(b"private-key-for-admin-only-12345678").decode()


@pytest.fixture
def backend(tmp_path):
    return Engine(Settings(database=str(tmp_path/"billing.db"), admin_key=ADMIN_KEY))


def headers(key=ADMIN_KEY):
    return {"Authorization": "Bearer " + key}


def test_disabled_empty_statistics_are_available_only_to_admin(backend):
    with TestClient(create_app(backend.settings, engine=backend, start_worker=False)) as client:
        response = client.get("/internal/admin/summary", headers=headers())
    assert response.status_code == 200 and response.headers["cache-control"] == "no-store"
    data = response.json()
    assert data["name"] == "Bonsai Sensei" and data["service"] == "bonsai-sensei-billing"
    assert data["status"] == "preparing" and "BILLING_ENABLED" in data["missing_configuration"]
    assert data["purchases"] == {"total": 0, "active": 0, "pending_purchase": 0,
                                 "processing": 0, "revoked": 0, "permanent": 0, "consumable": 0}
    assert data["users"] == {"purchasing_accounts": 0, "registered": None, "active": None}
    assert data["counted_total"] == 0 and len(data["daily_utc"]) == 30
    assert all(day["purchases"] == day["revocations"] == 0 for day in data["daily_utc"])
    assert data["generated_at"].endswith("Z") and data["source"] == "sqlite_purchase_ledger"
    assert data["count_unit"] == "receipt_rows" and data["catalog"] == []
    assert data["revenue"]["available"] is False and data["ads"]["available"] is False
    assert "amount" not in data["revenue"]


@pytest.mark.parametrize("provided", [None, "wrong-key", "", "alice"])
def test_missing_or_wrong_admin_bearer_rejected_when_billing_disabled(backend, provided):
    with TestClient(create_app(backend.settings, engine=backend, start_worker=False)) as client:
        response = client.get("/internal/admin/summary", headers=headers(provided) if provided is not None else {})
    assert response.status_code == 401
    assert response.json() == {"error": "invalid_admin_identity"}


@pytest.mark.parametrize("configured", ["", "not-base64", base64.b64encode(b"short").decode()])
def test_missing_or_weak_configured_admin_key_never_authenticates(backend, configured):
    backend.settings.admin_key = configured
    with TestClient(create_app(backend.settings, engine=backend, start_worker=False)) as client:
        assert client.get("/internal/admin/summary", headers=headers(configured)).status_code == 401
        assert client.get("/healthz").status_code == 200


def test_actual_aggregate_counts_utc_window_revocations_and_no_identifiers(backend):
    now = datetime.now(timezone.utc)
    today = now.replace(hour=0, minute=0, second=0, microsecond=0)
    rows = [
        ("alice-private-owner", "one", "permanent", "active", today, None),
        ("alice-private-owner", "two", "consumable", "pending_purchase", today-timedelta(days=1), None),
        ("bob-private-owner", "three", "permanent", "processing", today-timedelta(days=2), None),
        ("bob-private-owner", "four", "consumable", "revoked", today-timedelta(days=40), today),
        ("carol-private-owner", "five", "consumable", "active", today-timedelta(days=29), None),
    ]
    with backend.store.connection() as db:
        for owner, token, kind, state, created, revoked in rows:
            db.execute("INSERT INTO purchases VALUES(?,?,?,?,?,?,?,?,?,?)",
                       ("private-token-hash-"+token, owner, "catalog.product", kind, state,
                        b"encrypted-private-token", "private-receipt-"+token, created.timestamp(),
                        created.timestamp(), revoked.timestamp() if revoked else None))
        before = [tuple(row) for row in db.execute("SELECT * FROM purchases ORDER BY token_hash")]
    backend.settings.catalog = {"catalog.product": "permanent"}
    with TestClient(create_app(backend.settings, engine=backend, start_worker=False)) as client:
        response = client.get("/internal/admin/summary", headers=headers())
    data = response.json()
    assert data["purchases"] == {"total": 5, "active": 2, "pending_purchase": 1,
                                 "processing": 1, "revoked": 1, "permanent": 2, "consumable": 3}
    assert data["users"] == {"purchasing_accounts": 3, "registered": None, "active": None}
    assert data["counted_total"] == 5
    assert sum(day["purchases"] for day in data["daily_utc"]) == 4
    assert sum(day["revocations"] for day in data["daily_utc"]) == 1
    assert data["daily_utc"][0]["date"] == (today-timedelta(days=29)).date().isoformat()
    assert data["daily_utc"][-1]["date"] == today.date().isoformat()
    assert data["catalog"] == [{"id": "catalog.product", "kind": "permanent"}]
    for secret in (ADMIN_KEY, "alice-private-owner", "bob-private-owner", "carol-private-owner",
                   "encrypted-private-token", "private-token-hash", "private-receipt"):
        assert secret not in response.text
    with backend.store.connection() as db:
        after = [tuple(row) for row in db.execute("SELECT * FROM purchases ORDER BY token_hash")]
    assert after == before


def test_admin_secret_file_missing_does_not_prevent_service_preparation(tmp_path, monkeypatch):
    monkeypatch.setenv("ADMIN_STATS_KEY_FILE", str(tmp_path/"missing-key"))
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path/"billing.db"))
    settings = Settings.from_env()
    assert settings.admin_key == ""
    with TestClient(create_app(settings, start_worker=False)) as client:
        assert client.get("/healthz").status_code == 200
        assert client.get("/internal/admin/summary", headers=headers()).status_code == 401
