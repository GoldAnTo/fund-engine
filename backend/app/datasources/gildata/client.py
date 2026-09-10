"""Synchronous HTTP client for the Gildata (恒生聚源) MCP server.

The server speaks JSON-RPC 2.0 over HTTP POST.  Auth is a token carried in the
URL query string (not a header).  ``call_tool`` returns the raw
``result.content[0].text`` string so the adapter layer owns inner-JSON parsing;
this keeps the client a thin transport and makes the double-wrapped MCP
response easy to test.

Verified request shape (curl)::

    POST {base_url}?token={GILDATA_TOKEN}
    Content-Type: application/json
    Accept: application/json, text/event-stream

    {"jsonrpc":"2.0","id":1,"method":"tools/call",
     "params":{"name":"FinQuery","arguments":{"query":"..."}}}

The response is ``{"jsonrpc":"2.0","id":1,"result":{"content":[{"text":"..."}]}}``
where ``content[0].text`` is itself a JSON *string* encoding the inner payload
``{"code":"0","results":[...]}``.
"""
from __future__ import annotations

import os
from typing import Any

import httpx

DEFAULT_BASE_URL = "https://api.gildata.com/mcp-servers/aidata-assistant-srv-tool"

_HEADERS = {
    "Content-Type": "application/json",
    "Accept": "application/json, text/event-stream",
}


class GildataMCPError(Exception):
    """Raised on any transport, protocol, or application-level MCP failure."""


class GildataMCPClient:
    """Thin JSON-RPC 2.0 client backed by a reusable ``httpx.Client``.

    ``call_tool`` returns the raw ``result.content[0].text`` string.  Callers
    that want the parsed inner payload should use ``parse_content`` in
    :mod:`app.datasources.gildata.adapters`.
    """

    def __init__(
        self,
        token: str,
        base_url: str = DEFAULT_BASE_URL,
        *,
        timeout: float = 60.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        if not token:
            raise GildataMCPError("token must be a non-empty string")
        self._token = token
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout
        client_kwargs: dict[str, Any] = {"timeout": timeout}
        if transport is not None:
            client_kwargs["transport"] = transport
        self._client = httpx.Client(**client_kwargs)
        self._next_id = 0

    @classmethod
    def from_env(cls) -> "GildataMCPClient":
        """Build a client from the ``GILDATA_TOKEN`` environment variable.

        Raises :class:`GildataMCPError` when the variable is unset/empty -- this
        is a real data source, so missing credentials are a hard error, not a
        silent mock.
        """
        token = os.getenv("GILDATA_TOKEN")
        if not token:
            raise GildataMCPError(
                "GILDATA_TOKEN environment variable is not set; "
                "export it before calling from_env()"
            )
        return cls(token=token)

    # ------------------------------------------------------------------ transport

    def _url(self) -> str:
        return f"{self._base_url}?token={self._token}"

    def _post(self, payload: dict, *, timeout: float | None = None) -> dict:
        try:
            response = self._client.post(
                self._url(), json=payload, headers=_HEADERS, timeout=timeout
            )
        except httpx.HTTPError as exc:
            raise GildataMCPError(f"HTTP transport error: {exc}") from exc

        if response.status_code != 200:
            raise GildataMCPError(
                f"HTTP {response.status_code} from Gildata MCP: {response.text[:500]}"
            )

        try:
            outer = response.json()
        except ValueError as exc:
            raise GildataMCPError(
                f"response is not valid JSON: {response.text[:500]}"
            ) from exc

        if not isinstance(outer, dict):
            raise GildataMCPError(f"response is not a JSON object: {outer!r}")

        if "error" in outer:
            raise GildataMCPError(f"JSON-RPC error: {outer['error']}")
        return outer

    def _next_rpc_id(self) -> int:
        self._next_id += 1
        return self._next_id

    # ------------------------------------------------------------------ public API

    def call_tool(
        self,
        name: str,
        arguments: dict,
        timeout: float = 60,
    ) -> str:
        """Call an MCP tool by name and return ``result.content[0].text``.

        The returned string is the inner Gildata payload encoded as JSON text
        (e.g. ``'{"code":"0","results":[...]}'``).  Raises
        :class:`GildataMCPError` on HTTP failure, JSON-RPC level error, or a
        malformed result envelope.
        """
        payload = {
            "jsonrpc": "2.0",
            "id": self._next_rpc_id(),
            "method": "tools/call",
            "params": {"name": name, "arguments": arguments},
        }
        outer = self._post(payload, timeout=timeout)
        result = outer.get("result")
        if not isinstance(result, dict):
            raise GildataMCPError(
                f"JSON-RPC response missing 'result' object: {outer!r}"
            )
        content = result.get("content")
        if not isinstance(content, list) or not content:
            raise GildataMCPError(
                f"JSON-RPC result has no 'content' array: {result!r}"
            )
        first = content[0]
        text = first.get("text") if isinstance(first, dict) else None
        if not isinstance(text, str):
            raise GildataMCPError(f"content[0] has no 'text' string: {first!r}")
        return text

    def list_tools(self, timeout: float = 60) -> list[dict]:
        """Return the MCP server's tool descriptors (``tools/list``)."""
        payload = {
            "jsonrpc": "2.0",
            "id": self._next_rpc_id(),
            "method": "tools/list",
            "params": {},
        }
        outer = self._post(payload, timeout=timeout)
        result = outer.get("result")
        if not isinstance(result, dict):
            raise GildataMCPError(
                f"JSON-RPC response missing 'result' object: {outer!r}"
            )
        tools = result.get("tools")
        if not isinstance(tools, list):
            return []
        return [t for t in tools if isinstance(t, dict)]

    # ------------------------------------------------------------------ lifecycle

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "GildataMCPClient":
        return self

    def __exit__(self, *args: object) -> None:
        self.close()
