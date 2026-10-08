import base64
import hashlib
import hmac
import threading
import time

from cryptography.fernet import Fernet

from .config import BillingError
from .google import Identity, Play
from .store import Store, token_hash


class Engine:
    def __init__(self, settings, play=None, identity=None):
        self.settings = settings
        self.store = Store(settings.database)
        self.identity = identity or Identity(settings.firebase_project)
        self.play = play
        self.configuration = settings.missing()
        self.cipher = None
        if not self.configuration:
            try:
                self.cipher = Fernet(settings.encryption_key.encode())
                self.play = play or Play(settings.package, settings.credential_file)
            except Exception:
                self.configuration.append("GOOGLE_CREDENTIALS_OR_ENCRYPTION")
        self.stop = threading.Event()
        self.worker = None
        self.tick_lock = threading.Lock()

    def missing(self):
        missing = list(self.configuration)
        if not missing:
            last = float(self.store.meta("refund_sync") or 0)
            if time.time() - last > 900:
                missing.append("REFUND_RECONCILIATION")
        return missing

    def require_ready(self):
        if self.missing():
            raise BillingError("billing_not_configured_or_ready", 503)

    def account(self, uid):
        self.require_ready()
        return hmac.new(base64.b64decode(self.settings.hmac_key), uid.encode(), hashlib.sha256).hexdigest()

    def validate(self, purchase, product, owner):
        lines = purchase.get("productLineItem", [])
        if len(lines) != 1 or lines[0].get("productId") != product:
            raise BillingError("unsupported_purchase_line_items")
        details = lines[0].get("productOfferDetails", {})
        quantity = details.get("quantity")
        if (type(quantity) is not int or quantity != 1 or "rentOfferDetails" in details
                or "preorderOfferDetails" in details):
            raise BillingError("unsupported_purchase_option")
        if not hmac.compare_digest(str(purchase.get("obfuscatedExternalAccountId", "")), owner):
            raise BillingError("purchase_account_mismatch", 403)
        state = purchase.get("purchaseStateContext", {}).get("purchaseState")
        if state not in {"PURCHASED", "PENDING", "CANCELLED"}:
            raise BillingError("unknown_purchase_state")
        if state == "PURCHASED":
            refundable = details.get("refundableQuantity")
            if type(refundable) is not int or refundable not in {0, 1}:
                raise BillingError("unknown_refundable_quantity")
            if refundable == 0:
                return "CANCELLED", details
        return state, details

    def verify(self, token, product, uid):
        self.require_ready()
        if product not in self.settings.catalog:
            raise BillingError("product_not_allowed")
        owner, hashed = self.account(uid), token_hash(token)
        old = self.store.find(hashed)
        if old and (old["owner"] != owner or old["product"] != product):
            raise BillingError("purchase_owner_or_product_mismatch", 409)
        if self.store.revoked(hashed):
            raise BillingError("purchase_revoked", 409)
        purchase = self.play.get(token)
        state, details = self.validate(purchase, product, owner)
        if state == "CANCELLED":
            self.store.revoke(hashed)
            raise BillingError("purchase_revoked", 409)
        consumed = details.get("consumptionState") == "CONSUMPTION_STATE_CONSUMED"
        # Another request may have durably registered/finalized while Google was read.
        old = self.store.find(hashed)
        if old and (old["owner"] != owner or old["product"] != product):
            raise BillingError("purchase_owner_or_product_mismatch", 409)
        if consumed and not old:
            # A previously consumed token has no safe recoverable grant without this ledger.
            raise BillingError("unknown_consumed_purchase", 409)
        if consumed and self.settings.catalog[product] == "permanent":
            raise BillingError("permanent_product_consumed", 409)
        row = self.store.register(hashed, owner, product, self.settings.catalog[product],
                                  self.cipher.encrypt(token.encode()),
                                  "pending_purchase" if state == "PENDING" else "processing")
        if row["state"] != "active":
            self.process_one(hashed)
        row = self.store.find(hashed)
        return {"receipt_id": row["receipt_id"], "product_id": product,
                "state": row["state"], "granted": row["state"] == "active"}

    def process_one(self, hashed=None):
        row = self.store.claim(hashed)
        if not row:
            return False
        hashed = row["token_hash"]
        try:
            token = self.cipher.decrypt(row["token_cipher"]).decode()
            purchase = self.play.get(token)
            state, details = self.validate(purchase, row["product"], row["owner"])
            if state == "CANCELLED":
                self.store.revoke(hashed)
                return True
            if state == "PENDING":
                self.store.retry(hashed, row["attempts"])
                return True
            consumption = details.get("consumptionState")
            if consumption not in {"CONSUMPTION_STATE_YET_TO_BE_CONSUMED", "CONSUMPTION_STATE_CONSUMED"}:
                raise BillingError("unknown_consumption_state")
            acknowledged = purchase.get("acknowledgementState") == "ACKNOWLEDGEMENT_STATE_ACKNOWLEDGED"
            if row["kind"] == "consumable" and consumption == "CONSUMPTION_STATE_CONSUMED":
                if not row["finalize_attempted"]:
                    raise BillingError("consumed_without_server_finalization")
            elif row["kind"] == "permanent" and consumption == "CONSUMPTION_STATE_CONSUMED":
                raise BillingError("permanent_product_consumed")
            elif row["kind"] == "consumable" or not acknowledged:
                # This intent is durable before Google is contacted. A lost HTTP response
                # is recovered by querying Google's state, never by issuing a second grant.
                self.store.attempted(hashed)
                self.play.finish(token, row["product"], row["kind"])
            self.store.activate(hashed)
        except Exception:
            self.store.retry(hashed, row["attempts"])
        return True

    def reconcile_voided(self):
        now = time.time()
        last = float(self.store.meta("refund_sync") or 0)
        # Google's list covers only the previous 30 days. An older established ledger
        # needs a manual audit; silently skipping a long outage could restore a refund.
        if last and now-last > 29*86400:
            raise BillingError("refund_checkpoint_too_old", 503)
        start = max(now-30*86400+60, last-300) if last else now-30*86400+60
        for purchase in self.play.voided(int(start*1000)):
            token = purchase.get("purchaseToken")
            if token:
                self.store.revoke(token_hash(token))
        self.store.meta("refund_sync", now)

    def refresh(self, token):
        hashed = token_hash(token)
        purchase = self.play.get(token)
        state = purchase.get("purchaseStateContext", {}).get("purchaseState")
        lines = purchase.get("productLineItem", [])
        refund = any(item.get("productOfferDetails", {}).get("refundableQuantity") == 0 for item in lines)
        if state == "CANCELLED" or refund:
            self.store.revoke(hashed)
        else:
            row = self.store.find(hashed)
            if row:
                checked, _ = self.validate(purchase, row["product"], row["owner"])
                if checked == "CANCELLED":
                    self.store.revoke(hashed)
                elif checked == "PURCHASED":
                    self.store.refreshed(hashed)
            # A push for an unknown token never guesses an owner or creates a grant.

    def tick(self):
        if self.configuration or not self.tick_lock.acquire(blocking=False):
            return
        try:
            last = float(self.store.meta("refund_sync") or 0)
            if time.time()-last >= 300:
                try:
                    self.reconcile_voided()
                except Exception:
                    pass
            for row in self.store.push_jobs():
                try:
                    self.refresh(self.cipher.decrypt(row["token_cipher"]).decode())
                    self.store.push_done(row["message_id"])
                except Exception:
                    self.store.push_retry(row["message_id"], row["attempts"])
            if not self.missing():
                for _ in range(20):
                    if not self.process_one():
                        break
            for row in self.store.due_refresh():
                try:
                    self.refresh(self.cipher.decrypt(row["token_cipher"]).decode())
                except Exception:
                    pass
        finally:
            self.tick_lock.release()

    def start(self):
        def run():
            while not self.stop.is_set():
                try:
                    self.tick()
                except Exception:
                    pass  # No purchase tokens or HTTP URLs in logs.
                self.stop.wait(5)
        self.worker = threading.Thread(target=run, daemon=True, name="billing-outbox")
        self.worker.start()

    def close(self):
        self.stop.set()
        if self.worker:
            self.worker.join(timeout=25)
