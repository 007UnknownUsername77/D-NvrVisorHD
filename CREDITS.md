# Credits

Visor does not vendor these projects. Use the versions named here.

## go2rtc

AlexxIT. MIT License. <https://github.com/AlexxIT/go2rtc>

Pinned at 1.9.9. The page imports `video-stream.js` from the go2rtc server that is running. That file is not copied into this repository.

## FFmpeg

The container installs the Debian `ffmpeg` package. It is not vendored. The license depends on that Debian build and can include LGPL and GPL components. Read the copyright file of the Debian `ffmpeg` package for the exact terms. Do not treat this note as a single license for every FFmpeg build.

## Python

The app image is `python:3.12-slim-bookworm`. Python is covered by the Python Software Foundation license.

## Recorders

This project is not affiliated with Hikvision and does not ship a Hikvision SDK. Recording search speaks HTTP ISAPI to a recorder you operate. Live view is RTSP through go2rtc and is not limited to that brand.

Copyright 2026 the Visor authors. The license is MIT. See [LICENSE](LICENSE).
