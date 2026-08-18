# Account types: Guardian vs. Contact

Your Xplora account can relate to a given watch in one of two ways, and the integration mirrors what
the official Xplora "Life" app shows you for that role — so if your dashboard looks sparser than you
expected, this is usually why:

- **Guardian** — the account that owns and administers the watch. A Guardian gets **everything**: all
  sensors and trackers, plus the reboot/shutdown buttons and the alarm/silent-time controls and
  services.
- **Contact** — any other account linked to the watch (for example a family member who can chat with
  the child but doesn't administer the watch). A Contact is **chat-first**: it gets only the handful
  of things Xplora actually reports to a Contact, and none of the entities that would otherwise sit
  permanently empty — nor the Guardian-only controls, which the integration doesn't show a Contact,
  mirroring the official Xplora "Life" app.

The relationship is **per watch**. On an account with more than one watch you can be the Guardian of
one and a Contact of another, and each watch is gated independently — so missing entities on one watch
just mean you're a Contact of *that* watch, not a bug.

## What each account gets

|                                                       | Guardian | Contact |
| ----------------------------------------------------- | :------: | :-----: |
| Chat (messages)                                       |    ✅    |   ✅    |
| Online status                                         |    ✅    |   ✅    |
| Steps                                                 |    ✅    |   ✅    |
| XCoins                                                |    ✅    |   ✅    |
| Last update                                           |    ✅    |   ✅    |
| **Update** button (on-demand refresh)                 |    ✅    |   ✅    |
| Battery                                               |    ✅    |   —     |
| Charging                                              |    ✅    |   —     |
| Location (device tracker)                             |    ✅    |   —     |
| Distance                                              |    ✅    |   —     |
| Safe zones (binary sensor + label sensor + trackers)  |    ✅    |   —     |
| Location history                                      |    ✅    |   —     |
| Alarms / silent-time sensors                          |    ✅    |   —     |
| **Reboot** / **Shutdown** buttons                     |    ✅    |   —     |
| **Refresh Alarms & Silent Times** button              |    ✅    |   —     |
| Reboot / shutdown / alarm / silent-time **services**  |    ✅    |   —     |

A Contact never has the Guardian-only entities created in the first place — and if you upgraded from
an older version that did create them, they're removed automatically the first time the upgraded
integration starts. The overview card simply shows fewer tiles (and no location row); there's nothing
to disable by hand.

If a Contact — or one of their automations — calls a Guardian-only service (reboot, shutdown, or any
alarm/silent-time change), the call is refused with a clear message that the action is *restricted to
the watch's primary guardian*, matching the **"Primary guardian only"** note shown on those services
in Developer Tools. Chat, on-demand refresh (`xplora_watch.see`), and the history services stay
available to everyone.
