import { defineConfig, devices } from '@playwright/test'

// The end-to-end smoke test (e2e/): the built page and the API as one server, against a
// stand-in for the model and the embeddings endpoint, since a real one needs a key and answers
// differently every run. Needs the compose database up and a `pnpm build`.
const PORT = 18000
const STANDIN_PORT = 18001

export default defineConfig({
  testDir: 'e2e',
  // One flow, one member: nothing to run side by side.
  workers: 1,
  forbidOnly: !!process.env.CI,
  retries: 0,
  reporter: process.env.CI ? [['github'], ['html', { open: 'never' }]] : 'list',
  use: {
    baseURL: `http://127.0.0.1:${PORT}`,
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
  },
  projects: [{ name: 'chromium', use: { ...devices['Desktop Chrome'] } }],
  webServer: [
    {
      command: `python3 e2e/standin.py --port ${STANDIN_PORT}`,
      url: `http://127.0.0.1:${STANDIN_PORT}/health`,
      reuseExistingServer: !process.env.CI,
    },
    {
      command: `e2e/serve.sh ${PORT} ${STANDIN_PORT}`,
      url: `http://127.0.0.1:${PORT}/api/health`,
      // A fresh database is migrated first.
      timeout: 120_000,
      reuseExistingServer: !process.env.CI,
    },
  ],
})
