// Playwright globalSetup for the browser e2e suite. Runs AFTER the webServer (boot-ha.sh) is up on
// the port — Playwright starts the webServer before globalSetup — so it seeds the demo-only state
// (onboarding admin/password + the demo config entries + the per-watch dashboards) and then writes a
// Playwright storageState file so every test starts already authenticated.
//
// Demo account only, never a real login (ADR 0009). The browser is authenticated by reusing the
// owner token minted during onboarding (issued for this origin's clientId, exactly what the HA
// frontend expects in the `hassTokens` localStorage key) — no login form to drive, no real account.

import { writeFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";

import { seedDemoHa } from "./seed-demo.mjs";

const HERE = dirname(fileURLToPath(import.meta.url));
const PORT = process.env.E2E_HA_PORT || "8125";
const BASE_URL = process.env.HA_URL || `http://localhost:${PORT}`;
export const STORAGE_STATE = resolve(HERE, "../../.e2e-ha/storage-state.json");

export default async function globalSetup() {
  const { ownerTokens, baseUrl } = await seedDemoHa(BASE_URL);
  if (!ownerTokens?.access_token) throw new Error("seedDemoHa returned no owner token — cannot authenticate the browser");

  // The HA frontend reads its session from the `hassTokens` localStorage key: the token response
  // merged with { hassUrl, clientId, expires }. `clientId` must be the origin + "/" (what the
  // frontend uses), so the token minted with that clientId during onboarding is accepted verbatim
  // and can also be refreshed by the frontend when the short-lived access token expires.
  const origin = baseUrl.replace(/\/$/, "");
  const hassTokens = {
    ...ownerTokens,
    hassUrl: origin,
    clientId: `${origin}/`,
    expires: Date.now() + (ownerTokens.expires_in || 1800) * 1000,
  };
  const storageState = {
    cookies: [],
    origins: [{ origin, localStorage: [{ name: "hassTokens", value: JSON.stringify(hassTokens) }] }],
  };
  writeFileSync(STORAGE_STATE, JSON.stringify(storageState, null, 2));
  console.log(`✓ e2e: wrote authenticated storageState for ${origin}`);
}
