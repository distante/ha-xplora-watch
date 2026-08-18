# Update interval (polling)

Polling is the integration's only standing source of rate-limit/ban risk, so it is **off by
default** (no recurring cloud calls). The update interval is chosen from a small set of safe
presets: **Off / Every 30 minutes / Every hour / Every 2 hours** (in the integration's
_Options_).

With polling **Off**, data refreshes only when you call the `xplora_watch.see` service. For
faster or conditional updates, create your own automation that calls `xplora_watch.see` on
whatever schedule/trigger you want (and accept the corresponding ban risk):

```yaml
# Example: refresh every 10 minutes only while someone is home
automation:
  - alias: Xplora refresh
    trigger:
      - platform: time_pattern
        minutes: "/10"
    condition:
      - condition: state
        entity_id: zone.home
        state: "1"   # at least one person home
    action:
      - action: xplora_watch.see
        data:
          device_id: <your watch device>   # the "Watch(es)" field: pick one or more "Dana Watch (Mom)" devices
```

Existing installs are migrated automatically: a previously configured interval is snapped to
the nearest preset the next time you open _Options_ (anything faster than 30 minutes becomes
30 minutes).

## Alarms / silent times / safe zones interval

Alarms, silent-time windows and safe-zone definitions can't be bundled into the main status
fetch (Xplora serves each from its own per-watch request), but they rarely change — so they have
their **own** interval in _Options_, **"Alarms/silent/safe-zone refresh"**:
**Off (manual only) / Every 6 hours / Daily / With every poll**, defaulting to **Off**.

- With it **Off**, this data is fetched once and then reused, so normal polls stay lean. Refresh
  it on demand by calling the `xplora_watch.refresh_functions` service, or simply by **tapping the
  alarms/silent count on the overview card** (which opens the management list and refreshes it).
- The alarm/silent edit services (create/update/delete/enable) always refresh their own list
  immediately, so changes you make show up regardless of this setting.

## Auto-mark messages as read

A separate _Options_ toggle, **"Mark chat messages as read while polling"** (default **off**),
controls whether fetched chat messages are marked read on Xplora's servers. Leaving it off
preserves the unread-message count and avoids extra write traffic.
