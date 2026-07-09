#!/usr/bin/env node
// Seed a FRESH Home Assistant instance into a deterministic, network-free demo-only state:
//   1. onboarding (admin/password)
//   2. add the four demo config entries via the config-flow REST API
//   3. complete each entry's OPTIONS flow (selects the watch — without this NO entities are created)
//   4. enable the disabled-by-default card entities (alarms/silents/message/xcoin/buttons/…)
//   5. trigger a functions refresh so alarm/silent/chat data populates
//   6. build a per-watch dashboard ("Xplora Demo") with all the bundled cards
// No real Xplora login is ever performed (ADR 0009) — the demo swap keys off the email sentinel.
//
// This is the shared fixture recipe for browser e2e: the Playwright `globalSetup` imports
// `seedDemoHa`; it also runs standalone as a CLI:
//   node tests/e2e/seed-demo.mjs [baseUrl]     # default $HA_URL or http://localhost:8123
//
// Prerequisites (caller's job): HA already running and FRESH (onboarding not done), with the
// integration discoverable (`<config>/custom_components/xplora_watch`). Node 18+ (fetch) / 22+ (WebSocket).

const DEFAULT_URL = process.env.HA_URL || "http://localhost:8123";
const OWNER = { name: "E2E Admin", username: "admin", password: "password", language: "en" };
const DEMO_ACCOUNTS = [
  { email: "demo@xplora-watch.invalid", alias: "Dad" },
  { email: "demo-second-parent@xplora-watch.invalid", alias: "Mom" },
  { email: "demo-contact@xplora-watch.invalid", alias: "Contact" },
  { email: "demo-offline@xplora-watch.invalid", alias: "Offline" },
];
const CRED_DEFAULTS = { password: "demo", language: "en", timezone: "Europe/Berlin", userlang: "en-GB" };
const DASHBOARD = { title: "Xplora Demo", url_path: "xplora-demo", icon: "mdi:watch" };

