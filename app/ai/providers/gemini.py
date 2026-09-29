import base64
import json
import logging
import time
from collections.abc import Sequence
from io import BytesIO
from uuid import uuid4

import httpx
from PIL import Image, ImageStat, UnidentifiedImageError

from app.ai.base import AIAnalysis, AIProviderError, AIUsage
from app.ai.providers.openai_multimodal import PROMPT_VERSION, SYSTEM_PROMPT
from app.config import settings
from app.models import Plant, PlantPhoto
from app.schemas import DiagnosisCreate, DiagnosisResult
from app.services.knowledge_service import retrieve_knowledge
from app.services.storage_service import StorageError, read_photo

logger = logging.getLogger(__name__)


class GeminiMultimodalGateway:
    prompt_version = PROMPT_VERSION
    demo_mode = False

    def __init__(self, api_key: str, model: str, api_base: str, timeout_seconds: float = 60):
        self.model_name = model
        self._api_key = api_key
        self._api_base = api_base.rstrip("/")
        self._client = httpx.Client(timeout=timeout_seconds)

    @staticmethod
    def _photo_content(photo: PlantPhoto, request_id: str) -> tuple[str, bool]:
        try:
            with Image.open(BytesIO(read_photo(photo))) as image:
                image.thumbnail(
                    (settings.ai_image_max_dimension, settings.ai_image_max_dimension),
                    Image.Resampling.LANCZOS,
                )
                clean = image.convert("RGB")
                luminance = ImageStat.Stat(clean.convert("L").resize((64, 64))).mean[0]
                output = BytesIO()
                clean.save(output, format="JPEG", quality=settings.ai_image_jpeg_quality, optimize=True)
                clean.close()
            encoded = base64.b64encode(output.getvalue()).decode("ascii")
        except (UnidentifiedImageError, ValueError) as exc:
            raise AIProviderError("Файл изображения повреждён", http_status=422, error_type="invalid_image", request_id=request_id) from exc
        except (OSError, StorageError) as exc:
            raise AIProviderError("Не удалось прочитать фотографию", request_id=request_id) from exc
        return encoded, luminance < 8

    @staticmethod
    def _cannot_analyze(
        reason: str,
        *,
        input_status: str = "insufficient_data",
        plant_detected: bool = True,
        image_quality: str = "acceptable",
    ) -> DiagnosisResult:
        return DiagnosisResult(
            input_status=input_status,
            plant_detected=plant_detected,
            image_quality=image_quality,
            cannot_analyze_reason=reason,
            analysis_outcome="cannot_analyze",
            disclaimer="AI не смог безопасно выполнить диагностику по этим данным.",
            analysis_status="cannot_analyze",
            possible_causes=[],
            safe_actions=["Сделайте новый чёткий снимок растения при дневном рассеянном свете."],
            questions=["Можете загрузить крупный план повреждённой части растения?"],
            expert_required=False,
            sources=[],
        )

    @staticmethod
    def _classify_status(status_code: int, request_id: str, response_ms: int) -> AIProviderError:
        if status_code == 429:
            return AIProviderError("Превышен rate limit AI-провайдера", http_status=429, error_type="provider_rate_limit", request_id=request_id, response_ms=response_ms)
        if status_code in (400, 415):
            return AIProviderError("AI-провайдер отклонил изображение", http_status=422, error_type="invalid_image", request_id=request_id, response_ms=response_ms)
        if status_code in (401, 403):
            return AIProviderError("Ошибка конфигурации AI-провайдера", http_status=503, error_type="provider_auth", request_id=request_id, response_ms=response_ms)
        if status_code >= 500:
            return AIProviderError("AI-провайдер недоступен", http_status=503, error_type="provider_unavailable", request_id=request_id, response_ms=response_ms)
        return AIProviderError("Ошибка мультимодального AI-провайдера", http_status=503, error_type="provider_error", request_id=request_id, response_ms=response_ms)

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
        if not photos:
            raise AIProviderError("Для мультимодального анализа нужна фотография", http_status=422, error_type="invalid_image", request_id=local_request_id)

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
            "trusted_sources": [item.model_dump(mode="json") for item in knowledge],
        }
        prepared_photos = [self._photo_content(photo, local_request_id) for photo in photos]
        if all(is_dark for _, is_dark in prepared_photos):
            response_ms = round((time.perf_counter() - started) * 1000)
            return AIAnalysis(
                result=self._cannot_analyze(
                    "Фотография слишком тёмная для надёжного анализа.",
                    input_status="poor_quality",
                    image_quality="poor",
                ),
                usage=AIUsage(request_id=local_request_id, response_ms=response_ms),
            )

        parts: list[dict] = [
            {"text": "Контекст обращения:\n" + json.dumps(context, ensure_ascii=False)},
            *(
                {"inline_data": {"mime_type": "image/jpeg", "data": encoded}}
                for encoded, is_dark in prepared_photos
                if not is_dark
            ),
        ]
        payload = {
            "system_instruction": {"parts": [{"text": SYSTEM_PROMPT}]},
            "contents": [{"role": "user", "parts": parts}],
            "generationConfig": {
                "responseMimeType": "application/json",
                "maxOutputTokens": settings.gemini_max_output_tokens,
            },
        }
        url = f"{self._api_base}/v1beta/models/{self.model_name}:generateContent"
        try:
            response = self._client.post(url, params={"key": self._api_key}, json=payload)
            response_ms = round((time.perf_counter() - started) * 1000)
            if response.status_code != 200:
                classified = self._classify_status(response.status_code, local_request_id, response_ms)
                logger.error("ai_request_failed %s", json.dumps({"request_id": classified.request_id, "error_type": classified.error_type, "response_ms": response_ms}))
                raise classified
            data = response.json()
            candidates = data.get("candidates") or []
            finish_reason = candidates[0].get("finishReason") if candidates else None
            if finish_reason in {"SAFETY", "RECITATION", "BLOCKLIST"}:
                return AIAnalysis(
                    result=self._cannot_analyze("Модель отказалась анализировать этот вход по соображениям безопасности."),
                    usage=AIUsage(request_id=local_request_id, response_ms=response_ms),
                )
            text = "".join(
                part.get("text", "")
                for part in (candidates[0].get("content", {}).get("parts", []) if candidates else [])
            )
            if not text.strip():
                raise AIProviderError("AI-провайдер не вернул структурированный результат", request_id=local_request_id)
            try:
                result = DiagnosisResult.model_validate_json(text)
            except ValueError as exc:
                raise AIProviderError("AI-провайдер вернул некорректную структуру результата", request_id=local_request_id) from exc
        except AIProviderError as exc:
            logger.warning("ai_request_failed %s", json.dumps({"request_id": exc.request_id, "error_type": exc.error_type, "response_ms": exc.response_ms}))
            raise
        except httpx.TimeoutException as exc:
            response_ms = round((time.perf_counter() - started) * 1000)
            classified = AIProviderError("Тайм-аут AI-провайдера", http_status=504, error_type="provider_timeout", request_id=local_request_id, response_ms=response_ms)
            logger.error("ai_request_failed %s", json.dumps({"request_id": classified.request_id, "error_type": classified.error_type, "response_ms": response_ms}))
            raise classified from exc
        except httpx.TransportError as exc:
            response_ms = round((time.perf_counter() - started) * 1000)
            classified = AIProviderError("AI-провайдер недоступен", http_status=503, error_type="provider_unavailable", request_id=local_request_id, response_ms=response_ms)
            logger.error("ai_request_failed %s", json.dumps({"request_id": classified.request_id, "error_type": classified.error_type, "response_ms": response_ms}))
            raise classified from exc

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
        usage_metadata = data.get("usageMetadata") or {}
        input_tokens = int(usage_metadata.get("promptTokenCount", 0) or 0)
        output_tokens = int(usage_metadata.get("candidatesTokenCount", 0) or 0)
        estimated_cost = (
            input_tokens * settings.gemini_input_cost_per_million
            + output_tokens * settings.gemini_output_cost_per_million
        ) / 1_000_000
        logger.info("ai_request_succeeded %s", json.dumps({"request_id": local_request_id, "input_tokens": input_tokens, "output_tokens": output_tokens, "response_ms": response_ms, "estimated_cost": estimated_cost}))
        return AIAnalysis(
            result=result,
            usage=AIUsage(request_id=local_request_id, input_tokens=input_tokens, output_tokens=output_tokens, response_ms=response_ms, estimated_cost=estimated_cost),
        )
