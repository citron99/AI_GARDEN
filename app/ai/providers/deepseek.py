import json
import logging
import time
from collections.abc import Sequence
from uuid import uuid4

import httpx

from app.ai.base import AIAnalysis, AIProviderError, AIUsage
from app.ai.providers.openai_multimodal import PROMPT_VERSION, SYSTEM_PROMPT
from app.config import settings
from app.models import Plant, PlantPhoto
from app.schemas import DiagnosisCreate, DiagnosisResult
from app.services.knowledge_service import retrieve_knowledge

logger = logging.getLogger(__name__)

TEXT_ONLY_NOTICE = """
ВНИМАНИЕ: этот запрос обрабатывает текстовый AI-провайдер без доступа к фотографиям.
Проанализируй ситуацию по описанию симптомов, виду растения и контексту. Если для
вывода критически не хватает визуальной информации, честно оцени это в cannot_analyze_reason
и запроси уточняющие сведения или качественную фотографию.
При analysis_outcome=cannot_analyze: analysis_status="cannot_analyze", possible_causes пустой,
safe_actions всё равно заполни (например, как улучшить фото или что наблюдать).
"""

SCHEMA_INSTRUCTION = "\nОтветь строго одним JSON-объектом (json), соответствующим этой схеме диагностического результата:\n"


