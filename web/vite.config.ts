import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import { fileURLToPath, URL } from 'node:url'

// The API runs on its own port in development; in production the built client is served
// by the same FastAPI app, so requests are same-origin and the proxy is unused.
const API_TARGET = process.env.VITE_DEV_API ?? 'http://127.0.0.1:8000'

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: { '@': fileURLToPath(new URL('./src', import.meta.url)) },
  },
  server: {
    port: 5173,
    proxy: { '/api': { target: API_TARGET, changeOrigin: true } },
  },
  build: {
    outDir: 'dist',
    sourcemap: false,
    rollupOptions: {
      output: {
        // Keep the markdown renderer out of the entry chunk: only the chat and document
        // views need it, and both are lazily routed.
        manualChunks: {
          react: ['react', 'react-dom', 'react-router-dom'],
          query: ['@tanstack/react-query'],
          markdown: ['marked', 'dompurify'],
        },
      },
    },
  },
})
