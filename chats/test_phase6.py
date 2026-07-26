import json
import os
import uuid
from decimal import Decimal
from unittest import mock

import httpx
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import connection
from django.test import (
    SimpleTestCase,
    TestCase,
    TransactionTestCase,
    override_settings,
)
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase
from rest_framework_simplejwt.tokens import RefreshToken

from .ai.base import ChatCompletionResult, ProviderMessage
from .ai.context import build_conversation_context
from .ai.exceptions import (
    AIProviderAuthenticationError,
    AIProviderConfigurationError,
    AIProviderRateLimitError,
    AIProviderResponseError,
    AIProviderTimeoutError,
    AIProviderUnavailableError,
)
from .ai.factory import get_ai_provider
from .ai.mock import MockAIProvider
from .ai.openrouter import OpenRouterProvider
from .models import AIUsage, ChatMessage, ChatSession
from .services import (
    ChatProviderFailure,
    ChatResponsePersistenceError,
    ChatSessionOwnershipError,
    process_chat_message,
    record_ai_usage,
)


def completion_result(**overrides):
    values = {
        "content": "A deterministic assistant answer.",
        "model_name": "test-provider-model",
        "prompt_tokens": 12,
        "completion_tokens": 5,
        "total_tokens": 17,
        "cost": Decimal("0.123456"),
        "latency_ms": 240,
    }
    values.update(overrides)
    return ChatCompletionResult(**values)


class RecordingProvider:
    def __init__(self, result=None, error=None):
        self.result = result or completion_result()
        self.error = error
        self.calls = []

    def complete(self, *, messages, system_prompt):
        self.calls.append(
            {
                "messages": tuple(messages),
                "system_prompt": system_prompt,
                "in_atomic_block": connection.in_atomic_block,
            }
        )
        if self.error is not None:
            raise self.error
        return self.result


class StubResponse:
    def __init__(self, status_code=200, data=None, json_error=None):
        self.status_code = status_code
        self.data = data
        self.json_error = json_error

    def json(self):
        if self.json_error is not None:
            raise self.json_error
        return self.data


class StubHTTPClient:
    def __init__(self, response=None, error=None):
        self.response = response
        self.error = error
        self.calls = []
        self.closed = False

    def post(self, url, **kwargs):
        self.calls.append({"url": url, **kwargs})
        if self.error is not None:
            raise self.error
        return self.response

    def close(self):
        self.closed = True


class MockAIProviderTests(SimpleTestCase):
    def setUp(self):
        self.provider = MockAIProvider()
        self.messages = (
            ProviderMessage(role="user", content="First question"),
            ProviderMessage(role="assistant", content="Earlier answer"),
            ProviderMessage(role="user", content="Latest question"),
        )
        self.system_prompt = "Be helpful."

    @mock.patch("chats.ai.openrouter.httpx.Client")
    def test_mock_provider_is_offline_and_deterministic(self, client_mock):
        first = self.provider.complete(
            messages=self.messages,
            system_prompt=self.system_prompt,
        )
        second = self.provider.complete(
            messages=self.messages,
            system_prompt=self.system_prompt,
        )

        client_mock.assert_not_called()
        self.assertEqual(first, second)
        self.assertEqual(
            first.content,
            "Mock NexaChat response: Latest question",
        )

    def test_mock_provider_returns_consistent_usage_and_model(self):
        result = self.provider.complete(
            messages=self.messages,
            system_prompt=self.system_prompt,
        )

        self.assertEqual(result.model_name, "mock-nexachat-v1")
        self.assertIsInstance(result.cost, Decimal)
        self.assertEqual(result.cost, Decimal("0.000000"))
        self.assertGreaterEqual(result.prompt_tokens, 0)
        self.assertGreaterEqual(result.completion_tokens, 0)
        self.assertEqual(
            result.total_tokens,
            result.prompt_tokens + result.completion_tokens,
        )

    def test_mock_provider_accepts_user_and_assistant_history(self):
        result = self.provider.complete(
            messages=self.messages,
            system_prompt=self.system_prompt,
        )

        self.assertIn("Latest question", result.content)
        self.assertNotIn("Earlier answer", result.content)

    def test_mock_provider_rejects_history_without_a_usable_user_message(self):
        with self.assertRaises(AIProviderResponseError):
            self.provider.complete(
                messages=(
                    ProviderMessage(role="assistant", content="Only an answer"),
                ),
                system_prompt=self.system_prompt,
            )

        with self.assertRaises(AIProviderResponseError):
            self.provider.complete(messages=(), system_prompt=self.system_prompt)

    def test_mock_provider_does_not_read_environment_secrets(self):
        secret = "should-never-appear-in-mock-output"
        with mock.patch.dict(
            os.environ,
            {"OPENROUTER_API_KEY": secret},
            clear=False,
        ):
            result = self.provider.complete(
                messages=(ProviderMessage(role="user", content="Hello"),),
                system_prompt=self.system_prompt,
            )

        self.assertNotIn(secret, result.content)


