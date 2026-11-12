# Adapters

Visor separates two jobs.

## Live view

go2rtc opens the RTSP URL you declare. That covers a standalone IP camera, an analog camera the recorder has already digitized, and an IP camera the recorder republishes. The page does not talk to the camera. It talks to go2rtc.

The catalog (`cameras.json`) names the stream. The URL lives only in `go2rtc.yaml`.

`kind` is `analog` or `ip`. It is for the deployment report and so the operator can record what they have. The page does not pick a codec from that field.

- `analog`: a recorder channel that enters on coax or a video input. The viewer does not connect to the cable. It uses the channel the recorder exposes.
- `ip`: a network camera. It can sit on the recorder proxy (then it has `channel` and an archive) or be a bare RTSP URL. Without `channel` it is live only and does not enter Recording.

`dar` is the real aspect ratio, for example `[16, 9]`. Do not set it to stretch a camera that is not wide. Without `dar`, the page uses the pixels of the frame.

`stream_main` has to end in `_main`. The page asks for HD with the same name ending in `_hd`. That HD is limited-range H.264, one camera at a time. The mosaic and the TV stay on `stream_sub`.

## Recordings

Today the adapter is Hikvision ISAPI, in `app/cam_dashboard.py`. It covers analog channels and IP cameras that recorder indexes. It does not cover Dahua, Uniview, or a generic NVR.

A second manufacturer is a new module with the same pieces:

- search the segments of one channel between two times
- resolve the file that contains an instant (that file's name, size, and end)
- hand back the RTSP playback URL of that file, or the equivalent go2rtc can read
- enable or pause the recording track when the channel gains or loses a picture

The page keeps calling `/api/recordings`, `/api/recordings/archive`, `/api/recordings/export`, and `/api/recordings/play`. The interface does not need a second copy.

`VISOR_CHANNELS` is the highest channel that is queried. The default is 8. On a recorder with more channels, raise it, up to 64. Querying empty channels makes the archive slower.

Track writes on this Hikvision firmware do not accept the full XML. The app changes `Enable` and strips the read-only fields. An operator user can answer OK and not store the change; the write uses `admin` with the same password from the environment.

## Not a third-party cloud

Archived video stays on the recorder disk. Visor does not upload it to Hik-Connect, EZVIZ, or another service. A download prepares an MP4 on the host and the browser saves it on the device. That is the private copy: your recorder and your server.

The first HD frame can be gray for a moment. go2rtc 1.9.9, when it asks for video only, prepends a demux filter. The `rtsp/hd` template does not use `low_delay`; with full-range HEVC that flag leaves the gray in place. The picture clears on the next frame.
