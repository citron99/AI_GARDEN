from app.config import settings


def secure_transport_enabled() -> bool:
    return (
        settings.environment.lower() == "production"
        or settings.public_base_url.lower().startswith("https://")
    )
