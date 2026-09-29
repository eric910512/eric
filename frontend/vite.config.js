import { fileURLToPath, URL } from 'node:url'

import tailwindcss from '@tailwindcss/vite'
import vue from '@vitejs/plugin-vue'
import { defineConfig, loadEnv } from 'vite'

export default defineConfig(({ command, mode }) => {
  const env = { ...loadEnv(mode, process.cwd(), 'VITE_'), ...process.env }
  // Same-origin API: the browser calls /api/v1 on the frontend's own origin (Vite proxy in dev,
  // the static host's /api/* rewrite to the backend in production), so the refresh-token cookie
  // (HttpOnly, SameSite=Strict, Path=/api/v1/auth) is first-party. An absolute, cross-origin API
  // URL would silently break refreshing — refuse to build with one.
  if (command === 'build' && env.VITE_API_BASE_URL && !env.VITE_API_BASE_URL.startsWith('/')) {
    throw new Error('VITE_API_BASE_URL must be a same-origin path (default /api/v1); route /api/* to the backend on the static host instead of calling another origin.')
  }

  return {
    plugins: [vue(), tailwindcss()],
    resolve: {
      alias: { '@': fileURLToPath(new URL('./src', import.meta.url)) },
    },
    server: {
      port: 5173,
      // Dev only: the relative /api/v1 base URL is proxied to the local backend
      // (VITE_DEV_API_TARGET overrides it, e.g. for the E2E runner's own backend port).
      proxy: { '/api': env.VITE_DEV_API_TARGET || 'http://127.0.0.1:5000' },
    },
  }
})
