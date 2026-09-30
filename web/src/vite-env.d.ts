/// <reference types="vite/client" />

interface ImportMetaEnv {
  /** Absolute API origin. Empty in production, where the client is served by the API. */
  readonly VITE_API_BASE_URL?: string
}

interface ImportMeta {
  readonly env: ImportMetaEnv
}
