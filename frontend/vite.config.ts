import { readFileSync } from 'node:fs'
import tailwindcss from '@tailwindcss/vite'
import react from '@vitejs/plugin-react'
import type { Plugin } from 'vite'
import { defineConfig } from 'vitest/config'

// The cookie a signed-in browser carries (SESSION_COOKIE in backend/app/auth.py): a visitor
// without it is shown the public site's home page at /, as the backend does (backend/app/site.py).
const SESSION_COOKIE = 'emudoc_session'

// The dev server's way to the pages of their own, as the backend serves the build (`spa` in
// backend/app/main.py): /operator and the paths below it are the operator's area's page, a
// visitor's / the public site's home page, before the proxy below sends /api… to the backend;
// every other path the members'. The home page shows its placeholders as written here
// ($book_label), which the backend fills in from the plans and the settings as it serves the
// build.
const ownPages: Plugin = {
  name: 'own-pages',
  configureServer(server) {
    server.middlewares.use((request, _response, next) => {
      const url = request.url ?? ''
      if (/^\/operator(?:[/?]|$)/.test(url)) request.url = '/operator.html'
      else if (/^\/(?:[?#]|$)/.test(url) && !signedIn(request.headers.cookie)) request.url = '/home.html'
      next()
    })
  },
}

function signedIn(cookies: string | undefined): boolean {
  return (cookies ?? '').split(';').some((cookie) => cookie.trim().startsWith(`${SESSION_COOKIE}=`))
}

// The public site's page takes its header and footer from files of their own: `<!-- include
// header.html -->` is replaced by src/site/header.html, in the build and on the dev server alike.
const siteParts: Plugin = {
  name: 'site-parts',
  transformIndexHtml: {
    order: 'pre',
    handler: (html) =>
      html.replace(/<!-- include ([\w-]+\.html) -->/g, (_, name: string) =>
        readFileSync(new URL(`src/site/${name}`, import.meta.url), 'utf8').trim(),
      ),
  },
}

// The pages built beside the members' own, each by its mode: the operator's area, and the
// public site's home page, which has a stylesheet of its own and loads no script.
const OWN_PAGES: Record<string, string[]> = {
  operator: ['operator.html'],
  site: ['home.html'],
}

// https://vite.dev/config/
export default defineConfig(({ mode }) => ({
  plugins: [react(), tailwindcss(), ownPages, siteParts],
  server: {
    // The backend serves the API; in production it serves this app's build as well.
    proxy: { '/api': 'http://127.0.0.1:8000' },
  },
  // Three builds (`pnpm build` runs them all): the members' app, one script (frontend
  // conventions), then with `--mode operator` the operator's area, which only operators are
  // sent, and with `--mode site` the public site's home page, plain HTML that search engines
  // read as it is, each added beside it in dist/. A build of them at once would share their
  // common code in a chunk of its own, which the members' page would load as a second script.
  build: OWN_PAGES[mode]
    ? { emptyOutDir: false, rollupOptions: { input: OWN_PAGES[mode] } }
    : { rollupOptions: { input: 'index.html' } },
  // The unit tests, beside the modules they test; they run in Node, with fetch stubbed.
  test: {
    include: ['src/**/*.test.ts'],
  },
}))
