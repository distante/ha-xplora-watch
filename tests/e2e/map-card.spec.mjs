import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";

import { expect, test } from "@playwright/test";

import { personaByRole, slug, viewPath } from "./demo-personas.mjs";

// Browser e2e for the standalone watch-location map card (ADR 0008), graduating the interactive
// Playwright-MCP walkthrough into a committed, headless CI spec. Demo account only, never a real
// login (ADR 0009). Playwright's CSS selectors pierce open shadow DOM, so `xplora-watch-map-card
// <selector>` resolves the nested elements through HA's Lovelace shadow roots. Assertions are on
// external behaviour only — rendered DOM + observable HA state — reusing the exact selectors and
// expected texts from the jsdom suite (tests/card/map-card.test.js).
//
// Scope: the observable happy paths + steady states against the seeded demo dashboard. Two cases
// stay in jsdom (they can't be reproduced faithfully in a real browser): the cold-start registry
// ordering ("Locating…" vs the permanent Contact verdict — the live registry loads too fast to catch
// the empty-frame window), and the refresh-on-render single-`see` dedup (off by default). Both have
// teeth in tests/card/map-card.test.js.
//
// One warm, authenticated page is shared across the file (serial): the HA frontend compiles once
// (cold, per fresh browser context, it is slow), and every spec drives client-side navigation from
// there. The storageState written by global-setup.mjs makes the page already authenticated.

const MAP = "xplora-watch-map-card";
// Any console/page error whose text or source URL mentions the card (element names, the
// `xplora-watch-card.js` bundle, or the `/xplora_watch_static/` path) — HA's own noise and blocked
// OSM tile errors are ignored.
const CARD_ERROR = /xplora[-_]watch/i;
const STORAGE_STATE = resolve(dirname(fileURLToPath(import.meta.url)), "../../.e2e-ha/storage-state.json");

test.describe.configure({ mode: "serial" });

/** @type {import('@playwright/test').BrowserContext} */
let context;
/** @type {import('@playwright/test').Page} */
let page;

test.beforeAll(async ({ browser }) => {
  context = await browser.newContext({ storageState: STORAGE_STATE });
  page = await context.newPage();
  // Warm the frontend once (the cold compile is the slow part) and confirm we're authenticated.
  await page.goto("/");
  await expect(page.locator("home-assistant")).toBeAttached();
});

test.afterAll(async () => {
  await context?.close();
});

// Navigate to a demo view and wait for its map card to attach — the readiness gate every spec shares.
async function openView(role) {
  await page.goto(viewPath(role));
  await expect(page.locator(MAP).first()).toBeAttached();
}

// Read one HA state over REST, authenticated by the token the frontend already holds in localStorage
// (same-origin, so no CORS). Used to prove a reload actually reached the backend.
async function haState(entityId) {
  return page.evaluate(async (eid) => {
    const tokens = JSON.parse(localStorage.getItem("hassTokens"));
    const res = await fetch(`/api/states/${eid}`, { headers: { Authorization: `Bearer ${tokens.access_token}` } });
    return res.ok ? res.json() : null;
  }, entityId);
}

// The entity_id of a watch role for a persona: its id ends with the alias slug, and roles ride the
// integration-emitted `xplora_role` attribute (ADR 0005) — never parsed out of the id.
async function findEntityId(role, aliasSlug) {
  return page.evaluate(
    async ({ role, aliasSlug }) => {
      const tokens = JSON.parse(localStorage.getItem("hassTokens"));
      const res = await fetch("/api/states", { headers: { Authorization: `Bearer ${tokens.access_token}` } });
      const states = await res.json();
      const hit = states.find((s) => s.attributes?.xplora_role === role && s.entity_id.endsWith(`_${aliasSlug}`));
      return hit ? hit.entity_id : null;
    },
    { role, aliasSlug },
  );
}

// Collect card-scoped console errors over the body of a test, then stop listening.
async function withCardConsoleErrors(fn) {
  const errors = [];
  const onConsole = (m) => {
    if (m.type() !== "error") return;
    if (CARD_ERROR.test(m.text()) || CARD_ERROR.test(m.location()?.url || "")) errors.push(m.text());
  };
  const onPageError = (e) => CARD_ERROR.test(String(e)) && errors.push(String(e));
  page.on("console", onConsole);
  page.on("pageerror", onPageError);
  try {
    await fn(errors);
  } finally {
    page.off("console", onConsole);
    page.off("pageerror", onPageError);
  }
}

