import base64
import copy
import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from service.api import create_app
from service.config import BillingError, Settings
from service.engine import Engine
from service.store import token_hash


class FakePlay:
    def __init__(self):
        self.purchases = {}
        self.finish_calls = []
        self.voids = []
        self.lose_response = False
        self.fail_finish = False
        self.lock = threading.Lock()

    def get(self, token):
        with self.lock:
            return copy.deepcopy(self.purchases[token])

    def finish(self, token, product, kind):
        with self.lock:
            self.finish_calls.append((token, product, kind))
            if self.fail_finish:
                raise BillingError("google_temporarily_unavailable", 503)
            self.purchases[token]["acknowledgementState"] = "ACKNOWLEDGEMENT_STATE_ACKNOWLEDGED"
            if kind == "consumable":
                self.purchases[token]["productLineItem"][0]["productOfferDetails"]["consumptionState"] = "CONSUMPTION_STATE_CONSUMED"
            if self.lose_response:
                self.lose_response = False
                raise BillingError("google_temporarily_unavailable", 503)

    def voided(self, start_millis):
        yield from self.voids


class FakeIdentity:
    def player(self, bearer):
        if bearer not in {"alice", "bob"}:
            raise BillingError("invalid_identity", 401)
        return bearer

    def push(self, bearer, audience, email):
        if bearer != "signed-push":
            raise BillingError("invalid_push_identity", 401)


@pytest.fixture
def engine(tmp_path):
    credential = tmp_path / "google.json"
    credential.write_text("{}")
    settings = Settings(enabled=True, package="com.test.bonsaisensei", firebase_project="test-project",
                        credential_file=str(credential), hmac_key=base64.b64encode(b"h"*32).decode(),
                        encryption_key=Fernet.generate_key().decode(), database=str(tmp_path/"billing.db"),
                        catalog={"permanent": "permanent", "consumable": "consumable"},
                        rtdn_audience="https://billing.test/v1/notifications/google-play",
                        rtdn_email="pubsub@test-project.iam.gserviceaccount.com",
                        rtdn_subscription="projects/test-project/subscriptions/billing")
    backend = Engine(settings, play=FakePlay(), identity=FakeIdentity())
    backend.reconcile_voided()
    return backend


def purchase(engine, token="purchase-token", product="permanent", state="PURCHASED", owner="alice"):
    engine.play.purchases[token] = {
        "productLineItem": [{"productId": product, "productOfferDetails": {
            "quantity": 1, "refundableQuantity": 1,
            "consumptionState": "CONSUMPTION_STATE_YET_TO_BE_CONSUMED"}}],
        "purchaseStateContext": {"purchaseState": state},
        "obfuscatedExternalAccountId": engine.account(owner),
        "acknowledgementState": "ACKNOWLEDGEMENT_STATE_PENDING",
    }
    return token


def make_due(engine):
    with engine.store.connection() as db:
        db.execute("UPDATE outbox SET next_attempt=0,lease_until=0")


def test_unconfigured_is_healthy_but_fails_closed(tmp_path):
    settings = Settings(database=str(tmp_path/"billing.db"))
    with TestClient(create_app(settings, start_worker=False)) as client:
        assert client.get("/healthz").status_code == 200
        assert client.get("/healthz").json()["billing_enabled"] is False
        assert client.get("/readyz").status_code == 503
        assert client.get("/v1/account", headers={"Authorization": "Bearer alice"}).status_code == 503
        assert client.post("/v1/purchases/verify", json={"purchase_token": "x", "product_id": "x"}).status_code == 503
        assert client.get("/docs").status_code == 404


def test_explicit_disabled_flag_even_with_other_settings(engine):
    engine.settings.enabled = False
    disabled = Engine(engine.settings, play=engine.play, identity=engine.identity)
    assert "BILLING_ENABLED" in disabled.missing()
    with pytest.raises(BillingError):
        disabled.verify("token", "permanent", "alice")


def test_requires_first_refund_sync_and_stale_sync_blocks(engine):
    with engine.store.connection() as db:
        db.execute("DELETE FROM metadata")
    assert "REFUND_RECONCILIATION" in engine.missing()
    engine.reconcile_voided()
    assert not engine.missing()
    engine.store.meta("refund_sync", time.time()-901)
    assert engine.missing() == ["REFUND_RECONCILIATION"]


def test_pending_never_grants_and_worker_completes_without_app(engine):
    token = purchase(engine, state="PENDING")
    result = engine.verify(token, "permanent", "alice")
    assert result["state"] == "pending_purchase" and result["granted"] is False
    assert engine.play.finish_calls == []
    assert engine.store.entitlements(engine.account("alice"))["permanent_products"] == []
    engine.play.purchases[token]["purchaseStateContext"]["purchaseState"] = "PURCHASED"
    make_due(engine)
    engine.tick()
    assert engine.store.entitlements(engine.account("alice"))["permanent_products"] == ["permanent"]


def test_repeat_is_same_receipt_and_one_finalization(engine):
    token = purchase(engine)
    first = engine.verify(token, "permanent", "alice")
    second = engine.verify(token, "permanent", "alice")
    assert first == second
    assert first["granted"] is True
    assert len(engine.play.finish_calls) == 1


