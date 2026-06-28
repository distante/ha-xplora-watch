"""Tests for helper.encoded_base64_string_to_file and encoded_base64_string_to_mp3_file."""

from __future__ import annotations

import base64
import os
from unittest.mock import MagicMock, patch

from homeassistant.core import HomeAssistant

from custom_components.xplora_watch.helper import (
    encoded_base64_string_to_file,
    encoded_base64_string_to_mp3_file,
)


def test_encoded_base64_string_to_file_writes_decoded_content(hass: HomeAssistant) -> None:
    """A valid base64 string is decoded and written to the target file."""
    media_path = hass.config.path("www/image")
    os.makedirs(media_path, exist_ok=True)
    payload = base64.b64encode(b"hello world").decode()

    encoded_base64_string_to_file(hass, payload, "photo", "jpeg", "image")

    target = f"{media_path}/photo.jpeg"
    assert os.path.exists(target)
    with open(target, "rb") as f:
        assert f.read() == b"hello world"


def test_encoded_base64_string_to_file_skips_if_file_exists(hass: HomeAssistant) -> None:
    """If the target file already exists, its content is left untouched."""
    media_path = hass.config.path("www/image")
    os.makedirs(media_path, exist_ok=True)
    target = f"{media_path}/existing.jpeg"
    with open(target, "wb") as f:
        f.write(b"placeholder content")

    payload = base64.b64encode(b"new content").decode()
    encoded_base64_string_to_file(hass, payload, "existing", "jpeg", "image")

    with open(target, "rb") as f:
        assert f.read() == b"placeholder content"


def test_encoded_base64_string_to_file_bad_input_returns_none(hass: HomeAssistant) -> None:
    """A non-string base64_string (e.g. None) triggers AttributeError on .encode(), swallowed silently."""
    media_path = hass.config.path("www/image")
    os.makedirs(media_path, exist_ok=True)

    result = encoded_base64_string_to_file(hass, None, "broken", "jpeg", "image")

    assert result is None
    assert not os.path.exists(f"{media_path}/broken.jpeg")


def test_encoded_base64_string_to_mp3_file_converts_and_cleans_up_amr(hass: HomeAssistant) -> None:
    """AudioSegment is mocked; the .amr intermediate file is removed and .export is called correctly."""
    media_path = hass.config.path("www/voice")
    os.makedirs(media_path, exist_ok=True)
    payload = base64.b64encode(b"fake-amr-bytes").decode()

    mock_sound = MagicMock()
    with patch("custom_components.xplora_watch.helper.AudioSegment") as mock_audio_segment:
        mock_audio_segment.from_file.return_value = mock_sound

        encoded_base64_string_to_mp3_file(hass, payload, "voice1")

    amr_path = f"{media_path}/voice1.amr"
    mp3_path = f"{media_path}/voice1.mp3"

    mock_audio_segment.from_file.assert_called_once_with(amr_path, format="amr")
    mock_sound.export.assert_called_once_with(mp3_path, format="mp3")
    assert not os.path.exists(amr_path)


def test_encoded_base64_string_to_mp3_file_skips_if_mp3_exists(hass: HomeAssistant) -> None:
    """If the target mp3 already exists, no decoding/conversion happens at all."""
    media_path = hass.config.path("www/voice")
    os.makedirs(media_path, exist_ok=True)
    mp3_path = f"{media_path}/voice2.mp3"
    with open(mp3_path, "wb") as f:
        f.write(b"placeholder mp3")

    payload = base64.b64encode(b"fake-amr-bytes").decode()
    with patch("custom_components.xplora_watch.helper.AudioSegment") as mock_audio_segment:
        encoded_base64_string_to_mp3_file(hass, payload, "voice2")

    mock_audio_segment.from_file.assert_not_called()
    with open(mp3_path, "rb") as f:
        assert f.read() == b"placeholder mp3"
