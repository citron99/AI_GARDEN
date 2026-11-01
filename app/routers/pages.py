from fastapi import APIRouter
from fastapi.responses import FileResponse

from app.config import settings
from app.paths import WEB_DIR

router = APIRouter()


def _page(name: str) -> FileResponse:
    return FileResponse(WEB_DIR / name, media_type="text/html")


@router.get("/", response_class=FileResponse, include_in_schema=False)
def web_app() -> FileResponse:
    return _page("index.html")


@router.get("/privacy", response_class=FileResponse, include_in_schema=False)
def privacy_page() -> FileResponse:
    return _page("privacy.html")


@router.get("/terms", response_class=FileResponse, include_in_schema=False)
def terms_page() -> FileResponse:
    return _page("terms.html")


@router.get("/b2b-terms", response_class=FileResponse, include_in_schema=False)
def b2b_terms_page() -> FileResponse:
    return _page("b2b-terms.html")


@router.get("/api/v1/public/privacy-config")
def privacy_config() -> dict:
    """Publish non-secret controller and retention details used by legal pages."""
    ai_provider = settings.ai_provider.strip().lower()
    ai_labels = {
        "openai": "OpenAI",
        "gemini": "Google Gemini",
        "deepseek": "DeepSeek",
    }
    return {
        "controller_name": settings.data_controller_name,
        "contact_email": settings.privacy_contact_email,
        "policy_version": settings.legal_document_version,
        "effective_date": settings.legal_document_effective_date,
        "retention_days": {
            "completed_jobs": settings.completed_job_retention_days,
            "notifications": settings.notification_retention_days,
            "ai_logs": settings.ai_log_retention_days,
            "integration_events": settings.integration_event_retention_days,
        },
        "unattached_photo_retention_hours": settings.unattached_photo_retention_hours,
        "processors": {
            "ai": ai_labels.get(ai_provider),
            "ai_fallback": (
                "DeepSeek"
                if settings.deepseek_api_key and ai_provider in {"openai", "gemini"}
                else None
            ),
            "payments": "Stripe" if settings.billing_provider == "stripe" else None,
            "messaging": "Telegram" if settings.telegram_enabled else None,
            "weather": "Open-Meteo",
        },
    }
