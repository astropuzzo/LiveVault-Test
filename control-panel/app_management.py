"""Read-only, allowlisted app summaries for authenticated OpenAstro Control."""
from __future__ import annotations

import json
import os
import re
import stat
import urllib.request
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

ADMIN_KEY_FILE = Path("/etc/openastro-billing-admin.key")
BACKEND_TIMEOUT = 3
MAX_SUMMARY_BYTES = 65536
COOLIFY_PATH = "/project/8obr1exooiiaart70zgjlycw/environment/xnn2qsphju8xva9pnkljee2s/application/jxjbyszqndfoefprrxztv3fu"
# Only operators can expand this registry. Request parameters never supply URLs.
APP_REGISTRY = ({
    "id": "bonsai-sensei", "name": "Bonsai Sensei",
    "summary_url": "http://127.0.0.1:8095/internal/admin/summary",
    "links": {"coolify_local": "http://192.168.1.27:8000" + COOLIFY_PATH,
              "coolify_public": "https://openastro.tailf2871c.ts.net:10000" + COOLIFY_PATH,
              "google_play": "https://play.google.com/console/"},
},)
PURCHASE_FIELDS = ("total", "active", "pending_purchase", "processing", "revoked", "permanent", "consumable")
CONFIG_FIELDS = frozenset({"BILLING_ENABLED", "PLAY_PACKAGE_NAME", "FIREBASE_PROJECT_ID",
    "GOOGLE_APPLICATION_CREDENTIALS", "ACCOUNT_HMAC_KEY", "TOKEN_ENCRYPTION_KEY",
    "CATALOG", "RTDN_CONFIGURATION", "GOOGLE_CREDENTIALS_OR_ENCRYPTION", "REFUND_RECONCILIATION"})


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _admin_key() -> str:
    # Open without following a symlink; verify the descriptor, not an earlier stat.
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(ADMIN_KEY_FILE, flags)
    with os.fdopen(descriptor, "r", encoding="ascii") as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or stat.S_IMODE(info.st_mode) != 0o600:
            raise ValueError("Invalid key permissions")
        if hasattr(os, "geteuid") and info.st_uid != os.geteuid():
            raise ValueError("Invalid key owner")
        key = stream.read(4097).strip()
    if not re.fullmatch(r"[A-Za-z0-9_\-.~+/=]{32,4096}", key):
        raise ValueError("Invalid key")
    return key


def _open_backend(request):
    # Ignore environment proxies and refuse redirects so the bearer stays loopback.
    return urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect()).open(
        request, timeout=BACKEND_TIMEOUT)


def _count(value) -> int:
    if type(value) is not int or not 0 <= value <= 9007199254740991:
        raise ValueError("Invalid count")
    return value


def _sanitize(raw: dict) -> dict:
    if not isinstance(raw, dict) or raw.get("status") not in {"preparing", "ready"}:
        raise ValueError("Invalid summary")
    if type(raw.get("billing_enabled")) is not bool:
        raise ValueError("Invalid status")
    purchases = {key: _count(raw["purchases"][key]) for key in PURCHASE_FIELDS}
    accounts = _count(raw["users"]["purchasing_accounts"])
    daily = raw["daily_utc"]
    if not isinstance(daily, list) or len(daily) != 30:
        raise ValueError("Invalid period")
    clean_daily = []
    previous = None
    for row in daily:
        day = date.fromisoformat(row["date"])
        if row["date"] != day.isoformat() or (previous and day != previous + timedelta(days=1)):
            raise ValueError("Invalid date")
        clean_daily.append({"date": day.isoformat(), "purchases": _count(row["purchases"]),
                            "revocations": _count(row["revocations"])})
        previous = day
    catalog = []
    if not isinstance(raw.get("catalog"), list) or len(raw["catalog"]) > 100:
        raise ValueError("Invalid catalog")
    for item in raw["catalog"]:
        if not re.fullmatch(r"[a-zA-Z0-9_.-]{1,120}", item["id"]) or item["kind"] not in {"permanent", "consumable"}:
            raise ValueError("Invalid product")
        catalog.append({"id": item["id"], "kind": item["kind"]})
    missing = raw.get("missing_configuration", [])
    if not isinstance(missing, list):
        raise ValueError("Invalid configuration")
    generated = datetime.fromisoformat(raw["generated_at"].replace("Z", "+00:00"))
    if generated.utcoffset() != timedelta(0):
        raise ValueError("Expected UTC timestamp")
    version = raw.get("version", "")
    if not isinstance(version, str) or not re.fullmatch(r"[A-Za-z0-9_.+-]{1,40}", version):
        raise ValueError("Invalid version")
    return {"available": True, "status": raw["status"], "billing_enabled": raw["billing_enabled"],
            "version": version, "generated_at": generated.isoformat().replace("+00:00", "Z"),
            "source": "sqlite_purchase_ledger", "count_unit": "receipt_rows",
            "missing_configuration": sorted(set(x for x in missing if isinstance(x, str) and x in CONFIG_FIELDS)),
            "purchases": purchases, "users": {"purchasing_accounts": accounts, "registered": None, "active": None},
            "daily_utc": clean_daily, "catalog": catalog,
            "revenue": {"available": False, "reason": "google_financial_reports_not_configured"},
            "ads": {"available": False, "reason": "admob_not_configured"}}


def _unavailable() -> dict:
    return {"available": False, "status": "unavailable", "billing_enabled": None,
            "version": None, "generated_at": None, "source": "sqlite_purchase_ledger", "count_unit": "receipt_rows",
            "missing_configuration": [], "purchases": None,
            "users": {"purchasing_accounts": None, "registered": None, "active": None},
            "daily_utc": [], "catalog": [],
            "revenue": {"available": False, "reason": "google_financial_reports_not_configured"},
            "ads": {"available": False, "reason": "admob_not_configured"}}


def apps_summary() -> tuple[dict, int]:
    apps = []
    for entry in APP_REGISTRY:
        try:
            key = _admin_key()
            request = urllib.request.Request(entry["summary_url"], headers={"Authorization": "Bearer " + key,
                "Accept": "application/json"})
            with _open_backend(request) as response:
                body = response.read(MAX_SUMMARY_BYTES + 1)
            if len(body) > MAX_SUMMARY_BYTES:
                raise ValueError("Summary too large")
            summary = _sanitize(json.loads(body))
        except Exception:
            # Never return exception text, credentials, account IDs or raw upstream data.
            summary = _unavailable()
        apps.append({"id": entry["id"], "name": entry["name"], "links": dict(entry["links"]), **summary})
    available = any(app["available"] for app in apps)
    payload = {"ok": available, "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
               "period": {"days": 30, "timezone": "UTC"}, "apps": apps}
    if not available:
        payload["error"] = "Servizio app temporaneamente non disponibile."
    return payload, 200 if available else 503
