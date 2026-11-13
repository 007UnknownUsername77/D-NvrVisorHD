# Changelog

## 0.3.1 — 2026-11-12

Live view stays on the newest buffered frame. Playback speed stays at 1, so the picture does not drift seconds behind. On a computer the target is about one frame. On iPhone HLS the target is the newest half-second piece.

## 0.3.0 — 2026-11-11

First numbered public release. The number is in `VERSION`, in the page meta tag `visor-version`, and in `APP_VERSION` in `app/cam_dashboard.py`.

This tree is English. It has color themes, the setup script, and the app icon centered in this README.

Earlier commits, before the number existed:

- `4b1ec97` — color themes, the app icon, English text, and the setup script
- `d4807d7` — first run asks for the RTSP account
- `2aeb3b2` — initial public tree
