# Browser end-to-end tests

Playwright-driven tests (`@playwright/test`) that exercise the integration in a **real browser
against a real Home Assistant** — the surfaces the pytest and jsdom card suites can't see (the
Lovelace card mounted in a live dashboard, role discovery via the live entity registry, the served
bundle actually loading, HA's `hui-map-card` really mounting).

## Demo account only — never a real login (ADR 0009)

All browser/e2e testing runs against the built-in **demo account only**. A real Xplora login is never
performed and real credentials never go into CI. The demo swap is keyed off the sign-in email
sentinel (`demo*@xplora-watch.invalid`), so adding a demo entry is fully network-free. Five personas
cover the matrix (`tests/e2e/demo-personas.mjs` is the single source of truth): `Dad` (Guardian),
`Mom` (second Guardian), `Contact` (a non-guardian Contact), `Offline` (a Guardian whose watch is
offline), and `Error` (a Guardian whose watch loads normally but whose *forced* re-fix fails, so a
card's Reload button can be seen recovering from a rejected press).

## Self-contained harness — one command

`npm run test:e2e` runs the whole thing; nothing needs to be started by hand:

1. `playwright.config.mjs`'s **`webServer`** block runs `tests/e2e/boot-ha.sh`, which preps a fresh,
   throwaway config dir (the integration discovered via `<config>/custom_components`) and launches
   `hass` on a **dedicated port (`8125`, env-overridable via `E2E_HA_PORT`)** so it never collides
   with the dev / MCP HA on `:8123`. Playwright waits for the port.
2. **`globalSetup`** (`tests/e2e/global-setup.mjs`) then calls `seedDemoHa()` to bring that fresh HA
   to a deterministic demo-only state, and writes an authenticated `storageState` (reusing the owner
   token minted during onboarding) so every spec starts already logged in.
3. The specs run headless against that one backend, then the `webServer` is torn down.

Every run is fresh (`reuseExistingServer: false`); `seedDemoHa` refuses to double-seed.

## The seeding recipe

`seed-demo.mjs` takes a **fresh** HA instance and brings it to a deterministic demo-only state:
completes onboarding (`admin` / `password`), adds one demo config entry per persona via the
config-flow REST API, enables the disabled-by-default card entities, builds a per-watch **"Xplora
Demo"** dashboard (view paths keyed on the persona alias, e.g. `/xplora-demo/dad`), and verifies over
the WebSocket API that every entry loaded. It exports `seedDemoHa(baseUrl)` (imported by
`globalSetup`) and also runs standalone as a CLI:

```bash
node tests/e2e/seed-demo.mjs            # against $HA_URL or http://localhost:8123
```

We commit the **recipe**, not the state: the throwaway `.e2e-ha/` config dir (owner password hash +
tokens, `home-assistant_v2.db`) is regenerated each run and stays gitignored. Never commit a
pre-baked `.storage/` — the harness rebuilds it from this recipe every run.

## Running locally

Install the Playwright browser once (Playwright's own chromium — has an arm64 build, unlike branded
Chrome), then run the suite:

```bash
npx playwright install chromium
npm run test:e2e
```

Add `--headed` / `--ui` / `-g "<name>"` for debugging. The dev config (`config/`) and the dev/MCP HA
on `:8123` are left completely untouched.

## Running in CI

`.github/workflows/e2e-test.yml` runs the exact same command on `ubuntu-latest` (x86): install the
locked runtime deps (`requirements-lock.txt`, for a runnable `hass`), `npm install`, the standard
`npx playwright install --with-deps chromium`, then `npm run test:e2e`. On failure it uploads the
Playwright report/trace and the HA log as artifacts. (The arm64 headless-shell dance in the
devcontainer MCP notes is MCP-only — `@playwright/test` ships its own chromium, so CI uses the
standard install.)
