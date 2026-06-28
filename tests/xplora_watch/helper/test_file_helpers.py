"""Tests for helper.encoded_base64_string_to_file and encoded_base64_string_to_mp3_file."""

from __future__ import annotations

import base64
import os
from unittest.mock import AsyncMock, MagicMock, patch

from homeassistant.core import HomeAssistant

from custom_components.xplora_watch.helper import (
    encoded_base64_string_to_file,
    encoded_base64_string_to_mp3_file,
)


async def test_encoded_base64_string_to_file_writes_decoded_content(hass: HomeAssistant) -> None:
    """A valid base64 string is decoded and written to the target file."""
    media_path = hass.config.path("www/image")
    os.makedirs(media_path, exist_ok=True)
    payload = base64.b64encode(b"hello world").decode()

    await encoded_base64_string_to_file(hass, payload, "photo", "jpeg", "image")

    target = f"{media_path}/photo.jpeg"
    assert os.path.exists(target)
    with open(target, "rb") as f:
        assert f.read() == b"hello world"


async def test_encoded_base64_string_to_file_skips_if_file_exists(hass: HomeAssistant) -> None:
    """If the target file already exists, its content is left untouched."""
    media_path = hass.config.path("www/image")
    os.makedirs(media_path, exist_ok=True)
    target = f"{media_path}/existing.jpeg"
    with open(target, "wb") as f:
        f.write(b"placeholder content")

    payload = base64.b64encode(b"new content").decode()
    await encoded_base64_string_to_file(hass, payload, "existing", "jpeg", "image")

    with open(target, "rb") as f:
        assert f.read() == b"placeholder content"


async def test_encoded_base64_string_to_file_bad_input_returns_none(hass: HomeAssistant) -> None:
    """A non-string base64_string (e.g. None) triggers AttributeError on .encode(), swallowed silently."""
    media_path = hass.config.path("www/image")
    os.makedirs(media_path, exist_ok=True)

    result = await encoded_base64_string_to_file(hass, None, "broken", "jpeg", "image")

    assert result is None
    assert not os.path.exists(f"{media_path}/broken.jpeg")


def _fake_ffmpeg_process(returncode: int = 0) -> MagicMock:
    """A stand-in for the asyncio subprocess returned by create_subprocess_exec."""
    proc = MagicMock()
    proc.communicate = AsyncMock(return_value=(b"", b""))
    proc.returncode = returncode
    return proc


async def test_encoded_base64_string_to_mp3_file_converts_and_cleans_up_amr(hass: HomeAssistant) -> None:
    """ffmpeg is invoked on the decoded .amr and the intermediate .amr file is removed afterwards."""
    media_path = hass.config.path("www/voice")
    os.makedirs(media_path, exist_ok=True)
    payload = base64.b64encode(b"fake-amr-bytes").decode()

    amr_path = f"{media_path}/voice1.amr"
    mp3_path = f"{media_path}/voice1.mp3"

    with (
        patch("custom_components.xplora_watch.helper.get_ffmpeg_manager") as mock_get_manager,
        patch(
            "custom_components.xplora_watch.helper.asyncio.create_subprocess_exec",
            new=AsyncMock(return_value=_fake_ffmpeg_process()),
        ) as mock_exec,
    ):
        mock_get_manager.return_value.binary = "ffmpeg"

        await encoded_base64_string_to_mp3_file(hass, payload, "voice1")

    # ffmpeg is run with the HA-resolved binary to transcode the .amr into the target .mp3.
    mock_exec.assert_awaited_once()
    assert mock_exec.await_args.args == ("ffmpeg", "-y", "-i", amr_path, mp3_path)
    # The intermediate .amr is cleaned up; the real .amr was written then removed.
    assert not os.path.exists(amr_path)


async def test_encoded_base64_string_to_mp3_file_skips_if_mp3_exists(hass: HomeAssistant) -> None:
    """If the target mp3 already exists, no decoding/conversion happens at all."""
    media_path = hass.config.path("www/voice")
    os.makedirs(media_path, exist_ok=True)
    mp3_path = f"{media_path}/voice2.mp3"
    with open(mp3_path, "wb") as f:
        f.write(b"placeholder mp3")

    payload = base64.b64encode(b"fake-amr-bytes").decode()
    with (
        patch("custom_components.xplora_watch.helper.get_ffmpeg_manager") as mock_get_manager,
        patch(
            "custom_components.xplora_watch.helper.asyncio.create_subprocess_exec",
            new=AsyncMock(),
        ) as mock_exec,
    ):
        mock_get_manager.return_value.binary = "ffmpeg"
        await encoded_base64_string_to_mp3_file(hass, payload, "voice2")

    mock_exec.assert_not_awaited()
    with open(mp3_path, "rb") as f:
        assert f.read() == b"placeholder mp3"
