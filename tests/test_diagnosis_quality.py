from app.config import settings
from app.database import SessionLocal
from app.models import Diagnosis, Garden, Plant, User
from app.services.diagnosis_quality_service import build_diagnosis_quality_report, percentile


def test_percentile_uses_nearest_rank_and_handles_empty_input():
    assert percentile([], 0.95) is None
    assert percentile([100, 200, 300, 400, 500], 0.5) == 300
    assert percentile([100, 200, 300, 400, 500], 0.95) == 500


def test_fallback_rate_counts_deepseek_responses_when_provider_is_not_deepseek():
    with SessionLocal() as db:
        user = User(email="quality@example.com", name="Quality", password_hash="x")
        db.add(user)
        db.flush()
        garden = Garden(user_id=user.id, name="Garden")
        db.add(garden)
        db.flush()
        plant = Plant(garden_id=garden.id, name="Rose")
        db.add(plant)
        db.flush()
        for model in (settings.deepseek_model, settings.deepseek_model, "mock-rule-based"):
            db.add(
                Diagnosis(
                    plant_id=plant.id,
                    symptoms="wilt",
                    damaged_part="leaf",
                    status="needs_confirmation",
                    result={"analysis_status": "needs_confirmation"},
                    model_name=model,
                )
            )
        db.commit()

        report = build_diagnosis_quality_report(db)

        assert settings.ai_provider != "deepseek"
        assert report["kpis"]["diagnoses"] == 3
        assert report["kpis"]["fallback_rate"] == 66.67
