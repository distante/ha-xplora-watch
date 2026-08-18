# Try it without a watch (demo mode)

You can explore the integration with **no real device** using its network-free demo mode. Add the
integration and sign in with any of the demo accounts below (any password works):

| Email | Role | Watch |
| --- | --- | --- |
| `demo@xplora-watch.invalid` | Guardian | "Patrick" |
| `demo-second-parent@xplora-watch.invalid` | Guardian | "Rosa" |
| `demo-contact@xplora-watch.invalid` | Contact | "Timmy" |
| `demo-offline@xplora-watch.invalid` | Guardian | "Max" (offline) |

The four accounts let you see the **multi-account service fan-out**: put all four watch devices in one
area, then a single service call targeting that area acts on the online Guardian watches, skips the
Contact-only one, and reports the offline one as offline — with a partial-success notification listing
what didn't run. Target only the Contact watch to see the `not_guardian` error, or only the offline
watch to see `watch_offline`.
