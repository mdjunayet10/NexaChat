from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

from django.core.exceptions import ValidationError
from django.db import transaction

from .ai.base import ChatCompletionResult
from .ai.context import build_conversation_context
from .ai.exceptions import AIProviderError, AIProviderResponseError
from .ai.factory import get_ai_provider
from .models import AIUsage, ChatMessage


def _validate_token_count(value, field_name):
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValidationError(
            {field_name: "Token counts must be non-negative integers."}
        )


def _normalize_cost(value):
    if isinstance(value, float):
        raise ValidationError(
            {"cost": "Cost must be provided without floating-point values."}
        )

    try:
        cost = value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as error:
        raise ValidationError({"cost": "Enter a valid decimal cost."}) from error

    if not cost.is_finite() or cost < 0:
        raise ValidationError({"cost": "Cost must be non-negative."})

    return cost


def record_ai_usage(
    *,
    session,
    message,
    model_name,
    prompt_tokens=0,
    completion_tokens=0,
    cost=Decimal("0"),
    latency_ms=None,
):
    if message.session_id != session.pk:
        raise ValidationError(
            {"message": "The message must belong to the supplied session."}
        )
    if message.role != ChatMessage.Role.USER:
        raise ValidationError(
            {"message": "AI usage must be triggered by a user message."}
        )

    _validate_token_count(prompt_tokens, "prompt_tokens")
    _validate_token_count(completion_tokens, "completion_tokens")
    normalized_cost = _normalize_cost(cost)

    usage = AIUsage(
        session=session,
        message=message,
        model_name=model_name,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        cost=normalized_cost,
        latency_ms=latency_ms,
    )
    usage.save()
    return usage


@dataclass(frozen=True)
class ChatProcessResult:
    user_message: ChatMessage
    assistant_message: ChatMessage
    usage: AIUsage


class ChatSessionOwnershipError(Exception):
    pass


class ChatProviderFailure(Exception):
    def __init__(self, provider_error, user_message):
        super().__init__("The configured AI provider could not complete the request.")
        self.provider_error = provider_error
        self.user_message = user_message


class ChatResponsePersistenceError(Exception):
    def __init__(self, user_message):
        super().__init__("The AI response could not be saved.")
        self.user_message = user_message


def process_chat_message(*, session, user, content, provider=None):
    if session.user_id != user.pk:
        raise ChatSessionOwnershipError
    if not isinstance(content, str) or not content.strip():
        raise ValidationError({"content": "Message content cannot be blank."})

    user_message = ChatMessage(
        session=session,
        role=ChatMessage.Role.USER,
        content=content,
    )
    user_message.full_clean()
    user_message.save()

    try:
        context = build_conversation_context(
            session,
            current_user_message=user_message,
        )
        selected_provider = provider or get_ai_provider()
        provider_result = selected_provider.complete(
            messages=context.messages,
            system_prompt=context.system_prompt,
        )
        if not isinstance(provider_result, ChatCompletionResult):
            raise AIProviderResponseError(
                "The AI provider returned an unsupported result."
            )
    except AIProviderError as error:
        raise ChatProviderFailure(error, user_message) from error
    except Exception as error:
        provider_error = AIProviderResponseError(
            "The AI provider failed unexpectedly."
        )
        raise ChatProviderFailure(provider_error, user_message) from error

    try:
        with transaction.atomic():
            assistant_message = ChatMessage(
                session=session,
                role=ChatMessage.Role.ASSISTANT,
                content=provider_result.content,
            )
            assistant_message.full_clean()
            assistant_message.save()
            usage = record_ai_usage(
                session=session,
                message=user_message,
                model_name=provider_result.model_name,
                prompt_tokens=provider_result.prompt_tokens,
                completion_tokens=provider_result.completion_tokens,
                cost=provider_result.cost,
                latency_ms=provider_result.latency_ms,
            )
    except Exception as error:
        raise ChatResponsePersistenceError(user_message) from error

    return ChatProcessResult(
        user_message=user_message,
        assistant_message=assistant_message,
        usage=usage,
    )
