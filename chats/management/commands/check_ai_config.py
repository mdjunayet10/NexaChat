from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from chats.ai.exceptions import AIProviderConfigurationError
from chats.ai.factory import get_ai_provider


SUPPORTED_PROVIDER_NAMES = {"mock", "openrouter"}


def safe_provider_name():
    configured_name = str(getattr(settings, "AI_PROVIDER", "mock")).strip().lower()
    if configured_name in SUPPORTED_PROVIDER_NAMES:
        return configured_name
    return "unsupported"


def setting_is_configured(name):
    return bool(str(getattr(settings, name, "") or "").strip())


class Command(BaseCommand):
    help = "Validate the selected AI provider configuration without using the network."

    def handle(self, *args, **options):
        self.stdout.write(f"Provider: {safe_provider_name()}")
        self.stdout.write(
            "OpenRouter API key configured: "
            f"{'yes' if setting_is_configured('OPENROUTER_API_KEY') else 'no'}"
        )
        self.stdout.write(
            "OpenRouter model configured: "
            f"{'yes' if setting_is_configured('OPENROUTER_MODEL') else 'no'}"
        )

        try:
            get_ai_provider()
        except AIProviderConfigurationError:
            self.stdout.write("Ready: no")
            raise CommandError(
                "Selected AI provider configuration is invalid."
            ) from None

        self.stdout.write("Ready: yes")
