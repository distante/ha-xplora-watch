# Git hooks

Version-controlled git hooks for this repo. They are **not** active until you
point git at this directory (one time per clone):

```bash
git config core.hooksPath .githooks
```

| Hook | What it does |
|---|---|
| `pre-commit` | (1) Auto-formats staged Python files with `ruff` (matching the `ruff format --check` CI step) and re-stages them, so commits are always formatted. Requires `ruff` (`python3 -m ruff`). (2) Scans the staged changes for secrets with `gitleaks` (config: [`.gitleaks.toml`](../.gitleaks.toml)) and blocks the commit on a finding. |
| `pre-push` | Blocks pushing `wip/*` branches to GitHub. |

## gitleaks (secret backstop)

The `pre-commit` hook runs `gitleaks git --staged -c .gitleaks.toml` as a net against
committing live tokens/secrets (e.g. real Xplora account credentials or session tokens).

- **Install it** (the net is inactive without it — the hook warns but does not block):
  `brew install gitleaks`, or see <https://github.com/gitleaks/gitleaks#installing>.
- **False positive?** Add an allowlist entry to [`.gitleaks.toml`](../.gitleaks.toml) rather
  than disabling the scan.
- Scan the whole history manually: `gitleaks git -c .gitleaks.toml`.

Bypass a hook for a single command with `--no-verify`
(e.g. `git commit --no-verify`).
