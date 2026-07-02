# Contribution Guidelines

GitHub is used to host code, to track issues and feature requests, as well as accept pull requests.

## Contributing

### Planning

Contributions are very welcome. Please start with a planning step before working on code and submitting pull requests:

- Create an issue in which you describe your proposed change in detail:
  - Purpose and use case
  - Architecture (how will it work?)
  - Implementation (what will the code look like?)
- A maintainer will then look at your proposal and get back to you and either:
  - Approve
  - Request changes
  - Reject
- Once the proposal has been approved, please continue with the next step (pull request).

### Pull Requests

Changes to the codebase are made through pull requests (PRs). Procedure:

- Fork the repo and create your branch from `main`.
- Make your changes.
- Make sure your code lints (using `scripts/lint`).
- Test your contribution (use `scripts/test`; aim to keep coverage ≥ 99%).
- Update the documentation.
- Issue that pull request!

## Bug Reporting

Report a bug by [opening a new issue](../../issues/new).

**Great bug reports** tend to have:

- A quick summary and/or background
- Steps to reproduce
  - Be specific!
  - Give sample code if you can.
- What you expected would happen
- What actually happens
- Notes (possibly including why you think this might be happening, or stuff you tried that didn't work)

## Coding Style

This repository uses [Ruff](https://docs.astral.sh/ruff/) for formatting and linting.

- Format and lint locally with:

  ```bash
  ./scripts/lint
  ```

- The linter enforces import sorting and common correctness/style rules.

### Services: always target the watch with a filtered `device_id` field

Every `xplora_watch` service is aimed at a **watch device**, and the watch must be chosen *directly*
in the action form — never via Home Assistant's generic top-level `target:` picker (which renders an
"Add target" control that accepts any device/area/entity). When you add or change a service:

1. In [`services.yaml`](custom_components/xplora_watch/services.yaml), give it an inline `device_id`
   **field** whose `device` selector is filtered to this integration — and **no** top-level
   `target:` block:

   ```yaml
   my_service:
     name: My Service
     fields:
       device_id:
         name: Watch(es)
         description: The Xplora® watch(es) this action applies to.
         required: true
         selector:
           device:
             integration: xplora_watch
             multiple: true
       # ...any other fields...
   ```

   This shows a watch-only chooser in the UI, so the user picks the watch directly and cannot select
   an unrelated device/area.

2. Register the service in [`services.py`](custom_components/xplora_watch/services.py) with a schema
   built from `_target_schema(...)` (keep `device_id` **optional** in the schema — the bundled
   Lovelace card targets watches programmatically by `entity_id`). The handler resolves the call's
   `device_id` / `entity_id` / `area_id` to each `(account, watch)` via `XploraService._accounts`;
   reuse it instead of reading targets by hand. Gate control actions through `_guardian_targets`.

3. Keep `services.yaml` a **static, account-data-free** file. It is committed as-is and never
   regenerated at runtime, so it must never contain real watch ids or account names.

`tests/xplora_watch/helper/test_service_yaml.py` enforces this — it fails if any registered service
lacks the integration-filtered `device_id` field or reintroduces a `user`/`target`/`all` selector.

## Development Environment

This integration comes with a devcontainer, easy to use with Visual Studio Code. See this
[blog post](https://helgeklein.com/blog/developing-custom-integrations-for-home-assistant-getting-started/)
for helpful information on how to get started with Home Assistant integration development.

## Cutting a release

**The GitHub Release notes are this project's changelog.** There is no `CHANGELOG.md` file by design —
don't add one. The release body is what HACS shows users in the update dialog, so it is written by
hand for every release.

Pushing a version tag does **not** publish a release. The [`Release`
workflow](.github/workflows/release.yml) creates a **draft** so a tag can never ship empty,
auto-generated notes to users. To cut a release:

1. Bump `version` in [`manifest.json`](custom_components/xplora_watch/manifest.json) and commit.
2. Tag and push: `git tag vX.Y.Z && git push origin vX.Y.Z`.
3. The workflow creates a **draft** Release seeded with auto-generated notes (a commit/PR list and the
   compare link) as a starting point.
4. Open the draft on the [Releases page](../../releases), replace the body with real notes (what
   changed, plus any breaking changes / upgrade steps), keep **Set as the latest release** checked, and
   click **Publish**. HACS only sees the new version once the release is published.

## License

By contributing, you agree that your contributions will be licensed under its MIT License.
