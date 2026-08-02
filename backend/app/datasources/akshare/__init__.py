"""AKShare adapter package: A-share / fund / holding data.

Thin wrapper around the ``akshare`` Python library (MIT licensed,
scrapes public Eastmoney / Sina / Tonghuashun endpoints).  See
:mod:`app.datasources.akshare.client` for the transport layer and
:mod:`app.datasources.akshare.adapters` for canonical-key mapping.

The package is an **optional** dependency — the rest of the codebase
must keep working when ``akshare`` is not installed.  ``AkshareClient``
lazy-imports the library and raises ``AkshareError`` (an
``ImportError`` subclass) if the package is missing, so callers can
catch the failure and either skip the data source or report it
upwards.
"""
from __future__ import annotations

from app.datasources.akshare.client import AkshareClient, AkshareError

__all__ = ["AkshareClient", "AkshareError"]
