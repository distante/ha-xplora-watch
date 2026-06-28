"""Tests for helper.create_service_yaml_file.

NOTE on path safety: ``create_service_yaml_file`` resolves both the bundled json template
and the ``services.yaml`` output relative to ``os.path.dirname(__file__)`` -- i.e. the real
``custom_components/xplora_watch/`` package dir, which *does* contain ``jsons/service_en.json``.
(It previously used ``hass.config.path(f"{DATA_CUSTOM_COMPONENTS}/{DOMAIN}/...")``, which only
resolves to that same package dir in a HACS install; under PYTHONPATH-based dev/test layouts it
points at a non-existent ``config/custom_components/`` path and raised FileNotFoundError.) To keep
these tests hermetic and avoid overwriting the real committed ``services.yaml`` stub, ``aiofiles
.open``, ``load_yaml`` and ``save_yaml`` are mocked directly rather than touching any filesystem
path.
"""

from __future__ import annotations

import json
import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import yaml
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.xplora_watch import helper as helper_module
from custom_components.xplora_watch.const import DOMAIN
from custom_components.xplora_watch.helper import create_service_yaml_file

SAMPLE_CONFIGURATION = {
    "send_message": {
        "fields": {
            "target": {"selector": {"select": {"options": ["all"]}}},
            "user": {"selector": {"select": {"options": []}}},
        }
    },
    "see": {
        "fields": {
            "target": {"selector": {"select": {"options": ["all"]}}},
            "user": {"selector": {"select": {"options": []}}},
        }
    },
    "read_message": {
        "fields": {
            "target": {"selector": {"select": {"options": ["all"]}}},
            "user": {"selector": {"select": {"options": []}}},
        }
    },
    "shutdown": {
        "fields": {
            "target": {"selector": {"select": {"options": ["all"]}}},
            "user": {"selector": {"select": {"options": []}}},
        }
    },
    "reboot": {
        "fields": {
            "target": {"selector": {"select": {"options": ["all"]}}},
            "user": {"selector": {"select": {"options": []}}},
        }
    },
    "logout": {
        "fields": {
            "user": {"selector": {"select": {"options": []}}},
        }
    },
    "delete_message_from_app": {
        "fields": {
            "target": {"selector": {"select": {"options": ["all"]}}},
            "user": {"selector": {"select": {"options": []}}},
        }
    },
}


def test_shipped_services_yaml_is_a_clean_stub() -> None:
    """The committed `services.yaml` must stay a clean static stub.

    The integration regenerates it at runtime with the active account's real watch ids/usernames
    (`create_service_yaml_file`). Committing that would leak personal data *and* is unnecessary.
    This asserts every shipped `target` carries only the `all` sentinel and every `user` an empty
    option list -- guarding against an accidental commit of regenerated data, and guaranteeing a
    fresh install gets a valid, non-crashing file (each selector is a proper `select`).
    """
    path = os.path.join(os.path.dirname(helper_module.__file__), "services.yaml")
    with open(path, encoding="utf-8") as f:
        config = yaml.safe_load(f)

    for name, body in config.items():
        fields = body.get("fields", {})
        if "user" in fields:
            assert fields["user"]["selector"].get("select", {}).get("options") == [], (
                f"{name}.user must be an empty select stub -- found account data? regenerated file committed?"
            )
        if "target" in fields:
            assert fields["target"]["selector"].get("select", {}).get("options") == ["all"], (
                f"{name}.target must be the [all] sentinel stub -- found watch ids? regenerated file committed?"
            )


class _FakeReadFile:
    """Minimal async-context-manager stand-in for an aiofiles file handle, opened for reading."""

    def __init__(self, contents: str) -> None:
        self._contents = contents

    async def __aenter__(self) -> "_FakeReadFile":
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        return None

    async def read(self) -> str:
        return self._contents


@pytest.fixture
def fake_coordinator(mock_config_entry_phone: MockConfigEntry) -> MagicMock:
    """A minimal fake coordinator exposing controller.getUserName()/getWatchUserNames()."""
    coord = MagicMock()
    coord.controller.getUserName.return_value = "Parent Name"
    # The target dropdown labels each watch with the child's name resolved from its id.
    coord.controller.getWatchUserNames.side_effect = lambda wuid: f"Child {wuid}"
    return coord


def _install_coordinator(hass, entry: MockConfigEntry, fake_coordinator: MagicMock) -> None:
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = fake_coordinator


