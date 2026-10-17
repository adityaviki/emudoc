import tailwindcss from '@tailwindcss/vite'
import react from '@vitejs/plugin-react'
import { defineConfig } from 'vitest/config'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    // The backend serves the API; in production it serves this app's build as well.
    proxy: { '/api': 'http://127.0.0.1:8000' },
  },
  // The unit tests, beside the modules they test; they run in Node, with fetch stubbed.
  test: {
    include: ['src/**/*.test.ts'],
  },
})
