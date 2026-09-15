import { defineConfig } from '@playwright/test';

export default defineConfig({
  testDir: './tests', timeout: 30000, workers: 1,
  use: { baseURL: process.env.NEXUS_E2E_URL ?? 'http://127.0.0.1:8000', headless: true, channel: 'chrome', screenshot: 'only-on-failure', trace: 'retain-on-failure' },
});
