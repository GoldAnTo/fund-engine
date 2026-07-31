"""Domain errors raised by query modules and mapped to v1 error envelopes."""


class NotFoundError(Exception):
    """Raised when a requested ledger object does not exist."""


class ValidationFailedError(Exception):
    """Raised when a client-supplied value (e.g. cursor) is malformed.

    Mapped to a 422 ``validation_failed`` v1 error envelope.
    """
