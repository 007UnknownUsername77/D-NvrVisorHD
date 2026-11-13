# Changelog

## 0.3.2 — 2026-11-13

The desktop layout proposed in pull request 2 is accepted. The five themes, the sidebar, and the camera list beside the picture stay. Playback is the same.

The proposal needed four corrections before approval:

- The tool row stays in the video column. The proposed bar was `position: absolute` without a positioned parent, so on a wide screen it sat on the sidebar instead of under the picture.
- The camera list scrolls on the axis it uses. On a wide screen the list is a column, so the selection moves vertically. On a phone it stays a horizontal strip.
- Colors come from the theme. The proposed floating bar used a fixed near-black, which ignored Day. Amber keeps its own recording and live colors.
- The sidebar is not a `nav`. The wide-screen rule hides the phone bar, and a sidebar built as `nav` could disappear with it. A TV keeps the bottom bar and the horizontal list.

## 0.3.1 — 2026-11-12

Live view stays on the newest buffered frame. Playback speed stays at 1, so the picture does not drift seconds behind. On a computer the target is about one frame. On iPhone HLS the target is the newest half-second piece.

## 0.3.0 — 2026-11-11

First numbered public release. The number is in `VERSION`, in the page meta tag `visor-version`, and in `APP_VERSION` in `app/cam_dashboard.py`.

This tree is English. It has color themes, the setup script, and the app icon centered in this README.

Earlier commits, before the number existed:

- `4b1ec97` — color themes, the app icon, English text, and the setup script
- `d4807d7` — first run asks for the RTSP account
- `2aeb3b2` — initial public tree
