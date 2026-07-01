import { afterEach, beforeAll, describe, expect, it } from "vitest";

import { loadBundle } from "./helpers.js";

beforeAll(async () => {
  await loadBundle();
});

afterEach(() => {
  document.body.innerHTML = "";
});

// A hass whose bound entity has NO state. `registered` controls whether the entity registry
// (`hass.entities`) lists it: a registered entity is merely coming up ("Waiting for …"), while an
// unregistered one genuinely does not exist -- typically a card left pointing at an old entity id
// after a slug rename, where "Waiting" would wait forever.
function hassWithout(entityId, { registered = false } = {}) {
  return {
    states: {},
    entities: registered ? { [entityId]: { entity_id: entityId } } : {},
    locale: { language: "en" },
    callService: async () => {},
  };
}

function mount(tag, entityId, hass) {
  const el = document.createElement(tag);
  el.setConfig({ entity: entityId });
  document.body.appendChild(el);
  el.hass = hass;
  return el;
}

const text = (el) => el.shadowRoot.textContent.replace(/\s+/g, " ").trim();

describe.each([["xplora-watch-card"], ["xplora-watch-chat-card"]])("%s — bound entity has no state", (tag) => {
  const ENTITY = "sensor.xplora_patrick_watch_message";

  it("tells the user the entity is not found when it isn't in the registry", () => {
    const el = mount(tag, ENTITY, hassWithout(ENTITY, { registered: false }));
    const t = text(el);
    expect(t).toContain("not found");
    expect(t).toContain(ENTITY); // name the offending id so the user can fix the card
    expect(t).not.toContain("Waiting for");
  });

  it("still says 'waiting' while a registered entity is only coming up", () => {
    const el = mount(tag, ENTITY, hassWithout(ENTITY, { registered: true }));
    const t = text(el);
    expect(t).toContain("Waiting for");
    expect(t).not.toContain("not found");
  });

  it("falls back to 'waiting' when the entity registry isn't available yet", () => {
    const el = mount(tag, ENTITY, { states: {}, locale: { language: "en" }, callService: async () => {} });
    expect(text(el)).toContain("Waiting for");
  });

  it("switches from 'waiting' to 'not found' once the registry loads without the entity", () => {
    const el = mount(tag, ENTITY, { states: {}, locale: { language: "en" }, callService: async () => {} });
    expect(text(el)).toContain("Waiting for"); // registry not loaded yet
    el.hass = hassWithout(ENTITY, { registered: false }); // registry now loaded; entity absent
    expect(text(el)).toContain("not found");
  });
});
