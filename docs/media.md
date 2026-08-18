# Voice, video & image messages

All voice messages, videos and images are stored in `config/www/{voice|video|image|}`.

- The voice message will be downloaded as amr and converted to mp3.
  - This conversion uses **ffmpeg**, run via Home Assistant's built-in `ffmpeg` integration. Most
    installs already have it through `default_config`, so there is nothing to do; on a minimal
    setup that doesn't, add `ffmpeg:` to your `configuration.yaml`. (Home Assistant OS, Supervised,
    and the official Container image bundle the `ffmpeg` binary.) If the `ffmpeg` integration
    isn't available, only the voice→mp3 conversion is skipped (a warning is logged) — text
    messages, images and videos are unaffected.
- Videos as mp4 (plus a jpeg thumbnail)
- Images as jpeg

**Each attachment is downloaded only once.** Before fetching a voice/image/video from Xplora, the
integration checks for the already-cached file under `config/www/…` and skips the (rate-limited)
remote download if it's there. Re-reading a chat thread — whether from the card's refresh button,
a service call, or render-on-refresh — re-downloads nothing you already have. (A video is only
treated as cached once *both* the mp4 and its thumbnail are present.)

See the [chat card](dashboard-cards.md#chat-card) for how attachments show up on a dashboard.