test("Guardian: header, a success fix-age banner, reload + expand, and HA's map mounts", async () => {
  await openView("guardian");
  const card = page.locator(MAP);

  // Header shows THIS watch's name ("{ward} Watch ({alias})"). Assert the alias-tagged form rather
  // than just "Watch" (which every watch shows) or the ward name (Python-owned — the whole design
  // keys off the JS-owned alias to avoid that coupling).
  await expect(card.locator(".map-header .map-name")).toContainText(`Watch (${personaByRole("guardian").alias})`);

  // Fix-age banner carries a poll-outcome status — the demo's always-fresh fix reads as success.
  const banner = card.locator(".map-banner.success .map-banner-text");
  await expect(banner).toBeVisible();
  await expect(banner).toContainText("Updated");

  // Both controls present, and HA's built-in map card actually mounts in the body.
  await expect(card.locator(".map-reload")).toBeVisible();
  await expect(card.locator(".map-expand")).toBeVisible();
  await expect(card.locator(".map-body hui-map-card")).toBeAttached();
});

test("Guardian: reload spins, recovers, and the press reaches HA (last_update advances)", async () => {
  const aliasSlug = slug(personaByRole("guardian").alias);
  await openView("guardian");
  const card = page.locator(MAP);
  const reload = card.locator(".map-reload");
  await expect(reload).toBeVisible();

  const luId = await findEntityId("last_update", aliasSlug);
  expect(luId).toBeTruthy();
  const before = (await haState(luId)).attributes.last_update_time;

  await reload.click();
  // The forced re-fix takes ~1s server-side (LOCATE_POLL_DELAYS), so the disabled + spinning state
  // is observable...
  await expect(card.locator(".map-reload[disabled]")).toBeVisible();
  await expect(card.locator(".map-reload ha-icon.spin")).toBeVisible();
  // ...then it settles back to a live button.
  await expect(card.locator(".map-reload:not([disabled])")).toBeVisible();
  await expect(card.locator(".map-reload ha-icon.spin")).toHaveCount(0);

  // Prove the press reached HA: the last_update sensor's time attribute advanced. (We don't assert a
  // banner-text delta — the demo is always-fresh, so it stays "just now" and would pass vacuously.)
  await expect.poll(async () => (await haState(luId)).attributes.last_update_time).not.toBe(before);
});

test("Error watch: a failed reload recovers instead of staying stuck spinning", async () => {
  // The Error persona's watch loads with a real fix, but its *forced* re-fix (the Reload = the
  // watch's Update button) rejects — the one case the always-fresh demo can't produce elsewhere.
  await withCardConsoleErrors(async (cardErrors) => {
    await openView("error");
    const card = page.locator(MAP);
    const reload = card.locator(".map-reload");
    await expect(reload).toBeVisible();

    await reload.click();
    // The card surfaces the rejected press (proves the reload really failed, not that it no-op'd)...
    await expect.poll(() => cardErrors.some((t) => /reload failed/i.test(t))).toBeTruthy();
    // ...and the button is live again: present, not disabled, not spinning.
    await expect(card.locator(".map-reload:not([disabled])")).toBeVisible();
    await expect(card.locator(".map-reload ha-icon.spin")).toHaveCount(0);
  });
});

test("Contact watch: permanent empty state, no reload, no map", async () => {
  await openView("contact");
  const card = page.locator(MAP);

  const empty = card.locator(".map-empty.contact");
  await expect(empty).toBeVisible();
  await expect(empty).toContainText(/account type/i);
  await expect(card.locator(".map-reload")).toHaveCount(0);
  await expect(card.locator("hui-map-card")).toHaveCount(0);
});

test("Consolidation: the overview location row opens the map card full-screen (fill mode)", async () => {
  await openView("guardian");
  const locationRow = page.locator("xplora-watch-overview-card .row.location");
  await expect(locationRow).toBeVisible();
  await locationRow.click();

  const popupCard = page.locator(".modal-host .card-popup.fill xplora-watch-map-card");
  await expect(popupCard).toBeVisible();
  // Fill mode suppresses the header + expand (no recursive re-open), keeps reload, and mounts the map.
  await expect(popupCard.locator(".map-header")).toHaveCount(0);
  await expect(popupCard.locator(".map-expand")).toHaveCount(0);
  await expect(popupCard.locator(".map-reload")).toBeVisible();
  await expect(popupCard.locator("hui-map-card")).toBeAttached();
});

test("Guardian view loads with zero card-related console errors", async () => {
  await withCardConsoleErrors(async (cardErrors) => {
    await openView("guardian");
    // Let the card fully settle (map build + banner) before judging the console.
    await expect(page.locator(`${MAP} .map-body hui-map-card`)).toBeAttached();
    await page.waitForTimeout(500);
    expect(cardErrors).toEqual([]);
  });
});
