from collections.abc import Sequence
from uuid import uuid4

from app.ai.base import AIAnalysis, AIUsage
from app.models import Plant, PlantPhoto
from app.schemas import DiagnosisCreate, DiagnosisResult, PossibleCause


class MockAIGateway:
    """Воспроизводимая заглушка для разработки и тестов."""

    model_name = "mock-rule-based"
    prompt_version = "mock-v1"
    demo_mode = True

    def analyze(
        self,
        plant: Plant,
        request: DiagnosisCreate,
        photos: Sequence[PlantPhoto],
        answers: Sequence[str] = (),
        safety_identifier: str | None = None,
    ) -> AIAnalysis:
        text = " ".join((request.symptoms, *answers)).lower()
        healthy = any(word in text for word in ("здоров", "без проблем", "healthy", "vesels"))
        if any(word in text for word in ("желт", "бледн", "хлороз")):
            causes = [
                PossibleCause(name="Нарушение режима полива", cause_code="care.water_stress", confidence="medium", matched_signs=["пожелтение листьев"], missing_signs=["неизвестна влажность почвы"], checks=["Проверьте влажность почвы на глубине 3–5 см"], source_ids=["uc-ipm-water"]),
                PossibleCause(name="Дефицит питания", cause_code="abiotic.nutrient_deficiency", confidence="low", matched_signs=["изменение окраски листьев"], missing_signs=["неизвестно, какие листья повреждены первыми"], checks=["Сравните молодые и старые листья"], source_ids=["uc-ipm-nutrients"]),
                PossibleCause(name="Стресс корневой системы", cause_code="abiotic.root_stress", confidence="low", matched_signs=["общее ослабление растения"], missing_signs=["корни не осмотрены"], checks=["Проверьте дренаж и запах грунта"], source_ids=["uc-ipm-diagnosis"]),
            ]
        else:
            causes = [
                PossibleCause(name="Грибковое поражение", cause_code="disease.fungal_unspecified", confidence="medium", matched_signs=["описаны видимые повреждения"], missing_signs=["нет снимка нижней стороны листа"], checks=["Осмотрите нижнюю сторону повреждённых участков"]),
                PossibleCause(name="Повреждение вредителями", cause_code="pest.unspecified", confidence="low", matched_signs=["локальные симптомы"], missing_signs=["насекомые не подтверждены"], checks=["Проверьте наличие насекомых, паутины и липкого налёта"]),
                PossibleCause(name="Абиотический стресс", cause_code="abiotic.unspecified", confidence="low", matched_signs=["симптомы могут быть связаны с условиями"], missing_signs=["нет данных о погоде и поливе"], checks=["Вспомните изменения полива, температуры и освещения"]),
            ]
        if healthy:
            causes = []
        result = DiagnosisResult(
            input_status="valid",
            plant_detected=True,
            image_quality="acceptable",
            cannot_analyze_reason=None,
            analysis_outcome="no_visible_problem" if healthy else "possible_problem",
            disclaimer="По фотографии и вашему описанию наиболее вероятны следующие причины. Это предварительная оценка, а не подтверждённый диагноз.",
            analysis_status="needs_confirmation",
            possible_causes=causes,
            safe_actions=(["Продолжайте обычный уход и наблюдение."] if healthy else ["Изолируйте растение, если рядом есть другие растения", "Не применяйте химические препараты до уточнения причины", "Сделайте чёткие фотографии при дневном свете"]),
            questions=([] if healthy else ["Повреждены молодые или старые листья?", "Есть ли пятна снизу листа, насекомые или паутина?", "Менялись ли полив, подкормка или температура за последние две недели?"]),
            expert_required=False,
            sources=["https://ipm.ucanr.edu/agriculture/floriculture-and-ornamental-nurseries/diagnosing-plant-problems/"],
        )
        return AIAnalysis(result=result, usage=AIUsage(request_id=f"mock_{uuid4().hex}"))
