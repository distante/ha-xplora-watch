# Why this fork exists: the ban problem and solution

The original [Ludy87/xplora_watch](https://github.com/Ludy87/xplora_watch) did the hard work of
making Xplora watches usable in Home Assistant at all. As Xplora tightened its rate-limits over
time, the original's chattier API patterns started leading to throttling and bans. This fork
reworks those patterns to be a good citizen of a service that, frankly, works well — through
three changes:

- **No per-poll re-login.** The biggest driver of the bans: the old client logged in *again* on
  every single poll. This fork logs in **once** and reuses the session token for its full
  lifetime (~35 days); a real login only happens when the token actually expires. This is the
  change that matters most.
- **Polling off by default.** New installs make **no recurring calls at all** — data refreshes
  only when you call `xplora_watch.see` (manually or from your own automation). If you do opt
  into polling, it's a safe preset (30 min / 1 hour / 2 hours), never a tight loop.
- **Fewer calls per refresh.** Account-wide status (battery, location, online, steps, unread
  count, …) comes from a single consolidated request, and the rarely-changing alarm/silent/safe-zone
  data has its own [interval](polling.md#alarms--silent-times--safe-zones-interval) that defaults
  to *off* (refresh on demand). Redundant calls that re-fetched data already in hand were removed,
  and reverse-geocoding is cached, so a watch that hasn't moved is never looked up twice.
- **Nothing is downloaded twice.** Chat attachments (voice, image, video) are fetched from Xplora
  once and then served from the local `config/www/…` copy — re-reading a thread never
  re-downloads media you already have. And concurrent refreshes that ask for the same thing (two
  cards rendering at once, a button press racing a scheduled poll) are coalesced onto a *single*
  network request instead of each hitting the API.

## By the numbers

A request count derived from the actual code paths (old = the unpatched upstream client at tag
`2.12.9`; counts exclude the retry loops the old client added, so they are conservative floors).
One watch:

| | Old integration | This fork |
| --- | --- | --- |
| Logins per refresh | **≥ 1** (every poll) | **0** (token reused ~35 days) |
| Xplora requests per refresh | **~28** | **~3** (lean default) … **~8** (everything enabled) |
| Default poll cadence | every 180 s (480/day) | **off** (0/day until you ask) |
| Busiest possible day | **~13,400 requests + ≥480 logins** (at the 180 s default; far higher on its free-form slider) | **~384 requests, 0 logins** (fastest 30-min preset, everything on) |

So this fork's *worst* day is roughly **35× lighter** than the old integration's *idle, default*
day — and it removes the per-refresh login that actually triggered the bans.

**The goal is not to trick or evade Xplora's rate limits** — it's to use the API responsibly so
accounts stay healthy and the integration remains viable for everyone. The default configuration
talks to Xplora's servers only when *you* ask it to.