class ProviderFactoryTests(SimpleTestCase):
    @override_settings(AI_PROVIDER="mock", OPENROUTER_API_KEY="", OPENROUTER_MODEL="")
    def test_mock_is_available_without_openrouter_configuration(self):
        self.assertIsInstance(get_ai_provider(), MockAIProvider)

    @override_settings(AI_PROVIDER="MoCk")
    def test_provider_selection_is_case_insensitive(self):
        self.assertIsInstance(get_ai_provider(), MockAIProvider)

    @override_settings(
        AI_PROVIDER="openrouter",
        OPENROUTER_API_KEY="test-key",
        OPENROUTER_MODEL="test/model",
        OPENROUTER_BASE_URL="https://openrouter.test/api/v1",
        AI_REQUEST_TIMEOUT_SECONDS="15",
        AI_MAX_OUTPUT_TOKENS="256",
        AI_TEMPERATURE="0.5",
    )
    def test_openrouter_configuration_returns_openrouter_provider(self):
        provider = get_ai_provider(http_client=StubHTTPClient())

        self.assertIsInstance(provider, OpenRouterProvider)
        self.assertEqual(provider.model, "test/model")
        self.assertEqual(provider.timeout_seconds, 15)

    @override_settings(AI_PROVIDER="unknown")
    def test_unknown_provider_does_not_fall_back(self):
        with self.assertRaises(AIProviderConfigurationError):
            get_ai_provider()

    @override_settings(
        AI_PROVIDER="openrouter",
        OPENROUTER_API_KEY="",
        OPENROUTER_MODEL="test/model",
    )
    def test_openrouter_requires_api_key(self):
        with self.assertRaises(AIProviderConfigurationError):
            get_ai_provider()

    @override_settings(
        AI_PROVIDER="openrouter",
        OPENROUTER_API_KEY="test-key",
        OPENROUTER_MODEL="",
    )
    def test_openrouter_requires_model(self):
        with self.assertRaises(AIProviderConfigurationError):
            get_ai_provider()

    @override_settings(
        AI_PROVIDER="openrouter",
        OPENROUTER_API_KEY="test-key",
        OPENROUTER_MODEL="test/model",
        AI_REQUEST_TIMEOUT_SECONDS="nan",
    )
    def test_invalid_numeric_configuration_fails_safely(self):
        with self.assertRaises(AIProviderConfigurationError):
            get_ai_provider()


