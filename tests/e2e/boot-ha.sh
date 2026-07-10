#!/usr/bin/env bash
#
# boot-ha.sh — boot a FRESH, demo-only Home Assistant for the Playwright e2e suite.
#
# Demo account only, never a real login (ADR 0009). Invoked by playwright.config.mjs's
# `webServer.command`: it preps a throwaway config dir (so the integration is discovered the
# canonical way, via <config>/custom_components) and then execs `hass` in the FOREGROUND, so
# Playwright owns the process lifecycle and waits on the port. Seeding runs afterwards in
# global-setup.mjs (Playwright starts the webServer before globalSetup) — the seed recipe refuses to
# run twice, so the config dir MUST be fresh, which the `rm -rf` below guarantees.
#
# Pinned to a dedicated port so it never collides with the everyday dev / MCP HA on :8123.
#
# Env: E2E_HA_PORT (default 8125), E2E_HA_CONFIG (default <integration>/.e2e-ha/config).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INTEGRATION="$(cd "$SCRIPT_DIR/../.." && pwd)"
PORT="${E2E_HA_PORT:-8125}"
CONFIG_DIR="${E2E_HA_CONFIG:-$INTEGRATION/.e2e-ha/config}"
LOG_FILE="$(dirname "$CONFIG_DIR")/ha.log" # uploaded as a CI artifact on failure (headless is otherwise blind)

# A minimal config dedicated to the e2e suite (NOT the everyday dev config/configuration.yaml): it
# declares only what the map-card flow needs, so CI boots clean and fast and never breaks when the
# dev config changes. See tests/e2e/ha-config.yaml for the full rationale.
E2E_CONFIG_SRC="$SCRIPT_DIR/ha-config.yaml"

command -v hass >/dev/null || { echo "error: 'hass' not found — install the locked deps first." >&2; exit 1; }
[ -f "$E2E_CONFIG_SRC" ] || { echo "error: e2e config missing at $E2E_CONFIG_SRC (recurse the submodule)." >&2; exit 1; }

# Fresh config dir every run (the seed refuses to double-seed). The integration is discovered via the
# canonical <config>/custom_components symlink; the http port is pinned so runs never collide.
rm -rf "$CONFIG_DIR"
mkdir -p "$CONFIG_DIR"
cp "$E2E_CONFIG_SRC" "$CONFIG_DIR/configuration.yaml"
printf '\n# e2e harness: pin the port so it never collides with the dev/MCP HA on :8123.\nhttp:\n  server_port: %s\n' "$PORT" >> "$CONFIG_DIR/configuration.yaml"
ln -sfn "$INTEGRATION/custom_components" "$CONFIG_DIR/custom_components"

echo "e2e: booting demo-only Home Assistant on :$PORT (config: $CONFIG_DIR)"
exec hass --config "$CONFIG_DIR" --log-file "$LOG_FILE"
