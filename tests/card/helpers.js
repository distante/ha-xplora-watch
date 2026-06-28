// Shared helpers for the card tests: import the bundle once, build mock hass/state objects.

// Importing the bundle for its side effects registers all four custom elements. A syntax error in
// the bundle (e.g. a stray backtick in a _css() comment) makes this import throw -> every test
// fails loudly, which is exactly the regression guard we want.
export async function loadBundle() {
  await import("../../custom_components/xplora_watch/www/xplora-watch-card.js");
}

const MESSAGE_ENTITY = "sensor.watch_message";

// A SimpleChat-shaped entry (see pyxplora_api/model.py). `sender` is the watch wuid for incoming
// messages or the account id for ones we sent.
export function chat(msgId, { type = "TEXT", sender = "watch1", text = "", emoticonId, emojiId, create = 1700000000000 } = {}) {
  return {
    msgId,
    type,
    sender: { id: sender },
    data: { text, sender_name: "Dana", emoticon_id: emoticonId, emoji_id: emojiId },
    create,
  };
}

// A minimal `hass` exposing one message sensor. `lastUpdated` drives the card's re-render guard;
// `state` deliberately stays constant so tests prove attribute-only updates still re-render.
export function makeHass(messages, { lastUpdated = "2026-06-27T10:00:00Z", locale = "en", callService } = {}) {
  return {
    states: {
      [MESSAGE_ENTITY]: {
        entity_id: MESSAGE_ENTITY,
        state: "ok",
        last_changed: "2026-06-27T09:00:00Z",
        last_updated: lastUpdated,
        attributes: {
          entry_id: "entry1",
          wuid: "watch1",
          account_user_id: "acct1",
          friendly_name: "Dana Watch Message",
          list: messages,
        },
      },
    },
    locale: { language: locale },
    callService: callService || (async () => {}),
  };
}

// Create, configure, connect, and hydrate a chat card. Returns the element; read `el.shadowRoot`.
export function mountChat(messages, hassOpts) {
  const el = document.createElement("xplora-watch-chat-card");
  el.setConfig({ entity: MESSAGE_ENTITY });
  document.body.appendChild(el);
  el.hass = makeHass(messages, hassOpts);
  return el;
}

export function bubbleTexts(el) {
  return [...el.shadowRoot.querySelectorAll(".bubble-text")].map((n) => n.textContent);
}

export function bubbleRows(el) {
  return [...el.shadowRoot.querySelectorAll(".bubble-row")];
}

export { MESSAGE_ENTITY };
