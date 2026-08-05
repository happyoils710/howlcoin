/// <reference types="vite/client" />

interface ImportMetaEnv {
  readonly VITE_WC_PROJECT_ID?: string
  readonly VITE_BASESCAN_KEY?: string
  readonly VITE_ALCHEMY_KEY?: string
  readonly VITE_ZEROX_API_KEY?: string
  readonly VITE_DEFAULT_CHAIN_ID?: string
  readonly VITE_BASE_RPC?: string
}

interface ImportMeta {
  readonly env: ImportMetaEnv
}
