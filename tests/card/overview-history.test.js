import { beforeAll, describe, expect, it } from "vitest";

import { loadBundle } from "./helpers.js";

// Overview card location-history row + date-bar/calendar-popover popup. jsdom has no
// `ha-map`/`loadCardHelpers`, so these tests exercise the LIST fallback branch, the date bar
// (prev/next/today + label), the calendar popover (data days highlighted, others disabled), and the
// per-day websocket fetch (Leaflet rendering is covered in overview-history-map).

const DEVICE = "dev1";
const HIST = "sensor.xplora_kid_watch_location_history";

function points(n, baseTm = 1700000000000) {
  return Array.from({ length: n }, (_, i) => ({
    tm: baseTm + i * 60000,
    lat: 52.52 + i * 0.001,
    lng: 13.405 + i * 0.001,
    addr: `Stop ${i}`,
  }));
}

// Same YYYY-MM-DD (UTC) the card computes (the fixtures use timezone: UTC).
function dayKey(ms) {
  return new Intl.DateTimeFormat("en-CA", { timeZone: "UTC", year: "numeric", month: "2-digit", day: "2-digit" }).format(new Date(ms));
}

function makeHass({ attrPoints = points(2), total, historyDays = [], callWS, callService } = {}) {
  const hass = {
    states: {
      [HIST]: {
        entity_id: HIST,
        state: String(attrPoints.length),
        last_changed: "2026-06-27T09:00:00Z",
        last_updated: "2026-06-27T10:00:00Z",
        attributes: {
          entry_id: "entry1",
          wuid: "watch1",
          timezone: "UTC", // deterministic day keys in tests
          history_days: historyDays,
          history_points: attrPoints,
          history_total_points: total ?? attrPoints.length,
          history_window_hours: 24,
          friendly_name: "Kid Watch Location History",
        },
      },
    },
    entities: { [HIST]: { entity_id: HIST, device_id: DEVICE } },
    devices: { [DEVICE]: { name: "Kid Watch" } },
    locale: { language: "en" },
    callService: callService || (async () => {}),
  };
  if (callWS) hass.callWS = callWS;
  return hass;
}

function mount(hass, config = { device: DEVICE }) {
  const el = document.createElement("xplora-watch-overview-card");
  el.setConfig(config);
  document.body.appendChild(el);
  el.hass = hass;
  return el;
}

async function waitFor(fn, timeout = 1000) {
  const start = Date.now();
  for (;;) {
    const v = fn();
    if (v) return v;
    if (Date.now() - start > timeout) throw new Error("waitFor timeout");
    await new Promise((r) => setTimeout(r, 5));
  }
}

beforeAll(async () => {
  await loadBundle();
});

describe("location history row", () => {
  it("renders a history row with the retained point count", () => {
    const el = mount(makeHass({ attrPoints: points(2), total: 5 }));
    const row = el.shadowRoot.querySelector("[data-history]");
    expect(row).toBeTruthy();
    expect(row.textContent).toContain("Location history");
    expect(row.textContent).toContain("5 points kept");
  });

  it("hides the history row when show_history is false", () => {
    const el = mount(makeHass(), { device: DEVICE, show_history: false });
    expect(el.shadowRoot.querySelector("[data-history]")).toBeNull();
  });
});