class DeepSeekGateway:
    """Текстовый провайдер через OpenAI-совместимый chat/completions API.

    Используется как резервный (fallback), когда мультимодальный провайдер
    недоступен или превысил лимит: фотографии в запросе игнорируются, анализ
    опирается на текстовое описание симптомов.
    """

    prompt_version = PROMPT_VERSION
    demo_mode = False

    def __init__(self, api_key: str, model: str, api_base: str, timeout_seconds: float = 60, verify_ssl: bool = True):
        self.model_name = model
        self._api_key = api_key
        self._api_base = api_base.rstrip("/")
        self._client = httpx.Client(timeout=timeout_seconds, verify=verify_ssl)

    @staticmethod
    def _classify_status(status_code: int, request_id: str, response_ms: int) -> AIProviderError:
        if status_code == 429:
            return AIProviderError("Превышен rate limit AI-провайдера", http_status=429, error_type="provider_rate_limit", request_id=request_id, response_ms=response_ms)
        if status_code in (400, 415):
            return AIProviderError("AI-провайдер отклонил запрос", http_status=422, error_type="invalid_request", request_id=request_id, response_ms=response_ms)
        if status_code in (401, 403):
            return AIProviderError("Ошибка конфигурации AI-провайдера", http_status=503, error_type="provider_auth", request_id=request_id, response_ms=response_ms)
        if status_code == 422:
            return AIProviderError("AI-провайдер не смог обработать запрос", http_status=422, error_type="provider_error", request_id=request_id, response_ms=response_ms)
        if status_code >= 500:
            return AIProviderError("AI-провайдер недоступен", http_status=503, error_type="provider_unavailable", request_id=request_id, response_ms=response_ms)
        return AIProviderError("Ошибка AI-провайдера", http_status=503, error_type="provider_error", request_id=request_id, response_ms=response_ms)

    @staticmethod
    def _validate_response(data: dict | None, request_id: str) -> AIProviderError | None:
        """Проверяет сырой ответ провайдера; возвращает ошибку или None, если ответ валиден."""
        if not isinstance(data, dict):
            return AIProviderError("AI-провайдер вернул некорректную структуру результата", request_id=request_id)
        choices = data.get("choices") or []
        if not choices or choices[0].get("finish_reason") not in {"stop", "length"}:
            return AIProviderError("AI-провайдер не вернул структурированный результат", request_id=request_id)
        if not str((choices[0].get("message") or {}).get("content") or "").strip():
            return AIProviderError("AI-провайдер вернул пустой результат", request_id=request_id)
        return None

    def _parse_result(self, data: dict, request_id: str, response_ms: int) -> DiagnosisResult:
        choices = data.get("choices") or []
        text = str((choices[0].get("message") or {}).get("content") or "")
        try:
            return DiagnosisResult.model_validate_json(text)
        except ValueError as exc:
            raise AIProviderError("AI-провайдер вернул некорректную структуру результата", request_id=request_id, response_ms=response_ms) from exc

    def analyze(
        self,
        plant: Plant,
        request: DiagnosisCreate,
        photos: Sequence[PlantPhoto],
        answers: Sequence[str] = (),
        safety_identifier: str | None = None,
    ) -> AIAnalysis:
        local_request_id = f"ai_{uuid4().hex}"
        started = time.perf_counter()

        query = " ".join((request.symptoms, plant.species or "", *answers))
        knowledge = retrieve_knowledge(query, region=plant.region)
        context = {
            "plant_name": plant.name,
            "species": plant.species,
            "growing_place": plant.growing_place,
            "region": plant.region,
            "symptoms": request.symptoms,
            "damaged_part": request.damaged_part.value,
            "answers": list(answers),
            "photos_attached": len(photos),
            "photos_available_to_model": False,
            "trusted_sources": [item.model_dump(mode="json") for item in knowledge],
        }
        schema_hint = SCHEMA_INSTRUCTION + json.dumps(DiagnosisResult.model_json_schema(), ensure_ascii=False)
        payload = {
            "model": self.model_name,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT + TEXT_ONLY_NOTICE + schema_hint},
                {"role": "user", "content": "Контекст обращения:\n" + json.dumps(context, ensure_ascii=False)},
            ],
            "response_format": {"type": "json_object"},
            "max_tokens": settings.deepseek_max_output_tokens,
        }
        url = f"{self._api_base}/chat/completions"
        max_attempts = 3
        last_failure: AIProviderError | None = None
        data: dict | None = None
        response_ms = 0
        for attempt in range(1, max_attempts + 1):
            try:
                response = self._client.post(
                    url,
                    headers={"Authorization": f"Bearer {self._api_key}"},
                    json=payload,
                )
                response_ms = round((time.perf_counter() - started) * 1000)
            except httpx.TimeoutException:
                response_ms = round((time.perf_counter() - started) * 1000)
                last_failure = AIProviderError("Тайм-аут AI-провайдера", http_status=504, error_type="provider_timeout", request_id=local_request_id, response_ms=response_ms)
                break
            except httpx.TransportError:
                if attempt < max_attempts:
                    logger.warning("ai_request_retry %s", json.dumps({"request_id": local_request_id, "attempt": attempt, "reason": "transport_error"}))
                    time.sleep(0.5 * attempt)
                    continue
                response_ms = round((time.perf_counter() - started) * 1000)
                last_failure = AIProviderError("AI-провайдер недоступен", http_status=503, error_type="provider_unavailable", request_id=local_request_id, response_ms=response_ms)
                break
            if response.status_code != 200:
                last_failure = self._classify_status(response.status_code, local_request_id, response_ms)
                if response.status_code >= 500 and attempt < max_attempts:
                    logger.warning("ai_request_retry %s", json.dumps({"request_id": local_request_id, "attempt": attempt, "status_code": response.status_code}))
                    time.sleep(0.5 * attempt)
                    continue
                break
            try:
                data = response.json()
            except ValueError:
                data = None
            failure = self._validate_response(data, local_request_id)
            if failure is None:
                last_failure = None
                break
            last_failure = failure
            snippet = ""
            if data is not None:
                try:
                    snippet = str((data.get("choices") or [{}])[0].get("message") or "")[:300]
                except (AttributeError, IndexError, TypeError):
                    snippet = str(data)[:300]
            if attempt < max_attempts:
                logger.warning("ai_request_retry %s", json.dumps({"request_id": local_request_id, "attempt": attempt, "reason": last_failure.error_type, "response_snippet": snippet}))
                time.sleep(0.5 * attempt)
                continue
            logger.error("ai_request_invalid_response %s", json.dumps({"request_id": local_request_id, "error_type": last_failure.error_type, "response_snippet": snippet}))
        if last_failure is not None or data is None:
            failure = last_failure or AIProviderError("AI-провайдер вернул некорректную структуру результата", request_id=local_request_id, response_ms=response_ms)
            logger.warning("ai_request_failed %s", json.dumps({"request_id": failure.request_id, "error_type": failure.error_type, "response_ms": failure.response_ms}))
            raise failure
        try:
            result = self._parse_result(data, local_request_id, response_ms)
        except AIProviderError as exc:
            choices = data.get("choices") or []
            snippet = str((choices[0].get("message") or {}).get("content") or "")[:300]
            logger.error("ai_request_invalid_response %s", json.dumps({"request_id": local_request_id, "error_type": exc.error_type, "response_snippet": snippet}))
            logger.warning("ai_request_failed %s", json.dumps({"request_id": exc.request_id, "error_type": exc.error_type, "response_ms": exc.response_ms}))
            raise

        allowed_source_ids = {item.id for item in knowledge}
        source_by_id = {item.id: item for item in knowledge}
        causes = [cause.model_copy(update={"source_ids": [item for item in cause.source_ids if item in allowed_source_ids]}) for cause in result.possible_causes]
        referenced_source_ids = list(dict.fromkeys(
            source_id for cause in causes for source_id in cause.source_ids
        ))
        result = result.model_copy(update={
            "possible_causes": causes,
            "sources": [str(source_by_id[source_id].url) for source_id in referenced_source_ids],
        })
        usage = data.get("usage") or {}
        input_tokens = int(usage.get("prompt_tokens", 0) or 0)
        output_tokens = int(usage.get("completion_tokens", 0) or 0)
        estimated_cost = (
            input_tokens * settings.deepseek_input_cost_per_million
            + output_tokens * settings.deepseek_output_cost_per_million
        ) / 1_000_000
        request_id = str(data.get("id") or local_request_id)
        logger.info("ai_request_succeeded %s", json.dumps({"request_id": request_id, "input_tokens": input_tokens, "output_tokens": output_tokens, "response_ms": response_ms, "estimated_cost": estimated_cost}))
        return AIAnalysis(
            result=result,
            usage=AIUsage(request_id=request_id, input_tokens=input_tokens, output_tokens=output_tokens, response_ms=response_ms, estimated_cost=estimated_cost),
        )
