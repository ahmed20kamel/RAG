import { defineConfig } from 'vitest/config'
import { fileURLToPath, URL } from 'node:url'

// Kept apart from vite.config.ts on purpose: Vitest ships its own copy of Vite, and
// merging the two configs makes the plugin types of the two copies disagree. The tests
// need neither the React plugin nor the dev proxy, only the path alias.
export default defineConfig({
  resolve: {
    alias: { '@': fileURLToPath(new URL('./src', import.meta.url)) },
  },
  test: {
    environment: 'jsdom',
    globals: true,
    setupFiles: ['./src/test/setup.ts'],
    css: false,
  },
})
