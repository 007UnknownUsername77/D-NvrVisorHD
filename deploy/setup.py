#!/usr/bin/env python3
"""Pregunta la dirección y la cuenta RTSP, descubre los canales y arranca Visor."""
from __future__ import annotations

import getpass
import json
import os
import re
import stat
import subprocess
import sys
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import quote

ROOT = Path(__file__).resolve().parents[1]
EXAMPLE_YAML = ROOT / "config" / "go2rtc.yaml.example"
EXAMPLE_CAMERAS = ROOT / "config" / "cameras.example.json"


def explain(exc: BaseException) -> str:
    if isinstance(exc, HTTPError) and exc.code in (401, 403):
        return "El grabador rechazó el usuario o la contraseña."
    return "No llegué al grabador. Revisa la dirección y que este equipo esté en su red."


def check_host(host: str) -> str:
    host = host.strip()
    if not host or "://" in host or "@" in host or any(ch.isspace() for ch in host):
        raise SystemExit("La dirección es un nombre o una IP, sin usuario y sin contraseña.")
    if not re.fullmatch(r"[A-Za-z0-9._-]{1,253}", host):
        raise SystemExit("La dirección no es válida.")
    return host


def rtsp_url(user: str, password: str, host: str, channel_id: int) -> str:
    return (
        "rtsp://"
        + quote(user, safe="")
        + ":"
        + quote(password, safe="")
        + f"@{host}:554/Streaming/Channels/{int(channel_id)}"
    )


def render(host: str, user: str, password: str, cams: list[dict]) -> tuple[str, str, str]:
    if not cams:
        raise SystemExit("No hay canales para escribir.")
    head = EXAMPLE_YAML.read_text(encoding="utf-8").split("\nstreams:", 1)[0].rstrip()
    blocks = [head, "", "streams:"]
    catalog_cams = []
    top = 1
    for cam in cams:
        kind = cam["kind"]
        number = int(cam["channel"])
        top = max(top, number)
        prefix = f"ch{number}" if kind == "analog" else f"ip{number}"
        main_url = rtsp_url(user, password, host, cam["main_id"])
        sub_url = rtsp_url(user, password, host, cam["sub_id"])
        hd = f"ffmpeg:{main_url}#video=h264/phone#input=rtsp/hd"
        blocks.append(f"  {prefix}_sub: {json.dumps(sub_url)}")
        blocks.append(f"  {prefix}_main:")
        blocks.append(f"    - {json.dumps(main_url)}")
        blocks.append(f"  {prefix}_hd:")
        blocks.append(f"    - {json.dumps(hd)}")
        blocks.append("")
        entry = {
            "id": f"{number:02d}" if kind == "analog" else f"ip{number:02d}",
            "name": cam.get("name") or (f"Cámara {number}" if kind == "analog" else f"Cámara IP {number}"),
            "type": "stream",
            "kind": kind,
            "channel": number,
            "stream_main": f"{prefix}_main",
            "stream_sub": f"{prefix}_sub",
        }
        catalog_cams.append(entry)
    blocks.append("# dvr_playback lo crea la app al abrir una grabación.")
    yaml = "\n".join(blocks).rstrip() + "\n"
    base = json.loads(EXAMPLE_CAMERAS.read_text(encoding="utf-8"))
    base["cameras"] = catalog_cams
    catalog = json.dumps(base, ensure_ascii=False, indent=2) + "\n"
    channels = min(64, max(8, top))
    env = "\n".join(
        [
            f"DVR_HOST={_env(host)}",
            f"DVR_USER={_env(user)}",
            f"DVR_PASS={_env(password)}",
            f"VISOR_CHANNELS={channels}",
            "VISOR_DEMO=0",
            "CAM_VIEWER_HOST=127.0.0.1",
            "CAM_VIEWER_PORT=8090",
            "GO2RTC_URL=http://127.0.0.1:1984",
            "GO2RTC_YAML=/etc/go2rtc/go2rtc.yaml",
            "",
        ]
    )
    return env, yaml, catalog


def _env(value: str) -> str:
    if any(ch in value for ch in "\r\n"):
        raise SystemExit("El valor no puede llevar saltos de línea.")
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def discover(host: str, user: str, password: str) -> list[dict]:
    os.environ["DVR_HOST"] = host
    os.environ["DVR_USER"] = user
    os.environ["DVR_PASS"] = password
    sys.path.insert(0, str(ROOT / "app"))
    import importlib

    import cam_dashboard as dash

    importlib.reload(dash)
    try:
        analog = dash.analog_video()
    except (HTTPError, URLError, OSError, TimeoutError) as exc:
        raise SystemExit(explain(exc)) from None
    cams = [
        {
            "channel": n,
            "kind": "analog",
            "main_id": n * 100 + 1,
            "sub_id": n * 100 + 2,
            "name": f"Cámara {n}",
        }
        for n, has_video in sorted(analog.items())
        if has_video
    ]
    if not cams and analog:
        cams = [
            {
                "channel": n,
                "kind": "analog",
                "main_id": n * 100 + 1,
                "sub_id": n * 100 + 2,
                "name": f"Cámara {n}",
            }
            for n in sorted(analog)
        ]
    try:
        for item in dash.ip_online():
            mains = [n for n in item["streams"] if n % 100 == 1]
            subs = [n for n in item["streams"] if n % 100 == 2]
            if not mains and not subs:
                continue
            main_id = mains[0] if mains else subs[0] - 1
            sub_id = subs[0] if subs else main_id + 1
            number = main_id // 100 or int(item["id"])
            cams.append(
                {
                    "channel": number,
                    "kind": "ip",
                    "main_id": main_id,
                    "sub_id": sub_id,
                    "name": f"Cámara IP {number}",
                }
            )
    except (HTTPError, URLError, OSError, TimeoutError):
        pass
    return cams