export async function seedDemoHa(baseUrl = DEFAULT_URL) {
  const base = baseUrl.replace(/\/$/, "");
  const clientId = `${base}/`;
  let token = null;

  async function req(method, path, { json, form } = {}) {
    const headers = {};
    let body;
    if (json !== undefined) { headers["Content-Type"] = "application/json"; body = JSON.stringify(json); }
    if (form !== undefined) { headers["Content-Type"] = "application/x-www-form-urlencoded"; body = new URLSearchParams(form).toString(); }
    if (token) headers["Authorization"] = `Bearer ${token}`;
    const res = await fetch(`${base}${path}`, { method, headers, body });
    const text = await res.text();
    let data; try { data = text ? JSON.parse(text) : null; } catch { data = text; }
    return { status: res.status, ok: res.ok, data };
  }
  const fail = (msg, extra) => { throw new Error(extra !== undefined ? `${msg}\n${JSON.stringify(extra, null, 2)}` : msg); };

  // Open one authenticated WebSocket session and run `fn(call)` against it (call(type, extra) → result).
  function withWs(fn) {
    return new Promise((resolve, reject) => {
      const ws = new WebSocket(`${base.replace(/^http/, "ws")}/api/websocket`);
      let id = 0; const pending = new Map();
      const timer = setTimeout(() => { try { ws.close(); } catch {} reject(new Error("ws timeout")); }, 90000);
      const call = (type, extra = {}) => new Promise((res, rej) => { const mid = ++id; pending.set(mid, { res, rej }); ws.send(JSON.stringify({ id: mid, type, ...extra })); });
      ws.addEventListener("message", async (ev) => {
        const m = JSON.parse(ev.data);
        if (m.type === "auth_required") return ws.send(JSON.stringify({ type: "auth", access_token: token }));
        if (m.type === "auth_invalid") { clearTimeout(timer); ws.close(); return reject(new Error("ws auth invalid")); }
        if (m.type === "auth_ok") { try { const r = await fn(call); clearTimeout(timer); ws.close(); resolve(r); } catch (e) { clearTimeout(timer); ws.close(); reject(e); } return; }
        if (m.type === "result") { const p = pending.get(m.id); if (p) { pending.delete(m.id); m.success ? p.res(m.result) : p.rej(new Error(JSON.stringify(m.error))); } }
      });
      ws.addEventListener("error", () => { clearTimeout(timer); reject(new Error("ws error")); });
    });
  }
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

  // --- 1. onboarding ----------------------------------------------------------------------------
  const steps = await req("GET", "/api/onboarding");
  if (steps.status === 404) fail("onboarding endpoint missing — HA not ready?", steps);
  if ((Array.isArray(steps.data) ? steps.data : []).some((s) => s.step === "user" && s.done))
    fail("HA is not fresh — the 'user' onboarding step is already done. Seed against an empty config dir.", steps.data);
  const u = await req("POST", "/api/onboarding/users", { json: { client_id: clientId, ...OWNER } });
  if (!u.ok || !u.data?.auth_code) fail("onboarding/users failed", u);
  const t = await req("POST", "/auth/token", { form: { grant_type: "authorization_code", code: u.data.auth_code, client_id: clientId } });
  if (!t.ok || !t.data?.access_token) fail("token exchange failed", t);
  token = t.data.access_token;
  console.log("✓ owner created + token acquired (admin/password)");
  for (const step of ["core_config", "analytics"]) await req("POST", `/api/onboarding/${step}`, { json: {} });
  await req("POST", "/api/onboarding/integration", { json: { client_id: clientId, redirect_uri: `${base}/?auth_callback=1` } });
  console.log("✓ onboarding complete");

  // --- 2. add the four demo entries via the config flow -----------------------------------------
  for (const { email, alias } of DEMO_ACCOUNTS) {
    const start = await req("POST", "/api/config/config_entries/flow", { json: { handler: "xplora_watch", show_advanced_options: false } });
    if (!start.ok) fail(`flow start failed for ${email} (is the integration discoverable via <config>/custom_components?)`, start);
    const flowId = start.data.flow_id;
    if (start.data.type === "menu") {
      const m = await req("POST", `/api/config/config_entries/flow/${flowId}`, { json: { next_step_id: "user_email" } });
      if (m.data?.step_id !== "user_email") fail(`menu→user_email failed for ${email}`, m);
    }
    const cred = await req("POST", `/api/config/config_entries/flow/${flowId}`, { json: { email, ...CRED_DEFAULTS } });
    if (cred.data?.step_id !== "alias") fail(`credentials step did not advance to alias for ${email}`, cred);
    const created = await req("POST", `/api/config/config_entries/flow/${flowId}`, { json: { account_alias: alias } });
    if (created.data?.type !== "create_entry") fail(`entry not created for ${email}`, created);
    console.log(`✓ added demo entry: ${alias.padEnd(8)} (${email})`);
  }

  // --- 3. complete each entry's OPTIONS flow (selects the watch → creates entities) -------------
  const entries = (await withWs((call) => call("config_entries/get"))).filter((e) => e.domain === "xplora_watch");
  for (const e of entries) {
    const start = await req("POST", "/api/config/config_entries/options/flow", { json: { handler: e.entry_id, show_advanced_options: true } });
    if (!start.ok || start.data?.step_id !== "init") fail(`options flow start failed for ${e.title}`, start);
    const input = {};
    for (const f of start.data.data_schema || []) if (f.default !== undefined) input[f.name] = f.default;
    const done = await req("POST", `/api/config/config_entries/options/flow/${start.data.flow_id}`, { json: input });
    if (done.data?.type !== "create_entry") fail(`options flow submit failed for ${e.title}`, done);
    console.log(`✓ options set (watch selected): ${e.title}`);
  }
  await sleep(4000); // let the entries reload and create entities

  // --- 4. enable disabled-by-default entities, then 5. build dashboards -------------------------
  const summary = await withWs(async (call) => {
    const reg = (await call("config/entity_registry/list")).filter((e) => e.platform === "xplora_watch");
    for (const e of reg.filter((e) => e.disabled_by)) await call("config/entity_registry/update", { entity_id: e.entity_id, disabled_by: null });
    const devices = await call("config/device_registry/list");
    const devName = Object.fromEntries(devices.map((d) => [d.id, d.name_by_user || d.name]));
    const byDevice = {};
    for (const e of reg) (byDevice[e.device_id] ||= []).push(e.entity_id);
    const pick = (ids, re) => ids.find((id) => re.test(id));
    const views = [], built = [];
    for (const [devId, ids] of Object.entries(byDevice)) {
      const name = devName[devId] || devId;
      const anchor = pick(ids, /^device_tracker\./) || pick(ids, /_message/) || pick(ids, /_last_update/) || ids[0];
      const alarms = pick(ids, /_alarms/), silents = pick(ids, /_silents/), message = pick(ids, /_message/);
      const cards = [
        { type: "custom:xplora-watch-overview-card", entity: anchor },
        { type: "custom:xplora-watch-map-card", entity: anchor },
        { type: "custom:xplora-watch-actions-card", entity: anchor },
      ];
      if (alarms) cards.push({ type: "custom:xplora-watch-card", entity: alarms, title: "Alarms" });
      if (silents) cards.push({ type: "custom:xplora-watch-card", entity: silents, title: "Silent times" });
      if (message) cards.push({ type: "custom:xplora-watch-chat-card", entity: message, title: "Chat" });
      views.push({ title: name, path: name.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, ""), cards });
      built.push(`${name} (${cards.length} cards)`);
    }
    try { await call("lovelace/dashboards/create", { ...DASHBOARD, mode: "storage", show_in_sidebar: true, require_admin: false }); } catch {}
    await call("lovelace/config/save", { url_path: DASHBOARD.url_path, config: { title: DASHBOARD.title, views } });
    return built;
  });
  console.log(`✓ enabled card entities + built dashboard "${DASHBOARD.title}": ${summary.join(", ")}`);

  // --- 6. trigger a functions refresh so alarms/silents/chat populate (best-effort) -------------
  await withWs(async (call) => {
    const states = await call("get_states");
    const buttons = states.map((s) => s.entity_id).filter((id) => /^button\..*refresh_functions/.test(id));
    for (const entity_id of buttons) { try { await call("call_service", { domain: "button", service: "press", service_data: {}, target: { entity_id } }); } catch {} }
    return buttons.length;
  }).catch(() => {});

  // --- verify -----------------------------------------------------------------------------------
  const verify = await withWs(async (call) => {
    const ce = (await call("config_entries/get")).filter((e) => e.domain === "xplora_watch");
    const dashes = await call("lovelace/dashboards/list");
    return { entries: ce.length, loaded: ce.filter((e) => e.state === "loaded").length, hasDashboard: dashes.some((d) => d.url_path === DASHBOARD.url_path) };
  });
  if (verify.entries !== 4 || verify.loaded !== 4) fail(`expected 4 loaded entries, got ${verify.loaded}/${verify.entries}`);
  if (!verify.hasDashboard) fail(`dashboard "${DASHBOARD.url_path}" was not created`);
  console.log(`✓ VERIFIED: 4 demo entries loaded, dashboard "${DASHBOARD.url_path}" present, zero real accounts`);
  return { entries: verify.entries, dashboard: DASHBOARD.url_path };
}

// CLI entrypoint.
if (import.meta.url === `file://${process.argv[1]}`) {
  seedDemoHa(process.argv[2])
    .then((r) => console.log(`\n✓ Demo-only HA seeded (dashboard: /${r.dashboard}).`))
    .catch((err) => { console.error(`\n✗ ${err.message}`); process.exit(1); });
}
