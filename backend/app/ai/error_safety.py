"""Stable error values safe for persistence and API serialization.

Provider exceptions can embed request URLs, credentials, headers, or response
bodies. The original exception remains available through Python exception
chaining, while durable and client-visible fields use only these fixed values.
"""

AI_OPERATION_ERROR_MESSAGE = "AI operation failed"
AI_OPERATION_ERROR_TYPE = "operation_failure"
AI_COMPLIANCE_ERROR_MESSAGE = "compliance refused by policy"
AI_COMPLIANCE_ERROR_TYPE = "compliance_refused"
AI_PROTOCOL_ERROR_MESSAGE = "researchability gate blocked"
PROVIDER_OPERATION_ERROR_MESSAGE = "provider operation failed"


def provider_failure_payload() -> dict[str, str]:
    """Return a fresh fixed payload that cannot contain upstream details."""
    return {
        "error_type": AI_OPERATION_ERROR_TYPE,
        "error": PROVIDER_OPERATION_ERROR_MESSAGE,
    }
