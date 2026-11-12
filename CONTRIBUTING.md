# Contributing

The interface and the user-facing text are English. A new comment is one line, and only when the reason is not visible in the code.

## Before a pull request

From the repository root:

```bash
make check
```

That compiles both Python modules, checks the syntax of the module in `app/index.html`, and looks for private addresses, RTSP credentials, and private keys. The example report has to print one analog camera, one IP camera, and `Mail: off`.

Do not include `.env`, `config/cameras.json`, `config/go2rtc.yaml`, video, or screenshots.

## Adapters

Live view is go2rtc. Recording search and download live in `app/cam_dashboard.py` and speak Hikvision ISAPI today. Another brand is a module with the same search and playback functions, not a change spread through the page. The contract is in `docs/ADAPTERS.md`.

## What not to change lightly

- Single-camera HD uses the stream whose name ends in `_hd` (H.264, TV range). The mosaic and the TV stay on the substream.
- Playback of one recording is one producer. The app deletes `dvr_playback` and then creates it. Do not add an `ffmpeg:` source that points at that same name.
- On iPhone, a recording is HLS H.264 without the G.711 audio. If that audio is attached, the first picture stalls.
- A download is a copy of the stored video, retagged `hvc1` when the codec is H.265. G.711 audio does not go in the MP4.
- `go2rtc` stays at 1.9.9 while the `h264/phone`, `rtsp/hd`, and `rtsp/play` templates are the ones in the example. Raising the image without looking at a real frame can bring back the gray picture of full-range HEVC.
