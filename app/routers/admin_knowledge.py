import logging
from collections.abc import Sequence

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.auth import get_admin_user
from app.database import get_db
from app.models import KnowledgeChunk, KnowledgeSourceRecord, User
from app.schemas import (
    KnowledgeSourceAdminRead,
    KnowledgeSourceUpsert,
    KnowledgeSyncRead,
)
from app.services.knowledge_service import (
    KnowledgeSource,
    create_embedding_provider,
    sync_builtin_knowledge,
    upsert_knowledge_source,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/admin/knowledge")


def _knowledge_source_read(
    item: KnowledgeSourceRecord,
    chunk_count: int,
    embedding_model: str | None,
) -> dict:
    return {
        "id": item.id,
        "title": item.title,
        "url": item.url,
        "summary": item.summary,
        "keywords": item.keywords,
        "region": item.region,
        "language": item.languages,
        "plant_types": item.plant_types,
        "problem_types": item.problem_types,
        "last_verified_at": item.last_verified_at,
        "next_review_at": item.next_review_at,
        "reviewed_by": item.reviewed_by,
        "review_role": item.review_role,
        "usage_basis": item.usage_basis,
        "source_version": item.source_version,
        "active": item.active,
        "chunks": chunk_count,
        "embedding_model": embedding_model,
        "updated_at": item.updated_at,
    }


def _chunk_stats(db: Session, source_ids: Sequence[str]) -> dict[str, tuple[int, str | None]]:
    """Агрегат вместо ленивой загрузки: relationship тянет векторы эмбеддингов всех чанков."""
    if not source_ids:
        return {}
    rows = db.execute(
        select(
            KnowledgeChunk.source_id,
            func.count(KnowledgeChunk.id),
            # Модель едина для источника: он синхронизируется одним провайдером.
            func.min(KnowledgeChunk.embedding_model),
        )
        .where(KnowledgeChunk.source_id.in_(source_ids))
        .group_by(KnowledgeChunk.source_id)
    )
    return {source_id: (count, model) for source_id, count, model in rows}


def _knowledge_admin_read(item: KnowledgeSourceRecord) -> dict:
    return _knowledge_source_read(
        item,
        len(item.chunks),
        item.chunks[0].embedding_model if item.chunks else None,
    )


@router.get("/sources", response_model=list[KnowledgeSourceAdminRead])
def admin_knowledge_sources(
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=200),
    _admin: User = Depends(get_admin_user),
    db: Session = Depends(get_db),
) -> list[dict]:
    items = list(
        db.scalars(
            select(KnowledgeSourceRecord)
            .order_by(KnowledgeSourceRecord.id)
            .offset(offset)
            .limit(limit)
        )
    )
    stats = _chunk_stats(db, [item.id for item in items])
    return [
        _knowledge_source_read(item, *stats.get(item.id, (0, None))) for item in items
    ]


@router.post("/sources", response_model=KnowledgeSourceAdminRead)
def admin_upsert_knowledge_source(
    payload: KnowledgeSourceUpsert,
    _admin: User = Depends(get_admin_user),
    db: Session = Depends(get_db),
) -> dict:
    source = KnowledgeSource.model_validate(payload.model_dump(exclude={"active"}))
    try:
        item = upsert_knowledge_source(db, source, active=payload.active)
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, "Источник с таким URL уже существует")
    except Exception as exc:
        db.rollback()
        logger.exception("knowledge source embedding failed")
        raise HTTPException(503, "Сервис embeddings временно недоступен") from exc
    return _knowledge_admin_read(item)


@router.post("/sync", response_model=KnowledgeSyncRead)
def admin_sync_knowledge(
    _admin: User = Depends(get_admin_user),
    db: Session = Depends(get_db),
) -> dict:
    provider = create_embedding_provider()
    try:
        changed = sync_builtin_knowledge(db, provider)
    except Exception as exc:
        db.rollback()
        logger.exception("knowledge synchronization failed")
        raise HTTPException(503, "Сервис embeddings временно недоступен") from exc
    return {
        "refreshed_sources": changed,
        "total_sources": db.scalar(select(func.count(KnowledgeSourceRecord.id))) or 0,
        "embedding_model": provider.model_name,
    }
