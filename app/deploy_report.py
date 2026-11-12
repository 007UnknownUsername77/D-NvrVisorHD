#!/usr/bin/env python3
"""Deployment report: counts analog and IP cameras. Prints no URLs and no passwords."""
from __future__ import annotations

import json
import os
import ssl
from pathlib import Path
from urllib import error, request

ROOT = Path(__file__).resolve().parents[1]


def catalog_path() -> Path:
    chosen = os.environ.get("VISOR_CAMERAS", "").strip()
    if chosen:
        return Path(chosen)
    live = ROOT / "config" / "cameras.json"
    if live.is_file():
        return live
    return ROOT / "config" / "cameras.example.json"


def counts(data: dict) -> tuple[int, int, int]:
    analog = ip = bare = 0
    for cam in data.get("cameras") or []:
        if not isinstance(cam, dict) or cam.get("type") == "multiview":
            continue
        kind = str(cam.get("kind") or "").strip().lower()
        if kind == "analog":
            analog += 1
        elif kind == "ip":
            ip += 1
        else:
            bare += 1
    return analog, ip, bare


def report_text(analog: int, ip: int, bare: int) -> str:
    return (
        "Visor. Deployment report.\n"
        f"Analog: {analog}\n"
        f"IP: {ip}\n"
        f"Untyped: {bare}\n"
        "Live view is RTSP, whether the camera is on a recorder or direct.\n"
        "Recordings depend on the recorder adapter. Today that adapter is Hikvision ISAPI.\n"
        "This report includes no addresses and no passwords.\n"
    )


def send(text: str) -> int:
    hook = os.environ.get("VISOR_REPORT_WEBHOOK", "").strip().rstrip("/")
    token = os.environ.get("VISOR_REPORT_TOKEN", "").strip()
    to = os.environ.get("VISOR_REPORT_TO", "").strip()
    if not hook and not token and not to:
        print("Mail: off")
        return 0
    if not hook or not token or not to:
        print("Mail: missing VISOR_REPORT_WEBHOOK, VISOR_REPORT_TOKEN, or VISOR_REPORT_TO")
        return 2
    if hook.endswith("/notify"):
        hook = hook[: -len("/notify")]
    ua = os.environ.get("VISOR_REPORT_USER_AGENT", "VisorDeployReport/1.0").strip()
    if not ua or any(ch in ua for ch in "\r\n"):
        print("Mail: invalid User-Agent")
        return 2
    body = json.dumps(
        {"to": to, "subject": "Visor: deployment report", "text": text, "html": f"<pre>{text}</pre>"},
        ensure_ascii=False,
    ).encode()
    req = request.Request(
        hook + "/report",
        data=body,
        method="POST",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "User-Agent": ua,
        },
    )
    try:
        with request.urlopen(req, timeout=20, context=ssl.create_default_context()) as res:
            print(f"Mail: HTTP {res.status}")
            return 0 if res.status == 200 else 1
    except error.HTTPError as exc:
        print(f"Mail: HTTP {exc.code}")
        return 1


def main() -> int:
    path = catalog_path()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        print("The camera catalog could not be read.")
        return 1
    if not isinstance(data, dict):
        print("The catalog is not an object.")
        return 1
    analog, ip, bare = counts(data)
    text = report_text(analog, ip, bare)
    print(text, end="")
    return send(text)


if __name__ == "__main__":
    raise SystemExit(main())
