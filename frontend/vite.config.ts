import tailwindcss from '@tailwindcss/vite'
import react from '@vitejs/plugin-react'
import type { Plugin } from 'vite'
import { defineConfig } from 'vitest/config'

// The dev server's way to the operator's area's page, as the backend serves the build (`spa` in
// backend/app/main.py): /operator and the paths below it are its page, before the proxy below
// sends /api… to the backend; every other path the members'.
const ownPages: Plugin = {
  name: 'own-pages',
  configureServer(server) {
    server.middlewares.use((request, _response, next) => {
      if (/^\/operator(?:[/?]|$)/.test(request.url ?? '')) request.url = '/operator.html'
      next()
    })
  },
}

// The pages built beside the members' own, each by its mode: the operator's area.
const OWN_PAGES: Record<string, string[]> = {
  operator: ['operator.html'],
}

// https://vite.dev/config/
export default defineConfig(({ mode }) => ({
  plugins: [react(), tailwindcss(), ownPages],
  server: {
    // The backend serves the API; in production it serves this app's build as well.
    proxy: { '/api': 'http://127.0.0.1:8000' },
  },
  // Two builds (`pnpm build` runs both): the members' app, one script (frontend conventions),
  // then with `--mode operator` the operator's area, which only operators are sent, added beside
  // it in dist/. A build of both at once would share their common code in a chunk of its own,
  // which the members' page would load as a second script.
  build: OWN_PAGES[mode]
    ? { emptyOutDir: false, rollupOptions: { input: OWN_PAGES[mode] } }
    : { rollupOptions: { input: 'index.html' } },
  // The unit tests, beside the modules they test; they run in Node, with fetch stubbed.
  test: {
    include: ['src/**/*.test.ts'],
  },
}))
