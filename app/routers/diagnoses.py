from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from app.auth import get_current_user
from app.database import get_db
from app.models import (
    CareEvent,
    Diagnosis,
    DiagnosisAnswer,
    DiagnosisFeedback,
    DiagnosisJob,
    DiagnosisQuestion,
    DiagnosisRevision,
    Reminder,
    User,
)
from app.ownership import owned_diagnosis, owned_plant
from app.schemas import (
    DiagnosisAnswersCreate,
    DiagnosisCreate,
    DiagnosisFeedbackCreate,
    DiagnosisFeedbackRead,
    DiagnosisJobRead,
    DiagnosisQuestionRead,
    DiagnosisRead,
    PlantHistoryRead,
)
from app.services.diagnosis_presentation import diagnosis_read
from app.services.diagnosis_quota_service import (
    create_and_dispatch_diagnosis_job,
    queue_reanalysis_job,
)

router = APIRouter(prefix="/api/v1")


@router.post("/diagnoses", response_model=DiagnosisJobRead, status_code=202)
def create_diagnosis(
    payload: DiagnosisCreate, user: User = Depends(get_current_user), db: Session = Depends(get_db)
) -> DiagnosisJob:
    return create_and_dispatch_diagnosis_job(payload, user, db)


@router.post("/diagnoses/async", response_model=DiagnosisJobRead, status_code=202)
def create_diagnosis_job(
    payload: DiagnosisCreate,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> DiagnosisJob:
    return create_and_dispatch_diagnosis_job(payload, user, db)


@router.get("/diagnosis-jobs/{job_id}", response_model=DiagnosisJobRead)
def get_diagnosis_job(
    job_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> DiagnosisJob:
    job = db.scalar(
        select(DiagnosisJob).where(DiagnosisJob.id == job_id, DiagnosisJob.user_id == user.id)
    )
    if not job:
        raise HTTPException(404, "Задание диагностики не найдено")
    return job


@router.get("/diagnoses/{diagnosis_id}", response_model=DiagnosisRead)
def get_diagnosis(
    diagnosis_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)
) -> DiagnosisRead:
    return diagnosis_read(owned_diagnosis(diagnosis_id, user, db), user.language)


@router.get("/plants/{plant_id}/diagnoses", response_model=list[DiagnosisRead])
def list_plant_diagnoses(
    plant_id: int,
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=100),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[DiagnosisRead]:
    owned_plant(plant_id, user, db)
    diagnoses = list(
        db.scalars(
            select(Diagnosis)
            .options(selectinload(Diagnosis.photos))
            .where(Diagnosis.plant_id == plant_id)
            .order_by(Diagnosis.created_at.desc())
            .offset(offset)
            .limit(limit)
        )
    )
    return [diagnosis_read(diagnosis, user.language) for diagnosis in diagnoses]


@router.get("/plants/{plant_id}/history", response_model=PlantHistoryRead)
def plant_history(
    plant_id: int,
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=100),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    plant = owned_plant(plant_id, user, db)
    diagnoses = list(
        db.scalars(
            select(Diagnosis)
            .options(selectinload(Diagnosis.photos))
            .where(Diagnosis.plant_id == plant_id)
            .order_by(Diagnosis.created_at.desc())
            .offset(offset)
            .limit(limit)
        )
    )
    diagnosis_ids = [item.id for item in diagnoses]
    revisions = (
        list(
            db.scalars(
                select(DiagnosisRevision)
                .where(DiagnosisRevision.diagnosis_id.in_(diagnosis_ids))
                .order_by(DiagnosisRevision.created_at.desc())
            )
        )
        if diagnosis_ids
        else []
    )
    answers = (
        list(
            db.execute(
                select(
                    DiagnosisQuestion.diagnosis_id,
                    DiagnosisQuestion.text,
                    DiagnosisAnswer.answer,
                    DiagnosisQuestion.revision_number,
                )
                .join(DiagnosisAnswer, DiagnosisAnswer.question_id == DiagnosisQuestion.id)
                .where(DiagnosisQuestion.diagnosis_id.in_(diagnosis_ids))
                .order_by(DiagnosisQuestion.revision_number, DiagnosisQuestion.position)
            ).all()
        )
        if diagnosis_ids
        else []
    )
    care_events = list(
        db.scalars(
            select(CareEvent)
            .where(CareEvent.plant_id == plant_id)
            .order_by(CareEvent.occurred_at.desc(), CareEvent.id.desc())
            .limit(200)
        )
    )
    reminders = list(
        db.scalars(
            select(Reminder)
            .where(Reminder.plant_id == plant_id)
            .order_by(Reminder.due_at, Reminder.id)
            .limit(200)
        )
    )
    return {
        "plant": plant,
        "diagnoses": [diagnosis_read(diagnosis, user.language) for diagnosis in diagnoses],
        "revisions": revisions,
        "answers": [
            {
                "diagnosis_id": diagnosis_id,
                "question": question,
                "answer": answer,
                "revision_number": revision_number,
            }
            for diagnosis_id, question, answer, revision_number in answers
        ],
        "care_events": care_events,
        "reminders": reminders,
    }


@router.get("/diagnoses/{diagnosis_id}/questions", response_model=list[DiagnosisQuestionRead])
def diagnosis_questions(
    diagnosis_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)
) -> list[DiagnosisQuestion]:
    diagnosis = owned_diagnosis(diagnosis_id, user, db)
    return list(
        db.scalars(
            select(DiagnosisQuestion)
            .where(
                DiagnosisQuestion.diagnosis_id == diagnosis.id,
                DiagnosisQuestion.revision_number == diagnosis.current_revision,
            )
            .order_by(DiagnosisQuestion.position)
        )
    )


