import base64
import json
from contextlib import asynccontextmanager
from typing import Annotated

from fastapi import Depends, FastAPI, Header, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, StringConstraints

from .config import BillingError, Settings
from .engine import Engine
from .store import token_hash

VERSION = "0.1.0"


class PurchaseRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    purchase_token: Annotated[str, StringConstraints(min_length=1, max_length=8192, pattern=r"^[^\s\x00-\x1f]+$")]
    product_id: Annotated[str, StringConstraints(min_length=1, max_length=200)]


class BodyTooLarge(Exception):
    pass


class BodyLimit:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        size = 0

        async def bounded_receive():
            nonlocal size
            message = await receive()
            size += len(message.get("body", b""))
            if size > 32768:
                raise BodyTooLarge()
            return message
        try:
            await self.app(scope, bounded_receive, send)
        except BodyTooLarge:
            await JSONResponse({"error": "request_too_large"}, status_code=413)(scope, receive, send)


def create_app(settings=None, engine=None, start_worker=True):
    settings = settings or Settings.from_env()

    @asynccontextmanager
    async def lifespan(application):
        backend = engine or Engine(settings)
        application.state.backend = backend
        if start_worker:
            backend.start()
        try:
            yield
        finally:
            backend.close()

    application = FastAPI(title="Bonsai Sensei Billing", version=VERSION,
                          docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)
    application.add_middleware(BodyLimit)

    @application.exception_handler(BillingError)
    async def billing_error(request, error):
        return JSONResponse({"error": error.code}, status_code=error.status)

    @application.exception_handler(RequestValidationError)
    async def validation_error(request, error):
        # Pydantic's default error includes the submitted token in `input`.
        return JSONResponse({"error": "invalid_request"}, status_code=422)

    @application.exception_handler(Exception)
    async def unexpected_error(request, error):
        return JSONResponse({"error": "internal_error"}, status_code=500)

    def backend(request: Request):
        return request.app.state.backend

    def bearer(authorization: str | None):
        if not authorization or not authorization.startswith("Bearer ") or len(authorization) > 16384:
            raise BillingError("bearer_identity_required", 401)
        return authorization[7:]

    def player(service=Depends(backend), authorization: str | None = Header(default=None)):
        service.require_ready()
        return service.identity.player(bearer(authorization))

    @application.get("/healthz")
    def health(service=Depends(backend)):
        return {"service": "bonsai-sensei-billing", "name": "Bonsai Sensei", "version": VERSION,
                "billing_enabled": not bool(service.missing())}

    @application.get("/readyz")
    def ready(service=Depends(backend)):
        missing = service.missing()
        return JSONResponse({"ready": not bool(missing), "missing_configuration": missing},
                            status_code=503 if missing else 200)

    @application.get("/v1/account")
    def account(uid=Depends(player), service=Depends(backend)):
        return {"obfuscated_account_id": service.account(uid)}

    @application.post("/v1/purchases/verify")
    def verify(body: PurchaseRequest, uid=Depends(player), service=Depends(backend)):
        return service.verify(body.purchase_token, body.product_id, uid)

    @application.get("/v1/entitlements")
    def entitlements(uid=Depends(player), service=Depends(backend)):
        return service.store.entitlements(service.account(uid))

    @application.post("/v1/notifications/google-play")
    def notification(body: dict, authorization: str | None = Header(default=None), service=Depends(backend)):
        if service.configuration or not all((settings.rtdn_audience, settings.rtdn_email, settings.rtdn_subscription)):
            raise BillingError("rtdn_not_configured", 503)
        service.identity.push(bearer(authorization), settings.rtdn_audience, settings.rtdn_email)
        try:
            if body.get("subscription") != settings.rtdn_subscription:
                raise ValueError()
            message = body["message"]
            message_id = message["messageId"]
            if not isinstance(message_id, str) or not 1 <= len(message_id) <= 200:
                raise ValueError()
            payload = json.loads(base64.b64decode(message["data"], validate=True))
            if not isinstance(payload, dict):
                raise ValueError()
            if payload.get("packageName") != settings.package:
                raise ValueError()
            variants = {"testNotification", "oneTimeProductNotification", "voidedPurchaseNotification",
                        "subscriptionNotification", "pendingRefundReviewNotification"} & payload.keys()
            if len(variants) != 1:
                raise ValueError()
            if "testNotification" in variants:
                return {"accepted": True}
            item = payload.get("oneTimeProductNotification") or payload.get("voidedPurchaseNotification")
            token = item["purchaseToken"]
            if not isinstance(token, str) or not 1 <= len(token) <= 8192:
                raise ValueError()
            event_kind = "refresh"
            if "voidedPurchaseNotification" in variants:
                if type(item.get("productType")) is not int or item["productType"] != 2:
                    raise ValueError()
                if type(item.get("refundType")) is not int or item["refundType"] not in {1, 2}:
                    raise ValueError()
                event_kind = "voided_full" if item["refundType"] == 1 else "voided_partial"
        except (ValueError, KeyError, TypeError):
            raise BillingError("invalid_notification") from None
        service.store.push(message_id, service.cipher.encrypt(token.encode()), event_kind=event_kind,
                           revoked_hash=token_hash(token) if event_kind == "voided_full" else None)
        return {"accepted": True}

    return application


app = create_app()
