import hmac
import secrets
from datetime import UTC, date, datetime
from uuid import uuid4

from fastapi import (
    APIRouter,
    Depends,
    Header,
    HTTPException,
    Query,
)
from fastapi.responses import RedirectResponse
from sqlalchemy import and_, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.auth import get_current_user
from app.catalog_taxonomy import (
    resolve_catalog_code,
)
from app.database import get_db
from app.models import (
    Diagnosis,
    Garden,
    Partner,
    PartnerMember,
    Plant,
    Product,
    ProductLead,
    ProductRecommendationRule,
    RegulatedProductRegistration,
    User,
)
from app.schemas import (
    PartnerConversionPostback,
    PartnerLeadRead,
    PartnerPostbackKeyRead,
    ProductLeadCreate,
    ProductLeadRead,
    ProductRead,
)
from app.services.partner_attribution_service import (
    AttributionTokenError,
    add_click_id,
    hash_partner_key,
    lead_idempotency_key,
    verify_click_token,
)
from app.services.partner_catalog_service import (
    get_partner_member,
    lead_dict,
    product_dict,
    product_matches_region,
)

router = APIRouter(prefix="/api/v1")
public_router = APIRouter()

@router.get("/catalog/products", response_model=list[ProductRead])
def catalog_products(
    category: str | None = None,
    region: str | None = Query(default=None, max_length=120),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=100),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[dict]:
    query = (
        select(Product, Partner.name)
        .join(Partner)
        .where(
            Product.active.is_(True),
            Product.moderation_status == "approved",
            Partner.active.is_(True),
        )
    )
    if category:
        query = query.where(Product.category == category)
    rows = db.execute(query.order_by(Product.id.desc()).limit(200)).all()
    selected_region = region or user.region
    matching = [
        product_dict(product, partner_name)
        for product, partner_name in rows
        if product_matches_region(product, selected_region)
    ]
    return matching[offset : offset + limit]


