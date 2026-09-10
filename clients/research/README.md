# Research API client

Private TypeScript clients and generated contracts for the research APIs. This package has no application entry point, pages, router, or development server.

```sh
npm ci
npm run typecheck
npm test
```

`src/data/` contains the HTTP clients, response validation, and submission idempotency helpers. `src/contracts/v1.ts` and `openapi.json` are generated API contracts. API tests use jsdom for browser storage; the package does not render or modify the DOM.
