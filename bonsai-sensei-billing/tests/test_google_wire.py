import threading

import pytest

from service.config import BillingError
from service.google import Identity, Play


class Response:
    def __init__(self, body=None, status=200):
        self.body = body or {}
        self.status_code = status
        self.content = b"{}" if body else b""

    def json(self):
        return self.body


class RecordingSession:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        return self.responses.pop(0)


def play(*responses):
    client = Play.__new__(Play)
    client.base = "https://androidpublisher.googleapis.com/androidpublisher/v3/applications/com.test.bonsaisensei"
    client.session = RecordingSession(*responses)
    client.lock = threading.Lock()
    return client


def test_google_v2_get_exact_path_and_empty_body():
    client = play(Response({"purchaseStateContext": {"purchaseState": "PURCHASED"}}))
    result = client.get("token/with?query")
    assert result["purchaseStateContext"]["purchaseState"] == "PURCHASED"
    method, url, kwargs = client.session.calls[0]
    assert method == "GET"
    assert url.endswith("/purchases/productsv2/tokens/token%2Fwith%3Fquery")
    assert set(kwargs) == {"timeout"}


@pytest.mark.parametrize("kind,action,body", [
    ("consumable", "consume", None), ("permanent", "acknowledge", {})])
def test_google_finalization_wire_shape(kind, action, body):
    client = play(Response())
    client.finish("token", "real.product", kind)
    method, url, kwargs = client.session.calls[0]
    assert method == "POST"
    assert url.endswith(f"/purchases/products/real.product/tokens/token:{action}")
    if body is None:
        assert "json" not in kwargs and "data" not in kwargs
    else:
        assert kwargs["json"] == body


def test_google_voided_pagination_is_fully_read():
    client = play(Response({"voidedPurchases": [{"purchaseToken": "one"}],
                            "tokenPagination": {"nextPageToken": "second"}}),
                  Response({"voidedPurchases": [{"purchaseToken": "two"}]}))
    assert list(client.voided(1234567)) == [{"purchaseToken": "one"}, {"purchaseToken": "two"}]
    assert client.session.calls[0][2]["params"]["startTime"] == "1234567"
    assert client.session.calls[1][2]["params"]["token"] == "second"
    assert client.session.calls[1][2]["params"]["includeQuantityBasedPartialRefund"] == "true"


def test_google_http_errors_never_expose_token_or_url():
    client = play(Response(status=503))
    with pytest.raises(BillingError) as failure:
        client.get("secret-purchase-token")
    assert "secret" not in str(failure.value) and "http" not in str(failure.value)


@pytest.mark.parametrize("issuer", ["https://securetoken.google.com/other-project", "https://accounts.google.com"])
def test_firebase_wrong_issuer_cannot_be_player(monkeypatch, issuer):
    monkeypatch.setattr("service.google.id_token.verify_firebase_token",
                        lambda *args, **kwargs: {"sub": "alice", "iss": issuer, "iat": 1, "auth_time": 1})
    with pytest.raises(BillingError, match="invalid_identity"):
        Identity("correct-project").player("signed-token")


@pytest.mark.parametrize("changed", ["email", "email_verified", "iss"])
def test_pubsub_wrong_service_identity_rejected(monkeypatch, changed):
    claims = {"iss": "https://accounts.google.com", "email": "push@example.test", "email_verified": True}
    claims[changed] = False if changed == "email_verified" else "unexpected"
    monkeypatch.setattr("service.google.id_token.verify_oauth2_token", lambda *args, **kwargs: claims)
    with pytest.raises(BillingError, match="invalid_push_identity"):
        Identity("project").push("signed", "exact-audience", "push@example.test")
