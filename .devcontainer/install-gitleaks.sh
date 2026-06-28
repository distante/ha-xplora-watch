#!/usr/bin/env bash
# install-gitleaks.sh — install gitleaks (secret-scanning backstop) from its
# GitHub release into ~/.local/bin. The .githooks/pre-commit hook runs
# `gitleaks git --staged -c .gitleaks.toml` and only WARNS (does not block) when
# the binary is missing, so without this the secret net is inactive in the
# devcontainer. gitleaks is a single static Go binary not available via apt,
# so it is installed from a GitHub release.
#
# PATH (~/.local/bin) is exported declaratively via devcontainer.json's
# remoteEnv, so this script does not touch shell profiles.
#
# Idempotent: re-running skips the install when gitleaks is already present.
set -euo pipefail

LOCAL_BIN="$HOME/.local/bin"
mkdir -p "$LOCAL_BIN"

info() { echo "[gitleaks] $*"; }

if command -v gitleaks &>/dev/null || [[ -x "$LOCAL_BIN/gitleaks" ]]; then
  info "already installed ($(gitleaks version 2>/dev/null || echo '?')), skipping."
  exit 0
fi

# Map `uname -m` to the arch string used in gitleaks release asset names
# (x86_64 -> x64, aarch64/arm64 -> arm64).
case "$(uname -m)" in
  x86_64 | amd64) arch="x64" ;;
  aarch64 | arm64) arch="arm64" ;;
  *) info "unsupported arch '$(uname -m)'; skipping (install manually if needed)."; exit 0 ;;
esac

# Resolve the latest release tag (anonymous API; a one-time build is well within
# the ~60 req/hr unauthenticated limit).
tag="$(curl -fsSL https://api.github.com/repos/gitleaks/gitleaks/releases/latest \
  | grep '"tag_name"' | head -1 \
  | sed 's/.*"tag_name":[[:space:]]*"\([^"]*\)".*/\1/')"
version="${tag#v}"

info "Installing gitleaks $version (linux_$arch)..."
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT
curl -fsSL -o "$tmp/gitleaks.tar.gz" \
  "https://github.com/gitleaks/gitleaks/releases/download/${tag}/gitleaks_${version}_linux_${arch}.tar.gz"
tar -xzf "$tmp/gitleaks.tar.gz" -C "$tmp" gitleaks
install -m 0755 "$tmp/gitleaks" "$LOCAL_BIN/gitleaks"

info "installed: $("$LOCAL_BIN/gitleaks" version)"
