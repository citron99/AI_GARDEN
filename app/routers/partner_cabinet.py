import csv
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime, timedelta
from io import StringIO
from typing import Any

from fastapi import (
    APIRouter,
    Depends,
    File,
    HTTPException,
    Query,
    Response,
    UploadFile,
)
from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.auth import get_current_user
from app.database import get_db
from app.models import (
    AdminAuditLog,
    Partner,
    PartnerInvoice,
    PartnerMember,
    Product,
    ProductLead,
    User,
)
from app.schemas import (
    AdminProductRead,
    PartnerAccountRead,
    PartnerConversionClaim,
    PartnerInvoiceRead,
    PartnerLeadRead,
    PartnerOverviewRead,
    PartnerProductCreate,
    ProductUpdate,
)
from app.services.partner_catalog_service import (
    get_partner_member,
    invoice_cancellation_pdf_response,
    invoice_pdf_response,
    product_dict,
    require_b2b_invoicing,
    require_product_manager,
)

router = APIRouter(prefix="/api/v1")

@router.get("/partner/me", response_model=PartnerAccountRead | None)
def partner_account(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict | None:
    member = db.scalar(
        select(PartnerMember)
        .join(Partner)
        .where(
            PartnerMember.user_id == user.id,
            PartnerMember.active.is_(True),
            Partner.active.is_(True),
        )
    )
    if member is None:
        return None
    partner = db.get(Partner, member.partner_id)
    return {
        "partner_id": partner.id,
        "partner_name": partner.name,
        "website_url": partner.website_url,
        "role": member.role,
        "can_manage_products": member.role in {"owner", "editor"},
        "billing_plan": partner.billing_plan,
        "billing_currency": partner.billing_currency,
    }


@router.get("/partner/overview", response_model=PartnerOverviewRead)
def partner_overview(
    member: PartnerMember = Depends(get_partner_member),
    db: Session = Depends(get_db),
) -> dict:
    cutoff = datetime.now(UTC) - timedelta(days=30)
    lead_base = (
        select(func.count(ProductLead.id))
        .join(Product)
        .where(Product.partner_id == member.partner_id)
    )
    amount_base = (
        select(func.coalesce(func.sum(ProductLead.billable_amount_cents), 0))
        .join(Product)
        .where(
            Product.partner_id == member.partner_id,
            ProductLead.status == "confirmed",
            ProductLead.invoice_id.is_(None),
        )
    )
    return {
        "partner_id": member.partner_id,
        "partner_name": db.scalar(
            select(Partner.name).where(Partner.id == member.partner_id)
        ),
        "active_products": db.scalar(
            select(func.count(Product.id)).where(
                Product.partner_id == member.partner_id, Product.active.is_(True)
            )
        )
        or 0,
        "total_products": db.scalar(
            select(func.count(Product.id)).where(
                Product.partner_id == member.partner_id
            )
        )
        or 0,
        "total_leads": db.scalar(lead_base) or 0,
        "leads_last_30_days": db.scalar(
            lead_base.where(ProductLead.created_at >= cutoff)
        )
        or 0,
        "pending_conversion_claims": db.scalar(
            lead_base.where(ProductLead.status == "conversion_claimed")
        )
        or 0,
        "confirmed_conversions": db.scalar(
            lead_base.where(ProductLead.status == "confirmed")
        )
        or 0,
        "uninvoiced_amount_cents": db.scalar(amount_base) or 0,
        "billing_currency": db.scalar(
            select(Partner.billing_currency).where(Partner.id == member.partner_id)
        ),
    }


@router.get("/partner/products", response_model=list[AdminProductRead])
def partner_products(
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=200),
    member: PartnerMember = Depends(get_partner_member),
    db: Session = Depends(get_db),
) -> list[dict]:
    partner = db.get(Partner, member.partner_id)
    products = db.scalars(
        select(Product)
        .where(Product.partner_id == member.partner_id)
        .order_by(Product.id.desc())
        .offset(offset)
        .limit(limit)
    ).all()
    return [product_dict(product, partner.name) for product in products]


def _csv_boolean(value: str | None, *, default: bool = True) -> bool:
    normalized = (value or "").strip().casefold()
    if not normalized:
        return default
    if normalized in {"1", "true", "yes", "y", "да", "jā"}:
        return True
    if normalized in {"0", "false", "no", "n", "нет", "nē"}:
        return False
    raise ValueError("in_stock must be true or false")


