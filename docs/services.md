# Services reference

Every `xplora_watch.*` service targets the watch through the same **Watch(es)** field
(`device_id`) — a device picker filtered to your Xplora watches, so you always pick the watch
directly rather than a generic Home Assistant target. Most services also accept multiple watches
at once. Detailed usage (fields, YAML examples) for the alarm/silent, location-history and
messaging services lives on their feature pages, linked below; this page is the full list.

## Watch control

| Service | Purpose |
| --- | --- |
| `xplora_watch.see` | Manually refresh the watch's live status: location, battery, charging, online status and steps (the data from the watch's device list). Other data has its own services and is not refreshed here: chat messages via `read_message`, and location-history archiving via `fetch_history`. Alarms, silent times and safe zones refresh on their own interval or on demand via `refresh_functions`. See [Update interval (polling)](polling.md). |
| `xplora_watch.reboot` | Reboot the watch. Guardian-only — see [Account types](account-types.md). |
| `xplora_watch.shutdown` | Power down the watch. Guardian-only. |
| `xplora_watch.logout` | Log out the account: invalidate the current session token on Xplora's servers and force a fresh login on the next update. |

## Chat

| Service | Purpose |
| --- | --- |
| `xplora_watch.send_message` | Send a notification to the watch. See [Send a message](send-message.md). |
| `xplora_watch.read_message` | Read messages from the watch. |
| `xplora_watch.delete_message_from_app` | Delete a message in the Xplora® app. |

## Alarms

| Service | Purpose |
| --- | --- |
| `xplora_watch.create_alarm` | Create a new alarm on the watch. |
| `xplora_watch.update_alarm` | Modify an existing alarm's time, repeat days and/or name. |
| `xplora_watch.delete_alarm` | Delete an alarm from the watch. |
| `xplora_watch.set_alarm_enabled` | Enable or disable an alarm. |
| `xplora_watch.turn_all_alarms_on` / `turn_all_alarms_off` | Enable/disable all alarms on the watch(es). |

Full field reference and YAML examples: [Alarms & silent times](alarms-and-silent-times.md).

## Silent times

| Service | Purpose |
| --- | --- |
| `xplora_watch.create_silent` | Create a new silent-time window on the watch. |
| `xplora_watch.update_silent` | Modify an existing silent-time window's start, end and/or repeat days. |
| `xplora_watch.delete_silent` | Delete a silent-time window from the watch. |
| `xplora_watch.set_silent_enabled` | Enable or disable a silent-time window. |
| `xplora_watch.turn_all_silents_on` / `turn_all_silents_off` | Enable/disable all silent-time windows on the watch(es). |

Full field reference and YAML examples: [Alarms & silent times](alarms-and-silent-times.md).

## Safe zones

| Service | Purpose |
| --- | --- |
| `xplora_watch.refresh_functions` | Refresh alarms, silent times and safe zones from your watch on demand (these have a separate, default-off poll interval). See [Update interval (polling)](polling.md). |

## Location history

| Service | Purpose |
| --- | --- |
| `xplora_watch.fetch_history` | Fetch and cache a past day's location history (default: yesterday). The watch only serves the last few days, so automate this daily to keep a longer history in Home Assistant. See [Location history](location-history.md). |

All service fields, selectors and required/optional flags are also visible directly in Home
Assistant's Developer Tools → Actions, which reads them from the same source as this table
(`custom_components/xplora_watch/services.yaml`).