async def test_create_service_yaml_file_fresh_config_writes_watches(
    hass, mock_config_entry_phone: MockConfigEntry, fake_coordinator: MagicMock
) -> None:
    """With no existing services.yaml (load_yaml returns None), the json template is used and watches are merged in."""
    _install_coordinator(hass, mock_config_entry_phone, fake_coordinator)

    with (
        patch("custom_components.xplora_watch.helper.aiofiles.open") as mock_open,
        patch("custom_components.xplora_watch.helper.load_yaml", new=AsyncMock(return_value=None)),
        patch("custom_components.xplora_watch.helper.save_yaml", new=AsyncMock()) as mock_save_yaml,
    ):
        mock_open.return_value = _FakeReadFile(json.dumps(SAMPLE_CONFIGURATION))

        await create_service_yaml_file(hass, mock_config_entry_phone, ["watch-id-001"])

    mock_save_yaml.assert_awaited_once()
    saved_path, saved_config = mock_save_yaml.await_args.args
    assert saved_path.endswith("services.yaml")

    for service_name in ("send_message", "see", "read_message", "shutdown", "reboot", "delete_message_from_app"):
        options = saved_config[service_name]["fields"]["target"]["selector"]["select"]["options"]
        # The watch id stays the submitted value; the child's name is shown as the label.
        assert {"value": "watch-id-001", "label": "Child watch-id-001"} in options
        # The "all" sentinel is preserved (as a {value,label} option alongside the watches).
        assert {"value": "all", "label": "all"} in options

        users = saved_config[service_name]["fields"]["user"]["selector"]["select"]["options"]
        # The entry-id-prefixed value is kept (services parse the entry id from it), but only the
        # username is shown as the label.
        assert {"value": f"{mock_config_entry_phone.entry_id} (Parent Name)", "label": "Parent Name"} in users

    # logout is account-level: user is populated, but it has no per-watch target field.
    logout_fields = saved_config["logout"]["fields"]
    assert "target" not in logout_fields
    assert {"value": f"{mock_config_entry_phone.entry_id} (Parent Name)", "label": "Parent Name"} in logout_fields["user"]["selector"][
        "select"
    ]["options"]


async def test_create_service_yaml_file_missing_yaml_uses_template_without_reading(
    hass, mock_config_entry_phone: MockConfigEntry, fake_coordinator: MagicMock
) -> None:
    """services.yaml is gitignored, so a fresh checkout/install won't have it yet. A missing file
    must be treated like load_yaml returning None (use the json template), not abort via OSError.
    """
    _install_coordinator(hass, mock_config_entry_phone, fake_coordinator)

    with (
        patch("custom_components.xplora_watch.helper.aiofiles.open") as mock_open,
        patch("custom_components.xplora_watch.helper.os.path.exists", return_value=False),
        patch("custom_components.xplora_watch.helper.load_yaml", new=AsyncMock()) as mock_load_yaml,
        patch("custom_components.xplora_watch.helper.save_yaml", new=AsyncMock()) as mock_save_yaml,
    ):
        mock_open.return_value = _FakeReadFile(json.dumps(SAMPLE_CONFIGURATION))

        await create_service_yaml_file(hass, mock_config_entry_phone, ["watch-id-001"])

    mock_load_yaml.assert_not_called()
    mock_save_yaml.assert_awaited_once()
    _, saved_config = mock_save_yaml.await_args.args
    options = saved_config["see"]["fields"]["target"]["selector"]["select"]["options"]
    assert {"value": "watch-id-001", "label": "Child watch-id-001"} in options


async def test_create_service_yaml_file_existing_yaml_with_user_field_is_reused(
    hass, mock_config_entry_phone: MockConfigEntry, fake_coordinator: MagicMock
) -> None:
    """When load_yaml returns a dict with see.fields.user populated, that becomes the base configuration."""
    _install_coordinator(hass, mock_config_entry_phone, fake_coordinator)

    existing_yaml_service = json.loads(json.dumps(SAMPLE_CONFIGURATION))
    existing_yaml_service["see"]["fields"]["user"]["selector"]["select"]["options"] = ["existing-entry (Someone)"]

    with (
        patch("custom_components.xplora_watch.helper.aiofiles.open") as mock_open,
        # `services.yaml` is gitignored, so on a clean checkout (CI, a fresh clone) it isn't on disk
        # and `create_service_yaml_file` skips `load_yaml` entirely. Force the existence check True so
        # this test deterministically exercises the "reuse the previous services.yaml" branch it is
        # asserting about, instead of passing only when a dev runtime happens to have left the file.
        patch("custom_components.xplora_watch.helper.os.path.exists", return_value=True),
        patch("custom_components.xplora_watch.helper.load_yaml", new=AsyncMock(return_value=existing_yaml_service)),
        patch("custom_components.xplora_watch.helper.save_yaml", new=AsyncMock()) as mock_save_yaml,
    ):
        mock_open.return_value = _FakeReadFile(json.dumps(SAMPLE_CONFIGURATION))

        await create_service_yaml_file(hass, mock_config_entry_phone, ["watch-id-002"])

    mock_save_yaml.assert_awaited_once()
    _, saved_config = mock_save_yaml.await_args.args
    users = saved_config["see"]["fields"]["user"]["selector"]["select"]["options"]
    # The pre-existing unrelated user entry is preserved alongside the new one, with its username
    # recovered as the label from the legacy plain-string value.
    assert {"value": "existing-entry (Someone)", "label": "Someone"} in users
    assert {"value": f"{mock_config_entry_phone.entry_id} (Parent Name)", "label": "Parent Name"} in users


