import base64
import json
import os
from dataclasses import dataclass, field
from pathlib import Path

from cryptography.fernet import Fernet


@dataclass
class Settings:
    enabled: bool = False
    package: str = ""
    firebase_project: str = ""
    credential_file: str = ""
    hmac_key: str = ""
    encryption_key: str = ""
    database: str = "/data/bonsai-sensei/billing.sqlite3"
    catalog: dict[str, str] = field(default_factory=dict)
    catalog_error: bool = False
    rtdn_audience: str = ""
    rtdn_email: str = ""
    rtdn_subscription: str = ""

    @classmethod
    def from_env(cls):
        catalog, invalid = {}, False
        try:
            entries = json.loads(Path(os.environ.get("CATALOG_PATH", "/config/catalog.json")).read_text())
            if not isinstance(entries, list):
                raise ValueError()
            for item in entries:
                if set(item) != {"id", "kind"} or item["kind"] not in {"permanent", "consumable"}:
                    raise ValueError()
                if not isinstance(item["id"], str) or not item["id"] or len(item["id"]) > 200:
                    raise ValueError()
                if item["id"] in catalog:
                    raise ValueError()
                catalog[item["id"]] = item["kind"]
        except (OSError, ValueError, TypeError, KeyError):
            invalid = True
        def secret(name):
            path = os.environ.get(name + "_FILE", "")
            if path:
                try:
                    return Path(path).read_text().strip()
                except OSError:
                    return ""
            return os.environ.get(name, "")
        return cls(enabled=os.environ.get("BILLING_ENABLED", "false").lower() == "true",
                   package=os.environ.get("PLAY_PACKAGE_NAME", ""),
                   firebase_project=os.environ.get("FIREBASE_PROJECT_ID", ""),
                   credential_file=os.environ.get("GOOGLE_APPLICATION_CREDENTIALS", ""),
                   hmac_key=secret("ACCOUNT_HMAC_KEY"),
                   encryption_key=secret("TOKEN_ENCRYPTION_KEY"),
                   database=os.environ.get("DATABASE_PATH", "/data/bonsai-sensei/billing.sqlite3"),
                   catalog=catalog, catalog_error=invalid,
                   rtdn_audience=os.environ.get("RTDN_AUDIENCE", ""),
                   rtdn_email=os.environ.get("RTDN_SERVICE_ACCOUNT_EMAIL", ""),
                   rtdn_subscription=os.environ.get("RTDN_SUBSCRIPTION", ""))

    def missing(self):
        problems = []
        if not self.enabled:
            problems.append("BILLING_ENABLED")
        for name, value in (("PLAY_PACKAGE_NAME", self.package),
                            ("FIREBASE_PROJECT_ID", self.firebase_project)):
            if not value:
                problems.append(name)
        if not self.credential_file or not Path(self.credential_file).is_file():
            problems.append("GOOGLE_APPLICATION_CREDENTIALS")
        try:
            key = base64.b64decode(self.hmac_key, validate=True)
            if len(key) < 32:
                raise ValueError()
        except (ValueError, TypeError):
            problems.append("ACCOUNT_HMAC_KEY")
        try:
            Fernet(self.encryption_key.encode())
        except (ValueError, TypeError):
            problems.append("TOKEN_ENCRYPTION_KEY")
        if self.catalog_error or not self.catalog:
            problems.append("CATALOG")
        rtdn = (self.rtdn_audience, self.rtdn_email, self.rtdn_subscription)
        if any(rtdn) and not all(rtdn):
            problems.append("RTDN_CONFIGURATION")
        return problems


class BillingError(Exception):
    def __init__(self, code: str, status: int = 400):
        self.code, self.status = code, status
        super().__init__(code)
