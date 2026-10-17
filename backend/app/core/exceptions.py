"""
Nexus Exception Architecture (RFC-7807 Problem Details Compliant).
Provides a hierarchical, domain-driven exception system with distinct HTTP status codes,
machine-readable error codes, and strict separation of concerns following SOLID principles.

Only the exceptions that are actually raised/caught anywhere in the app live
here. A large block of speculative classes (resource sub-types, task-contract and
rate-limit errors, sandbox/provider timeouts, and a set of "backward
compatibility" aliases) was removed after an AST import-graph audit found no
importer for any of them -- text-matching had kept two of them alive by mistake,
because ``retry_policy.py`` names litellm's like-named ``AuthenticationError`` /
``ContextWindowExceededError``. Re-add a class when a caller needs it; do not
re-create the vocabulary for its own sake.
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
# 1. Authentication & Security Exceptions (401 / 409)
# =====================================================================

class AuthenticationError(NexusException):
    status_code = status.HTTP_401_UNAUTHORIZED
    error_code = "AUTHENTICATION_FAILED"
    message = "Invalid or missing authentication credentials."


class InvalidTokenError(AuthenticationError):
    error_code = "INVALID_TOKEN"
    message = "The authentication token is invalid, expired, or malformed."


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


# =====================================================================
# 3. Validation & Guardrail Exceptions (422)
# =====================================================================

class ValidationError(NexusException):
    # Using HTTP_422_UNPROCESSABLE_ENTITY without deprecation
    status_code = 422
    error_code = "VALIDATION_ERROR"
    message = "The request payload failed validation."


# =====================================================================
# 4. Storage & File Payload Exceptions (415)
# =====================================================================

class UnsupportedFileTypeError(NexusException):
    status_code = status.HTTP_415_UNSUPPORTED_MEDIA_TYPE
    error_code = "UNSUPPORTED_FILE_TYPE"
    message = "The uploaded file format is not supported for knowledge ingestion."


# =====================================================================
# 5. Tool, Agent & Approval Exceptions (502 / 403 / 410 / 409)
# =====================================================================

class ToolExecutionError(NexusException):
    status_code = status.HTTP_502_BAD_GATEWAY
    error_code = "TOOL_EXECUTION_FAILED"
    message = "Execution of the requested tool encountered an unexpected failure."


class ToolPermissionError(NexusException):
    status_code = status.HTTP_403_FORBIDDEN
    error_code = "TOOL_PERMISSION_DENIED"
    message = "Tool execution requires explicit human-in-the-loop (HITL) approval."


class ApprovalExpiredError(NexusException):
    status_code = status.HTTP_410_GONE
    error_code = "APPROVAL_EXPIRED"
    message = "The approval request has expired and can no longer be resolved."


class ApprovalConsumedError(NexusException):
    status_code = status.HTTP_409_CONFLICT
    error_code = "APPROVAL_ALREADY_CONSUMED"
    message = "This approval request has already been resolved and cannot be reused."
