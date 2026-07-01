"""Guard the shipped, static ``services.yaml`` (ADR 0003).

Services target the Xplora® watch via an inline ``device_id`` field whose ``device`` selector is
filtered to ``integration: xplora_watch`` -- so the action form shows a watch-only chooser, picked
directly, rather than HA's generic "add target" (which would accept any device/area). ``services.yaml``
is static: it is never regenerated and must never carry per-account watch ids / usernames.
"""

from __future__ import annotations

import os

import yaml
from homeassistant.core import HomeAssistant

from custom_components.xplora_watch import helper as helper_module
from custom_components.xplora_watch.const import DOMAIN
from custom_components.xplora_watch.services import async_setup_services


def _load_services_yaml() -> dict:
    path = os.path.join(os.path.dirname(helper_module.__file__), "services.yaml")
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def _is_watch_device_field(field: dict | None) -> bool:
    """True when `field` is a `device` selector filtered to this integration (the watch chooser)."""
    selector = (field or {}).get("selector", {})
    return "device" in selector and selector["device"].get("integration") == "xplora_watch"


def test_shipped_services_yaml_offers_a_watch_only_device_field_and_no_account_data() -> None:
    config = _load_services_yaml()

    assert config, "services.yaml is empty"
    for name, body in config.items():
        fields = body.get("fields", {})
        # Inline, watch-only device chooser shown directly in the action form.
        assert _is_watch_device_field(fields.get("device_id")), (
            f"{name} must expose a `device_id` field with a `device` selector filtered to "
            f"integration xplora_watch (watch-only, not any device) — see CONTRIBUTING.md"
        )
        # No generic top-level target picker, and none of the old account-data selectors.
        assert "target" not in body, f"{name} must not use the generic top-level `target` picker"
        assert "user" not in fields, f"{name} still declares a `user` selector (account data leak risk)"
        assert "target" not in fields, f"{name} still declares a `target` watch-id selector (watch-id leak risk)"


async def test_every_registered_service_has_the_watch_device_field(hass: HomeAssistant) -> None:
    """Enforce the convention for the *future*: every service actually registered by the integration
    must have a matching `services.yaml` entry whose `device_id` field is the integration-filtered
    watch chooser. Adding a new service without it (or registering one missing from `services.yaml`)
    fails here. See CONTRIBUTING.md → "Services: always target the watch with a filtered `device_id`
    field".
    """
    await async_setup_services(hass, "entry-under-test")
    registered = set(hass.services.async_services().get(DOMAIN, {}))
    assert registered, "no xplora_watch services were registered"

    config = _load_services_yaml()
    for service_name in sorted(registered):
        body = config.get(service_name)
        assert body is not None, f"service `{service_name}` is registered but missing from services.yaml"
        assert _is_watch_device_field(body.get("fields", {}).get("device_id")), (
            f"registered service `{service_name}` must expose the integration-filtered `device_id` watch field"
        )
