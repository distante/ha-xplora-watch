# Changelog

All notable changes to this integration are documented here. This project adheres to
[Semantic Versioning](https://semver.org/).

## v1.2.0

Best-effort service fan-out across accounts. A single service call can target several watches at
once — an area/floor/label target or a multi-device pick — and those watches can span multiple
accounts. Every service now shares one fan-out executor that acts on **every watch it can** instead
of letting one un-actionable watch cancel the whole command.

### Behaviour changes

- **A bulk command does the bulk of the work.** If your selection includes a watch this account is
  only a **Contact** of, that watch is quietly skipped (its command is never even sent to the Xplora
  server) and the watches you **Guardian** are still actioned.
- **One account's problem no longer blocks the others.** A temporary rate-limit or expired login on
  one account stops the rest of *that* account's watches (so a throttled account isn't pushed toward
  a ban) but the call continues to your other accounts, whose sessions are independent.
- **Honest feedback instead of silent success or silent failure.**
  - A call that could action **nothing** now raises a clear error explaining why — you targeted no
    Xplora watch, you're only a Contact, the watch appeared **offline**, or the server couldn't be
    reached — rather than returning as if it worked.
  - A call that **partly** succeeds actions the reachable watches and posts a single notification
    listing what was skipped and why. The notification updates in place on repeated runs and clears
    itself once a run is fully clean, so a repeatedly-partial automation never stacks notifications.
- **Targeting is more complete.** `floor_id` / `label_id` targets, and an entity assigned to an area
  whose device lives elsewhere, now resolve to the right watch instead of silently matching nothing
  (targeting now uses Home Assistant's native target helper).

The service **interface is unchanged** — the Watch(es) device picker and every field are the same, so
existing automations and dashboard service calls keep working; only the per-watch behaviour and
feedback changed.

### Try it with no watch

The network-free demo mode now offers **four** accounts so you can see the multi-account fan-out
without any real device: sign in with `demo@xplora-watch.invalid` (Guardian of "Patrick"),
`demo-second-parent@xplora-watch.invalid` (Guardian of "Rosa"), `demo-contact@xplora-watch.invalid`
(a Contact of "Timmy"), and `demo-offline@xplora-watch.invalid` (Guardian of "Max", whose watch is
offline); any password works. Put all four watch devices in one area, then a single service call
targeting that area acts on the online Guardian watches, skips the Contact one, and reports the
offline one as offline — with the partial-success notification listing what didn't run. Target only
the Contact watch to see the `not_guardian` error, or only the offline watch to see `watch_offline`.

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
