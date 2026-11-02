import logging
from contextlib import asynccontextmanager
from time import perf_counter
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.billing import router as billing_router
from app.config import settings, validate_runtime_settings
from app.i18n import normalize_language, translate_http_error
from app.paths import WEB_DIR
from app.routers import (
    admin,
    admin_knowledge,
    auth,
    calendar,
    care,
    diagnoses,
    gardens,
    health,
    notifications,
    pages,
    partner_admin,
    partner_cabinet,
    partner_catalog,
    photos,
    plants,
    users,
)
from app.security import secure_transport_enabled
from app.services.alert_service import dispatch_operational_alert
from app.services.metrics_service import (
    request_finished,
    request_started,
    unhandled_error,
)
from app.telegram import router as telegram_router

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_: FastAPI):
    validate_runtime_settings()
    settings.upload_dir.mkdir(parents=True, exist_ok=True)
    yield


app = FastAPI(title=settings.app_name, version="0.1.0", lifespan=lifespan)
app.add_middleware(
    TrustedHostMiddleware,
    allowed_hosts=[host.strip() for host in settings.trusted_hosts.split(",") if host.strip()],
)
app.mount("/static", StaticFiles(directory=WEB_DIR), name="static")
if settings.partner_commerce_enabled:
    app.include_router(partner_catalog.router)
    app.include_router(partner_catalog.public_router)
    app.include_router(partner_admin.router)
    app.include_router(partner_cabinet.router)
app.include_router(billing_router)
app.include_router(telegram_router)


@app.exception_handler(HTTPException)
async def localized_http_exception(request: Request, exc: HTTPException) -> JSONResponse:
    language = normalize_language(request.headers.get("accept-language"))
    detail = (
        translate_http_error(exc.detail, language, exc.status_code)
        if isinstance(exc.detail, str)
        else exc.detail
    )
    return JSONResponse(
        status_code=exc.status_code, content={"detail": detail}, headers=exc.headers
    )


_BODY_TOO_LARGE_DETAIL = "Тело запроса слишком большое"


def _request_header(scope: Scope, name: bytes) -> str | None:
    for header_name, value in scope.get("headers") or []:
        if header_name == name:
            return value.decode("latin-1")
    return None


async def _send_request_too_large(scope: Scope, receive: Receive, send: Send) -> None:
    language = normalize_language(_request_header(scope, b"accept-language"))
    detail = translate_http_error(_BODY_TOO_LARGE_DETAIL, language, 413)
    await JSONResponse(status_code=413, content={"detail": detail})(scope, receive, send)


class RequestBodyLimitMiddleware:
    """Ограничивает тело запроса по фактически прочитанным байтам.

    Проверять только Content-Length недостаточно: chunked-запрос может нести
    тело любого размера вообще без этого заголовка. BaseHTTPMiddleware не умеет
    оборачивать receive нижележащего приложения, поэтому лимит живёт в чистом
    ASGI-слое, а превышение поднимается как HTTPException — его локализует общий
    обработчик ошибок.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        limit = settings.max_request_body_mb * 1024 * 1024
        declared_length = _request_header(scope, b"content-length")
        if declared_length is not None:
            try:
                declared_size = int(declared_length)
            except ValueError:
                declared_size = limit + 1
            if declared_size > limit:
                await _send_request_too_large(scope, receive, send)
                return
        received_bytes = 0

        async def limited_receive() -> Message:
            nonlocal received_bytes
            message = await receive()
            if message["type"] == "http.request":
                received_bytes += len(message.get("body") or b"")
                if received_bytes > limit:
                    raise HTTPException(413, _BODY_TOO_LARGE_DETAIL)
            return message

        await self.app(scope, limited_receive, send)


app.add_middleware(RequestBodyLimitMiddleware)


@app.middleware("http")
async def security_headers(request, call_next):
    request_id = str(uuid4())
    request.state.request_id = request_id
    request_started()
    started_at = perf_counter()
    status_code = 500
    try:
        response = await call_next(request)
        status_code = response.status_code
    except Exception:
        route = getattr(request.scope.get("route"), "path", "unmatched")
        unhandled_error(request.method, route)
        logger.exception(
            "unhandled API error",
            extra={"request_id": request_id, "method": request.method, "path": request.url.path},
        )
        dispatch_operational_alert(
            "api_unhandled_error",
            details={"request_id": request_id, "method": request.method, "route": route},
        )
        raise
    finally:
        route = getattr(request.scope.get("route"), "path", "unmatched")
        request_finished(request.method, route, status_code, perf_counter() - started_at)
    response.headers["X-Request-ID"] = request_id
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["X-Permitted-Cross-Domain-Policies"] = "none"
    response.headers["Referrer-Policy"] = "same-origin"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    response.headers["Cross-Origin-Opener-Policy"] = "same-origin"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; script-src 'self'; style-src 'self'; "
        "img-src 'self' blob: data:; connect-src 'self'; font-src 'self'; "
        "object-src 'none'; base-uri 'self'; frame-ancestors 'none'"
    )
    if secure_transport_enabled():
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    return response


app.include_router(pages.router)
app.include_router(health.router)
app.include_router(auth.router)
app.include_router(users.router)
app.include_router(admin.router)
app.include_router(admin_knowledge.router)
app.include_router(gardens.router)
app.include_router(plants.router)
app.include_router(photos.router)
app.include_router(care.router)
app.include_router(calendar.router)
app.include_router(notifications.router)
app.include_router(diagnoses.router)