@router.post("/partner/products/import/csv")
async def import_partner_products_csv(
    file: UploadFile = File(...),
    member: PartnerMember = Depends(get_partner_member),
    db: Session = Depends(get_db),
) -> dict[str, int]:
    """Atomically create or update partner products by SKU from a bounded UTF-8 CSV."""
    require_product_manager(member)
    if file.content_type not in {
        "text/csv",
        "application/csv",
        "application/vnd.ms-excel",
    }:
        raise HTTPException(415, "Поддерживается только CSV")
    raw = await file.read(2 * 1024 * 1024 + 1)
    if len(raw) > 2 * 1024 * 1024:
        raise HTTPException(413, "CSV-файл слишком большой")
    try:
        reader = csv.DictReader(StringIO(raw.decode("utf-8-sig")))
    except UnicodeDecodeError as exc:
        raise HTTPException(422, {"message": "CSV must be UTF-8"}) from exc
    required = {"sku", "name", "category", "product_url"}
    if not reader.fieldnames or not required.issubset(set(reader.fieldnames)):
        raise HTTPException(
            422, {"message": "CSV headers are missing", "required": sorted(required)}
        )

    payloads: list[PartnerProductCreate] = []
    for row_number, row in enumerate(reader, start=2):
        if row_number > 1_001:
            raise HTTPException(
                422, {"message": "CSV row limit exceeded", "limit": 1_000}
            )
        try:
            payload = PartnerProductCreate.model_validate(
                {
                    "sku": (row.get("sku") or "").strip(),
                    "name": (row.get("name") or "").strip(),
                    "category": (row.get("category") or "").strip(),
                    "description": (row.get("description") or "").strip() or None,
                    "product_url": (row.get("product_url") or "").strip(),
                    "image_url": (row.get("image_url") or "").strip() or None,
                    "price_cents": (row.get("price_cents") or "").strip() or None,
                    "currency": (row.get("currency") or "eur").strip(),
                    "in_stock": _csv_boolean(row.get("in_stock")),
                    "regions": [
                        value.strip()
                        for value in (row.get("regions") or "").split("|")
                        if value.strip()
                    ],
                }
            )
            if not payload.sku:
                raise ValueError("sku is required")
            payloads.append(payload)
        except (ValidationError, ValueError) as exc:
            if isinstance(exc, ValidationError):
                errors: Sequence[Mapping[str, Any]] = exc.errors(include_url=False)
            else:
                errors = [{"type": "value_error", "msg": str(exc)}]
            raise HTTPException(
                422,
                {
                    "message": "Invalid CSV row",
                    "row": row_number,
                    "errors": errors,
                },
            ) from exc
    if not payloads:
        raise HTTPException(422, {"message": "CSV contains no products"})

    skus = {payload.sku for payload in payloads}
    existing = {
        product.sku: product
        for product in db.scalars(
            select(Product).where(
                Product.partner_id == member.partner_id, Product.sku.in_(skus)
            )
        )
    }
    created = 0
    updated = 0
    for payload in payloads:
        values = payload.model_dump(mode="json")
        product = existing.get(payload.sku)
        if product is None:
            product = Product(
                partner_id=member.partner_id, **values, moderation_status="draft"
            )
            db.add(product)
            existing[payload.sku] = product
            created += 1
        else:
            for field, value in values.items():
                setattr(product, field, value)
            product.moderation_status = "draft"
            product.moderation_note = None
            product.moderated_at = None
            updated += 1
    db.add(
        AdminAuditLog(
            admin_user_id=member.user_id,
            action="partner_products.import_csv",
            target_type="partner",
            target_id=str(member.partner_id),
            details={"created": created, "updated": updated},
        )
    )
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, "CSV содержит повторяющийся SKU")
    return {"created": created, "updated": updated}


@router.post("/partner/products", response_model=AdminProductRead, status_code=201)
def create_partner_product(
    payload: PartnerProductCreate,
    member: PartnerMember = Depends(get_partner_member),
    db: Session = Depends(get_db),
) -> dict:
    require_product_manager(member)
    partner = db.get(Partner, member.partner_id)
    product = Product(
        partner_id=member.partner_id,
        **payload.model_dump(mode="json"),
        moderation_status="draft",
    )
    db.add(product)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, "SKU товара уже используется этим партнёром")
    db.refresh(product)
    return product_dict(product, partner.name)


@router.patch("/partner/products/{product_id}", response_model=AdminProductRead)
def update_partner_product(
    product_id: int,
    payload: ProductUpdate,
    member: PartnerMember = Depends(get_partner_member),
    db: Session = Depends(get_db),
) -> dict:
    require_product_manager(member)
    product = db.scalar(
        select(Product).where(
            Product.id == product_id, Product.partner_id == member.partner_id
        )
    )
    if not product:
        raise HTTPException(404, "Товар не найден")
    changes = payload.model_dump(exclude_unset=True, mode="json")
    if "partner_id" in changes:
        raise HTTPException(422, "Нельзя перенести товар в другую организацию")
    for field, value in changes.items():
        setattr(product, field, value)
    if changes.keys() & {
        "name",
        "sku",
        "category",
        "description",
        "product_url",
        "image_url",
        "price_cents",
        "currency",
        "in_stock",
        "regions",
    }:
        product.moderation_status = "draft"
        product.moderation_note = None
        product.moderated_at = None
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, "SKU товара уже используется этим партнёром")
    db.refresh(product)
    partner_name = db.scalar(
        select(Partner.name).where(Partner.id == member.partner_id)
    )
    return product_dict(product, partner_name)


