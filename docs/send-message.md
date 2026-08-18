# Send a message

> **⚠️ Breaking change:** `notify.xplora_watch` has been **removed**. Use the
> `xplora_watch.send_message` service instead (available in the HA developer tools UI). **All
> services now pick the watch *device*** in a single **Watch(es)** field (e.g.
> `Dana Watch (Mom)`), filtered to your Xplora watches — one pick identifies both the watch and
> its account, replacing the old `user` + `target` selectors. Update any automation that used
> `user:` / `target:` (the magic `all` is gone — the field is multi-select instead).

```yaml
action: xplora_watch.send_message
data:
  device_id: <watch device>   # the "Watch(es)" field — select one or more "Dana Watch (Mom)" devices
  message: "Hello!"
```

To read and reply without calling services by hand, use the [chat card](dashboard-cards.md#chat-card)
instead.
