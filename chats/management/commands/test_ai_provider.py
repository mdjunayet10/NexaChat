from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from chats.ai.base import ProviderMessage
from chats.ai.exceptions import (
    AIProviderConfigurationError,
    AIProviderError,
    AIProviderTimeoutError,
)
from chats.ai.factory import get_ai_provider

from .check_ai_config import safe_provider_name


DEFAULT_TEST_MESSAGE = "Confirm that the NexaChat AI provider is available."


class Command(BaseCommand):
    help = (
        "Validate the AI provider without a request by default, or explicitly "
        "send one with --live."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--live",
            action="store_true",
            help="Explicitly allow one provider completion request.",
        )
        parser.add_argument(
            "--message",
            default=DEFAULT_TEST_MESSAGE,
            help="Optional test message used only with --live.",
        )

    def handle(self, *args, **options):
        self.stdout.write(f"Provider: {safe_provider_name()}")

        try:
            provider = get_ai_provider()
        except AIProviderConfigurationError:
            raise CommandError(
                "AI provider configuration is invalid."
            ) from None

        if not options["live"]:
            self.stdout.write("Mode: configuration only")
            self.stdout.write("Provider request sent: no")
            self.stdout.write("Ready for an explicit live test: yes")
            return

        message = str(options["message"] or "").strip()
        if not message:
            raise CommandError("The live test message must not be empty.")

        try:
            result = provider.complete(
                messages=(ProviderMessage(role="user", content=message),),
                system_prompt=str(getattr(settings, "AI_SYSTEM_PROMPT", "")),
            )
        except AIProviderTimeoutError:
            raise CommandError("AI provider test timed out.") from None
        except AIProviderConfigurationError:
            raise CommandError(
                "AI provider configuration is invalid."
            ) from None
        except AIProviderError:
            raise CommandError("AI provider test failed safely.") from None

        self.stdout.write("Mode: live")
        self.stdout.write("Provider request sent: yes")
        self.stdout.write("Response received: yes")
        self.stdout.write(f"Response characters: {len(result.content)}")
        self.stdout.write(f"Prompt tokens: {result.prompt_tokens}")
        self.stdout.write(f"Completion tokens: {result.completion_tokens}")
        self.stdout.write(f"Total tokens: {result.total_tokens}")
        if result.latency_ms is None:
            self.stdout.write("Latency milliseconds: unavailable")
        else:
            self.stdout.write(f"Latency milliseconds: {result.latency_ms}")