class OpenRouterProviderTests(SimpleTestCase):
    def response_data(self, **overrides):
        data = {
            "model": "provider/returned-model",
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": "Provider answer",
                    }
                }
            ],
            "usage": {
                "prompt_tokens": 10,
                "completion_tokens": 4,
                "total_tokens": 14,
                "cost": "0.0001234",
            },
        }
        data.update(overrides)
        return data

    def provider(self, client=None, **overrides):
        values = {
            "api_key": "private-test-key",
            "model": "configured/model",
            "base_url": "https://openrouter.test/api/v1",
            "timeout_seconds": "30",
            "max_output_tokens": "512",
            "temperature": "0.7",
            "site_url": "",
            "app_name": "",
            "http_client": client,
        }
        values.update(overrides)
        return OpenRouterProvider(**values)

    def messages(self):
        return (
            ProviderMessage(role="user", content="First question"),
            ProviderMessage(role="assistant", content="First answer"),
            ProviderMessage(role="user", content="Follow-up"),
        )

    @mock.patch(
        "chats.ai.openrouter.perf_counter",
        side_effect=(10.0, 10.245),
    )
    def test_successful_request_and_response_contract(self, perf_counter_mock):
        client = StubHTTPClient(
            response=StubResponse(data=self.response_data())
        )
        provider = self.provider(
            client,
            site_url="https://nexachat.example",
            app_name="NexaChat",
        )

        result = provider.complete(
            messages=self.messages(),
            system_prompt="System instructions",
        )

        self.assertEqual(len(client.calls), 1)
        request = client.calls[0]
        self.assertEqual(
            request["url"],
            "https://openrouter.test/api/v1/chat/completions",
        )
        self.assertEqual(
            request["headers"]["Authorization"],
            "Bearer private-test-key",
        )
        self.assertEqual(request["headers"]["Content-Type"], "application/json")
        self.assertEqual(
            request["headers"]["HTTP-Referer"],
            "https://nexachat.example",
        )
        self.assertEqual(request["headers"]["X-OpenRouter-Title"], "NexaChat")
        self.assertEqual(request["timeout"], 30)
        self.assertEqual(request["json"]["model"], "configured/model")
        self.assertEqual(request["json"]["temperature"], 0.7)
        self.assertEqual(request["json"]["max_tokens"], 512)
        self.assertEqual(
            request["json"]["messages"][0],
            {"role": "system", "content": "System instructions"},
        )
        self.assertEqual(
            request["json"]["messages"][-1],
            {"role": "user", "content": "Follow-up"},
        )
        self.assertEqual(result.content, "Provider answer")
        self.assertEqual(result.model_name, "provider/returned-model")
        self.assertEqual(result.prompt_tokens, 10)
        self.assertEqual(result.completion_tokens, 4)
        self.assertEqual(result.total_tokens, 14)
        self.assertEqual(result.cost, Decimal("0.000123"))
        self.assertEqual(result.latency_ms, 245)
        self.assertEqual(perf_counter_mock.call_count, 2)

    def test_optional_attribution_headers_are_omitted_when_unconfigured(self):
        client = StubHTTPClient(
            response=StubResponse(data=self.response_data())
        )

        self.provider(client).complete(
            messages=self.messages(),
            system_prompt="System instructions",
        )

        headers = client.calls[0]["headers"]
        self.assertNotIn("HTTP-Referer", headers)
        self.assertNotIn("X-OpenRouter-Title", headers)

    def test_missing_usage_and_cost_default_to_zero(self):
        client = StubHTTPClient(
            response=StubResponse(
                data=self.response_data(usage=None),
            )
        )

        result = self.provider(client).complete(
            messages=self.messages(),
            system_prompt="System instructions",
        )

        self.assertEqual(result.prompt_tokens, 0)
        self.assertEqual(result.completion_tokens, 0)
        self.assertEqual(result.total_tokens, 0)
        self.assertEqual(result.cost, Decimal("0.000000"))

    def test_timeout_and_network_errors_are_translated(self):
        timeout_request = httpx.Request(
            "POST",
            "https://openrouter.test/api/v1/chat/completions",
        )
        cases = (
            (
                httpx.ReadTimeout("timed out", request=timeout_request),
                AIProviderTimeoutError,
            ),
            (
                httpx.ConnectError("offline", request=timeout_request),
                AIProviderUnavailableError,
            ),
        )

        for low_level_error, expected_error in cases:
            with self.subTest(error=expected_error.__name__):
                client = StubHTTPClient(error=low_level_error)
                with self.assertRaises(expected_error):
                    self.provider(client).complete(
                        messages=self.messages(),
                        system_prompt="System instructions",
                    )

    def test_http_statuses_are_translated_without_exposing_response_body(self):
        cases = (
            (401, AIProviderAuthenticationError),
            (403, AIProviderAuthenticationError),
            (408, AIProviderTimeoutError),
            (429, AIProviderRateLimitError),
            (500, AIProviderUnavailableError),
            (400, AIProviderResponseError),
        )

        for response_status, expected_error in cases:
            with self.subTest(status=response_status):
                client = StubHTTPClient(
                    response=StubResponse(
                        status_code=response_status,
                        data={"error": "raw-upstream-private-body"},
                    )
                )
                with self.assertRaises(expected_error) as raised:
                    self.provider(client).complete(
                        messages=self.messages(),
                        system_prompt="System instructions",
                    )
                self.assertNotIn(
                    "raw-upstream-private-body",
                    str(raised.exception),
                )
                self.assertNotIn("private-test-key", str(raised.exception))

    def test_malformed_json_choices_and_content_are_rejected(self):
        cases = (
            StubResponse(json_error=ValueError("not json")),
            StubResponse(data=self.response_data(choices=[])),
            StubResponse(
                data=self.response_data(
                    choices=[{"message": {"content": "   "}}],
                )
            ),
        )

        for provider_response in cases:
            with self.subTest(response=provider_response.data):
                with self.assertRaises(AIProviderResponseError):
                    self.provider(
                        StubHTTPClient(response=provider_response)
                    ).complete(
                        messages=self.messages(),
                        system_prompt="System instructions",
                    )

    def test_negative_or_inconsistent_usage_is_rejected(self):
        cases = (
            {
                "prompt_tokens": -1,
                "completion_tokens": 4,
                "total_tokens": 3,
            },
            {
                "prompt_tokens": 10,
                "completion_tokens": 4,
                "total_tokens": 999,
            },
        )

        for usage in cases:
            with self.subTest(usage=usage):
                client = StubHTTPClient(
                    response=StubResponse(
                        data=self.response_data(usage=usage),
                    )
                )
                with self.assertRaises(AIProviderResponseError):
                    self.provider(client).complete(
                        messages=self.messages(),
                        system_prompt="System instructions",
                    )

    def test_invalid_cost_is_rejected(self):
        for cost in ("not-a-number", "-0.1", "Infinity"):
            with self.subTest(cost=cost):
                usage = {
                    "prompt_tokens": 1,
                    "completion_tokens": 1,
                    "total_tokens": 2,
                    "cost": cost,
                }
                with self.assertRaises(AIProviderResponseError):
                    self.provider(
                        StubHTTPClient(
                            response=StubResponse(
                                data=self.response_data(usage=usage),
                            )
                        )
                    ).complete(
                        messages=self.messages(),
                        system_prompt="System instructions",
                    )

    @mock.patch("chats.ai.openrouter.httpx.Client")
    def test_default_http_client_keeps_tls_verification_enabled(self, client_class):
        client = StubHTTPClient(
            response=StubResponse(data=self.response_data())
        )
        client_class.return_value = client

        self.provider().complete(
            messages=self.messages(),
            system_prompt="System instructions",
        )

        client_class.assert_called_once_with()
        self.assertTrue(client.closed)
        self.assertNotIn("verify", client_class.call_args.kwargs)


class ConversationContextTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="context-owner",
            password="ContextPass123!",
        )
        self.other_user = get_user_model().objects.create_user(
            username="context-other",
            password="ContextOtherPass123!",
        )
        self.session = ChatSession.objects.create(
            user=self.user,
            title="Context session",
        )
        self.other_session = ChatSession.objects.create(
            user=self.other_user,
            title="Private context session",
        )

    def create_message(self, content, role, *, session=None):
        return ChatMessage.objects.create(
            session=session or self.session,
            role=role,
            content=content,
        )

    def test_context_filters_session_maps_roles_and_preserves_order(self):
        self.create_message("First user", ChatMessage.Role.USER)
        self.create_message("First assistant", ChatMessage.Role.ASSISTANT)
        self.create_message(
            "Private message",
            ChatMessage.Role.USER,
            session=self.other_session,
        )
        current = self.create_message("Current user", ChatMessage.Role.USER)

        context = build_conversation_context(
            self.session,
            current_user_message=current,
            system_prompt="Separate system prompt",
            message_limit=20,
        )

        self.assertEqual(context.system_prompt, "Separate system prompt")
        self.assertEqual(
            [(message.role, message.content) for message in context.messages],
            [
                ("user", "First user"),
                ("assistant", "First assistant"),
                ("user", "Current user"),
            ],
        )
        self.assertNotIn(
            "Private message",
            [message.content for message in context.messages],
        )
        self.assertEqual(
            [message.content for message in context.messages].count("Current user"),
            1,
        )

    def test_context_excludes_unsupported_and_empty_historic_messages(self):
        self.create_message("Internal system", ChatMessage.Role.SYSTEM)
        self.create_message("   ", ChatMessage.Role.ASSISTANT)
        current = self.create_message("Usable", ChatMessage.Role.USER)

        context = build_conversation_context(
            self.session,
            current_user_message=current,
            system_prompt="Configured system",
            message_limit=20,
        )

        self.assertEqual(
            [(message.role, message.content) for message in context.messages],
            [("user", "Usable")],
        )

    def test_context_uses_a_bounded_single_query(self):
        for index in range(12):
            self.create_message(
                f"Message {index}",
                (
                    ChatMessage.Role.USER
                    if index % 2 == 0
                    else ChatMessage.Role.ASSISTANT
                ),
            )

        with CaptureQueriesContext(connection) as captured:
            context = build_conversation_context(
                self.session,
                system_prompt="Configured system",
                message_limit=3,
            )

        self.assertEqual(len(captured), 1)
        self.assertIn("LIMIT 3", captured[0]["sql"])
        self.assertEqual(
            [message.content for message in context.messages],
            ["Message 9", "Message 10", "Message 11"],
        )

    def test_current_message_relationship_is_validated(self):
        wrong_message = self.create_message(
            "Wrong session",
            ChatMessage.Role.USER,
            session=self.other_session,
        )
        assistant = self.create_message(
            "Not a current user message",
            ChatMessage.Role.ASSISTANT,
        )

        for invalid_message in (wrong_message, assistant):
            with self.subTest(message=invalid_message.id):
                with self.assertRaises(ValueError):
                    build_conversation_context(
                        self.session,
                        current_user_message=invalid_message,
                        system_prompt="Configured system",
                    )

    @override_settings(AI_CONTEXT_MESSAGE_LIMIT="0")
    def test_invalid_context_limit_fails_safely(self):
        with self.assertRaises(AIProviderConfigurationError):
            build_conversation_context(
                self.session,
                system_prompt="Configured system",
            )


