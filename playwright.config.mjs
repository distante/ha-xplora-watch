import { defineConfig, devices } from "@playwright/test";
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";

// Browser end-to-end tests: the bundled Lovelace cards mounted in a real Home Assistant, driven in a
// real browser — the surfaces the pytest and jsdom suites can't see (role discovery via the live
// entity registry, the HACS-served bundle actually loading, HA's `hui-map-card` really mounting).
//
// Self-contained: the `webServer` block boots a FRESH, demo-only HA (via tests/e2e/boot-ha.sh) on a
// dedicated port, `globalSetup` seeds it (demo account only, never a real login — ADR 0009) and
// writes an authenticated storageState, and every spec runs against that one backend. Kept off the
// everyday mixed dev `config/` and the dev/MCP HA on :8123.

const HERE = dirname(fileURLToPath(import.meta.url));
const PORT = process.env.E2E_HA_PORT || "8125";
const BASE_URL = `http://localhost:${PORT}`;
const STORAGE_STATE = resolve(HERE, ".e2e-ha/storage-state.json");
const isCI = !!process.env.CI;

export default defineConfig({
  testDir: "./tests/e2e",
  testMatch: /.*\.spec\.mjs$/,
  // One shared HA backend + a stateful demo (the Error watch's forced re-fix fails only after its
  // first cycle), so keep specs serial rather than racing them against a single instance.
  fullyParallel: false,
  workers: 1,
  forbidOnly: isCI,
  retries: isCI ? 2 : 0,
  timeout: 90_000,
  expect: { timeout: 20_000 },
  reporter: isCI ? [["list"], ["html", { open: "never" }]] : "list",
  globalSetup: "./tests/e2e/global-setup.mjs",
  use: {
    baseURL: BASE_URL,
    storageState: STORAGE_STATE,
    trace: isCI ? "on-first-retry" : "off",
  },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
  webServer: {
    command: "bash tests/e2e/boot-ha.sh",
    url: `${BASE_URL}/manifest.json`,
    reuseExistingServer: false,
    timeout: 180_000,
    stdout: "pipe",
    stderr: "pipe",
    env: { E2E_HA_PORT: PORT },
  },
});
