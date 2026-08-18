# Alarms & silent times

> **⚠️ Breaking change:** the per-entry `switch.*_alarm_*` / `switch.*_silent_*` entities have been
> **removed**. Each watch's alarms and silent-time windows are now exposed as **two stable list
> sensors** instead, and are managed through **services** (and an optional dashboard card). The old
> switches appeared and disappeared as the lists changed on the watch and could only be toggled;
> the new model lets you **view, create, edit, delete and enable/disable** entries from the UI
> without entities churning. The old `switch.*_alarm_*` / `switch.*_silent_*` registry entries are
> **removed automatically** the first time the upgraded integration starts (restart Home Assistant
> after updating), and any dashboard card that referenced them should be replaced with the new
> [card](dashboard-cards.md#alarms--silent-times-card).

## Sensors

Per watch (both **disabled by default** — enable them on the device page):

| Sensor               | State              | Key attributes                                                            |
| -------------------- | ------------------ | ------------------------------------------------------------------------- |
| `sensor.<watch>_alarms`  | number of alarms   | `alarm`: list of `{id, name, start, weekRepeat, weekdays, days, status}`  |
| `sensor.<watch>_silents` | number of silents  | `silent`: list of `{id, start, end, weekRepeat, weekdays, days, status}`  |

Each entry carries the `id` you pass to the update/delete/enable services, the time(s) as `HH:MM`,
and the repeat days in three forms: `weekRepeat` (raw 7-char `0`/`1` string, index 0 = Sunday),
`weekdays` (canonical keys, e.g. `["mon","tue"]`) and `days` (localized, e.g. `Mon, Tue`).

## Services

| Service                          | Purpose                                  |
| --------------------------------- | ----------------------------------------- |
| `xplora_watch.create_alarm`      | Create an alarm                          |
| `xplora_watch.update_alarm`      | Change an alarm's time / days / name     |
| `xplora_watch.delete_alarm`      | Delete an alarm                          |
| `xplora_watch.set_alarm_enabled` | Enable or disable an alarm               |
| `xplora_watch.create_silent`     | Create a silent-time window              |
| `xplora_watch.update_silent`     | Change a silent window's start/end/days  |
| `xplora_watch.delete_silent`     | Delete a silent-time window              |
| `xplora_watch.set_silent_enabled`| Enable or disable a silent-time window   |
| `xplora_watch.turn_all_alarms_on` / `..._off`  | Enable or disable **every** alarm on the watch(es) in one call         |
| `xplora_watch.turn_all_silents_on` / `..._off` | Enable or disable **every** silent-time window on the watch(es) in one call |

Common targeting: every service has a **Watch(es)** field — a device picker filtered to your Xplora
watches (e.g. `Dana Watch (Mom)`), so you choose the watch directly; one pick identifies both the
watch and the account behind it. Times (`start`, `end`) are `HH:MM`; `weekdays` is a list of
`mon`,`tue`,`wed`,`thu`,`fri`,`sat`,`sun`. The `turn_all_*` services need only the watch
(they enumerate every entry themselves). The `alarm_id` / `silent_id` for updates and deletes come
from the matching sensor's attributes — or, more easily, from the **copy buttons on the dashboard
card** (see [Alarms & Silent Times card](dashboard-cards.md#alarms--silent-times-card)), which hand
you the id or a complete, ready-to-paste service call.

```yaml
# Create a school-night silent window, 22:00–07:00, Mon–Fri
action: xplora_watch.create_silent
data:
  device_id: <watch device>     # the "Watch(es)" field — the "Dana Watch (Mom)" device
  start: "22:00"
  end: "07:00"
  weekdays: [mon, tue, wed, thu, fri]
```

```yaml
# Disable a single alarm (alarm_id taken from sensor.<watch>_alarms attributes)
action: xplora_watch.set_alarm_enabled
data:
  device_id: <watch device>
  alarm_id: alarm-123
  enabled: false
```

```yaml
# Silence everything for the day (e.g. a school holiday) — no per-entry ids needed
action: xplora_watch.turn_all_silents_off
data:
  device_id: <watch device>
```

> **Note (polling off by default):** with polling disabled, a successful create/update/delete/toggle
> refreshes only that one list, so the sensor reflects the change immediately without triggering a
> full account poll.

For the dashboard card that manages these entries visually — including the copy-service-call
buttons referenced above — see [Alarms & Silent Times card](dashboard-cards.md#alarms--silent-times-card).
