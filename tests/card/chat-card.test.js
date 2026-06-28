import { beforeAll, describe, expect, it } from "vitest";

import { bubbleRows, bubbleTexts, chat, loadBundle, makeHass, mountChat } from "./helpers.js";

beforeAll(async () => {
  await loadBundle();
});

describe("setConfig", () => {
  it("throws without an entity", () => {
    const el = document.createElement("xplora-watch-chat-card");
    expect(() => el.setConfig({})).toThrow(/entity/);
  });
});

describe("message direction (_incoming)", () => {
  it("treats the account id as outgoing and the wuid as incoming", () => {
    const el = mountChat([]);
    expect(el._incoming(chat("a", { sender: "acct1" }))).toBe(false); // we sent it
    expect(el._incoming(chat("b", { sender: "watch1" }))).toBe(true); // from the watch
  });
});

describe("emoji handling", () => {
  it("_isEmojiOnly distinguishes pure-emoji from mixed text", () => {
    const el = mountChat([]);
    expect(el._isEmojiOnly("😀")).toBe(true);
    expect(el._isEmojiOnly("😀😁")).toBe(true);
    expect(el._isEmojiOnly("hi 😀")).toBe(false);
    expect(el._isEmojiOnly("hello")).toBe(false);
    expect(el._isEmojiOnly("™")).toBe(false); // not enlarged for ordinary symbols
  });

  it("_richText wraps emoji in spans and HTML-escapes the text", () => {
    const el = mountChat([]);
    const html = el._richText("hi <b> 😀");
    expect(html).toContain('<span class="emoji">😀</span>');
    expect(html).toContain("&lt;b&gt;"); // escaped, not injected
    expect(html).not.toContain("<b>");
  });

  it("renders an emoji-only message bubble with the jumbo class", () => {
    const el = mountChat([chat("m1", { text: "😀" })]);
    expect(el.shadowRoot.querySelector(".bubble-text.emoji-only")).not.toBeNull();
  });
});

describe("rendering", () => {
  it("renders one bubble per message", () => {
    const el = mountChat([chat("m1", { text: "hola" }), chat("m2", { text: "adios", sender: "acct1" })]);
    expect(bubbleTexts(el)).toEqual(["hola", "adios"]);
  });

  it("places incoming left and outgoing right", () => {
    const el = mountChat([chat("in", { text: "hi", sender: "watch1" }), chat("out", { text: "yo", sender: "acct1" })]);
    const rows = bubbleRows(el);
    expect(rows[0].classList.contains("in")).toBe(true);
    expect(rows[1].classList.contains("out")).toBe(true);
  });
});

describe("re-render guard (regression: last_updated vs last_changed)", () => {
  it("re-renders when a new message arrives as an attribute change (state value unchanged)", () => {
    const el = mountChat([chat("m1", { text: "first" })]);
    expect(bubbleTexts(el)).toEqual(["first"]);

    // Same state ("ok") and same last_changed, but a new message + newer last_updated -- exactly the
    // shape of a chat refresh. Keying the guard on last_changed would miss this; last_updated catches it.
    el.hass = makeHass([chat("m1", { text: "first" }), chat("m2", { text: "second" })], {
      lastUpdated: "2026-06-27T10:05:00Z",
    });
    expect(bubbleTexts(el)).toEqual(["first", "second"]);
  });
});

describe("incremental rendering", () => {
  it("reuses the existing DOM node for an unchanged message and appends new ones", () => {
    const el = mountChat([chat("m1", { text: "first" })]);
    const firstNode = el.shadowRoot.querySelector('[data-key="m1"]');
    expect(firstNode).not.toBeNull();

    el.hass = makeHass([chat("m1", { text: "first" }), chat("m2", { text: "second" })], {
      lastUpdated: "2026-06-27T10:05:00Z",
    });

    // Same element instance survives (not a full innerHTML rebuild) -> preserves media/scroll/video.
    expect(el.shadowRoot.querySelector('[data-key="m1"]')).toBe(firstNode);
    expect(el.shadowRoot.querySelector('[data-key="m2"]')).not.toBeNull();
  });
});

describe("refresh button", () => {
  it("calls read_message for the configured watch", () => {
    const calls = [];
    const el = mountChat([chat("m1", { text: "hi" })], { callService: async (...a) => calls.push(a) });

    el.shadowRoot.querySelector(".refresh-btn").click();

    // The service call is issued synchronously before the await, so it's recorded immediately.
    expect(calls).toContainEqual(["xplora_watch", "read_message", { target: ["watch1"], user: ["entry1"] }, undefined, false]);
  });
});

describe("optimistic send", () => {
  it("shows the sent message immediately, before the server echoes it back", async () => {
    const calls = [];
    const el = mountChat([chat("m1", { text: "hi" })], {
      callService: async (domain, service, data) => {
        calls.push({ domain, service, data });
      },
    });

    el.shadowRoot.querySelector(".msg-input").value = "hello Dana";
    await el._send();

    // The send service was called, and the message appears as an outgoing bubble right away even
    // though the sensor (mock hass) still only has the original message.
    expect(calls.some((c) => c.service === "send_message")).toBe(true);
    expect(bubbleTexts(el)).toContain("hello Dana");
    const sentRow = [...bubbleRows(el)].find((r) => r.textContent.includes("hello Dana"));
    expect(sentRow.classList.contains("out")).toBe(true);
  });
});