async def test_create_service_yaml_file_text_selector_user_is_converted_to_select(
    hass, mock_config_entry_phone: MockConfigEntry, fake_coordinator: MagicMock
) -> None:
    """A previous/old services.yaml (or the shipped stub) may declare `user` as a *text* selector
    instead of a *select*. That used to crash set_watches with KeyError: 'select'. It must instead
    be normalized into a select selector carrying this account as an option.
    """
    _install_coordinator(hass, mock_config_entry_phone, fake_coordinator)

    existing_yaml_service = json.loads(json.dumps(SAMPLE_CONFIGURATION))
    # Mirror the malformed shape: user is a text selector with no "select" key.
    for service in existing_yaml_service.values():
        existing_yaml_service_user = service["fields"]["user"]
        existing_yaml_service_user["selector"] = {"text": None}

    with (
        patch("custom_components.xplora_watch.helper.aiofiles.open") as mock_open,
        patch("custom_components.xplora_watch.helper.os.path.exists", return_value=True),
        patch("custom_components.xplora_watch.helper.load_yaml", new=AsyncMock(return_value=existing_yaml_service)),
        patch("custom_components.xplora_watch.helper.save_yaml", new=AsyncMock()) as mock_save_yaml,
    ):
        mock_open.return_value = _FakeReadFile(json.dumps(SAMPLE_CONFIGURATION))

        await create_service_yaml_file(hass, mock_config_entry_phone, ["watch-id-003"])

    mock_save_yaml.assert_awaited_once()
    _, saved_config = mock_save_yaml.await_args.args
    # The text selector is rewritten as a select selector with this account as an option.
    user_selector = saved_config["see"]["fields"]["user"]["selector"]
    assert "select" in user_selector
    assert {"value": f"{mock_config_entry_phone.entry_id} (Parent Name)", "label": "Parent Name"} in user_selector["select"]["options"]


async def test_create_service_yaml_file_oserror_is_caught_and_logged(
    hass, mock_config_entry_phone: MockConfigEntry, fake_coordinator: MagicMock, caplog: pytest.LogCaptureFixture
) -> None:
    """An OSError while opening/writing files is caught and logged, not raised."""
    _install_coordinator(hass, mock_config_entry_phone, fake_coordinator)

    with patch("custom_components.xplora_watch.helper.aiofiles.open", side_effect=OSError("disk full")):
        await create_service_yaml_file(hass, mock_config_entry_phone, ["watch-id-001"])

    assert "Error writing service definition" in caplog.text


async def test_create_service_yaml_file_keyerror_is_caught_and_logged(
    hass, mock_config_entry_phone: MockConfigEntry, fake_coordinator: MagicMock, caplog: pytest.LogCaptureFixture
) -> None:
    """A KeyError (e.g. malformed json template missing expected keys) is caught and logged, not raised."""
    _install_coordinator(hass, mock_config_entry_phone, fake_coordinator)

    broken_configuration = {"send_message": {"fields": {}}}  # Missing "target"/"user" -> KeyError downstream.

    with (
        patch("custom_components.xplora_watch.helper.aiofiles.open") as mock_open,
        patch("custom_components.xplora_watch.helper.load_yaml", new=AsyncMock(return_value=None)),
        patch("custom_components.xplora_watch.helper.save_yaml", new=AsyncMock()),
    ):
        mock_open.return_value = _FakeReadFile(json.dumps(broken_configuration))

        await create_service_yaml_file(hass, mock_config_entry_phone, ["watch-id-001"])

    assert "not found" in caplog.text
