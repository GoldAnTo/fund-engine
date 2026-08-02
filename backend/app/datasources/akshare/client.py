"""Thin transport wrapper around the ``akshare`` library.

AKShare is a Python package (MIT licensed) that scrapes public A-share /
fund / holding data from Eastmoney, Sina, and Tonghuashun.  Its interface
surface is wide (180+ functions) and unstable — upstream changes the
function name and column names whenever Eastmoney tweaks a page.  This
client isolates that instability so the rest of the codebase only sees
typed calls that return ``pandas.DataFrame`` (or raise
:class:`AkshareError`).

Design rules:

1. **Lazy import.**  ``akshare`` is imported at construction time only,
   so a dev environment that never touches the AKShare path doesn't
   pay the dependency cost.
2. **Fail-closed.**  No credentials needed (all public endpoints), but
   missing dependency or bad function name both raise
   :class:`AkshareError` (an ``ImportError`` subclass) instead of
   silently returning an empty DataFrame.
3. **Thin transport.**  The client does not parse columns or rename
   keys.  Canonical-key mapping lives in
   :mod:`app.datasources.akshare.adapters`.
"""
from __future__ import annotations


class AkshareError(ImportError):
    """Raised on any akshare call failure (missing dep / unknown func / etc).

    Subclassing ``ImportError`` lets the rest of the codebase write a
    single ``except ImportError`` to mean "optional data source not
    available" — uniform with :class:`app.datasources.docling.DoclingNotInstalled`.
    """


class AkshareClient:
    """Wraps ``akshare`` with lazy import and error normalisation.

    The client is a thin transport: it accepts a function name plus
    keyword arguments, calls into the library, and returns the raw
    ``pandas.DataFrame``.  Canonical-key mapping happens in
    :mod:`app.datasources.akshare.adapters` so the rest of the pipeline
    can stay typed.

    Example::

        client = AkshareClient()
        df = client.call("fund_name_em")
    """

    def __init__(self) -> None:
        try:
            import akshare as ak  # noqa: F401
        except ImportError as exc:
            raise AkshareError(
                "akshare is not installed; pip install 'akshare>=1.18' "
                "to use this data source, or skip it gracefully"
            ) from exc
        self._ak = ak

    @classmethod
    def from_env(cls) -> "AkshareClient":
        """Build a client from the process environment.

        AKShare needs no credentials (public endpoints), so the
        environment hook is reserved for future key-based endpoints
        (Eastmoney's private API, Sina high-frequency tick, etc.).  The
        factory exists so :mod:`app.scripts.ingest_akshare` can call
        ``AkshareClient.from_env()`` uniformly with the Gildata script.
        """
        return cls()

    def call(self, func_name: str, **kwargs):
        """Call an akshare function by name and return the raw DataFrame.

        Raises :class:`AkshareError` when the function name is unknown
        (typo / upstream renamed) or when the call itself raises (network
        blip / rate limit / schema drift).  The original exception is
        chained via ``__cause__`` so logs preserve the underlying
        traceback for post-mortem.
        """
        func = getattr(self._ak, func_name, None)
        if func is None:
            raise AkshareError(
                f"akshare has no function '{func_name}' "
                f"(interface renamed upstream?)"
            )
        try:
            return func(**kwargs)
        except Exception as exc:
            raise AkshareError(
                f"akshare call '{func_name}({kwargs!r})' failed: {exc}"
            ) from exc
