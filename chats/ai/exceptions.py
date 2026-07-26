class AIProviderError(Exception):
    pass


class AIProviderConfigurationError(AIProviderError):
    pass


class AIProviderTimeoutError(AIProviderError):
    pass


class AIProviderResponseError(AIProviderError):
    pass


class AIProviderAuthenticationError(AIProviderError):
    pass


class AIProviderRateLimitError(AIProviderError):
    pass


class AIProviderUnavailableError(AIProviderError):
    pass
