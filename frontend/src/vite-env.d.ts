/// <reference types="vite/client" />

interface ImportMetaEnv {
  readonly VITE_API_BASE?: string;
  readonly VITE_USE_MOCK?: "0" | "1";
  readonly VITE_BEARER_TOKEN?: string;
  readonly VITE_TENANT_ID?: string;
  readonly VITE_MOCK_SCENARIO?:
    | "typical" | "empty" | "conflict" | "insufficient"
    | "parse_failed" | "historical" | "large" | "offline"
    | "permission" | "stale";
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}