import logging

from fastapi import (
    Depends,
    HTTPException,
    Response,
)
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth import get_current_user
from app.catalog_taxonomy import (
    country_code_from_region,
)
from app.config import settings
from app.database import get_db
from app.models import (
    Partner,
    PartnerInvoice,
    PartnerMember,
    Product,
    ProductLead,
    User,
)
from app.services.invoice_service import (
    InvoiceDocumentError,
    load_verified_invoice_cancellation_pdf,
    load_verified_invoice_pdf,
)
from app.services.partner_attribution_service import (
    sign_click_id,
)

logger = logging.getLogger(__name__)

def require_b2b_invoicing() -> None:
    if not settings.b2b_invoicing_enabled:
        raise HTTPException(404, "Модуль B2B-счетов отключён")


def product_dict(product: Product, partner_name: str) -> dict:
    return {
        "id": product.id,
        "partner_id": product.partner_id,
        "name": product.name,
        "sku": product.sku,
        "category": product.category,
        "description": product.description,
        "product_url": product.product_url,
        "image_url": product.image_url,
        "price_cents": product.price_cents,
        "currency": product.currency,
        "in_stock": product.in_stock,
        "regions": product.regions or [],
        "partner_name": partner_name,
        "active": product.active,
        "moderation_status": product.moderation_status,
        "created_at": product.created_at,
        "commercial_label": "Реклама партнёра",
    }


def lead_dict(lead: ProductLead) -> dict:
    if not lead.click_id:
        raise HTTPException(409, "Для старого перехода отсутствует click ID")
    return {
        "id": lead.id,
        "product_id": lead.product_id,
        "diagnosis_id": lead.diagnosis_id,
        "source": lead.source,
        "status": lead.status,
        "redirect_url": f"/product/go/{sign_click_id(lead.click_id)}",
        "created_at": lead.created_at,
    }


def product_matches_region(product: Product, region: str | None) -> bool:
    if not product.regions or not region:
        return True
    normalized = region.casefold()
    region_country = country_code_from_region(region)
    return any(
        (
            region_country is not None
            and country_code_from_region(item) == region_country
        )
        or item.casefold() in normalized
        or normalized in item.casefold()
        for item in product.regions
    )


def member_dict(member: PartnerMember, partner: Partner, user: User) -> dict:
    return {
        "id": member.id,
        "partner_id": partner.id,
        "partner_name": partner.name,
        "user_id": user.id,
        "email": user.email,
        "name": user.name,
        "role": member.role,
        "active": member.active,
        "created_at": member.created_at,
    }


def get_partner_member(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> PartnerMember:
    member = db.scalar(
        select(PartnerMember)
        .join(Partner)
        .where(
            PartnerMember.user_id == user.id,
            PartnerMember.active.is_(True),
            Partner.active.is_(True),
        )
    )
    if not member:
        raise HTTPException(403, "Нет доступа к кабинету партнёра")
    return member


def require_product_manager(member: PartnerMember) -> None:
    if member.role not in {"owner", "editor"}:
        raise HTTPException(403, "Недостаточно прав для управления товарами")


def invoice_pdf_response(invoice: PartnerInvoice) -> Response:
    try:
        document = load_verified_invoice_pdf(invoice, settings)
    except InvoiceDocumentError as exc:
        raise HTTPException(409, str(exc)) from exc
    return Response(
        content=document,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'attachment; filename="{invoice.invoice_number}.pdf"',
            "Cache-Control": "private, no-store",
            "ETag": f'"{invoice.pdf_sha256}"',
            "X-Content-Type-Options": "nosniff",
        },
    )


def invoice_cancellation_pdf_response(invoice: PartnerInvoice) -> Response:
    try:
        document = load_verified_invoice_cancellation_pdf(invoice, settings)
    except InvoiceDocumentError as exc:
        status_code = 404 if not invoice.cancellation_number else 409
        raise HTTPException(status_code, str(exc)) from exc
    return Response(
        content=document,
        media_type="application/pdf",
        headers={
            "Content-Disposition": (
                f'attachment; filename="{invoice.cancellation_number}.pdf"'
            ),
            "Cache-Control": "private, no-store",
            "ETag": f'"{invoice.cancellation_pdf_sha256}"',
            "X-Content-Type-Options": "nosniff",
        },
    )
