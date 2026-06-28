"""Bundled sample AMR voice clip for the demo account's end-to-end media test.

`DemoPyXploraApi.get_chat_voice` returns this clip (base64-encoded, exactly as the real API
delivers voice data) so that reading the demo watch's chat exercises the real AMR->mp3 ffmpeg
conversion path -- no live Xplora account required. The clip is a short, non-sensitive test
recording; it is decoded, written to `config/www/voice/<msgId>.amr`, transcoded to mp3 by
Home Assistant's ffmpeg binary, then cleaned up (see helper.encoded_base64_string_to_mp3_file).
"""

from typing import Final

DEMO_VOICE_AMR_B64: Final = (
    "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"
)
