import logging
from datetime import UTC, datetime
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.auth import (
    create_token_pair,
    hash_password,
    revoke_refresh_token,
    rotate_refresh_token,
    verify_password,
)
from app.config import settings
from app.database import get_db
from app.models import AuthSession, User
from app.schemas import (
    ActionTokenRequest,
    EmailVerificationRequest,
    LoginRequest,
    PasswordResetConfirm,
    RefreshTokenRequest,
    TokenRead,
    UserRegister,
)
from app.security import secure_transport_enabled
from app.services.account_security_service import (
    consume_action_token,
    issue_action_token,
    send_action_email,
)
from app.services.demo_seed import ensure_demo_workspace
from app.services.rate_limit_service import enforce_auth_rate_limit

router = APIRouter(prefix="/api/v1/auth")
logger = logging.getLogger(__name__)

DEMO_USER_EMAIL = "demo@ai-garden.local"


def _set_refresh_cookie(response: Response, refresh_token: str) -> None:
    response.set_cookie(
        "ai_garden_refresh",
        refresh_token,
        max_age=settings.refresh_token_expire_days * 86400,
        httponly=True,
        secure=secure_transport_enabled(),
        samesite="lax",
        path="/api/v1/auth",
    )


@router.post("/register", response_model=TokenRead, status_code=201)
def register(
    payload: UserRegister,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
) -> TokenRead:
    email = payload.email.strip().lower()
    enforce_auth_rate_limit(
        "register", f"{request.client.host if request.client else 'unknown'}:{email}"
    )
    if db.scalar(select(User.id).where(func.lower(User.email) == email)):
        raise HTTPException(409, "Пользователь с таким email уже зарегистрирован")
    user = User(
        email=email,
        name=payload.name,
        password_hash=hash_password(payload.password),
        language=payload.language,
        region=payload.region,
        email_verified_at=(datetime.now(UTC) if settings.environment == "test" else None),
    )
    db.add(user)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, "Пользователь с таким email уже зарегистрирован")
    db.refresh(user)
    pair = create_token_pair(user, db)
    _set_refresh_cookie(response, pair.refresh_token)
    if user.email_verified_at is None:
        pair.email_verification_required = True
        token = issue_action_token(db, user, "verify_email")
        try:
            send_action_email(user.email, "verify_email", token)
        except Exception:
            # The account is already committed. Keep registration idempotent and
            # let the user request a new link when the mail provider recovers.
            logger.exception("verification email delivery failed", extra={"user_id": user.id})
    return pair


@router.post("/login", response_model=TokenRead)
def login(
    payload: LoginRequest,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
) -> TokenRead:
    enforce_auth_rate_limit(
        "login", f"{request.client.host if request.client else 'unknown'}:{payload.email}"
    )
    user = db.scalar(select(User).where(func.lower(User.email) == payload.email.strip().lower()))
    if not user or user.is_blocked or not verify_password(payload.password, user.password_hash):
        raise HTTPException(401, "Неверный email или пароль")
    if user.email_verified_at is None:
        raise HTTPException(403, "Подтвердите email перед входом")
    pair = create_token_pair(user, db)
    _set_refresh_cookie(response, pair.refresh_token)
    return pair


@router.post("/demo-login", response_model=TokenRead)
def demo_login(
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
) -> TokenRead:
    if not settings.demo_login_enabled or settings.environment.lower() == "production":
        raise HTTPException(404, "Демонстрационный вход недоступен")
    client_host = request.client.host if request.client else "unknown"
    enforce_auth_rate_limit("login", f"{client_host}:{DEMO_USER_EMAIL}")
    user = db.scalar(select(User).where(func.lower(User.email) == DEMO_USER_EMAIL))
    if user and user.is_blocked:
        raise HTTPException(403, "Демо-аккаунт заблокирован")
    if not user:
        # Пароль случайный и нигде не используется: вход возможен только через этот эндпоинт
        user = User(
            email=DEMO_USER_EMAIL,
            name="Демо-пользователь",
            password_hash=hash_password(uuid4().hex),
            language="ru",
            email_verified_at=datetime.now(UTC),
        )
        db.add(user)
        db.flush()
    ensure_demo_workspace(db, user)
    db.commit()
    db.refresh(user)
    pair = create_token_pair(user, db)
    _set_refresh_cookie(response, pair.refresh_token)
    return pair


