import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

export default defineConfig({
  plugins: [vue()],
  server: {
    host: '127.0.0.1',
    port: 5176,
    strictPort: true,
    proxy: {
      // Use a proxy so the UI can call the backend (including SSE) without CORS.
      // Default backend for local dev: scripts/run_local.ps1 typically runs API on 18000.
      '/api/v1': {
        target: process.env.VITE_GEO_BACKEND_ORIGIN ?? 'http://127.0.0.1:18000',
        changeOrigin: true,
      },
    },
  },
})
