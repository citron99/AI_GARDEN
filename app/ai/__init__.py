from app.ai.base import AIGateway, AIProviderError
from app.config import settings


def create_ai_gateway(provider: str) -> AIGateway:
    normalized = provider.strip().lower()
    gateway: AIGateway
    if normalized == "mock":
        from app.ai.providers.mock import MockAIGateway

        gateway = MockAIGateway()
    elif normalized == "openai":
        from app.ai.providers.openai_multimodal import OpenAIMultimodalGateway

        if not settings.openai_api_key:
            raise RuntimeError("OPENAI_API_KEY обязателен для AI_PROVIDER=openai")
        gateway = OpenAIMultimodalGateway(
            api_key=settings.openai_api_key,
            model=settings.openai_model,
            timeout_seconds=settings.openai_timeout_seconds,
        )
    elif normalized == "gemini":
        from app.ai.providers.gemini import GeminiMultimodalGateway

        if not settings.gemini_api_key:
            raise RuntimeError("GEMINI_API_KEY обязателен для AI_PROVIDER=gemini")
        gateway = GeminiMultimodalGateway(
            api_key=settings.gemini_api_key,
            model=settings.gemini_model,
            api_base=settings.gemini_api_base,
            timeout_seconds=settings.gemini_timeout_seconds,
        )
    elif normalized == "deepseek":
        from app.ai.providers.deepseek import DeepSeekGateway

        if not settings.deepseek_api_key:
            raise RuntimeError("DEEPSEEK_API_KEY обязателен для AI_PROVIDER=deepseek")
        gateway = DeepSeekGateway(
            api_key=settings.deepseek_api_key,
            model=settings.deepseek_model,
            api_base=settings.deepseek_api_base,
            timeout_seconds=settings.deepseek_timeout_seconds,
            verify_ssl=settings.deepseek_verify_ssl,
        )
    else:
        raise RuntimeError(f"AI_PROVIDER={provider!r} не поддерживается")

    # Резервный текстовый провайдер: включается автоматически, когда задан его ключ
    if settings.deepseek_api_key and normalized in {"openai", "gemini"}:
        from app.ai.providers.deepseek import DeepSeekGateway
        from app.ai.providers.fallback import FallbackAIGateway

        gateway = FallbackAIGateway(
            gateway,
            DeepSeekGateway(
                api_key=settings.deepseek_api_key,
                model=settings.deepseek_model,
                api_base=settings.deepseek_api_base,
                timeout_seconds=settings.deepseek_timeout_seconds,
                verify_ssl=settings.deepseek_verify_ssl,
            ),
        )
    return gateway


__all__ = ["AIGateway", "AIProviderError", "create_ai_gateway"]