def test_concurrent_consumable_requests_grant_once(engine):
    token = purchase(engine, product="consumable")
    with ThreadPoolExecutor(max_workers=8) as pool:
        replies = list(pool.map(lambda _: engine.verify(token, "consumable", "alice"), range(16)))
    assert len({reply["receipt_id"] for reply in replies}) == 1
    assert len(engine.play.finish_calls) == 1
    receipts = engine.store.entitlements(engine.account("alice"))["consumable_receipts"]
    assert len(receipts) == 1 and receipts[0]["state"] == "active"


def test_owner_mismatch_and_client_cannot_supply_user_id(engine):
    token = purchase(engine)
    with pytest.raises(BillingError, match="purchase_account_mismatch"):
        engine.verify(token, "permanent", "bob")
    engine.verify(token, "permanent", "alice")
    with pytest.raises(BillingError, match="purchase_owner_or_product_mismatch"):
        engine.verify(token, "permanent", "bob")
    with TestClient(create_app(engine.settings, engine=engine, start_worker=False)) as client:
        result = client.post("/v1/purchases/verify", headers={"Authorization": "Bearer alice"},
                             json={"purchase_token": token, "product_id": "permanent", "user_id": "bob"})
        assert result.status_code == 422
        assert token not in result.text


@pytest.mark.parametrize("kind", ["permanent", "consumable"])
def test_lost_finalization_response_recovers_durably_without_second_grant(engine, kind):
    token = purchase(engine, product=kind)
    engine.play.lose_response = True
    first = engine.verify(token, kind, "alice")
    assert first["granted"] is False
    # Simulate a restart using the same persisted SQLite and encryption key.
    restarted = Engine(engine.settings, play=engine.play, identity=engine.identity)
    make_due(restarted)
    restarted.tick()
    final = restarted.verify(token, kind, "alice")
    assert final["receipt_id"] == first["receipt_id"] and final["granted"] is True
    assert len(engine.play.finish_calls) == 1


def test_unknown_consumed_token_rejected(engine):
    token = purchase(engine, product="consumable")
    engine.play.purchases[token]["productLineItem"][0]["productOfferDetails"]["consumptionState"] = "CONSUMPTION_STATE_CONSUMED"
    with pytest.raises(BillingError, match="unknown_consumed_purchase"):
        engine.verify(token, "consumable", "alice")
    assert engine.store.find(token_hash(token)) is None


def test_failed_ack_never_grants_until_success(engine):
    token = purchase(engine)
    engine.play.fail_finish = True
    assert engine.verify(token, "permanent", "alice")["granted"] is False
    assert engine.store.entitlements(engine.account("alice"))["permanent_products"] == []
    engine.play.fail_finish = False
    make_due(engine)
    engine.tick()
    assert engine.verify(token, "permanent", "alice")["granted"] is True


@pytest.mark.parametrize("change", ["quantity", "multi", "rental", "preorder", "wrong_product", "missing_owner"])
def test_unsupported_purchases_fail_closed(engine, change):
    token = purchase(engine)
    body = engine.play.purchases[token]
    offer = body["productLineItem"][0]["productOfferDetails"]
    if change == "quantity":
        offer["quantity"] = 2
    elif change == "multi":
        body["productLineItem"].append(copy.deepcopy(body["productLineItem"][0]))
    elif change == "rental":
        offer["rentOfferDetails"] = {}
    elif change == "preorder":
        offer["preorderOfferDetails"] = {}
    elif change == "wrong_product":
        body["productLineItem"][0]["productId"] = "other"
    else:
        del body["obfuscatedExternalAccountId"]
    with pytest.raises(BillingError):
        engine.verify(token, "permanent", "alice")
    assert engine.store.find(token_hash(token)) is None


def test_refund_revokes_permanently_even_if_later_google_response_is_old(engine):
    token = purchase(engine)
    engine.verify(token, "permanent", "alice")
    engine.play.voids = [{"purchaseToken": token}]
    engine.reconcile_voided()
    assert engine.store.entitlements(engine.account("alice"))["permanent_products"] == []
    with pytest.raises(BillingError, match="purchase_revoked"):
        engine.verify(token, "permanent", "alice")
    engine.store.activate(token_hash(token))
    assert engine.store.find(token_hash(token))["state"] == "revoked"


def test_cancellation_during_finalization_cannot_resurrect(engine):
    token = purchase(engine)
    original_finish = engine.play.finish

    def finish_and_refund(*args):
        original_finish(*args)
        engine.store.revoke(token_hash(token))
    engine.play.finish = finish_and_refund
    result = engine.verify(token, "permanent", "alice")
    assert result["granted"] is False and result["state"] == "revoked"


def test_unknown_refund_tombstone_blocks_future_import(engine):
    token = purchase(engine)
    engine.play.voids = [{"purchaseToken": token}]
    engine.reconcile_voided()
    with pytest.raises(BillingError, match="purchase_revoked"):
        engine.verify(token, "permanent", "alice")


