from uuid import uuid4

from app.config import settings
from app.database import SessionLocal
from app.models import DiagnosisJob
from app.services.diagnosis_job_service import process_diagnosis_job


def dispatch_diagnosis_job(job_id: str, *, gateway=None) -> str | None:
    if settings.diagnosis_execution_mode == "celery":
        task_id = str(uuid4())
        with SessionLocal() as db:
            job = db.get(DiagnosisJob, job_id)
            if not job:
                raise RuntimeError("Diagnosis job not found")
            job.celery_task_id = task_id
            db.commit()
        from app.tasks import analyze_diagnosis_task

        try:
            analyze_diagnosis_task.apply_async(args=[job_id], task_id=task_id)
        except Exception:
            with SessionLocal() as db:
                job = db.get(DiagnosisJob, job_id)
                if job and job.status == "queued" and job.celery_task_id == task_id:
                    job.celery_task_id = None
                    db.commit()
            raise
        return task_id
    process_diagnosis_job(job_id, gateway=gateway)
    return None
