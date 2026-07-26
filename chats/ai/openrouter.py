from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from math import isfinite
from time import perf_counter
from typing import Sequence
from urllib.parse import urlparse

import httpx

from .base import AIProvider, ChatCompletionResult, ProviderMessage
from .exceptions import (
    AIProviderAuthenticationError,
    AIProviderConfigurationError,
    AIProviderRateLimitError,
    AIProviderResponseError,
    AIProviderTimeoutError,
    AIProviderUnavailableError,
)

COST_QUANTUM = Decimal("0.000001")
MAX_STORABLE_COST = Decimal("999999.999999")


class OpenRouterProvider(AIProvider):
    def __init__(
        self,
        *,
        api_key,
        model,
        base_url,
        timeout_seconds,
        max_output_tokens,
        temperature,
        site_url="",
        app_name="",
        http_client=None,
    ):
        self.api_key = self._require_text(api_key, "OpenRouter API key")
        self.model = self._require_text(model, "OpenRouter model")
        self.base_url = self._validate_base_url(base_url)
        self.timeout_seconds = self._positive_float(
            timeout_seconds,
            "AI request timeout",
        )
        self.max_output_tokens = self._positive_integer(
            max_output_tokens,
            "AI maximum output tokens",
        )
        self.temperature = self._temperature(temperature)
        self.site_url = str(site_url).strip()
        self.app_name = str(app_name).strip()
        self.http_client = http_client

    @staticmethod
    def _require_text(value, label):
        normalized = str(value or "").strip()
        if not normalized:
            raise AIProviderConfigurationError(f"{label} is not configured.")
        return normalized

    @staticmethod
    def _positive_float(value, label):
        try:
            normalized = float(value)
        except (TypeError, ValueError) as error:
            raise AIProviderConfigurationError(
                f"{label} must be a positive number."
            ) from error
        if not isfinite(normalized) or normalized <= 0:
            raise AIProviderConfigurationError(
                f"{label} must be a positive number."
            )
        return normalized

    @staticmethod
    def _positive_integer(value, label):
        if isinstance(value, bool):
            raise AIProviderConfigurationError(
                f"{label} must be a positive integer."
            )
        try:
            normalized = int(value)
        except (TypeError, ValueError) as error:
            raise AIProviderConfigurationError(
                f"{label} must be a positive integer."
            ) from error
        if normalized <= 0 or str(value).strip() != str(normalized):
            raise AIProviderConfigurationError(
                f"{label} must be a positive integer."
            )
        return normalized

    @staticmethod
    def _temperature(value):
        try:
            normalized = float(value)
        except (TypeError, ValueError) as error:
            raise AIProviderConfigurationError(
                "AI temperature must be between 0 and 2."
            ) from error
        if not isfinite(normalized) or not 0 <= normalized <= 2:
            raise AIProviderConfigurationError(
                "AI temperature must be between 0 and 2."
            )
        return normalized

    @staticmethod
    def _validate_base_url(value):
        normalized = str(value or "").strip().rstrip("/")
        parsed = urlparse(normalized)
        if parsed.scheme != "https" or not parsed.netloc:
            raise AIProviderConfigurationError(
                "OpenRouter base URL must be a valid HTTPS URL."
            )
        return normalized

    @property
    def completion_url(self):
        if self.base_url.endswith("/chat/completions"):
            return self.base_url
        return f"{self.base_url}/chat/completions"

    def _headers(self):
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        if self.site_url:
            headers["HTTP-Referer"] = self.site_url
        if self.app_name:
            headers["X-OpenRouter-Title"] = self.app_name
        return headers

    @staticmethod
    def _token_count(usage, field_name):
        value = usage.get(field_name, 0)
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise AIProviderResponseError("Provider usage values are invalid.")
        return value

    @staticmethod
    def _cost(usage):
        value = usage.get("cost")
        if value is None:
            return Decimal("0.000000")
        try:
            cost = Decimal(str(value))
        except (InvalidOperation, TypeError, ValueError) as error:
            raise AIProviderResponseError("Provider cost is invalid.") from error
        if not cost.is_finite() or cost < 0 or cost > MAX_STORABLE_COST:
            raise AIProviderResponseError("Provider cost is invalid.")
        return cost.quantize(COST_QUANTUM, rounding=ROUND_HALF_UP)

    @staticmethod
    def _raise_for_status(response):
        if 200 <= response.status_code < 300:
            return
        if response.status_code in {401, 403}:
            raise AIProviderAuthenticationError(
                "The AI provider rejected its credentials."
            )
        if response.status_code in {408}:
            raise AIProviderTimeoutError("The AI provider request timed out.")
        if response.status_code == 429:
            raise AIProviderRateLimitError("The AI provider rate limit was reached.")
        if response.status_code == 402 or response.status_code >= 500:
            raise AIProviderUnavailableError("The AI provider is unavailable.")
        raise AIProviderResponseError("The AI provider rejected the request.")

    @staticmethod
    def _parse_result(data, latency_ms):
        if not isinstance(data, dict) or data.get("error"):
            raise AIProviderResponseError("The provider response is invalid.")

        choices = data.get("choices")
        if not isinstance(choices, list) or not choices:
            raise AIProviderResponseError("The provider response has no completion.")
        first_choice = choices[0]
        message = (
            first_choice.get("message")
            if isinstance(first_choice, dict)
            else None
        )
        content = message.get("content") if isinstance(message, dict) else None
        if not isinstance(content, str) or not content.strip():
            raise AIProviderResponseError("The provider response has no content.")

        model_name = data.get("model")
        if not isinstance(model_name, str) or not model_name.strip():
            raise AIProviderResponseError("The provider response has no model.")

        usage = data.get("usage") or {}
        if not isinstance(usage, dict):
            raise AIProviderResponseError("Provider usage values are invalid.")
        prompt_tokens = OpenRouterProvider._token_count(usage, "prompt_tokens")
        completion_tokens = OpenRouterProvider._token_count(
            usage,
            "completion_tokens",
        )
        expected_total = prompt_tokens + completion_tokens
        total_tokens = usage.get("total_tokens", expected_total)
        if (
            isinstance(total_tokens, bool)
            or not isinstance(total_tokens, int)
            or total_tokens < 0
            or total_tokens != expected_total
        ):
            raise AIProviderResponseError("Provider token totals are inconsistent.")

        try:
            return ChatCompletionResult(
                content=content,
                model_name=model_name,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                total_tokens=total_tokens,
                cost=OpenRouterProvider._cost(usage),
                latency_ms=latency_ms,
            )
        except ValueError as error:
            raise AIProviderResponseError(
                "The provider response is invalid."
            ) from error

    def complete(
        self,
        *,
        messages: Sequence[ProviderMessage],
        system_prompt: str,
    ) -> ChatCompletionResult:
        if not isinstance(system_prompt, str) or not system_prompt.strip():
            raise AIProviderConfigurationError(
                "The AI system prompt is not configured."
            )
        if not any(message.role == "user" for message in messages):
            raise AIProviderResponseError("No user message was provided.")

        provider_messages = [
            {"role": "system", "content": system_prompt},
            *[
                {"role": message.role, "content": message.content}
                for message in messages
            ],
        ]
        payload = {
            "model": self.model,
            "messages": provider_messages,
            "temperature": self.temperature,
            "max_tokens": self.max_output_tokens,
        }

        created_client = self.http_client is None
        client = self.http_client or httpx.Client()
        started_at = perf_counter()
        try:
            response = client.post(
                self.completion_url,
                headers=self._headers(),
                json=payload,
                timeout=self.timeout_seconds,
            )
        except httpx.TimeoutException as error:
            raise AIProviderTimeoutError(
                "The AI provider request timed out."
            ) from error
        except httpx.RequestError as error:
            raise AIProviderUnavailableError(
                "The AI provider is unavailable."
            ) from error
        finally:
            if created_client:
                client.close()

        latency_ms = max(0, round((perf_counter() - started_at) * 1000))
        self._raise_for_status(response)
        try:
            data = response.json()
        except (TypeError, ValueError) as error:
            raise AIProviderResponseError(
                "The provider returned malformed JSON."
            ) from error
        return self._parse_result(data, latency_ms)
