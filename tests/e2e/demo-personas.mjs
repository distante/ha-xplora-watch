// The canonical list of network-free demo personas, shared by the seeding recipe (`seed-demo.mjs`)
// and the browser e2e specs. Demo account only, never a real login (ADR 0009).
//
// Single source of truth so the two sides can't drift: `seed-demo.mjs` adds one config entry per
// persona and names each dashboard view's PATH `slug(alias)`; the specs import the SAME list and
// navigate by `viewPath(...)`. Keying the path on the alias (JS-owned, below) rather than the
// device name avoids coupling to the ward child name, which is Python-owned (`demo.py`) -- the
// device name is "{ward} Watch ({alias})", so a name-derived slug would silently drift if a child
// name changed. The view TITLE stays the friendly device name.
//
// `email` must match a sentinel in `custom_components/xplora_watch/const.py` (`DEMO_*_ACCOUNT_EMAIL`);
// `role` is the behaviour the spec selects on, so a test asks for "the Guardian" / "the Contact" /
// "the Error watch" instead of hardcoding an alias.

export const PERSONAS = [
  { email: "demo@xplora-watch.invalid", alias: "Dad", role: "guardian" },
  { email: "demo-second-parent@xplora-watch.invalid", alias: "Mom", role: "guardian-second" },
  { email: "demo-contact@xplora-watch.invalid", alias: "Contact", role: "contact" },
  { email: "demo-offline@xplora-watch.invalid", alias: "Offline", role: "offline" },
  { email: "demo-error@xplora-watch.invalid", alias: "Error", role: "error" },
];

// The demo dashboard's url_path (matches `seed-demo.mjs`'s DASHBOARD.url_path).
export const DASHBOARD_PATH = "xplora-demo";

// Slugify an alias into a token used both as the Lovelace view path segment AND as the trailing
// segment the spec matches against an entity_id (`findEntityId`, `_${slug}`). Underscore (not dash)
// is deliberate: it mirrors HA's `slugify()` so the entity-id suffix lines up. "Dad" -> "dad",
// "Guardian 2" -> "guardian_2". (Underscores are valid in a Lovelace view path too.)
//
// Assumes single-word / plain-ASCII aliases -- true for every persona above. This does NOT
// reproduce HA's Unicode transliteration (accents/emoji), which HA drops from the slug anyway; a
// non-ASCII multi-part alias would need a fuller port to keep the entity match exact.
export function slug(alias) {
  return String(alias)
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "_")
    .replace(/^_|_$/g, "");
}

// The persona seeding a given behaviour role (throws if unknown, so a typo fails loudly).
export function personaByRole(role) {
  const persona = PERSONAS.find((p) => p.role === role);
  if (!persona) throw new Error(`no demo persona with role "${role}" (have: ${PERSONAS.map((p) => p.role).join(", ")})`);
  return persona;
}

// The dashboard view path for a role, e.g. viewPath("guardian") -> "/xplora-demo/dad".
export function viewPath(role) {
  return `/${DASHBOARD_PATH}/${slug(personaByRole(role).alias)}`;
}
