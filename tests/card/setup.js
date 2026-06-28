// Shims for browser APIs jsdom doesn't implement but the cards call. Run synchronously so DOM
// assertions can read the result immediately after a render.

// The cards use requestAnimationFrame for scroll-to-bottom; run the callback inline.
if (!globalThis.requestAnimationFrame) {
  globalThis.requestAnimationFrame = (cb) => {
    cb(0);
    return 0;
  };
  globalThis.cancelAnimationFrame = () => {};
}

// The chat card's fullscreen toggle calls these; jsdom lacks the Fullscreen API. The card guards on
// their presence, so leaving them undefined exercises the CSS-maximize fallback path safely.
