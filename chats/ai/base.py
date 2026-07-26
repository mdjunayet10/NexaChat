from abc import ABC, abstractmethod
from dataclasses import dataclass
from decimal import Decimal
from typing import Sequence


@dataclass(frozen=True)
class ProviderMessage:
    role: str
    content: str

    def __post_init__(self):
        if self.role not in {"user", "assistant"}:
            raise ValueError("Unsupported provider-message role.")
        if not isinstance(self.content, str) or not self.content.strip():
            raise ValueError("Provider-message content must not be empty.")


@dataclass(frozen=True)
class ChatCompletionResult:
    content: str
    model_name: str
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    cost: Decimal
    latency_ms: int | None = None

    def __post_init__(self):
        if not isinstance(self.content, str) or not self.content.strip():
            raise ValueError("Assistant content must not be empty.")
        if not isinstance(self.model_name, str) or not self.model_name.strip():
            raise ValueError("Model name must not be empty.")
        if len(self.model_name) > 100:
            raise ValueError("Model name exceeds the supported length.")

        for field_name in (
            "prompt_tokens",
            "completion_tokens",
            "total_tokens",
        ):
            value = getattr(self, field_name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f"{field_name} must be a non-negative integer.")

        if self.total_tokens != self.prompt_tokens + self.completion_tokens:
            raise ValueError("Total tokens do not match prompt and completion tokens.")

        if not isinstance(self.cost, Decimal):
            raise ValueError("Cost must be represented as Decimal.")
        if not self.cost.is_finite() or self.cost < 0:
            raise ValueError("Cost must be a finite, non-negative Decimal.")

        if self.latency_ms is not None and (
            isinstance(self.latency_ms, bool)
            or not isinstance(self.latency_ms, int)
            or self.latency_ms < 0
        ):
            raise ValueError("Latency must be a non-negative integer or null.")


class AIProvider(ABC):
    @abstractmethod
    def complete(
        self,
        *,
        messages: Sequence[ProviderMessage],
        system_prompt: str,
    ) -> ChatCompletionResult:
        raise NotImplementedError
