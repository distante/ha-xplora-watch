# Migrating from the original Ludy87/xplora_watch integration

This fork keeps the same integration domain (`xplora_watch`) and the same config-entry version as
the original, so it's installed **in place of** it, not alongside it — your existing config entry,
login, and entities carry over. You do **not** need to remove and re-add the integration or
re-enter your credentials.

## What's handled automatically

The first time Home Assistant starts on the new code:

- Entity unique IDs are reformatted to the current naming scheme. Legacy dash/space-separated
  unique IDs are rewritten to the underscore-separated form used today; entity IDs, history, and
  customizations are preserved — only the internal unique ID changes.
- The old per-watch alarm/silent `switch.*` entities are removed. They were replaced by the
  `*_alarms` / `*_silents` sensors described in
  [Alarms & Silent Times](alarms-and-silent-times.md). If you have automations, scripts, or
  dashboards referencing the old switches, point them at the new sensors (see that page for the
  attribute layout).
- A previously configured polling interval is kept, but snapped to the nearest
  [supported preset](polling.md) (30 minutes minimum) — it is **not** reset to "off". Polling-off
  is only the default for brand-new installs; upgrades keep polling, just at a safer interval.

## What you still need to do yourself

1. Back up your Home Assistant instance before upgrading, as you would for any integration update.
2. If you added the original as a HACS custom repository (pointing at `Ludy87/xplora_watch`),
   remove that custom repository and add this one (`distante/ha-xplora-watch`) instead — HACS
   tracks repositories by URL, not by integration domain, so it won't follow the fork on its own.
   If you installed manually, just replace the contents of `custom_components/xplora_watch/`.
3. Restart Home Assistant once after upgrading so the entity/option migrations above can run.
4. Open the integration's **Options** afterward and review the settings this fork adds: the scan
   interval is now a fixed dropdown of presets (instead of a free-form number) and there's a new
   "Mark chat messages as read while polling" toggle (`auto_mark_read`, default off).
5. For the rate-limit-safe behavior this fork exists to provide, consider setting the scan interval
   to **Off** and driving updates via the `xplora_watch.see` service on your own schedule instead of
   keeping a migrated polling interval — see [Update interval (polling)](polling.md).
