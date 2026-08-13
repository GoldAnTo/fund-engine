"""Stable error values safe for persistence and API serialization.

Provider exceptions can embed request URLs, credentials, headers, or response
bodies. The original exception remains available through Python exception
chaining, while durable and client-visible fields use only these fixed values.
"""

AI_OPERATION_ERROR_MESSAGE = "AI operation failed"
AI_PROVIDER_ERROR_TYPE = "provider_failure"
AI_COMPLIANCE_ERROR_MESSAGE = "compliance refused by policy"
AI_COMPLIANCE_ERROR_TYPE = "compliance_refused"
AI_PROTOCOL_ERROR_MESSAGE = "researchability gate blocked"
