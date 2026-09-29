from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

from app.models import Plant, PlantPhoto
from app.schemas import DiagnosisCreate, DiagnosisResult


@dataclass(frozen=True)
class AIUsage:
    request_id: str
    input_tokens: int = 0
    output_tokens: int = 0
    response_ms: int = 0
    estimated_cost: float = 0


@dataclass(frozen=True)
class AIAnalysis:
    result: DiagnosisResult
    usage: AIUsage
    # Модель, реально обслужившая запрос (заполняется фолбэк-шлюзом; None у прямых провайдеров)
    responder_model: str | None = None


class AIProviderError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        http_status: int = 503,
        error_type: str = "provider_unavailable",
        request_id: str = "unknown",
        response_ms: int = 0,
        model_name: str | None = None,
    ):
        super().__init__(message)
        self.http_status = http_status
        self.error_type = error_type
        self.request_id = request_id
        self.response_ms = response_ms
        self.model_name = model_name


class AIGateway(Protocol):
    model_name: str
    prompt_version: str
    demo_mode: bool

    def analyze(
        self,
        plant: Plant,
        request: DiagnosisCreate,
        photos: Sequence[PlantPhoto],
        answers: Sequence[str] = (),
        safety_identifier: str | None = None,
    ) -> AIAnalysis: ...
