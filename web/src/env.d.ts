/// <reference types="vite/client" />

interface ImportMetaEnv {
  /** 后端 API 根地址，如 http://localhost:8000；留空表示同源 */
  readonly VITE_API_BASE_URL: string
}

interface ImportMeta {
  readonly env: ImportMetaEnv
}