@router.post("/partner/products/{product_id}/submit", response_model=AdminProductRead)
def submit_partner_product(
    product_id: int,
    member: PartnerMember = Depends(get_partner_member),
    db: Session = Depends(get_db),
) -> dict:
    require_product_manager(member)
    product = db.scalar(
        select(Product).where(
            Product.id == product_id, Product.partner_id == member.partner_id
        )
    )
    if not product:
        raise HTTPException(404, "Товар не найден")
    if product.moderation_status not in {"draft", "rejected"}:
        raise HTTPException(409, "Товар уже отправлен на модерацию")
    product.moderation_status = "pending"
    product.moderation_note = None
    db.commit()
    db.refresh(product)
    partner_name = db.scalar(
        select(Partner.name).where(Partner.id == member.partner_id)
    )
    return product_dict(product, partner_name)


@router.get("/partner/leads", response_model=list[PartnerLeadRead])
def partner_leads(
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=100, ge=1, le=200),
    member: PartnerMember = Depends(get_partner_member),
    db: Session = Depends(get_db),
) -> list[dict]:
    rows = db.execute(
        select(ProductLead, Product.name)
        .join(Product, Product.id == ProductLead.product_id)
        .where(Product.partner_id == member.partner_id)
        .order_by(ProductLead.created_at.desc())
        .offset(offset)
        .limit(limit)
    ).all()
    return [
        {
            "id": lead.id,
            "product_id": lead.product_id,
            "product_name": product_name,
            "source": lead.source,
            "status": lead.status,
            "partner_reference": lead.partner_reference,
            "conversion_value_cents": lead.conversion_value_cents,
            "billable_amount_cents": lead.billable_amount_cents,
            "claimed_at": lead.claimed_at,
            "confirmed_at": lead.confirmed_at,
            "created_at": lead.created_at,
        }
        for lead, product_name in rows
    ]


@router.post("/partner/leads/{lead_id}/conversion", response_model=PartnerLeadRead)
def claim_partner_conversion(
    lead_id: int,
    payload: PartnerConversionClaim,
    member: PartnerMember = Depends(get_partner_member),
    db: Session = Depends(get_db),
) -> dict:
    require_product_manager(member)
    row = db.execute(
        select(ProductLead, Product.name)
        .join(Product, Product.id == ProductLead.product_id)
        .where(ProductLead.id == lead_id, Product.partner_id == member.partner_id)
        .with_for_update()
    ).one_or_none()
    if not row:
        raise HTTPException(404, "Переход не найден")
    lead, product_name = row
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
        "product_name": product_name,
        "source": lead.source,
        "status": lead.status,
        "partner_reference": lead.partner_reference,
        "conversion_value_cents": lead.conversion_value_cents,
        "billable_amount_cents": lead.billable_amount_cents,
        "claimed_at": lead.claimed_at,
        "confirmed_at": lead.confirmed_at,
        "created_at": lead.created_at,
    }


@router.get("/partner/invoices", response_model=list[PartnerInvoiceRead])
def partner_invoices(
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=200),
    member: PartnerMember = Depends(get_partner_member),
    db: Session = Depends(get_db),
) -> list[PartnerInvoice]:
    require_b2b_invoicing()
    return list(
        db.scalars(
            select(PartnerInvoice)
            .where(PartnerInvoice.partner_id == member.partner_id)
            .order_by(PartnerInvoice.created_at.desc())
            .offset(offset)
            .limit(limit)
        )
    )


@router.get("/partner/invoices/{invoice_id}/pdf")
def partner_invoice_pdf(
    invoice_id: int,
    member: PartnerMember = Depends(get_partner_member),
    db: Session = Depends(get_db),
) -> Response:
    require_b2b_invoicing()
    invoice = db.scalar(
        select(PartnerInvoice).where(
            PartnerInvoice.id == invoice_id,
            PartnerInvoice.partner_id == member.partner_id,
        )
    )
    if not invoice:
        raise HTTPException(404, "Счёт не найден")
    return invoice_pdf_response(invoice)


@router.get("/partner/invoices/{invoice_id}/cancellation.pdf")
def partner_invoice_cancellation_pdf(
    invoice_id: int,
    member: PartnerMember = Depends(get_partner_member),
    db: Session = Depends(get_db),
) -> Response:
    require_b2b_invoicing()
    invoice = db.scalar(
        select(PartnerInvoice).where(
            PartnerInvoice.id == invoice_id,
            PartnerInvoice.partner_id == member.partner_id,
        )
    )
    if not invoice:
        raise HTTPException(404, "Счёт не найден")
    return invoice_cancellation_pdf_response(invoice)
