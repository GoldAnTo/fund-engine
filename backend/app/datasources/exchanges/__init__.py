"""Governed official-exchange announcement acquisition adapters."""

from app.datasources.exchanges.sse import SSEAnnouncementSource
from app.datasources.exchanges.szse import SZSEAnnouncementSource

__all__ = ["SSEAnnouncementSource", "SZSEAnnouncementSource"]
