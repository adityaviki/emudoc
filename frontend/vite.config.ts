import tailwindcss from '@tailwindcss/vite'
import react from '@vitejs/plugin-react'
import type { Plugin } from 'vite'
import { defineConfig } from 'vitest/config'

// The dev server's way to the operator's area: /operator and the paths below it are its page,
// as the backend serves the build (`spa` in backend/app/main.py); every other path the members'.
const operatorPage: Plugin = {
  name: 'operator-page',
  configureServer(server) {
    server.middlewares.use((request, _response, next) => {
      if (request.url && /^\/operator(?:[/?]|$)/.test(request.url)) request.url = '/operator.html'
      next()
    })
  },
}

// https://vite.dev/config/
export default defineConfig(({ mode }) => ({
  plugins: [react(), tailwindcss(), operatorPage],
  server: {
    // The backend serves the API; in production it serves this app's build as well.
    proxy: { '/api': 'http://127.0.0.1:8000' },
  },
  // Two pages, each built on its own (`pnpm build` runs both): the members' app, one script
  // (frontend conventions), and with `--mode operator` the operator's area, added beside it in
  // dist/, which only operators are sent. A build of both at once would share their common
  // code in a chunk of its own, which the members' page would load as a second script.
  build:
    mode === 'operator'
      ? { emptyOutDir: false, rollupOptions: { input: 'operator.html' } }
      : { rollupOptions: { input: 'index.html' } },
  // The unit tests, beside the modules they test; they run in Node, with fetch stubbed.
  test: {
    include: ['src/**/*.test.ts'],
  },
}))
