# Changelog

All notable changes to this integration are documented here. This project adheres to
[Semantic Versioning](https://semver.org/).

## v1.1.0

Differentiate the same watch across accounts, and re-target services onto Home Assistant devices.

### ⚠️ Breaking change — services now pick the watch device

Every service (`update`/`see`, `reboot`, `shutdown`, `send_message`, `read_message`,
`delete_message_from_app`, `fetch_history`, `refresh_functions`, `logout`, and the alarm / silent-time
CRUD + bulk-toggle services) **no longer takes the `user` and `target` selectors**. Instead each
service has a single **Watch(es)** field — a device picker filtered to your Xplora® watches
(multi-select) — so you choose the watch directly, and one pick identifies both the account and the
watch.

You must **update existing automations / scripts / dashboard service calls**: replace

```yaml
action: xplora_watch.shutdown
data:
  target: <watch-id>
  user: <entry-id> (<username>)
```

with the watch device:

```yaml
action: xplora_watch.shutdown
data:
  device_id: <device-id>   # the "Watch(es)" field — e.g. the "Dana Watch (Mom)" device
```

- The magic `all` target is **removed** — the **Watch(es)** field is multi-select instead.
- Account-level `logout` resolves the picked device to its account and logs that account out.
- Control actions (`reboot`, `shutdown`, alarm/silent CRUD) remain restricted to a watch's primary
  **Guardian**; targeting a watch the account is only a **Contact** of is rejected.
- The bundled Lovelace card and YAML automations may also target a watch by one of its `entity_id`s
  (or an `area_id`); the handler resolves either to the watch's device.

Released as a **minor** version (not the major a breaking change would normally warrant) because the
integration has no external users yet.

### Changed

- Each account's copy of a watch is now a distinctly-named Home Assistant device,
  `"<Ward> Watch (<account alias>)"`, with the account token also appended to entity slugs, so the
  same physical watch linked to several accounts is no longer ambiguous. The alias is set at setup
  (pre-filled with the account display name) and editable later via the options flow.
- `services.yaml` is now a **static** file. The integration no longer regenerates it at runtime, so
  real account names / watch ids are never written to disk.