def ask_account() -> tuple[str, str, str]:
    host = os.environ.get("VISOR_SETUP_HOST", "").strip()
    user = os.environ.get("VISOR_SETUP_USER", "").strip()
    password = os.environ.get("VISOR_SETUP_PASS", "")
    if host and user and password:
        return check_host(host), user, password
    if not sys.stdin.isatty():
        raise SystemExit("Hace falta una terminal para preguntar la contraseña.")
    host = check_host(input("Dirección del grabador: "))
    user = input("Usuario RTSP: ").strip()
    if not user:
        raise SystemExit("Falta el usuario.")
    password = getpass.getpass("Contraseña RTSP: ")
    again = getpass.getpass("Repite la contraseña: ")
    if not password or password != again:
        raise SystemExit("La contraseña no coincide o está vacía.")
    return host, user, password


def one_camera() -> list[dict]:
    if not sys.stdin.isatty():
        raise SystemExit("Sin canales y sin terminal para preguntar la ruta.")
    raw = input("Ruta RTSP de una cámara [Streaming/Channels/101]: ").strip()
    if not raw:
        raw = "Streaming/Channels/101"
    match = re.search(r"(\d+)\s*$", raw)
    if not match:
        raise SystemExit("La ruta tiene que terminar en el número de canal, por ejemplo 101.")
    channel_id = int(match.group(1))
    kind = input("¿analógica o ip? [analog]: ").strip().lower() or "analog"
    if kind not in ("analog", "ip"):
        raise SystemExit("Escribe analog o ip.")
    number = channel_id // 100 or 1
    return [
        {
            "channel": number,
            "kind": kind,
            "main_id": channel_id,
            "sub_id": channel_id + 1 if channel_id % 100 == 1 else channel_id,
            "name": "Cámara 1" if kind == "analog" else "Cámara IP 1",
        }
    ]


def write_tree(dest: Path, env: str, yaml: str, catalog: str, force: bool) -> None:
    files = {
        dest / ".env": env,
        dest / "config" / "go2rtc.yaml": yaml,
        dest / "config" / "cameras.json": catalog,
    }
    for path in files:
        if path.exists() and not force:
            raise SystemExit(f"Ya existe {path.name}. Vuelve a lanzarlo con --force para reemplazarlo.")
    (dest / "config").mkdir(parents=True, exist_ok=True)
    for path, text in files.items():
        path.write_text(text, encoding="utf-8")
        path.chmod(stat.S_IRUSR | stat.S_IWUSR)


def start_docker(dest: Path) -> None:
    if not shutil_which("docker"):
        print("Docker no está en este equipo. Cuando lo tengas: docker compose up -d --build")
        return
    if sys.stdin.isatty():
        answer = input("¿Arranco las cámaras ahora? [S/n] ").strip().lower()
        if answer and answer not in ("s", "si", "sí", "y", "yes"):
            print("Archivos listos. Para arrancar: docker compose up -d --build")
            return
    elif "--start" not in sys.argv:
        print("Archivos listos. Para arrancar: docker compose up -d --build")
        return
    subprocess.run(["docker", "compose", "up", "-d", "--build"], cwd=dest, check=False)


def shutil_which(name: str) -> str | None:
    from shutil import which

    return which(name)


def self_test() -> int:
    user = "op"
    password = "p" + "@ss:word#1"
    host = "recorder.example"
    cams = [
        {"channel": 1, "kind": "analog", "main_id": 101, "sub_id": 102, "name": "Entrada"},
        {"channel": 2, "kind": "ip", "main_id": 201, "sub_id": 202, "name": "Porche"},
    ]
    env, yaml, catalog = render(host, user, password, cams)
    encoded = quote(password, safe="")
    try:
        raise HTTPError("http://recorder.example/ISAPI", 401, "Unauthorized", hdrs=None, fp=None)
    except HTTPError as exc:
        auth_text = explain(exc)
    checks = {
        "encoded": encoded in yaml and password not in yaml,
        "quoted-hash": '"ffmpeg:' in yaml and "#video=h264/phone#input=rtsp/hd" in yaml,
        "env": password in env and env.startswith('DVR_HOST="'),
        "kinds": '"analog"' in catalog and '"ip"' in catalog,
        "names": "ch1_main" in catalog and "ip2_hd" in yaml,
        "explain": auth_text == "El grabador rechazó el usuario o la contraseña." and password not in auth_text,
    }
    failed = [name for name, ok in checks.items() if not ok]
    if failed:
        print("SELFTEST FAIL " + ",".join(failed))
        return 1
    print("SELFTEST OK")
    return 0


def main() -> int:
    if "--self-test" in sys.argv:
        return self_test()
    force = "--force" in sys.argv
    host, user, password = ask_account()
    cams = discover(host, user, password)
    if not cams:
        print("El grabador no devolvió canales con imagen.")
        cams = one_camera()
    analog = sum(1 for cam in cams if cam["kind"] == "analog")
    ip = sum(1 for cam in cams if cam["kind"] == "ip")
    print(f"Analógicas: {analog}")
    print(f"IP: {ip}")
    env, yaml, catalog = render(host, user, password, cams)
    write_tree(ROOT, env, yaml, catalog, force)
    if "--no-start" in sys.argv:
        print("Archivos listos. Página cuando arranque: http://127.0.0.1:8090/")
        return 0
    start_docker(ROOT)
    print("Página: http://127.0.0.1:8090/")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
