# Security

Report a flaw that should not be public in the Security tab of the repository. Do not open a public issue with passwords, RTSP URLs, or a configuration dump.

## What must stay on the machine

- The `.env` file.
- `config/cameras.json` and `config/go2rtc.yaml` once they hold real names or passwords.
- Any RTSP URL that carries a user and a password.
- Private keys, webhook tokens, and copies of video.

The application ships no default user and no default password. If `DVR_HOST`, `DVR_USER`, or `DVR_PASS` is missing, it does not start. `VISOR_DEMO=1` only opens the page.

## Network

go2rtc does not authenticate its API. Anyone who can reach port 1984 can request video and, in some listings, see the source URL with the password. Compose therefore publishes 1984 and 8555 only on 127.0.0.1, and does not publish RTSP 8554.

On a server without Docker, `api.listen` and `webrtc.listen` have to be `127.0.0.1` before the process starts. For a phone on your LAN, publish those two ports on the LAN address. Do not open them to the internet, and do not give a guest network a route to the recorder.

The browser talks to go2rtc directly. A proxy in front of port 8090 alone does not cover 1984. The page also needs 8555 for WebRTC. Publishing only 8090 leaves the picture unable to connect, and publishing 1984 or 8555 on a public address exposes the API.

## Remote access

Reach the viewer through Tailscale, or through the VPN you already operate. Do not put 8090, 1984, 8555, or 8554 on a public IP.

Put the viewer host in a DMZ. The browser may reach the viewer. The viewer may reach the recorder on the recorder's own ports. A guest, and the rest of the LAN, must not reach the recorder. The recorder must not be reachable from the VPN client except through the viewer.

## Egress

Allow the viewer and the recorder to leave the network only for what they need to stay correct: DNS, NTP, and the updates you choose to install. Do not give the video path a route to the internet. A camera or a recorder that can open arbitrary outbound connections can leak a picture. Deny that egress, and do not add a NAT rule that would create it.

These are deployment choices for the operator. This repository does not ship firewall rules for a particular site.

## Process

The systemd unit runs as a system user, with `NoNewPrivileges`, `PrivateTmp`, and a read-only filesystem except the page directory and `/var/backups`. The environment file is `root:visor` mode 640.

Errors that return to the browser pass through a filter that strips `user:password@` from a URL. Even so, do not forward the log to a public site.

## Report

`deploy_report.py` counts `analog` and `ip` cameras. It does not read the go2rtc yaml and it does not include stream names. Sending stays off until `VISOR_REPORT_WEBHOOK`, `VISOR_REPORT_TOKEN`, and `VISOR_REPORT_TO` are all set.
