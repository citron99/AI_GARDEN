import hashlib
import hmac

from app.config import settings


def safety_identifier_for_user(user_id: int) -> str:
    digest = hmac.new(
        settings.ai_safety_secret.encode("utf-8"),
        str(user_id).encode("ascii"),
        hashlib.sha256,
    ).hexdigest()
    return f"user_{digest}"