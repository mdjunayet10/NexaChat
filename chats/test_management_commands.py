from decimal import Decimal
from io import StringIO
from unittest import mock

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import SimpleTestCase, TestCase, override_settings

from chats.ai.base import ChatCompletionResult, ProviderMessage
from chats.ai.exceptions import (
    AIProviderConfigurationError,
    AIProviderTimeoutError,
    AIProviderUnavailableError,
)
from chats.ai.mock import MockAIProvider
from chats.models import AIUsage, ChatMessage


VALID_OPENROUTER_SETTINGS = {
    "AI_PROVIDER": "openrouter",
    "OPENROUTER_API_KEY": "private-command-test-key",
    "OPENROUTER_MODEL": "openrouter/free",
    "OPENROUTER_BASE_URL": "https://openrouter.test/api/v1",
    "AI_REQUEST_TIMEOUT_SECONDS": "30",
    "AI_MAX_OUTPUT_TOKENS": "512",
    "AI_TEMPERATURE": "0.7",
}


def provider_result(**overrides):
    values = {
        "content": "Private provider response body.",
        "model_name": "provider/test-model",
        "prompt_tokens": 7,
        "completion_tokens": 3,
        "total_tokens": 10,
        "cost": Decimal("0.000000"),
        "latency_ms": 125,
    }
    values.update(overrides)
    return ChatCompletionResult(**values)


class CheckAIConfigCommandTests(SimpleTestCase):
    @override_settings(
        AI_PROVIDER="mock",
        OPENROUTER_API_KEY="",
        OPENROUTER_MODEL="",
    )
    def test_mock_configuration_is_ready_without_openrouter_settings(self):
        stdout = StringIO()

        call_command("check_ai_config", stdout=stdout)

        output = stdout.getvalue()
        self.assertIn("Provider: mock", output)
        self.assertIn("OpenRouter API key configured: no", output)
        self.assertIn("OpenRouter model configured: no", output)
        self.assertIn("Ready: yes", output)

    @override_settings(**VALID_OPENROUTER_SETTINGS)
    @mock.patch("chats.ai.openrouter.httpx.Client")
    @mock.patch("chats.ai.openrouter.OpenRouterProvider.complete")
    def test_openrouter_check_is_offline_and_never_prints_the_key(
        self,
        complete_mock,
        client_mock,
    ):
        stdout = StringIO()

        call_command("check_ai_config", stdout=stdout)

        output = stdout.getvalue()
        self.assertIn("Provider: openrouter", output)
        self.assertIn("OpenRouter API key configured: yes", output)
        self.assertIn("OpenRouter model configured: yes", output)
        self.assertIn("Ready: yes", output)
        self.assertNotIn(VALID_OPENROUTER_SETTINGS["OPENROUTER_API_KEY"], output)
        complete_mock.assert_not_called()
        client_mock.assert_not_called()

    @override_settings(
        **{
            **VALID_OPENROUTER_SETTINGS,
            "OPENROUTER_API_KEY": "",
        }
    )
    def test_invalid_selected_provider_configuration_returns_nonzero(self):
        stdout = StringIO()

        with self.assertRaises(CommandError) as captured:
            call_command("check_ai_config", stdout=stdout)

        self.assertIn("OpenRouter API key configured: no", stdout.getvalue())
        self.assertIn("Ready: no", stdout.getvalue())
        self.assertEqual(
            str(captured.exception),
            "Selected AI provider configuration is invalid.",
        )

    @override_settings(
        AI_PROVIDER="unexpected-provider",
        OPENROUTER_API_KEY="private-command-test-key",
        OPENROUTER_MODEL="openrouter/free",
    )
    def test_unsupported_provider_name_is_reported_safely(self):
        stdout = StringIO()

        with self.assertRaises(CommandError):
            call_command("check_ai_config", stdout=stdout)

        output = stdout.getvalue()
        self.assertIn("Provider: unsupported", output)
        self.assertIn("Ready: no", output)
        self.assertNotIn("unexpected-provider", output)
        self.assertNotIn("private-command-test-key", output)