class ChatOrchestrationServiceTests(TransactionTestCase):
    reset_sequences = True

    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="orchestration-owner",
            password="OrchestrationPass123!",
        )
        self.other_user = get_user_model().objects.create_user(
            username="orchestration-other",
            password="OrchestrationOtherPass123!",
        )
        self.session = ChatSession.objects.create(
            user=self.user,
            title="Orchestration session",
        )

    def test_complete_flow_creates_linked_messages_and_usage(self):
        provider = RecordingProvider()

        result = process_chat_message(
            session=self.session,
            user=self.user,
            content="Explain DRF.\nPreserve this line.",
            provider=provider,
        )

        self.assertEqual(ChatMessage.objects.count(), 2)
        self.assertEqual(AIUsage.objects.count(), 1)
        self.assertEqual(result.user_message.role, ChatMessage.Role.USER)
        self.assertEqual(
            result.assistant_message.role,
            ChatMessage.Role.ASSISTANT,
        )
        self.assertEqual(result.user_message.session, self.session)
        self.assertEqual(result.assistant_message.session, self.session)
        self.assertEqual(result.usage.session, self.session)
        self.assertEqual(result.usage.message, result.user_message)
        self.assertEqual(result.usage.prompt_tokens, 12)
        self.assertEqual(result.usage.completion_tokens, 5)
        self.assertEqual(result.usage.total_tokens, 17)
        self.assertEqual(result.usage.cost, Decimal("0.123456"))
        self.assertEqual(result.usage.latency_ms, 240)
        self.assertEqual(result.usage.model_name, "test-provider-model")

        self.session.refresh_from_db()
        self.assertEqual(
            self.session.last_message_at,
            result.assistant_message.created_at,
        )
        self.assertEqual(len(provider.calls), 1)
        self.assertFalse(provider.calls[0]["in_atomic_block"])
        self.assertEqual(
            [
                message.content
                for message in provider.calls[0]["messages"]
            ].count("Explain DRF.\nPreserve this line."),
            1,
        )

    def test_flow_reuses_record_ai_usage_once_without_duplicates(self):
        provider = RecordingProvider()
        with mock.patch(
            "chats.services.record_ai_usage",
            wraps=record_ai_usage,
        ) as usage_recorder:
            process_chat_message(
                session=self.session,
                user=self.user,
                content="One request",
                provider=provider,
            )

        usage_recorder.assert_called_once()
        self.assertEqual(ChatMessage.objects.count(), 2)
        self.assertEqual(AIUsage.objects.count(), 1)

    def test_provider_failure_keeps_only_the_user_message(self):
        provider = RecordingProvider(
            error=AIProviderTimeoutError("provider timeout"),
        )

        with self.assertRaises(ChatProviderFailure) as raised:
            process_chat_message(
                session=self.session,
                user=self.user,
                content="Persist this question",
                provider=provider,
            )

        self.assertIsInstance(
            raised.exception.provider_error,
            AIProviderTimeoutError,
        )
        self.assertEqual(
            raised.exception.user_message.content,
            "Persist this question",
        )
        self.assertEqual(ChatMessage.objects.count(), 1)
        self.assertEqual(
            ChatMessage.objects.get().role,
            ChatMessage.Role.USER,
        )
        self.assertFalse(AIUsage.objects.exists())

    def test_unexpected_provider_failure_is_safely_translated(self):
        provider = RecordingProvider(error=RuntimeError("private failure"))

        with self.assertRaises(ChatProviderFailure) as raised:
            process_chat_message(
                session=self.session,
                user=self.user,
                content="Question",
                provider=provider,
            )

        self.assertIsInstance(
            raised.exception.provider_error,
            AIProviderResponseError,
        )
        self.assertEqual(ChatMessage.objects.count(), 1)
        self.assertFalse(AIUsage.objects.exists())

    def test_assistant_and_usage_writes_roll_back_together(self):
        provider = RecordingProvider()

        with mock.patch(
            "chats.services.record_ai_usage",
            side_effect=ValidationError("usage failed"),
        ):
            with self.assertRaises(ChatResponsePersistenceError):
                process_chat_message(
                    session=self.session,
                    user=self.user,
                    content="Keep only this user message",
                    provider=provider,
                )

        messages = list(ChatMessage.objects.all())
        self.assertEqual(len(messages), 1)
        self.assertEqual(messages[0].role, ChatMessage.Role.USER)
        self.assertFalse(AIUsage.objects.exists())
        self.session.refresh_from_db()
        self.assertEqual(self.session.last_message_at, messages[0].created_at)

    def test_service_rejects_another_users_session(self):
        provider = RecordingProvider()

        with self.assertRaises(ChatSessionOwnershipError):
            process_chat_message(
                session=self.session,
                user=self.other_user,
                content="Not allowed",
                provider=provider,
            )

        self.assertFalse(ChatMessage.objects.exists())
        self.assertFalse(AIUsage.objects.exists())
        self.assertEqual(provider.calls, [])

    def test_service_rejects_whitespace_content(self):
        with self.assertRaises(ValidationError):
            process_chat_message(
                session=self.session,
                user=self.user,
                content=" \n\t ",
                provider=RecordingProvider(),
            )

        self.assertFalse(ChatMessage.objects.exists())