def test_token_encrypted_not_stored_in_plaintext(engine):
    token = purchase(engine, token="secret-purchase-token-never-log")
    engine.play.fail_finish = True
    engine.verify(token, "permanent", "alice")
    row = engine.store.find(token_hash(token))
    assert token.encode() not in row["token_cipher"]
    assert engine.cipher.decrypt(row["token_cipher"]).decode() == token
    assert row["owner"] != "alice"


def test_authentication_is_required_and_entitlements_are_owner_scoped(engine):
    token = purchase(engine)
    engine.verify(token, "permanent", "alice")
    with TestClient(create_app(engine.settings, engine=engine, start_worker=False)) as client:
        assert client.get("/v1/entitlements").status_code == 401
        assert client.get("/v1/entitlements", headers={"Authorization": "Bearer fake"}).status_code == 401
        assert client.get("/v1/entitlements", headers={"Authorization": "Bearer bob"}).json()["permanent_products"] == []


def test_rtdn_authenticated_durable_deduplicated_and_no_owner_guess(engine):
    token = purchase(engine, state="CANCELLED")
    payload = {"packageName": engine.settings.package, "oneTimeProductNotification": {"purchaseToken": token}}
    body = {"subscription": engine.settings.rtdn_subscription, "message": {
        "messageId": "notification-1", "data": base64.b64encode(json.dumps(payload).encode()).decode()}}
    with TestClient(create_app(engine.settings, engine=engine, start_worker=False)) as client:
        assert client.post("/v1/notifications/google-play", json=body).status_code == 401
        headers = {"Authorization": "Bearer signed-push"}
        assert client.post("/v1/notifications/google-play", json=body, headers=headers).status_code == 200
        assert client.post("/v1/notifications/google-play", json=body, headers=headers).status_code == 200
    assert len(engine.store.push_jobs()) == 1
    engine.tick()
    assert engine.store.revoked(token_hash(token))
    assert engine.store.find(token_hash(token)) is None
    assert engine.store.push_jobs() == []


def test_checkpoint_beyond_google_retention_does_not_silently_skip(engine):
    engine.store.meta("refund_sync", time.time()-30*86400)
    with pytest.raises(BillingError, match="refund_checkpoint_too_old"):
        engine.reconcile_voided()
    assert "REFUND_RECONCILIATION" in engine.missing()


@pytest.mark.parametrize("value", [None, False, True, "1"])
def test_unknown_refundable_quantity_never_grants(engine, value):
    token = purchase(engine)
    offer = engine.play.purchases[token]["productLineItem"][0]["productOfferDetails"]
    if value is None:
        del offer["refundableQuantity"]
    else:
        offer["refundableQuantity"] = value
    with pytest.raises(BillingError, match="unknown_refundable_quantity"):
        engine.verify(token, "permanent", "alice")
    assert engine.play.finish_calls == []


def test_zero_refundable_quantity_is_revoked_before_grant(engine):
    token = purchase(engine)
    engine.play.purchases[token]["productLineItem"][0]["productOfferDetails"]["refundableQuantity"] = 0
    with pytest.raises(BillingError, match="purchase_revoked"):
        engine.verify(token, "permanent", "alice")
    assert engine.store.revoked(token_hash(token))
    assert engine.play.finish_calls == []


def test_authenticated_full_void_tombstones_immediately_and_records_event(engine):
    token = purchase(engine)
    engine.verify(token, "permanent", "alice")
    payload = {"packageName": engine.settings.package, "voidedPurchaseNotification": {
        "purchaseToken": token, "productType": 2, "refundType": 1}}
    body = {"subscription": engine.settings.rtdn_subscription, "message": {
        "messageId": "full-void", "data": base64.b64encode(json.dumps(payload).encode()).decode()}}
    with TestClient(create_app(engine.settings, engine=engine, start_worker=False)) as client:
        assert client.post("/v1/notifications/google-play", json=body,
                           headers={"Authorization": "Bearer signed-push"}).status_code == 200
    assert engine.store.find(token_hash(token))["state"] == "revoked"
    assert engine.store.push_jobs()[0]["event_kind"] == "voided_full"
    # A stale PURCHASED Google response cannot revive the receipt during reconciliation.
    engine.tick()
    assert engine.store.find(token_hash(token))["state"] == "revoked"


def test_rtdn_wrong_subscription_and_package_rejected(engine):
    payload = {"packageName": "com.other.app", "oneTimeProductNotification": {"purchaseToken": "token"}}
    body = {"subscription": engine.settings.rtdn_subscription, "message": {
        "messageId": "wrong-app", "data": base64.b64encode(json.dumps(payload).encode()).decode()}}
    with TestClient(create_app(engine.settings, engine=engine, start_worker=False)) as client:
        headers = {"Authorization": "Bearer signed-push"}
        assert client.post("/v1/notifications/google-play", json=body, headers=headers).status_code == 400
        body["subscription"] = "projects/other/subscriptions/other"
        assert client.post("/v1/notifications/google-play", json=body, headers=headers).status_code == 400
    assert engine.store.push_jobs() == []