class TestAIProviderCommandTests(SimpleTestCase):
    @override_settings(**VALID_OPENROUTER_SETTINGS)
    @mock.patch("chats.ai.openrouter.httpx.Client")
    @mock.patch("chats.ai.openrouter.OpenRouterProvider.complete")
    def test_default_mode_never_calls_the_provider(
        self,
        complete_mock,
        client_mock,
    ):
        stdout = StringIO()

        call_command(
            "test_ai_provider",
            "--message",
            "This must not be sent.",
            stdout=stdout,
        )

        output = stdout.getvalue()
        self.assertIn("Provider: openrouter", output)
        self.assertIn("Mode: configuration only", output)
        self.assertIn("Provider request sent: no", output)
        self.assertNotIn("This must not be sent.", output)
        self.assertNotIn(VALID_OPENROUTER_SETTINGS["OPENROUTER_API_KEY"], output)
        complete_mock.assert_not_called()
        client_mock.assert_not_called()

    @override_settings(
        **{
            **VALID_OPENROUTER_SETTINGS,
            "OPENROUTER_MODEL": "",
        }
    )
    def test_configuration_error_returns_nonzero(self):
        with self.assertRaises(CommandError) as captured:
            call_command("test_ai_provider", stdout=StringIO())

        self.assertEqual(
            str(captured.exception),
            "AI provider configuration is invalid.",
        )

    @override_settings(
        AI_PROVIDER="mock",
        AI_SYSTEM_PROMPT="Safe system prompt.",
    )
    @mock.patch("chats.management.commands.test_ai_provider.get_ai_provider")
    def test_live_mode_uses_provider_abstraction_and_safe_metadata(
        self,
        factory_mock,
    ):
        provider = mock.Mock()
        provider.complete.return_value = provider_result()
        factory_mock.return_value = provider
        stdout = StringIO()

        call_command(
            "test_ai_provider",
            "--live",
            "--message",
            "Focused health check.",
            stdout=stdout,
        )

        provider.complete.assert_called_once_with(
            messages=(
                ProviderMessage(role="user", content="Focused health check."),
            ),
            system_prompt="Safe system prompt.",
        )
        output = stdout.getvalue()
        self.assertIn("Mode: live", output)
        self.assertIn("Response received: yes", output)
        self.assertIn("Response characters: 31", output)
        self.assertIn("Prompt tokens: 7", output)
        self.assertIn("Completion tokens: 3", output)
        self.assertIn("Total tokens: 10", output)
        self.assertIn("Latency milliseconds: 125", output)
        self.assertNotIn("Private provider response body.", output)
        self.assertNotIn("provider/test-model", output)
        self.assertNotIn("Focused health check.", output)

    @override_settings(AI_PROVIDER="mock")
    @mock.patch("chats.management.commands.test_ai_provider.get_ai_provider")
    def test_timeout_error_returns_nonzero(self, factory_mock):
        provider = mock.Mock()
        provider.complete.side_effect = AIProviderTimeoutError("private detail")
        factory_mock.return_value = provider

        with self.assertRaises(CommandError) as captured:
            call_command("test_ai_provider", "--live", stdout=StringIO())

        self.assertEqual(str(captured.exception), "AI provider test timed out.")
        self.assertNotIn("private detail", str(captured.exception))

    @override_settings(AI_PROVIDER="mock")
    @mock.patch("chats.management.commands.test_ai_provider.get_ai_provider")
    def test_provider_error_returns_nonzero(self, factory_mock):
        provider = mock.Mock()
        provider.complete.side_effect = AIProviderUnavailableError(
            "private detail"
        )
        factory_mock.return_value = provider

        with self.assertRaises(CommandError) as captured:
            call_command("test_ai_provider", "--live", stdout=StringIO())

        self.assertEqual(
            str(captured.exception),
            "AI provider test failed safely.",
        )
        self.assertNotIn("private detail", str(captured.exception))

    @override_settings(AI_PROVIDER="mock", AI_SYSTEM_PROMPT="")
    @mock.patch("chats.management.commands.test_ai_provider.get_ai_provider")
    def test_completion_configuration_error_returns_nonzero(self, factory_mock):
        provider = mock.Mock()
        provider.complete.side_effect = AIProviderConfigurationError(
            "private detail"
        )
        factory_mock.return_value = provider

        with self.assertRaises(CommandError) as captured:
            call_command("test_ai_provider", "--live", stdout=StringIO())

        self.assertEqual(
            str(captured.exception),
            "AI provider configuration is invalid.",
        )
        self.assertNotIn("private detail", str(captured.exception))


class TestAIProviderDatabaseIsolationTests(TestCase):
    @override_settings(
        AI_PROVIDER="mock",
        AI_SYSTEM_PROMPT="Safe system prompt.",
    )
    def test_live_mock_check_creates_no_chat_or_usage_records(self):
        self.assertEqual(ChatMessage.objects.count(), 0)
        self.assertEqual(AIUsage.objects.count(), 0)

        call_command(
            "test_ai_provider",
            "--live",
            "--message",
            "Database isolation check.",
            stdout=StringIO(),
        )

        self.assertEqual(ChatMessage.objects.count(), 0)
        self.assertEqual(AIUsage.objects.count(), 0)

