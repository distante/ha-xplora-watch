import { beforeAll, describe, expect, it } from "vitest";

import { loadBundle } from "./helpers.js";

// The single most valuable guard: the bundle parses and all four cards register. A stray backtick in
// a _css() template-literal comment (which broke the whole module twice during development) makes the
// import throw, failing every test here.
describe("bundle registration", () => {
  beforeAll(async () => {
    await loadBundle();
  });

  it.each(["xplora-watch-card", "xplora-watch-actions-card", "xplora-watch-overview-card", "xplora-watch-chat-card"])(
    "defines <%s>",
    (tag) => {
      expect(customElements.get(tag)).toBeTypeOf("function");
    },
  );

  it("registers cards in window.customCards", () => {
    const types = (window.customCards || []).map((c) => c.type);
    expect(types).toContain("xplora-watch-chat-card");
  });
});
