import os
import runpy
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from dotenv import load_dotenv as real_load_dotenv
from django.core.management import call_command
from django.test import SimpleTestCase, override_settings


SETTINGS_PATH = Path(__file__).resolve().parent / "settings.py"
ENVIRONMENT_SETTING_NAMES = (
    "DJANGO_SECRET_KEY",
    "DJANGO_DEBUG",
    "DJANGO_ALLOWED_HOSTS",
    "DJANGO_EMAIL_BACKEND",
    "DJANGO_DEFAULT_FROM_EMAIL",
    "PASSWORD_RESET_TOKEN_TTL_MINUTES",
    "AI_PROVIDER",
    "AI_REQUEST_TIMEOUT_SECONDS",
    "AI_MAX_OUTPUT_TOKENS",
    "AI_TEMPERATURE",
    "AI_CONTEXT_MESSAGE_LIMIT",
    "AI_SYSTEM_PROMPT",
    "OPENROUTER_API_KEY",
    "OPENROUTER_MODEL",
    "OPENROUTER_BASE_URL",
    "OPENROUTER_SITE_URL",
    "OPENROUTER_APP_NAME",
)


class SettingsEnvironmentLoadingTests(SimpleTestCase):
    def execute_settings(self, env_contents="", process_environment=None):
        process_environment = process_environment or {}

        with TemporaryDirectory() as temporary_directory:
            env_path = Path(temporary_directory) / ".env"
            env_path.write_text(env_contents, encoding="utf-8")

            with mock.patch.dict(os.environ, {}, clear=False):
                for name in ENVIRONMENT_SETTING_NAMES:
                    os.environ.pop(name, None)
                os.environ.update(process_environment)

                def load_temporary_env(_configured_path):
                    return real_load_dotenv(env_path)

                with mock.patch(
                    "dotenv.load_dotenv",
                    side_effect=load_temporary_env,
                ) as load_mock:
                    settings_values = runpy.run_path(str(SETTINGS_PATH))

                load_calls = list(load_mock.call_args_list)

        return settings_values, load_calls

    def test_root_env_is_loaded_before_ai_settings_are_consumed(self):
        temporary_key = "temporary-settings-test-credential"
        settings_values, load_calls = self.execute_settings(
            "\n".join(
                (
                    "AI_PROVIDER= OpenRouter ",
                    f"OPENROUTER_API_KEY= {temporary_key} ",
                    "OPENROUTER_MODEL= openrouter/free ",
                )
            )
        )

        self.assertEqual(settings_values["AI_PROVIDER"], "openrouter")
        self.assertTrue(bool(settings_values["OPENROUTER_API_KEY"]))
        self.assertEqual(
            settings_values["OPENROUTER_MODEL"],
            "openrouter/free",
        )
        self.assertEqual(len(load_calls), 1)
        self.assertEqual(
            load_calls[0].args[0],
            SETTINGS_PATH.parent.parent / ".env",
        )

    def test_process_environment_takes_precedence_over_env_file(self):
        settings_values, _load_calls = self.execute_settings(
            "\n".join(
                (
                    "AI_PROVIDER=openrouter",
                    "OPENROUTER_API_KEY=env-file-test-credential",
                    "OPENROUTER_MODEL=openrouter/free",
                )
            ),
            process_environment={
                "AI_PROVIDER": "mock",
                "OPENROUTER_API_KEY": "process-test-credential",
                "OPENROUTER_MODEL": "process/test-model",
            },
        )

        self.assertEqual(settings_values["AI_PROVIDER"], "mock")
        self.assertEqual(
            settings_values["OPENROUTER_API_KEY"],
            "process-test-credential",
        )
        self.assertEqual(
            settings_values["OPENROUTER_MODEL"],
            "process/test-model",
        )

    def test_mock_is_the_default_when_no_provider_is_configured(self):
        settings_values, _load_calls = self.execute_settings()

        self.assertEqual(settings_values["AI_PROVIDER"], "mock")
        self.assertEqual(settings_values["OPENROUTER_API_KEY"], "")
        self.assertEqual(settings_values["OPENROUTER_MODEL"], "")

    def test_loaded_openrouter_configuration_is_reported_without_secret(self):
        temporary_key = "temporary-command-test-credential"
        settings_values, _load_calls = self.execute_settings(
            "\n".join(
                (
                    "AI_PROVIDER=openrouter",
                    f"OPENROUTER_API_KEY={temporary_key}",
                    "OPENROUTER_MODEL=openrouter/free",
                )
            )
        )
        stdout = StringIO()

        with override_settings(
            AI_PROVIDER=settings_values["AI_PROVIDER"],
            OPENROUTER_API_KEY=settings_values["OPENROUTER_API_KEY"],
            OPENROUTER_MODEL=settings_values["OPENROUTER_MODEL"],
            OPENROUTER_BASE_URL=settings_values["OPENROUTER_BASE_URL"],
            AI_REQUEST_TIMEOUT_SECONDS=settings_values[
                "AI_REQUEST_TIMEOUT_SECONDS"
            ],
            AI_MAX_OUTPUT_TOKENS=settings_values["AI_MAX_OUTPUT_TOKENS"],
            AI_TEMPERATURE=settings_values["AI_TEMPERATURE"],
        ):
            with mock.patch("chats.ai.openrouter.httpx.Client") as client_mock:
                call_command("check_ai_config", stdout=stdout)

        output = stdout.getvalue()
        self.assertIn("Provider: openrouter", output)
        self.assertIn("OpenRouter API key configured: yes", output)
        self.assertIn("OpenRouter model configured: yes", output)
        self.assertIn("Ready: yes", output)
        self.assertNotIn(temporary_key, output)
        client_mock.assert_not_called()

