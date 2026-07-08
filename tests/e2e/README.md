# Browser end-to-end tests

Playwright-driven tests that exercise the integration in a **real browser against a real Home
Assistant** — the surfaces the pytest and jsdom card suites can't see (the Lovelace card mounted in a
live dashboard, the rendered config-flow form).

## Demo account only — never a real login (ADR 0009)

All browser/e2e testing runs against the built-in **demo account only**. A real Xplora login is never
performed and real credentials never go into CI. The demo swap is keyed off the sign-in email
sentinel (`demo*@xplora-watch.invalid`), so adding a demo entry is fully network-free. Four personas
cover the matrix: `Dad` (Guardian), `Mom` (second Guardian), `Contact` (a non-guardian Contact),
`Offline` (a Guardian whose watch is offline).

## The seeding recipe

`seed-demo.mjs` takes a **fresh** HA instance and brings it to a deterministic demo-only state:
completes onboarding (`admin` / `password`) and adds the four demo config entries via the config-flow
REST API, then verifies over the WebSocket API that exactly four entries exist and all loaded. It
exports `seedDemoHa(baseUrl)` (imported by the Playwright `globalSetup`) and also runs as a CLI.

We commit the **recipe**, not the state: the runtime `.storage/` (owner password hash + tokens) and
`home-assistant_v2.db` are regenerated each run and stay gitignored (same split as the dev `config/`).
Never commit a pre-baked `.storage/` — CI rebuilds it from this recipe.

## Running locally

The recipe needs HA already running, **fresh** (onboarding not done), with the integration
discoverable (`<config>/custom_components/xplora_watch` must resolve). From the repo root:

```bash
# 1. Stand up a fresh, demo-ready HA config dir
E2E="$(mktemp -d)/config"; mkdir -p "$E2E"
cp config/configuration.yaml "$E2E/"
ln -s "$PWD/custom_components" "$E2E/custom_components"   # canonical HA discovery

# 2. Boot HA against it (leave running in another shell)
hass --config "$E2E" --debug

# 3. Seed it (once HA is up on :8123)
node tests/e2e/seed-demo.mjs            # or: node tests/e2e/seed-demo.mjs http://localhost:8123
```

Log in at http://localhost:8123 with `admin` / `password`. Re-running the recipe against an
already-seeded instance fails fast on purpose (it refuses to double-seed).

## Running in CI

Same recipe. A CI job boots HA against a throwaway config dir (config from the committed
`configuration.yaml`, `custom_components` symlinked to the checkout), waits for `:8123`, then the
Playwright `globalSetup` calls `seedDemoHa()` before the specs run. Wiring that job is a follow-up;
the recipe is the foundation it builds on.
