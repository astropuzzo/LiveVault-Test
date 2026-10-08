import time
import threading
from urllib.parse import quote

from google.auth.transport.requests import AuthorizedSession, Request
from google.oauth2 import id_token, service_account

from .config import BillingError


class TimeoutRequest(Request):
    def __call__(self, *args, **kwargs):
        kwargs.setdefault("timeout", 10)
        return super().__call__(*args, **kwargs)


class Identity:
    def __init__(self, project):
        self.project = project

    def player(self, bearer):
        if not self.project:
            raise BillingError("authentication_not_configured", 503)
        try:
            claims = id_token.verify_firebase_token(bearer, TimeoutRequest(), audience=self.project)
            now = time.time()
            uid = claims.get("sub", "")
            if (claims.get("iss") != f"https://securetoken.google.com/{self.project}"
                    or not isinstance(uid, str) or not 1 <= len(uid) <= 128
                    or claims.get("auth_time", now + 1) > now
                    or claims.get("iat", now + 1) > now):
                raise ValueError()
            return uid
        except Exception:
            raise BillingError("invalid_identity", 401) from None

    def push(self, bearer, audience, email):
        try:
            claims = id_token.verify_oauth2_token(bearer, TimeoutRequest(), audience=audience)
            if (claims.get("iss") not in {"accounts.google.com", "https://accounts.google.com"}
                    or claims.get("email") != email or claims.get("email_verified") is not True):
                raise ValueError()
        except Exception:
            raise BillingError("invalid_push_identity", 401) from None


class Play:
    def __init__(self, package, credential_file):
        self.base = "https://androidpublisher.googleapis.com/androidpublisher/v3/applications/" + quote(package, safe="")
        credentials = service_account.Credentials.from_service_account_file(
            credential_file, scopes=["https://www.googleapis.com/auth/androidpublisher"])
        self.session = AuthorizedSession(credentials, refresh_timeout=20)
        self.lock = threading.Lock()

    def request(self, method, path, **kwargs):
        try:
            with self.lock:
                response = self.session.request(method, self.base + path, timeout=20, **kwargs)
            if response.status_code == 404:
                raise BillingError("purchase_not_found", 400)
            if response.status_code >= 400:
                raise BillingError("google_temporarily_unavailable", 503)
            return response.json() if response.content else {}
        except BillingError:
            raise
        except Exception:
            # Exception strings from HTTP clients can contain purchase tokens in URLs.
            raise BillingError("google_temporarily_unavailable", 503) from None

    def get(self, token):
        return self.request("GET", "/purchases/productsv2/tokens/" + quote(token, safe=""))

    def finish(self, token, product, kind):
        action = "consume" if kind == "consumable" else "acknowledge"
        path = f"/purchases/products/{quote(product, safe='')}/tokens/{quote(token, safe='')}:{action}"
        return self.request("POST", path) if kind == "consumable" else self.request("POST", path, json={})

    def voided(self, start_millis):
        page = None
        while True:
            params = {"startTime": str(start_millis), "type": 0, "includeQuantityBasedPartialRefund": "true"}
            if page:
                params["token"] = page
            body = self.request("GET", "/purchases/voidedpurchases", params=params)
            yield from body.get("voidedPurchases", [])
            page = body.get("tokenPagination", {}).get("nextPageToken")
            if not page:
                break