@router.get(
    "/recommendations/{diagnosis_id}/products", response_model=list[ProductRead]
)
def recommendation_products(
    diagnosis_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[dict]:
    diagnosis = db.scalar(
        select(Diagnosis)
        .join(Plant)
        .join(Garden)
        .where(Diagnosis.id == diagnosis_id, Garden.user_id == user.id)
    )
    if not diagnosis:
        raise HTTPException(404, "Диагностика не найдена")
    if diagnosis.result.get("analysis_outcome") != "possible_problem":
        return []
    plant = db.get(Plant, diagnosis.plant_id)
    cause_names = {
        str(item.get("name", "")).strip().casefold()
        for item in diagnosis.result.get("possible_causes", [])
        if item.get("name") and item.get("confidence") in {"high", "medium"}
    }
    cause_codes = {
        str(item.get("cause_code", "")).strip().lower()
        for item in diagnosis.result.get("possible_causes", [])
        if item.get("cause_code") and item.get("confidence") in {"high", "medium"}
    }
    if not cause_names and not cause_codes:
        return []
    region_value = (plant.region or user.region or "").strip()
    region = region_value.casefold()
    country_code = resolve_catalog_code(db, "country", region_value, user.language)
    crop = (plant.species or plant.name).strip().casefold()
    taxon_id = (
        plant.taxon_id.lower()
        if plant.taxon_id
        else resolve_catalog_code(
            db, "plant", plant.species or plant.name, user.language
        )
    )
    for cause_name in cause_names:
        resolved = resolve_catalog_code(db, "problem", cause_name, user.language)
        if resolved:
            cause_codes.add(resolved)
    rows = db.execute(
        select(Product, Partner.name, ProductRecommendationRule)
        .join(Partner, Partner.id == Product.partner_id)
        .join(
            ProductRecommendationRule,
            ProductRecommendationRule.product_id == Product.id,
        )
        .outerjoin(
            RegulatedProductRegistration,
            RegulatedProductRegistration.id
            == ProductRecommendationRule.registry_entry_id,
        )
        .where(
            Product.active.is_(True),
            Product.in_stock.is_(True),
            Product.moderation_status == "approved",
            Product.category.in_(("tools", "irrigation")),
            Partner.active.is_(True),
            ProductRecommendationRule.active.is_(True),
            ProductRecommendationRule.expert_verified.is_(True),
            or_(
                ProductRecommendationRule.registry_entry_id.is_(None),
                and_(
                    RegulatedProductRegistration.status == "active",
                    or_(
                        RegulatedProductRegistration.valid_until.is_(None),
                        RegulatedProductRegistration.valid_until >= date.today(),
                    ),
                ),
            ),
            (
                ProductRecommendationRule.registration_expires_on.is_(None)
                | (ProductRecommendationRule.registration_expires_on >= date.today())
            ),
        )
        .order_by(Product.id.desc())
        .limit(200)
    ).all()
    selected: list[dict] = []
    seen: set[int] = set()
    for product, partner_name, rule in rows:
        rule_crop = rule.crop_name.casefold()
        rule_problem = rule.problem_name.casefold()
        rule_region = rule.region_code.casefold()
        crop_matches = (
            taxon_id is not None and taxon_id == rule.plant_taxon_id
            if rule.plant_taxon_id
            else rule_crop == "*" or rule_crop in crop or crop in rule_crop
        )
        problem_matches = (
            rule.problem_code in cause_codes
            if rule.problem_code
            else rule_problem == "*"
            or any(
                rule_problem in cause or cause in rule_problem for cause in cause_names
            )
        )
        region_matches = (
            country_code is not None and country_code == rule.country_code
            if rule.country_code
            else rule_region == "*"
            or (region and (rule_region in region or region in rule_region))
        )
        if (
            product.id not in seen
            and crop_matches
            and problem_matches
            and region_matches
            and product_matches_region(product, region)
        ):
            selected.append(product_dict(product, partner_name))
            seen.add(product.id)
    return selected[:10]


@router.post(
    "/products/{product_id}/lead", response_model=ProductLeadRead, status_code=201
)
def create_product_lead(
    product_id: int,
    payload: ProductLeadCreate,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    product = db.scalar(
        select(Product)
        .join(Partner)
        .where(
            Product.id == product_id,
            Product.active.is_(True),
            Product.moderation_status == "approved",
            Partner.active.is_(True),
        )
    )
    if not product:
        raise HTTPException(404, "Товар не найден")
    if payload.diagnosis_id is not None:
        owned = db.scalar(
            select(Diagnosis.id)
            .join(Plant)
            .join(Garden)
            .where(Diagnosis.id == payload.diagnosis_id, Garden.user_id == user.id)
        )
        if not owned:
            raise HTTPException(404, "Диагностика не найдена")
    idempotency_key = lead_idempotency_key(user.id, product.id, payload.diagnosis_id)
    existing = db.scalar(
        select(ProductLead).where(
            ProductLead.idempotency_key == idempotency_key,
        )
    )
    if existing:
        return lead_dict(existing)
    lead = ProductLead(
        user_id=user.id,
        product_id=product.id,
        diagnosis_id=payload.diagnosis_id,
        click_id=str(uuid4()),
        idempotency_key=idempotency_key,
        source="diagnosis" if payload.diagnosis_id is not None else "catalog",
    )
    db.add(lead)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        existing = db.scalar(
            select(ProductLead).where(
                ProductLead.idempotency_key == idempotency_key,
            )
        )
        if existing is None:
            raise
        return lead_dict(existing)
    db.refresh(lead)
    return lead_dict(lead)


@public_router.get("/product/go/{signed_click_id}", include_in_schema=False)
def product_redirect(
    signed_click_id: str, db: Session = Depends(get_db)
) -> RedirectResponse:
    try:
        click_id = verify_click_token(signed_click_id)
    except AttributionTokenError as exc:
        raise HTTPException(404, "Переход недействителен или устарел") from exc
    row = db.execute(
        select(ProductLead, Product, Partner)
        .join(Product, Product.id == ProductLead.product_id)
        .join(Partner, Partner.id == Product.partner_id)
        .where(
            ProductLead.click_id == click_id,
            Product.active.is_(True),
            Product.moderation_status == "approved",
            Partner.active.is_(True),
        )
        .with_for_update()
    ).one_or_none()
    if not row:
        raise HTTPException(404, "Переход недействителен или устарел")
    lead, product, _partner = row
    lead.redirected_at = datetime.now(UTC)
    lead.redirect_count = (lead.redirect_count or 0) + 1
    db.commit()
    return RedirectResponse(
        add_click_id(product.product_url, signed_click_id), status_code=302
    )


@router.post("/partner/postback-key", response_model=PartnerPostbackKeyRead)
def rotate_partner_postback_key(
    member: PartnerMember = Depends(get_partner_member),
    db: Session = Depends(get_db),
) -> dict:
    if member.role != "owner":
        raise HTTPException(403, "Только владелец может обновить postback key")
    partner = db.get(Partner, member.partner_id)
    api_key = secrets.token_urlsafe(40)
    partner.postback_secret_hash = hash_partner_key(api_key)
    db.commit()
    return {"api_key": api_key}


@router.post("/partner/conversions/postback", response_model=PartnerLeadRead)
def partner_conversion_postback(
    payload: PartnerConversionPostback,
    partner_key: str | None = Header(default=None, alias="X-Partner-Key"),
    db: Session = Depends(get_db),
) -> dict:
    try:
        click_id = verify_click_token(payload.signed_click_id)
    except AttributionTokenError as exc:
        raise HTTPException(404, "Переход не найден") from exc
    row = db.execute(
        select(ProductLead, Product, Partner)
        .join(Product, Product.id == ProductLead.product_id)
        .join(Partner, Partner.id == Product.partner_id)
        .where(ProductLead.click_id == click_id)
        .with_for_update()
    ).one_or_none()
    if not row:
        raise HTTPException(404, "Переход не найден")
    lead, product, partner = row
    supplied_hash = hash_partner_key(partner_key or "")
    if not partner.postback_secret_hash or not hmac.compare_digest(
        supplied_hash,
        partner.postback_secret_hash,
    ):
        raise HTTPException(401, "Некорректный partner key")
    if lead.redirected_at is None:
        raise HTTPException(409, "Переход ещё не был зарегистрирован")
    if lead.status not in {"clicked", "rejected"} or lead.invoice_id is not None:
        raise HTTPException(409, "Конверсия уже обрабатывается или включена в счёт")
    lead.status = "conversion_claimed"
    lead.partner_reference = payload.partner_reference
    lead.conversion_value_cents = payload.conversion_value_cents
    lead.claimed_at = datetime.now(UTC)
    lead.confirmed_at = None
    lead.rejected_at = None
    lead.review_note = None
    lead.reviewed_by_admin_id = None
    lead.billable_amount_cents = 0
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, "Такая ссылка на продажу уже использована для товара")
    db.refresh(lead)
    return {
        "id": lead.id,
        "product_id": lead.product_id,
        "product_name": product.name,
        "source": lead.source,
        "status": lead.status,
        "partner_reference": lead.partner_reference,
        "conversion_value_cents": lead.conversion_value_cents,
        "billable_amount_cents": lead.billable_amount_cents,
        "claimed_at": lead.claimed_at,
        "confirmed_at": lead.confirmed_at,
        "created_at": lead.created_at,
    }
