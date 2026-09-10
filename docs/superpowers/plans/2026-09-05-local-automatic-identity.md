# Local automatic research identity

## Approved scope

The user wants to open and refresh the current local FundClaw workstation without manually entering an access token. This is a single-user, loopback-only development workstation, not multi-user login. Keep the existing Gateway actor, tenant/subject isolation and live-stream authorization checks.

## Design

The browser sends same-origin requests without credentials containing a bearer token. A local Vite server proxy attaches one explicitly configured server-side `GATEWAY_PROXY_TOKEN` to the isolated Gateway. The secret is never a `VITE_*` variable, rendered in the page, returned in a response or stored in the browser. Missing configuration fails closed with an inline retry/configuration notice.

The automatic-identity bridge allows only the Gateway's supported API paths/methods, loopback connections and a loopback HTTP target. It checks Host, Origin and Fetch Metadata before forwarding, removes supplied Authorization, and uses only the server-configured identity. It never exposes the legacy API using that identity. SSE remains an unbuffered streaming proxy. Network/multi-user deployment requires an authenticated same-origin reverse proxy; this local bridge must not be exposed to a LAN or the internet.

## Implementation steps

1. UI (delegated): write failing bootstrap/remount and no-token UI tests; remove the token dialog and host authorization seam; preserve redaction and show retry guidance for missing server identity.
2. Proxy: write failing real HTTP proxy tests for automatic identity, refresh-style repeated requests, request allowlist, same-origin protection, missing configuration, upstream isolation, and live SSE. Implement a small server-only proxy helper and wire it into Vite.
3. Verify: run frontend tests, typecheck and build; inspect the client bundle for secret absence; verify direct backend access is still protected; restart the existing local frontend with server-side identity and inspect the live page after reload.
4. Review spec compliance then code quality; resolve findings. Update local startup documentation. Do not alter professional-role execution semantics or unrelated worktree changes.

## Acceptance criteria

- Page open/reload identifies the configured researcher without a token input.
- Browser API requests contain no bearer secret; failed bootstrap cannot display private content.
- Direct Gateway requests without authorization still fail.
- Untrusted origins/hosts, non-loopback use and out-of-scope routes cannot obtain the proxy's identity.
- Snapshot/history and SSE work through the same identity bridge.
- Existing role cards and research-start behavior remain unchanged.

## Verification record

- UI and proxy changes implemented test-first; 57 frontend tests pass, plus typecheck and production build.
- Live browser open/reload automatically reconnects the researcher, private history and SSE; request capture shows no browser Authorization header and no token input/button.
- Live requests return 200 through configured proxy, 403 for hostile Origin, 404 for legacy route, 401 directly to backend without bearer.
- Production bundle excludes the server-secret build sentinel. Preview API access is explicitly disabled; the inherited Vite preview-proxy gap found in independent spec review was reproduced and fixed with a regression test.
- Independent spec and code-quality/security reviews passed. Current scope remains loopback single-user development; no backend auth weakening or multi-user login added. Existing worktree is retained without merging, committing or cleaning unrelated changes.