describe("history popup (date bar + calendar popover)", () => {
  it("opens with a date bar showing today; the popover is closed and the day's points are listed", async () => {
    const el = mount(makeHass({ attrPoints: points(3) }));
    el.shadowRoot.querySelector("[data-history]").click();
    const body = await waitFor(() => el.shadowRoot.querySelector(".hist-body[data-history-mode]"));
    const today = dayKey(Date.now());
    expect(el.shadowRoot.querySelector(".hist-bar")).toBeTruthy();
    const label = el.shadowRoot.querySelector(".hist-date-label").textContent;
    expect(label).toContain(today.slice(0, 4)); // year (locale-numeric, so assert the parts)
    expect(label).toContain(today.slice(8, 10)); // day of month
    // Popover starts closed; the next/today buttons are disabled because we're already on today.
    expect(el.shadowRoot.querySelector(".hist-pop").hidden).toBe(true);
    expect(el.shadowRoot.querySelector('[data-step="1"]').disabled).toBe(true);
    expect(el.shadowRoot.querySelector(".hist-today").disabled).toBe(true);
    expect(el.shadowRoot.querySelector('[data-step="-1"]').disabled).toBe(false);
    expect(body.getAttribute("data-history-mode")).toBe("list");
    expect(body.querySelectorAll(".hist-item").length).toBe(3);
  });

  it("clicking the date opens the calendar popover with today highlighted+selected", async () => {
    const el = mount(makeHass({ attrPoints: points(3) }));
    el.shadowRoot.querySelector("[data-history]").click();
    await waitFor(() => el.shadowRoot.querySelector(".hist-bar"));
    el.shadowRoot.querySelector(".hist-date").click();
    expect(el.shadowRoot.querySelector(".hist-pop").hidden).toBe(false);
    const today = dayKey(Date.now());
    const todayCell = el.shadowRoot.querySelector(`.hist-cell[data-day="${today}"]`);
    expect(todayCell).toBeTruthy();
    expect(todayCell.classList.contains("today")).toBe(true);
    expect(todayCell.classList.contains("sel")).toBe(true);
    expect(todayCell.classList.contains("has-data")).toBe(true); // recent days are always selectable
  });

  it("the previous-day arrow steps back and the today button jumps forward", async () => {
    const today = dayKey(Date.now());
    const yesterday = dayKey(Date.now() - 86400000);
    const wsCalls = [];
    const el = mount(
      makeHass({
        callWS: async (msg) => {
          wsCalls.push(msg);
          return { wuid: msg.wuid, day: msg.day, points: points(2) };
        },
      }),
    );
    el.shadowRoot.querySelector("[data-history]").click();
    await waitFor(() => el.shadowRoot.querySelector(".hist-bar"));
    // Step back one day -> label shows yesterday, websocket fetches it, today button re-enables.
    el.shadowRoot.querySelector('[data-step="-1"]').click();
    await waitFor(() => wsCalls.some((c) => c.day === yesterday));
    expect(el.shadowRoot.querySelector(".hist-date-label").textContent).toContain(yesterday.slice(8, 10));
    expect(el.shadowRoot.querySelector(".hist-today").disabled).toBe(false);
    // Today button jumps back to today and disables itself again.
    el.shadowRoot.querySelector(".hist-today").click();
    await waitFor(() => wsCalls.some((c) => c.day === today));
    expect(el.shadowRoot.querySelector(".hist-today").disabled).toBe(true);
  });

  it("highlights data days, disables empty days, and bounds month navigation", () => {
    const el = mount(makeHass());
    const cal = document.createElement("div");
    el._renderCalendarInto(cal, {
      year: 2026,
      month: 0, // January 2026
      selectable: new Set(["2026-01-15", "2026-01-20"]),
      selected: "2026-01-20",
      today: "2026-01-25",
      minKey: "2025-11-10",
    });
    expect(cal.querySelector(".hist-cal-title").textContent).toContain("January");
    const c15 = cal.querySelector('.hist-cell[data-day="2026-01-15"]');
    expect(c15.classList.contains("has-data")).toBe(true);
    expect(c15.disabled).toBe(false);
    expect(cal.querySelector('.hist-cell[data-day="2026-01-20"]').classList.contains("sel")).toBe(true);
    const c16 = cal.querySelector('.hist-cell[data-day="2026-01-16"]'); // not selectable
    expect(c16.classList.contains("has-data")).toBe(false);
    expect(c16.disabled).toBe(true);
    // minKey is Nov 2025 (prev enabled); today is Jan 2026 (next disabled at the current month).
    expect(cal.querySelector('[data-nav="-1"]').disabled).toBe(false);
    expect(cal.querySelector('[data-nav="1"]').disabled).toBe(true);
  });

  it("refreshes functions for the watch when the row is tapped", () => {
    const calls = [];
    const el = mount(makeHass({ callService: async (...a) => calls.push(a) }));
    el.shadowRoot.querySelector("[data-history]").click();
    const refresh = calls.find((c) => c[1] === "refresh_functions");
    expect(refresh).toBeTruthy();
    // Service targets the watch by the tapped list entity (resolved to its device server-side).
    expect(refresh[2]).toMatchObject({ entity_id: [HIST] });
  });

  it("loads a clicked calendar day via the websocket", async () => {
    const today = dayKey(Date.now());
    const dnum = Number(today.slice(8, 10));
    // A day guaranteed visible in the current month and <= today (falls back to today on the 1st).
    const otherDay = dnum >= 2 ? `${today.slice(0, 7)}-${String(dnum - 1).padStart(2, "0")}` : today;
    const wsCalls = [];
    const el = mount(
      makeHass({
        historyDays: [otherDay],
        callWS: async (msg) => {
          wsCalls.push(msg);
          return { wuid: msg.wuid, day: msg.day, points: points(4) };
        },
      }),
    );
    el.shadowRoot.querySelector("[data-history]").click();
    await waitFor(() => el.shadowRoot.querySelector(".hist-bar"));
    el.shadowRoot.querySelector(".hist-date").click(); // open the calendar popover
    const cell = await waitFor(() => el.shadowRoot.querySelector(`.hist-cell[data-day="${otherDay}"]`));
    expect(cell.classList.contains("has-data")).toBe(true);
    cell.click();
    await waitFor(() => wsCalls.some((c) => c.day === otherDay));
    await waitFor(() => el.shadowRoot.querySelectorAll(".hist-item").length === 4);
    expect(el.shadowRoot.querySelector(".hist-pop").hidden).toBe(true); // selecting a day closes the popover
  });
});
