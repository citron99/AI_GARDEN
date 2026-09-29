import json
import logging
from collections.abc import Sequence
from dataclasses import replace

from app.ai.base import AIAnalysis, AIProviderError
from app.models import Plant, PlantPhoto
from app.schemas import DiagnosisCreate

logger = logging.getLogger(__name__)

# Ошибки, при которых имеет смысл переключиться на резервного провайдера
RETRYABLE_ERROR_TYPES = frozenset({
    "provider_unavailable",
    "provider_timeout",
    "provider_rate_limit",
    "provider_error",
})


class FallbackAIGateway:
    """Сначала основной провайдер; при недоступности или исчерпании лимита — резервный."""

    def __init__(self, primary, fallback):
        self._primary = primary
        self._fallback = fallback
        self.model_name = primary.model_name
        self.prompt_version = primary.prompt_version
        self.demo_mode = primary.demo_mode

    def analyze(
        self,
        plant: Plant,
        request: DiagnosisCreate,
        photos: Sequence[PlantPhoto],
        answers: Sequence[str] = (),
        safety_identifier: str | None = None,
    ) -> AIAnalysis:
        # Модель-ответчик возвращается внутри AIAnalysis, а не хранится на шлюзе:
        # шлюз — синглтон, а sync-эндпоинты исполняются в тредпуле, поэтому
        # изменяемое состояние здесь давало бы гонку между параллельными диагнозами.
        try:
            analysis = self._primary.analyze(plant, request, photos, answers, safety_identifier)
        except AIProviderError as exc:
            if exc.error_type not in RETRYABLE_ERROR_TYPES:
                exc.model_name = exc.model_name or self._primary.model_name
                raise
            logger.warning("ai_fallback_activated %s", json.dumps({
                "primary_error_type": exc.error_type,
                "fallback_model": self._fallback.model_name,
            }))
        else:
            return replace(analysis, responder_model=self._primary.model_name)
        try:
            analysis = self._fallback.analyze(plant, request, photos, answers, safety_identifier)
        except AIProviderError as exc:
            exc.model_name = exc.model_name or self._fallback.model_name
            raise
        return replace(analysis, responder_model=self._fallback.model_name)