@override_settings(AI_PROVIDER="mock")
class SessionChatAPITests(APITestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_user(
            username="chat-completion-owner",
            password="ChatCompletionPass123!",
        )
        cls.other_user = get_user_model().objects.create_user(
            username="chat-completion-other",
            password="ChatCompletionOtherPass123!",
        )
        cls.session = ChatSession.objects.create(
            user=cls.user,
            title="Complete chat session",
        )
        cls.other_session = ChatSession.objects.create(
            user=cls.other_user,
            title="Other complete chat session",
        )

    def setUp(self):
        self.authenticate_as(self.user)
        self.url = reverse(
            "chats:session-chat",
            kwargs={"session_id": self.session.id},
        )

    def authenticate_as(self, user):
        access = RefreshToken.for_user(user).access_token
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {access}")

    def post(self, data=None):
        return self.client.post(
            self.url,
            data if data is not None else {"content": "How does DRF work?"},
            format="json",
        )

    def test_endpoint_requires_authentication(self):
        self.client.credentials()

        response = self.post()

        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)
        self.assertFalse(ChatMessage.objects.exists())

    def test_missing_and_unowned_sessions_return_not_found(self):
        missing_url = reverse(
            "chats:session-chat",
            kwargs={"session_id": uuid.uuid4()},
        )
        missing_response = self.client.post(
            missing_url,
            {"content": "Missing"},
            format="json",
        )

        self.authenticate_as(self.other_user)
        unowned_response = self.post()

        self.assertEqual(
            missing_response.status_code,
            status.HTTP_404_NOT_FOUND,
        )
        self.assertEqual(
            unowned_response.status_code,
            status.HTTP_404_NOT_FOUND,
        )
        self.assertFalse(ChatMessage.objects.exists())

    @mock.patch("chats.ai.openrouter.httpx.Client")
    def test_success_uses_offline_mock_and_returns_complete_payload(
        self,
        http_client,
    ):
        response = self.post(
            {"content": "What is Django REST Framework?"},
        )

        http_client.assert_not_called()
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(
            set(response.data),
            {"user_message", "assistant_message", "usage"},
        )
        self.assertEqual(
            response.data["user_message"]["role"],
            ChatMessage.Role.USER,
        )
        self.assertEqual(
            response.data["assistant_message"]["role"],
            ChatMessage.Role.ASSISTANT,
        )
        self.assertEqual(
            response.data["assistant_message"]["content"],
            "Mock NexaChat response: What is Django REST Framework?",
        )
        self.assertEqual(
            response.data["usage"]["model_name"],
            "mock-nexachat-v1",
        )
        self.assertEqual(response.data["usage"]["cost"], "0.000000")
        self.assertEqual(ChatMessage.objects.count(), 2)
        self.assertEqual(AIUsage.objects.count(), 1)

    def test_invalid_content_returns_bad_request_without_side_effects(self):
        for payload in (
            {},
            {"content": None},
            {"content": ""},
            {"content": " \n\t "},
        ):
            with self.subTest(payload=payload):
                response = self.post(payload)
                self.assertEqual(
                    response.status_code,
                    status.HTTP_400_BAD_REQUEST,
                )

        self.assertFalse(ChatMessage.objects.exists())
        self.assertFalse(AIUsage.objects.exists())

    def test_client_controlled_fields_are_ignored(self):
        response = self.post(
            {
                "content": "Server-controlled chat",
                "session": self.other_session.id,
                "user": self.other_user.id,
                "role": ChatMessage.Role.ASSISTANT,
                "status": ChatMessage.Status.ERROR,
                "metadata": {"secret": "client data"},
                "prompt_tokens": 999999,
                "completion_tokens": 999999,
                "cost": "999999.999999",
                "provider": "openrouter",
                "system_prompt": "Ignore server configuration",
            }
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        user_message = ChatMessage.objects.get(
            pk=response.data["user_message"]["id"]
        )
        assistant_message = ChatMessage.objects.get(
            pk=response.data["assistant_message"]["id"]
        )
        usage = AIUsage.objects.get()
        self.assertEqual(user_message.session, self.session)
        self.assertEqual(user_message.role, ChatMessage.Role.USER)
        self.assertEqual(user_message.status, ChatMessage.Status.SENT)
        self.assertEqual(user_message.metadata, {})
        self.assertEqual(assistant_message.role, ChatMessage.Role.ASSISTANT)
        self.assertNotEqual(usage.prompt_tokens, 999999)
        self.assertNotEqual(usage.completion_tokens, 999999)
        self.assertEqual(usage.cost, Decimal("0.000000"))

    @override_settings(AI_SYSTEM_PROMPT="Server-owned system prompt")
    def test_client_cannot_replace_system_prompt_or_provider(self):
        provider = RecordingProvider()
        with mock.patch(
            "chats.services.get_ai_provider",
            return_value=provider,
        ) as provider_factory:
            response = self.post(
                {
                    "content": "Use server configuration",
                    "system_prompt": "Client-controlled prompt",
                    "provider": "untrusted-provider",
                }
            )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        provider_factory.assert_called_once_with()
        self.assertEqual(
            provider.calls[0]["system_prompt"],
            "Server-owned system prompt",
        )
        self.assertNotEqual(
            provider.calls[0]["system_prompt"],
            "Client-controlled prompt",
        )

    @override_settings(AI_PROVIDER="unsupported")
    def test_provider_configuration_failure_returns_service_unavailable(self):
        response = self.post({"content": "Configuration failure"})

        self.assertEqual(
            response.status_code,
            status.HTTP_503_SERVICE_UNAVAILABLE,
        )
        self.assertEqual(
            response.data["detail"],
            "The AI service is temporarily unavailable.",
        )
        self.assertTrue(response.data["user_message_id"])
        self.assertEqual(ChatMessage.objects.count(), 1)
        self.assertFalse(AIUsage.objects.exists())

    def test_timeout_returns_gateway_timeout_with_persisted_user_message(self):
        provider = RecordingProvider(
            error=AIProviderTimeoutError("private timeout"),
        )
        with mock.patch(
            "chats.services.get_ai_provider",
            return_value=provider,
        ):
            response = self.post({"content": "Timeout question"})

        self.assertEqual(
            response.status_code,
            status.HTTP_504_GATEWAY_TIMEOUT,
        )
        self.assertEqual(response.data["detail"], "The AI service timed out.")
        self.assertEqual(ChatMessage.objects.count(), 1)
        self.assertEqual(
            str(ChatMessage.objects.get().id),
            str(response.data["user_message_id"]),
        )
        self.assertFalse(AIUsage.objects.exists())

    def test_malformed_provider_response_returns_safe_bad_gateway(self):
        raw_body = "raw upstream data that must stay private"
        provider = RecordingProvider(
            error=AIProviderResponseError(raw_body),
        )
        with mock.patch(
            "chats.services.get_ai_provider",
            return_value=provider,
        ):
            response = self.post({"content": "Malformed response"})

        self.assertEqual(response.status_code, status.HTTP_502_BAD_GATEWAY)
        rendered = json.dumps(response.data, default=str)
        self.assertNotIn(raw_body, rendered)
        self.assertEqual(ChatMessage.objects.count(), 1)
        self.assertFalse(AIUsage.objects.exists())

    def test_authentication_and_rate_limit_failures_are_safe(self):
        api_key = "private-openrouter-key"
        cases = (
            AIProviderAuthenticationError(api_key),
            AIProviderRateLimitError("private provider detail"),
            AIProviderUnavailableError("private provider detail"),
        )

        for provider_error in cases:
            with self.subTest(error=type(provider_error).__name__):
                ChatMessage.objects.all().delete()
                provider = RecordingProvider(error=provider_error)
                with mock.patch(
                    "chats.services.get_ai_provider",
                    return_value=provider,
                ):
                    response = self.post({"content": "Provider failure"})

                self.assertEqual(
                    response.status_code,
                    status.HTTP_503_SERVICE_UNAVAILABLE,
                )
                rendered = json.dumps(response.data, default=str)
                self.assertNotIn(api_key, rendered)
                self.assertNotIn("private provider detail", rendered)
                self.assertEqual(ChatMessage.objects.count(), 1)
                self.assertFalse(AIUsage.objects.exists())

    def test_raw_message_endpoint_never_invokes_provider_or_creates_usage(self):
        raw_url = reverse(
            "chats:session-message-list-create",
            kwargs={"session_id": self.session.id},
        )

        with mock.patch("chats.services.get_ai_provider") as provider_factory:
            response = self.client.post(
                raw_url,
                {"content": "Store only"},
                format="json",
            )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        provider_factory.assert_not_called()
        self.assertEqual(ChatMessage.objects.count(), 1)
        self.assertEqual(ChatMessage.objects.get().role, ChatMessage.Role.USER)
        self.assertFalse(AIUsage.objects.exists())

    def test_automatic_usage_appears_in_all_usage_endpoints(self):
        chat_response = self.post({"content": "Track usage"})
        usage = chat_response.data["usage"]

        list_response = self.client.get(reverse("chats:usage-list"))
        session_response = self.client.get(
            reverse(
                "chats:session-usage-list",
                kwargs={"session_id": self.session.id},
            )
        )
        summary_response = self.client.get(reverse("chats:usage-summary"))

        self.assertEqual(list_response.status_code, status.HTTP_200_OK)
        self.assertEqual(session_response.status_code, status.HTTP_200_OK)
        self.assertEqual(list_response.data[0]["id"], usage["id"])
        self.assertEqual(session_response.data[0]["id"], usage["id"])
        self.assertEqual(summary_response.data["usage_records"], 1)
        self.assertEqual(
            summary_response.data["prompt_tokens"],
            usage["prompt_tokens"],
        )
        self.assertEqual(
            summary_response.data["completion_tokens"],
            usage["completion_tokens"],
        )
        self.assertEqual(
            summary_response.data["total_tokens"],
            usage["total_tokens"],
        )
        self.assertEqual(
            summary_response.data["total_cost"],
            usage["cost"],
        )


class PhaseSixRoutingAndSchemaTests(SimpleTestCase):
    def test_chat_route_is_named_with_uuid_session_path(self):
        session_id = uuid.uuid4()

        self.assertEqual(
            reverse(
                "chats:session-chat",
                kwargs={"session_id": session_id},
            ),
            f"/api/chats/sessions/{session_id}/chat/",
        )

    def test_openapi_documents_complete_chat_operation(self):
        response = self.client.get(
            reverse("schema-json", kwargs={"format": ".json"})
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        schema = json.loads(response.content)
        path = schema["paths"]["/chats/sessions/{session_id}/chat/"]
        self.assertEqual(set(path) - {"parameters"}, {"post"})

        operation = path["post"]
        self.assertEqual(operation["security"], [{"Bearer": []}])
        path_parameter = [
            parameter
            for parameter in operation["parameters"]
            if parameter["in"] == "path"
        ][0]
        self.assertEqual(path_parameter["type"], "string")
        self.assertEqual(path_parameter["format"], "uuid")

        body_parameter = [
            parameter
            for parameter in operation["parameters"]
            if parameter["in"] == "body"
        ][0]
        request_definition = body_parameter["schema"]["$ref"].split("/")[-1]
        self.assertEqual(
            set(schema["definitions"][request_definition]["properties"]),
            {"content"},
        )
        self.assertEqual(
            set(operation["responses"]),
            {"201", "400", "401", "404", "500", "502", "503", "504"},
        )
        success_schema = operation["responses"]["201"]["schema"]["$ref"]
        response_definition = success_schema.split("/")[-1]
        self.assertEqual(
            set(schema["definitions"][response_definition]["properties"]),
            {"user_message", "assistant_message", "usage"},
        )
