"""Формирование ответа диагностики с флагом деградации резервного провайдера."""

from app.models import Diagnosis
from app.schemas import DiagnosisRead
from app.services.diagnosis_job_service import is_fallback_response

DEGRADED_NOTICES = {
    "ru": (
        "Основной AI-провайдер временно недоступен (лимит исчерпан). Этот результат "
        "сформировал резервный текстовый провайдер без доступа к фотографиям — "
        "считайте его предварительным и при необходимости уточните у специалиста."
    ),
    "lv": (
        "Galvenais AI pakalpojums īslaicīgi nav pieejams (limits izsmelts). Šo rezultātu "
        "sagatavoja rezerves teksta pakalpojums bez piekļuves fotogrāfijām — uzskatiet "
        "to par sākotnēju un nepieciešamības gadījumā precizējiet pie speciālista."
    ),
    "en": (
        "The primary AI provider is temporarily unavailable (limit exhausted). A backup "
        "text-only provider without photo access produced this result — treat it as "
        "preliminary and confirm with a specialist if needed."
    ),
}


def diagnosis_read(diagnosis: Diagnosis, language: str | None = None) -> DiagnosisRead:
    degraded = is_fallback_response(diagnosis.model_name)
    lang = language if language in DEGRADED_NOTICES else "ru"
    return DiagnosisRead.model_validate(diagnosis).model_copy(
        update={
            "ai_degraded": degraded,
            "degraded_notice": DEGRADED_NOTICES[lang] if degraded else None,
        }
    )
