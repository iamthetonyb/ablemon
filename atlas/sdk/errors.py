"""Structured error types for the ATLAS SDK."""


class ATLASError(Exception):
    """Base error for all ATLAS SDK operations."""

    def is_retryable(self) -> bool:
        return False

    def is_context_limit(self) -> bool:
        return False


class APIError(ATLASError):
    """Provider returned an error."""

    def __init__(self, message: str, status_code: int = 0, provider: str = ""):
        super().__init__(message)
        self.status_code = status_code
        self.provider = provider

    def is_retryable(self) -> bool:
        return self.status_code in (429, 500, 502, 503, 529)


class AuthError(ATLASError):
    """Authentication or permission denied."""

    pass


class RateLimitError(ATLASError):
    """Rate limited by provider."""

    def __init__(self, message: str, retry_after: float = 0):
        super().__init__(message)
        self.retry_after = retry_after

    def is_retryable(self) -> bool:
        return True


class ContextOverflow(ATLASError):
    """Context window exceeded."""

    def is_context_limit(self) -> bool:
        return True


class ToolError(ATLASError):
    """Tool execution failed."""

    def __init__(self, message: str, tool_name: str = "", retryable: bool = False):
        super().__init__(message)
        self.tool_name = tool_name
        self._retryable = retryable

    def is_retryable(self) -> bool:
        return self._retryable


class BudgetExhausted(ATLASError):
    """Cost/budget limit reached."""

    pass
