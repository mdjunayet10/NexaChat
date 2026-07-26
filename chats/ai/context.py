from dataclasses import dataclass

from django.conf import settings

from chats.models import ChatMessage, ChatSession

from .base import ProviderMessage
from .exceptions import AIProviderConfigurationError


@dataclass(frozen=True)
class ConversationContext:
    system_prompt: str
    messages: tuple[ProviderMessage, ...]


def _positive_message_limit(value):
    if isinstance(value, bool):
        raise AIProviderConfigurationError(
            "AI context message limit must be a positive integer."
        )
    try:
        normalized = int(value)
    except (TypeError, ValueError) as error:
        raise AIProviderConfigurationError(
            "AI context message limit must be a positive integer."
        ) from error
    if normalized <= 0 or str(value).strip() != str(normalized):
        raise AIProviderConfigurationError(
            "AI context message limit must be a positive integer."
        )
    return normalized


def build_conversation_context(
    session,
    *,
    current_user_message=None,
    message_limit=None,
    system_prompt=None,
):
    if not isinstance(session, ChatSession) or session.pk is None:
        raise ValueError("A saved chat session is required.")

    limit = _positive_message_limit(
        message_limit
        if message_limit is not None
        else getattr(settings, "AI_CONTEXT_MESSAGE_LIMIT", "20")
    )
    resolved_system_prompt = str(
        system_prompt
        if system_prompt is not None
        else getattr(settings, "AI_SYSTEM_PROMPT", "")
    ).strip()
    if not resolved_system_prompt:
        raise AIProviderConfigurationError(
            "The AI system prompt is not configured."
        )

    if current_user_message is not None and (
        current_user_message.session_id != session.pk
        or current_user_message.role != ChatMessage.Role.USER
    ):
        raise ValueError(
            "The current user message must belong to the supplied session."
        )

    historic_limit = limit - 1 if current_user_message is not None else limit
    historic_messages = ChatMessage.objects.filter(
        session=session,
        role__in=(
            ChatMessage.Role.USER,
            ChatMessage.Role.ASSISTANT,
        ),
    )
    if current_user_message is not None:
        historic_messages = historic_messages.exclude(pk=current_user_message.pk)

    historic_messages = list(
        historic_messages.order_by("-created_at")[:historic_limit]
    )
    historic_messages.reverse()

    provider_messages = []
    for message in historic_messages:
        if not message.content.strip():
            continue
        provider_messages.append(
            ProviderMessage(role=message.role, content=message.content)
        )

    if current_user_message is not None:
        provider_messages.append(
            ProviderMessage(
                role=ChatMessage.Role.USER,
                content=current_user_message.content,
            )
        )

    return ConversationContext(
        system_prompt=resolved_system_prompt,
        messages=tuple(provider_messages),
    )