@router.post("/diagnoses/{diagnosis_id}/answers", response_model=DiagnosisJobRead, status_code=202)
def answer_diagnosis_questions(
    diagnosis_id: int,
    payload: DiagnosisAnswersCreate,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> DiagnosisJob:
    diagnosis = owned_diagnosis(diagnosis_id, user, db)
    question_ids = [item.question_id for item in payload.answers]
    if len(question_ids) != len(set(question_ids)):
        raise HTTPException(422, "Один вопрос нельзя передать дважды")
    questions = list(
        db.scalars(
            select(DiagnosisQuestion)
            .options(selectinload(DiagnosisQuestion.answer))
            .where(
                DiagnosisQuestion.id.in_(question_ids),
                DiagnosisQuestion.diagnosis_id == diagnosis.id,
                DiagnosisQuestion.revision_number == diagnosis.current_revision,
            )
        )
    )
    if {question.id for question in questions} != set(question_ids):
        raise HTTPException(400, "Некоторые вопросы не принадлежат текущей версии диагностики")
    by_id = {question.id: question for question in questions}
    for item in payload.answers:
        question = by_id[item.question_id]
        if question.answer:
            question.answer.answer = item.answer
        else:
            db.add(DiagnosisAnswer(question_id=question.id, answer=item.answer))
    db.flush()
    return queue_reanalysis_job(diagnosis, user, db, reason="answers")


@router.post(
    "/diagnoses/{diagnosis_id}/reanalyze", response_model=DiagnosisJobRead, status_code=202
)
def reanalyze_diagnosis(
    diagnosis_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)
) -> DiagnosisJob:
    diagnosis = owned_diagnosis(diagnosis_id, user, db)
    return queue_reanalysis_job(diagnosis, user, db, reason="manual")


@router.post("/diagnoses/{diagnosis_id}/feedback", response_model=DiagnosisFeedbackRead)
def create_diagnosis_feedback(
    diagnosis_id: int,
    payload: DiagnosisFeedbackCreate,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> DiagnosisFeedback:
    diagnosis = owned_diagnosis(diagnosis_id, user, db)
    feedback = db.scalar(
        select(DiagnosisFeedback).where(
            DiagnosisFeedback.diagnosis_id == diagnosis.id,
            DiagnosisFeedback.user_id == user.id,
        )
    )
    if feedback:
        for field, value in payload.model_dump().items():
            setattr(feedback, field, value)
    else:
        feedback = DiagnosisFeedback(
            diagnosis_id=diagnosis.id, user_id=user.id, **payload.model_dump()
        )
        db.add(feedback)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        feedback = db.scalar(
            select(DiagnosisFeedback).where(
                DiagnosisFeedback.diagnosis_id == diagnosis.id,
                DiagnosisFeedback.user_id == user.id,
            )
        )
        if not feedback:
            raise
        for field, value in payload.model_dump().items():
            setattr(feedback, field, value)
        db.commit()
    db.refresh(feedback)
    return feedback
