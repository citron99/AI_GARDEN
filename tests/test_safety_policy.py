from sqlalchemy import select

from app.database import SessionLocal
from app.models import AISafetyAdjustment
from app.schemas import DiagnosisResult, PossibleCause
from app.services.safety_policy_service import enforce_diagnosis_safety


def _unsafe_result() -> DiagnosisResult:
    return DiagnosisResult(
        input_status="valid",
        plant_detected=True,
        image_quality="good",
        analysis_outcome="possible_problem",
        disclaimer="Preliminary assessment only.",
        analysis_status="preliminary",
        possible_causes=[PossibleCause(
            name="Possible fungal disease",
            confidence="medium",
            matched_signs=["leaf spots"],
            missing_signs=[],
            checks=["inspect both leaf surfaces"],
        )],
        safe_actions=[
            "Remove fallen leaves and continue observation.",
            "Apply fungicide at 25 ml per litre.",
            "Mix pesticide with fertilizer and spray the plant.",
        ],
        questions=[],
        expert_required=False,
        sources=[],
    )


def test_safety_policy_removes_dosages_mixing_and_chemical_application():
    with SessionLocal() as db:
        result, decision = enforce_diagnosis_safety(db, _unsafe_result())

    assert decision.adjusted is True
    assert decision.removed_action_count == 2
    assert set(decision.reason_codes) == {
        "chemical_mixing",
        "unregistered_product",
        "unverified_chemical_action",
        "unverified_dosage",
    }
    assert result.expert_required is True
    assert result.analysis_status == "expert_required"
    assert "Remove fallen leaves and continue observation." in result.safe_actions
    assert all("25 ml" not in action for action in result.safe_actions)
    assert all("Mix pesticide" not in action for action in result.safe_actions)


def test_safety_policy_keeps_explicit_prohibition():
    source = _unsafe_result()
    source.safe_actions = [
        "Do not mix pesticides; consult the approved label.",
        "Do not apply chemical pesticides before expert confirmation.",
    ]

    with SessionLocal() as db:
        result, decision = enforce_diagnosis_safety(db, source)

    assert decision.adjusted is False
    assert result.safe_actions == source.safe_actions
    assert db_scalar_safety_adjustment_count() == 0


def db_scalar_safety_adjustment_count() -> int:
    with SessionLocal() as db:
        return len(list(db.scalars(select(AISafetyAdjustment.id))))
