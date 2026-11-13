# Visor

<p align="center">
  <img src="docs/icon.png" alt="Visor" width="128">
</p>

<p align="center"><strong>0.3.1</strong></p>

Self-hosted viewer and recording browser for your own recorder. Live picture is any RTSP camera through [go2rtc](https://github.com/AlexxIT/go2rtc). Recording search and download use a Hikvision ISAPI adapter, for both analog channels and IP cameras on that recorder. Nothing is uploaded to a vendor cloud.

The video file stays on the recorder disk. This server only prepares the view and, when you ask for a copy, an MP4.

There are two kinds of camera, and both are declared in the catalog:

- **Analog.** It enters the recorder on a video input. Visor does not speak to the cable. It uses the channel the recorder has already digitized.
- **IP.** A network camera. If the recorder indexes it, it has a channel and Recording works. If you only have the RTSP URL, it is live and `channel` stays unset.

Live view works for any camera go2rtc can open. Recording, today, works for a Hikvision recorder. Another brand needs the adapter described in [docs/ADAPTERS.md](docs/ADAPTERS.md). That adapter is not written yet.

## Version

Public app **0.3.1**. See [CHANGELOG.md](CHANGELOG.md). The lab viewer is a separate build and it is not this tree.

## What you need

- A recorder or NVR on your network, not exposed to the internet.
- go2rtc 1.9.9. The tag is pinned on purpose. That version matches the color templates in the example.
- Python 3.12 and ffmpeg on the same machine as the page, if you are not using Docker.
- A surveillance disk in the recorder if you want days of archive. An SSD does not make playback start sooner when the limit is the recorder port.

`VISOR_CHANNELS` defaults to 8. On a machine with more channels, raise it, up to 64.

## First run

From the root of this repository, on the machine that can see the recorder:

```bash
./deploy/setup.sh
```

It asks for three things: the recorder address, the RTSP user, and the password (hidden while you type). It then reads the recorder channels, separates analog and IP, writes the configuration, and offers to start the cameras. The page is at `http://127.0.0.1:8090/`.

`deploy/setup.sh` is a thin wrapper around `python3 deploy/setup.py`. It does not install packages.

Those files (`.env`, `config/go2rtc.yaml`, `config/cameras.json`) stay on the machine and do not enter git. If they already exist, the helper does not overwrite them: use `--force`.

If the recorder does not answer as Hikvision, it asks for one camera's RTSP path. Enter uses channel 101.

By hand, if you prefer not to use the helper: copy `.env.example`, `config/cameras.example.json`, and `config/go2rtc.yaml.example`, fill in the account, then `docker compose up -d --build`.

The page is at `http://127.0.0.1:8090/`. The browser on that same machine talks to go2rtc on port 1984. For a phone on your LAN, change the `127.0.0.1` of ports 8090, 1984, and 8555 in `docker-compose.yml` to the server's LAN address. Do not use `0.0.0.0` on a machine that has a public IP, and do not publish 8554.

`VISOR_DEMO=1` opens the page even when the password is missing. Archive requests return an error. It is for looking at the interface, not for watching cameras.

Inside the container the app listens on every interface. Compose is what limits the host to loopback.

## Color themes

The header has a theme menu: Night, Harbor, Pine, Amber, and Day. The choice is stored in the browser under the key `visor-theme` and is applied before the cameras load. Night is the default. The picture itself stays on a black stage.

## Remote access

Use Tailscale, or the VPN you already run, to open the viewer from outside the building. Host the viewer in a DMZ. Do not publish 8090, 1984, 8555, or 8554 on a public IP. The browser talks to go2rtc on 1984, so a proxy on 8090 alone is not enough.

Keep egress to an allowlist: DNS, NTP, and the updates you choose. Do not give the video a path to the internet, and do not let guests reach the recorder. The detail is in [SECURITY.md](SECURITY.md).

## Start on a server, without Docker

```bash
sudo useradd --system --home /var/www/cam-viewer --shell /usr/sbin/nologin visor
sudo install -d -o visor -g visor -m 755 /var/www/cam-viewer /etc/visor /etc/go2rtc /var/backups
sudo install -o visor -g visor -m 644 app/index.html app/icon.png config/cameras.json /var/www/cam-viewer/
sudo install -o visor -g visor -m 644 app/cam_dashboard.py /usr/local/bin/cam_dashboard.py
sudo install -o root -g visor -m 640 .env /etc/visor/visor.env
sudo install -o root -g visor -m 640 config/go2rtc.yaml /etc/go2rtc/go2rtc.yaml
```

Before copying the yaml, change `api.listen` and `webrtc.listen` to `127.0.0.1:1984` and `127.0.0.1:8555`. 8554 is already on localhost. Install the go2rtc 1.9.9 binary at `/usr/local/bin/go2rtc`.

```bash
sudo install -m 644 deploy/go2rtc.service deploy/visor.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now go2rtc visor
```

The page unit loads `/etc/visor/visor.env` and binds the process to `127.0.0.1`. `ffmpeg` has to be on `PATH` or in `/usr/local/bin`.

## Variables

| Variable | Use |
| --- | --- |
| `DVR_HOST` | Recorder name or address. Empty, the app does not start. |
| `DVR_USER` `DVR_PASS` | Recorder account. They are not in the code. |
| `VISOR_CHANNELS` | Channels to query. Default 8, maximum 64. |
| `VISOR_DEMO` | `1` shows the page with no recorder. |
| `CAM_VIEWER_HOST` | `127.0.0.1` on the server. `0.0.0.0` only inside the container. |
| `GO2RTC_URL` | API the Python process uses. The browser uses the page host, port 1984. |
| `GO2RTC_YAML` | Where a new IP camera copies its URL from. |
| `VISOR_REPORT_WEBHOOK` `VISOR_REPORT_TOKEN` `VISOR_REPORT_TO` | Optional report. Empty, nothing is sent. |

## Deployment report

```bash
python3 app/deploy_report.py
```

It prints how many cameras are analog, how many are IP, and how many have no `kind`. It does not print stream names or URLs. If the three webhook variables are set, it `POST`s `{to, subject, text, html}` to `{webhook}/report` with `Authorization: Bearer`. Some gateways require a specific `User-Agent`: change it with `VISOR_REPORT_USER_AGENT`.

## How it looks

One camera at a time can be HD. That HD is television-range H.264, because full-range HEVC paints gray in the browser. The mosaic and the TV stay on the substream, which is lighter.

On a phone, Screen opens a large view inside the page. Native fullscreen squashes tall cameras. On the desktop, Screen and a double click enlarge the picture box, and the button stays inside it.

An iPhone recording plays as HLS H.264, without the microphone: G.711 audio stalls the first picture. A download keeps the video as it is stored on disk. When that video is H.265, the tag stays `hvc1` so the phone can open it. Download audio is not in the file.

The first HD frame can look gray and then clear. The read template does not use `low_delay`. With that flag the gray stays.

## Development

```bash
make check
```

It compiles Python, checks the page module with `node --check`, and runs the secret scan. How to contribute is in [CONTRIBUTING.md](CONTRIBUTING.md). The short threat model is in [SECURITY.md](SECURITY.md). Third-party credits are in [CREDITS.md](CREDITS.md).

The license is MIT, with copyright "the Visor authors". The text is in [LICENSE](LICENSE).
