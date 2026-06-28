import { defineConfig } from "vitest/config";

// Tests for the bundled Lovelace cards (custom_components/xplora_watch/www/xplora-watch-card.js).
// jsdom gives us the Custom Elements + Shadow DOM APIs the cards rely on; it has no layout engine,
// so these tests cover logic, behavior, and DOM structure -- not pixel sizing. Kept separate from
// the Python pytest suite.
export default defineConfig({
  test: {
    environment: "jsdom",
    include: ["tests/card/**/*.test.js"],
    setupFiles: ["tests/card/setup.js"],
  },
});
