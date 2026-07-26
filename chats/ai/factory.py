from django.conf import settings

from .exceptions import AIProviderConfigurationError
from .mock import MockAIProvider
from .openrouter import OpenRouterProvider


def get_ai_provider(*, http_client=None):
    provider_name = str(getattr(settings, "AI_PROVIDER", "mock")).strip().lower()

    if provider_name == "mock":
        return MockAIProvider()
    if provider_name == "openrouter":
        return OpenRouterProvider(
            api_key=getattr(settings, "OPENROUTER_API_KEY", ""),
            model=getattr(settings, "OPENROUTER_MODEL", ""),
            base_url=getattr(
                settings,
                "OPENROUTER_BASE_URL",
                "https://openrouter.ai/api/v1",
            ),
            timeout_seconds=getattr(
                settings,
                "AI_REQUEST_TIMEOUT_SECONDS",
                "30",
            ),
            max_output_tokens=getattr(
                settings,
                "AI_MAX_OUTPUT_TOKENS",
                "512",
            ),
            temperature=getattr(settings, "AI_TEMPERATURE", "0.7"),
            site_url=getattr(settings, "OPENROUTER_SITE_URL", ""),
            app_name=getattr(settings, "OPENROUTER_APP_NAME", "NexaChat"),
            http_client=http_client,
        )

    raise AIProviderConfigurationError(
        f"Unsupported AI provider: {provider_name or '<empty>'}."
    )
