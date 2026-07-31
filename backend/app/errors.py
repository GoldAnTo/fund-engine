"""Domain errors raised by query modules and mapped to v1 error envelopes."""


class NotFoundError(Exception):
    """Raised when a requested ledger object does not exist."""
