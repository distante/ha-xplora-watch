"""Python Xplora® Api translation from https://github.com/MiGoller/xplora-api.js.

Vendored into this integration from https://github.com/Ludy87/pyxplora_api
  version: 2.12.9
  commit:  42c973bffb8d6ef2940300d344d14af72a9672dd
  license: MIT (see ./LICENSE)

This is an in-tree copy so the integration can ship without an external pip
dependency and so the upstream API client can be patched directly (e.g. the
per-poll re-login behaviour behind the provider rate-limit/ban issues). When
syncing fixes upstream, diff against the tag above. Internal imports are
relative, so no source changes are required to use it as a subpackage.
"""
