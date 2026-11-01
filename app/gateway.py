"""Точка создания AI-шлюза HTTP-приложения.

Синглтон живёт в отдельном модуле, а не в ``app.main``: все потребители обращаются
через атрибут ``gateway.ai_gateway``, поэтому подмена шлюза в тестах действует
одновременно для эндпоинтов диагностики и health.
"""

from app.ai import create_ai_gateway
from app.config import settings

ai_gateway = create_ai_gateway(settings.ai_provider)
