from decimal import Decimal
from typing import Sequence

from .base import AIProvider, ChatCompletionResult, ProviderMessage
from .exceptions import AIProviderResponseError


class MockAIProvider(AIProvider):
    model_name = "mock-nexachat-v1"
    maximum_echo_characters = 500

    @staticmethod
    def _estimate_tokens(value):
        return len(value.split())

    def complete(
        self,
        *,
        messages: Sequence[ProviderMessage],
        system_prompt: str,
    ) -> ChatCompletionResult:
        latest_user_message = next(
            (
                message.content.strip()
                for message in reversed(messages)
                if message.role == "user" and message.content.strip()
            ),
            None,
        )
        if latest_user_message is None:
            raise AIProviderResponseError("No usable user message was provided.")

        assistant_content = (
            "Mock NexaChat response: "
            f"{latest_user_message[: self.maximum_echo_characters]}"
        )
        prompt_tokens = self._estimate_tokens(system_prompt) + sum(
            self._estimate_tokens(message.content) for message in messages
        )
        completion_tokens = self._estimate_tokens(assistant_content)

        return ChatCompletionResult(
            content=assistant_content,
            model_name=self.model_name,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=prompt_tokens + completion_tokens,
            cost=Decimal("0.000000"),
            latency_ms=0,
        )