@router.post("/refresh", response_model=TokenRead)
def refresh_session(
    request: Request,
    response: Response,
    payload: RefreshTokenRequest | None = None,
    db: Session = Depends(get_db),
) -> TokenRead:
    refresh_token = payload.refresh_token if payload else request.cookies.get("ai_garden_refresh")
    if not refresh_token:
        raise HTTPException(401, "Refresh-токен недействителен")
    pair = rotate_refresh_token(refresh_token, db)
    _set_refresh_cookie(response, pair.refresh_token)
    return pair


@router.post("/logout", status_code=204)
def logout_session(
    request: Request,
    response: Response,
    payload: RefreshTokenRequest | None = None,
    db: Session = Depends(get_db),
) -> Response:
    refresh_token = payload.refresh_token if payload else request.cookies.get("ai_garden_refresh")
    if refresh_token:
        revoke_refresh_token(refresh_token, db)
    response.delete_cookie("ai_garden_refresh", path="/api/v1/auth")
    response.status_code = 204
    return response


@router.post("/email-verification/request")
def request_email_verification(
    payload: EmailVerificationRequest,
    request: Request,
    db: Session = Depends(get_db),
) -> dict:
    enforce_auth_rate_limit(
        "verify_email", f"{request.client.host if request.client else 'unknown'}:{payload.email}"
    )
    user = db.scalar(select(User).where(func.lower(User.email) == payload.email.strip().lower()))
    if user and user.email_verified_at is None and not user.is_blocked:
        token = issue_action_token(db, user, "verify_email")
        try:
            send_action_email(user.email, "verify_email", token)
        except Exception:
            logger.exception("verification email delivery failed", extra={"user_id": user.id})
    return {"accepted": True}


@router.post("/email-verification/confirm")
def confirm_email_verification(payload: ActionTokenRequest, db: Session = Depends(get_db)) -> dict:
    user = consume_action_token(db, payload.token, "verify_email")
    if not user:
        raise HTTPException(400, "Ссылка подтверждения недействительна или устарела")
    user.email_verified_at = datetime.now(UTC)
    db.commit()
    return {"verified": True}


@router.post("/password-reset/request", status_code=202)
def request_password_reset(
    payload: EmailVerificationRequest,
    request: Request,
    db: Session = Depends(get_db),
) -> dict:
    enforce_auth_rate_limit(
        "password_reset", f"{request.client.host if request.client else 'unknown'}:{payload.email}"
    )
    user = db.scalar(select(User).where(func.lower(User.email) == payload.email.strip().lower()))
    if user and not user.is_blocked:
        token = issue_action_token(db, user, "password_reset")
        try:
            send_action_email(user.email, "password_reset", token)
        except Exception:
            logger.exception("password reset email delivery failed", extra={"user_id": user.id})
    return {"accepted": True}


@router.post("/password-reset/confirm")
def confirm_password_reset(payload: PasswordResetConfirm, db: Session = Depends(get_db)) -> dict:
    user = consume_action_token(db, payload.token, "password_reset")
    if not user:
        raise HTTPException(400, "Ссылка восстановления недействительна или устарела")
    user.password_hash = hash_password(payload.new_password)
    db.query(AuthSession).filter(
        AuthSession.user_id == user.id, AuthSession.revoked_at.is_(None)
    ).update({AuthSession.revoked_at: datetime.now(UTC)}, synchronize_session=False)
    db.commit()
    return {"password_reset": True}
