from __future__ import annotations

import secrets
from datetime import UTC, datetime

from sqlalchemy import select

from app.auth import hash_password
from app.config import settings
from app.database import SessionLocal
from app.models import Partner, PartnerMember, User

DEMO_ACCOUNTS = (
    ("demo@ai-garden.local", "Demo Gardener", False),
    ("partner@ai-garden.local", "Demo Partner", False),
    ("admin@ai-garden.local", "Demo Administrator", True),
)


def seed_demo_users() -> dict[str, str]:
    """Создаёт демо-пользователей со случайными паролями.

    Возвращает словарь email → password для вывода в консоль.
    """
    if settings.environment == "production":
        raise RuntimeError("Demo users must never be seeded in production")

    now = datetime.now(UTC)
    passwords: dict[str, str] = {}
    with SessionLocal() as db:
        users: dict[str, User] = {}
        for email, name, is_admin in DEMO_ACCOUNTS:
            password = secrets.token_urlsafe(16)
            passwords[email] = password
            user = db.scalar(select(User).where(User.email == email))
            if user is None:
                user = User(email=email, name=name, password_hash="")
                db.add(user)
            user.name = name
            user.password_hash = hash_password(password)
            user.language = "ru"
            user.region = "Riga"
            user.is_admin = is_admin
            user.is_blocked = False
            user.email_verified_at = now
            users[email] = user
        db.flush()

        partner = db.scalar(select(Partner).where(Partner.name == "Demo Garden Center"))
        if partner is None:
            partner = Partner(
                name="Demo Garden Center",
                website_url="https://example.local",
            )
            db.add(partner)
        partner.legal_name = "Demo Garden Center SIA"
        partner.registration_number = "DEMO-001"
        partner.billing_address = "Riga"
        partner.billing_email = "partner@ai-garden.local"
        partner.active = True
        db.flush()

        partner_user = users["partner@ai-garden.local"]
        membership = db.scalar(
            select(PartnerMember).where(PartnerMember.user_id == partner_user.id)
        )
        if membership is None:
            membership = PartnerMember(
                partner_id=partner.id,
                user_id=partner_user.id,
                role="owner",
            )
            db.add(membership)
        else:
            membership.partner_id = partner.id
            membership.role = "owner"
        membership.active = True
        db.commit()
    return passwords


def main() -> int:
    passwords = seed_demo_users()
    for email, _name, is_admin in DEMO_ACCOUNTS:
        role = (
            "admin"
            if is_admin
            else ("partner" if email.startswith("partner") else "gardener")
        )
        print(f"{role}: {email} / {passwords[email]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
