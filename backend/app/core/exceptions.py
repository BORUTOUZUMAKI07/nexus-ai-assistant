"""
Nexus Exception Architecture (RFC-7807 Problem Details Compliant).
Provides a hierarchical, domain-driven exception system with distinct HTTP status codes,
machine-readable error codes, and strict separation of concerns following SOLID principles.
"""
from typing import Any

from fastapi import status


class NexusException(Exception):
    """
    Base domain exception for all Nexus AI Assistant operations.
    Conforms to RFC-7807 Problem Details format.
    """
    status_code: int = status.HTTP_500_INTERNAL_SERVER_ERROR
    error_code: str = "INTERNAL_SERVER_ERROR"
    message: str = "An unexpected error occurred."

    def __init__(
        self,
        message: str | None = None,
        details: dict[str, Any] | None = None,
        status_code: int | None = None,
    ):
        super().__init__(message or self.message)
        self.message = message or self.message
        self.details = details or {}
        if status_code is not None:
            self.status_code = status_code

    def to_dict(self) -> dict[str, Any]:
        """Serializes exception into RFC-7807 Problem Details payload."""
        return {
            "type": f"https://nexus-assistant.ai/errors/{self.error_code.lower().replace('_', '-')}",
            "title": self.error_code,
            "status": self.status_code,
            "detail": self.message,
            "details": self.details,
        }


# =====================================================================
# 1. Authentication & Security Exceptions (401 / 403 / 409)
# =====================================================================

class AuthenticationError(NexusException):
    status_code = status.HTTP_401_UNAUTHORIZED
    error_code = "AUTHENTICATION_FAILED"
    message = "Invalid or missing authentication credentials."


class InvalidTokenError(AuthenticationError):
    error_code = "INVALID_TOKEN"
    message = "The authentication token is invalid, expired, or malformed."


class PermissionDeniedError(NexusException):
    status_code = status.HTTP_403_FORBIDDEN
    error_code = "PERMISSION_DENIED"
    message = "You do not have required permissions to perform this action."


class UserNotFoundError(NexusException):
    status_code = status.HTTP_404_NOT_FOUND
    error_code = "USER_NOT_FOUND"
    message = "The requested user account was not found."


class UserAlreadyExistsError(NexusException):
    status_code = status.HTTP_409_CONFLICT
    error_code = "USER_ALREADY_EXISTS"
    message = "A user with this email or username already exists."


# =====================================================================
# 2. Resource & Entity Exceptions (404)
# =====================================================================

class ResourceNotFoundError(NexusException):
    status_code = status.HTTP_404_NOT_FOUND
    error_code = "RESOURCE_NOT_FOUND"
    message = "The requested resource was not found."

    def __init__(self, resource_type: str = "Resource", identifier: str = ""):
        msg = f"{resource_type} '{identifier}' was not found." if identifier else f"{resource_type} not found."
        super().__init__(message=msg, details={"resource": resource_type, "id": identifier})


class ConversationNotFoundError(ResourceNotFoundError):
    error_code = "CONVERSATION_NOT_FOUND"
    def __init__(self, conversation_id: str = ""):
        super().__init__("Conversation", conversation_id)


class MessageNotFoundError(ResourceNotFoundError):
    error_code = "MESSAGE_NOT_FOUND"
    def __init__(self, message_id: str = ""):
        super().__init__("Message", message_id)


class FileNotFoundError(ResourceNotFoundError):
    error_code = "FILE_NOT_FOUND"
    def __init__(self, file_id: str = ""):
        super().__init__("File", file_id)


# =====================================================================
# 3. Validation & Guardrail Exceptions (422)
# =====================================================================

class ValidationError(NexusException):
    # Using HTTP_422_UNPROCESSABLE_ENTITY without deprecation
    status_code = 422
    error_code = "VALIDATION_ERROR"
    message = "The request payload failed validation."


class TaskContractViolationError(ValidationError):
    error_code = "TASK_CONTRACT_VIOLATED"
    message = "Generated output violated the defined task contract schema."


class EvidenceGateFailedError(ValidationError):
    error_code = "EVIDENCE_GATE_FAILED"
    message = "Output failed evidence verification thresholds (unsupported claims or hallucinations)."


# =====================================================================
# 4. Storage & File Payload Exceptions (413 / 415)
# =====================================================================

class FileTooLargeError(NexusException):
    # Using HTTP 413 without Starlette deprecation warning
    status_code = 413
    error_code = "FILE_TOO_LARGE"
    message = "File size exceeds the 50MB maximum threshold."


class UnsupportedFileTypeError(NexusException):
    status_code = status.HTTP_415_UNSUPPORTED_MEDIA_TYPE
    error_code = "UNSUPPORTED_FILE_TYPE"
    message = "The uploaded file format is not supported for knowledge ingestion."


# =====================================================================
# 5. Rate Limiting & Capacity Exceptions (429 / 400)
# =====================================================================

class RateLimitExceededError(NexusException):
    status_code = status.HTTP_429_TOO_MANY_REQUESTS
    error_code = "RATE_LIMIT_EXCEEDED"
    message = "Rate limit exceeded. Please wait before issuing additional requests."


class ContextWindowExceededError(NexusException):
    status_code = status.HTTP_400_BAD_REQUEST
    error_code = "CONTEXT_WINDOW_EXCEEDED"
    message = "The conversation context exceeds the model maximum token limit."


# =====================================================================
# 6. Tool, Agent & Sandbox Execution Exceptions (502 / 504)
# =====================================================================

class ToolExecutionError(NexusException):
    status_code = status.HTTP_502_BAD_GATEWAY
    error_code = "TOOL_EXECUTION_FAILED"
    message = "Execution of the requested tool encountered an unexpected failure."


class ToolPermissionError(NexusException):
    status_code = status.HTTP_403_FORBIDDEN
    error_code = "TOOL_PERMISSION_DENIED"
    message = "Tool execution requires explicit human-in-the-loop (HITL) approval."


class SandboxTimeoutError(NexusException):
    status_code = status.HTTP_504_GATEWAY_TIMEOUT
    error_code = "SANDBOX_TIMEOUT"
    message = "Code execution timed out inside the isolated sandbox."


class LLMProviderError(NexusException):
    status_code = status.HTTP_502_BAD_GATEWAY
    error_code = "LLM_PROVIDER_ERROR"
    message = "Upstream language model provider returned an error."


# =====================================================================
# 7. Backward Compatibility Aliases (clean domain service interoperability)
# =====================================================================

AppException = NexusException
AuthenticationException = AuthenticationError
AuthorizationException = PermissionDeniedError
UserAlreadyExistsException = UserAlreadyExistsError
ResourceNotFoundException = ResourceNotFoundError
ValidationException = ValidationError
