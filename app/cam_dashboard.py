#!/usr/bin/env python3
"""Visor: page and API for your own recorder. No third-party cloud.

Live view goes through go2rtc (any RTSP). Search and download use
Hikvision ISAPI. Another brand needs another adapter.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import shutil
import signal
import socket
import subprocess
import tempfile
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlencode, urlparse
from urllib.request import (
    HTTPBasicAuthHandler,
    HTTPDigestAuthHandler,
    HTTPPasswordMgrWithDefaultRealm,
    Request,
    build_opener,
    urlopen,
)

APP_VERSION = "0.3.0"
ROOT = Path(os.environ.get("CAM_VIEWER_ROOT", "/var/www/cam-viewer"))
PORT = int(os.environ.get("CAM_VIEWER_PORT", "8090"))
HOST = os.environ.get("CAM_VIEWER_HOST", os.environ.get("VISOR_HOST", "127.0.0.1"))
DVR = os.environ.get("DVR_HOST", "")
DVR_USER = os.environ.get("DVR_USER", "")
DVR_PASS = os.environ.get("DVR_PASS", "")
GO2RTC = os.environ.get("GO2RTC_URL", "http://127.0.0.1:1984")
NS = {"h": "http://www.hikvision.com/ver20/XMLSchema"}
_play_lock = threading.Lock()
_export_lock = threading.Lock()
_jobs_lock = threading.Lock()
_export_ready: dict[str, dict] = {}
_export_job: dict | None = None


def _bounded_int(name: str, default: int, lo: int, hi: int) -> int:
    try:
        n = int(os.environ.get(name, str(default)) or default)
    except ValueError:
        n = default
    return min(hi, max(lo, n))


# 8 matches the 8-channel recorder already tested. Raise VISOR_CHANNELS on a larger one.
MAX_CAM = _bounded_int("VISOR_CHANNELS", 8, 1, 64)
GO2RTC_YAML = Path(os.environ.get("GO2RTC_YAML", "/etc/go2rtc/go2rtc.yaml"))


def _tool(name: str) -> str:
    return shutil.which(name) or f"/usr/local/bin/{name}"


_FFMPEG = _tool("ffmpeg")
_FFPROBE = _tool("ffprobe")
_EXPORT_MAX_S = 4 * 3600
# ISAPI download on this DVR's 100 Mbps port measured about 77 Mbps.
# RTSP playback stays near the file rate, so the choice below uses 70 Mbps.
_HTTP_LINK_BPS = 70_000_000
_offset_cache = {"at": 0.0, "offset": timedelta(hours=-5), "basis": "localz"}


def dvr_opener():
    if not DVR or not DVR_USER or not DVR_PASS:
        raise RuntimeError("missing credential")
    pm = HTTPPasswordMgrWithDefaultRealm()
    pm.add_password(None, f"http://{DVR}", DVR_USER, DVR_PASS)
    return build_opener(HTTPDigestAuthHandler(pm), HTTPBasicAuthHandler(pm))


def dvr_req(path: str, method: str = "GET", data: bytes | None = None, timeout: int = 20):
    headers = {}
    if data is not None:
        headers["Content-Type"] = "application/xml"
    req = Request(f"http://{DVR}{path}", data=data, method=method, headers=headers)
    return dvr_opener().open(req, timeout=timeout).read()


def dvr_admin_req(path: str, method: str = "GET", data: bytes | None = None, timeout: int = 15):
    # On this firmware an operator permission answers OK and is not stored.
    # The ISAPI write uses the admin user and the same password.
    if not DVR or not DVR_PASS:
        raise RuntimeError("missing credential")
    pm = HTTPPasswordMgrWithDefaultRealm()
    pm.add_password(None, f"http://{DVR}", "admin", DVR_PASS)
    opener = build_opener(HTTPDigestAuthHandler(pm), HTTPBasicAuthHandler(pm))
    headers = {"Content-Type": "application/xml"} if data is not None else {}
    req = Request(f"http://{DVR}{path}", data=data, method=method, headers=headers)
    return opener.open(req, timeout=timeout).read()


def _tag(el) -> str:
    return el.tag.split("}")[-1] if el is not None and el.tag else ""


_live_cache: dict = {"channels": []}
_live_lock = threading.Lock()


def analog_video() -> dict[int, bool]:
    raw = dvr_req("/ISAPI/System/Video/inputs/channels", timeout=8)
    root = ET.fromstring(raw)
    out: dict[int, bool] = {}
    for ch in root.iter():
        if _tag(ch) != "VideoInputChannel":
            continue
        cid = desc = None
        for el in list(ch):
            name = _tag(el)
            if name == "id" and el.text:
                cid = el.text.strip()
            elif name == "resDesc" and el.text:
                desc = el.text.strip()
        if cid and cid.isdigit():
            out[int(cid)] = bool(desc) and "NO VIDEO" not in desc.upper()
    return out


def operator_permission_path() -> str:
    try:
        raw = dvr_admin_req("/ISAPI/Security/users", timeout=10)
        root = ET.fromstring(raw)
    except (HTTPError, URLError, OSError, ET.ParseError):
        return "/ISAPI/Security/UserPermission/2"
    for user in root.iter():
        if _tag(user) != "User":
            continue
        uid = name = None
        for el in list(user):
            if _tag(el) == "id" and el.text:
                uid = el.text.strip()
            elif _tag(el) == "userName" and el.text:
                name = el.text.strip()
        if name == DVR_USER and uid and uid.isdigit():
            return f"/ISAPI/Security/UserPermission/{uid}"
    return "/ISAPI/Security/UserPermission/2"


def sync_remote_preview(live: dict[int, bool]) -> None:
    path = operator_permission_path()
    text = dvr_admin_req(path, timeout=12).decode("utf-8", "replace")
    start = text.find("<remotePermission")
    end = text.find("</remotePermission>")
    if start < 0 or end < 0:
        return
    remote = text[start:end]
    updated = remote
    for cid, on in live.items():
        if not 1 <= int(cid) <= 8:
            continue
        want = "true" if on else "false"
        off = f"<id>{cid}</id>\n<preview>false</preview>"
        on_s = f"<id>{cid}</id>\n<preview>true</preview>"
        if want == "true" and off in updated:
            updated = updated.replace(off, on_s, 1)
        elif want == "false" and on_s in updated:
            updated = updated.replace(on_s, off, 1)
    if updated == remote:
        return
    dvr_admin_req(path, "PUT", (text[:start] + updated + text[end:]).encode(), timeout=15)


def ip_online() -> list[dict]:
    raw = dvr_req("/ISAPI/ContentMgmt/InputProxy/channels/status", timeout=8)
    root = ET.fromstring(raw)
    found = []
    for ch in root.iter():
        if _tag(ch) != "InputProxyChannelStatus":
            continue
        cid = None
        online = False
        streams: list[int] = []
        for el in list(ch):
            name = _tag(el)
            if name == "id" and el.text and el.text.strip().isdigit():
                cid = int(el.text.strip())
            elif name == "online" and el.text:
                online = el.text.strip().lower() == "true"
            elif name == "streamingProxyChannelIdList":
                for sub in el.iter():
                    if _tag(sub) == "streamingProxyChannelId" and sub.text and sub.text.strip().isdigit():
                        streams.append(int(sub.text.strip()))
        if cid and online and streams:
            found.append({"id": cid, "streams": streams})
    return found


def _rtsp_base() -> str:
    text = GO2RTC_YAML.read_text(encoding="utf-8")
    match = re.search(r"rtsp://[^'\"\s]+@[^/'\"\s]+:554/Streaming/Channels/\d+", text)
    if not match:
        raise RuntimeError("rtsp template")
    return match.group(0)


def _put_go2rtc(name: str, src: str) -> None:
    query = urlencode([("name", name), ("src", src)])
    urlopen(Request(f"{GO2RTC}/api/streams?{query}", method="PUT"), timeout=8).read()


def ensure_ip_cameras(items: list[dict]) -> None:
    if not items:
        return
    base = _rtsp_base()
    yaml_path = GO2RTC_YAML
    yaml = yaml_path.read_text(encoding="utf-8")
    catalog_path = ROOT / "cameras.json"
    catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
    cameras = catalog.setdefault("cameras", [])
    known = {c.get("stream_main") for c in cameras}
    yaml_added = False
    catalog_added = False
    for item in items:
        streams = sorted(set(item["streams"]))
        mains = [n for n in streams if n % 100 == 1]
        subs = [n for n in streams if n % 100 == 2]
        main_id = mains[0] if mains else (subs[0] - 1 if subs else streams[0])
        sub_id = subs[0] if subs else main_id + 1
        proxy_id = int(item["id"])
        main_name = f"dvr_ip{proxy_id}_main"
        sub_name = f"dvr_ip{proxy_id}_sub"
        hd_name = f"dvr_ip{proxy_id}_hd"
        channel = main_id // 100
        if main_name not in known:
            cameras.append(
                {
                    "id": f"ip{proxy_id:02d}",
                    "name": f"IP camera {proxy_id:02d}",
                    "type": "stream",
                    "kind": "ip",
                    "channel": channel,
                    "stream_main": main_name,
                    "stream_sub": sub_name,
                }
            )
            known.add(main_name)
            catalog_added = True
        for name, number, hd in (
            (sub_name, sub_id, False),
            (main_name, main_id, False),
            (hd_name, main_id, True),
        ):
            src = re.sub(r"Channels/\d+", f"Channels/{number}", base)
            if hd:
                src = f"ffmpeg:{src}#video=h264/phone#input=rtsp/hd"
            if f"\n  {name}:" in f"\n{yaml}":
                continue
            if not yaml_added:
                bak = Path(f"/var/backups/go2rtc.yaml.bak-ip-{datetime.now().strftime('%Y%m%d')}")
                try:
                    if not bak.exists():
                        bak.parent.mkdir(parents=True, exist_ok=True)
                        bak.write_text(yaml, encoding="utf-8")
                        bak.chmod(0o600)
                except OSError:
                    pass
                yaml_added = True
            # El # de la plantilla es un comentario YAML si la URL va sin comillas.
            quoted = json.dumps(src)
            if hd or name.endswith("_main"):
                yaml += f"  {name}:\n    - {quoted}\n"
            else:
                yaml += f"  {name}: {quoted}\n"
            try:
                _put_go2rtc(name, src)
            except (HTTPError, URLError, OSError):
                pass
    if yaml_added:
        try:
            yaml_path.write_text(yaml, encoding="utf-8")
        except OSError:
            pass
    if catalog_added:
        try:
            catalog_path.write_text(
                json.dumps(catalog, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
            )
        except OSError:
            pass


_record_state: dict[int, bool] = {}
_record_checked = 0.0


def set_track_recording(track: int, enabled: bool) -> None:
    # El PUT completo lo rechaza el DVR. Hay que quitar los campos de solo lectura.
    path = f"/ISAPI/ContentMgmt/record/tracks/{track}"
    original = dvr_req(path, timeout=15)
    text = original.decode("utf-8", "replace")
    found = re.search(r"<Enable>\s*(true|false)\s*</Enable>", text, re.I)
    if not found:
        return
    current = found.group(1).lower() == "true"
    if current == enabled:
        return
    flag = "true" if enabled else "false"
    updated = text[: found.start()] + f"<Enable>{flag}</Enable>" + text[found.end() :]
    updated = re.sub(r"<SrcDescriptor>.*?</SrcDescriptor>", "", updated, count=1, flags=re.S)
    updated = re.sub(r"<TrackGUID>.*?</TrackGUID>", "", updated, count=1, flags=re.S)
    updated = re.sub(r"<Size>.*?</Size>", "", updated, count=1, flags=re.S)
    updated = re.sub(r"<Duration>.*?</Duration>", "", updated, count=1, flags=re.S)
    bak = Path(f"/var/backups/dvr-track-{track}-{datetime.now().strftime('%Y%m%d')}.xml")
    try:
        if not bak.exists():
            bak.parent.mkdir(parents=True, exist_ok=True)
            bak.write_bytes(original)
            bak.chmod(0o600)
    except OSError:
        pass
    dvr_admin_req(path, "PUT", updated.encode(), timeout=20)
    print(f"record track {track} enable={flag}", flush=True)


def sync_recording(wanted: dict[int, bool]) -> None:
    import time as _time

    global _record_checked
    now = _time.monotonic()
    force = now - _record_checked >= 600
    for cam in range(1, MAX_CAM + 1):
        if cam not in wanted:
            continue
        desire = bool(wanted[cam])
        if not force and _record_state.get(cam) is desire:
            continue
        try:
            set_track_recording(cam * 100 + 1, desire)
        except (HTTPError, URLError, OSError, ET.ParseError):
            continue
        _record_state[cam] = desire
    _record_checked = now


def refresh_live_sources() -> None:
    live = analog_video()
    try:
        sync_remote_preview(live)
    except (HTTPError, URLError, OSError, ET.ParseError):
        pass
    record = {n: bool(live.get(n)) for n in range(1, MAX_CAM + 1) if n in live}
    channels = [
        {"channel": n, "video": bool(live.get(n)), "kind": "analog"}
        for n in range(1, MAX_CAM + 1)
    ]
    try:
        ips = ip_online()
        ensure_ip_cameras(ips)
        for item in ips:
            mains = [n for n in item["streams"] if n % 100 == 1]
            channel = (mains[0] if mains else item["streams"][0]) // 100
            if 1 <= channel <= MAX_CAM:
                record[channel] = True
            channels.append({"channel": channel, "video": True, "kind": "ip", "proxy": item["id"]})
    except (HTTPError, URLError, OSError, ET.ParseError, RuntimeError, json.JSONDecodeError):
        pass
    try:
        sync_recording(record)
    except (HTTPError, URLError, OSError, ET.ParseError):
        pass
    with _live_lock:
        _live_cache["channels"] = channels


def _live_loop() -> None:
    import time as _time
    _time.sleep(4)
    while True:
        try:
            refresh_live_sources()
        except Exception:
            pass
        _time.sleep(60)


def xml_text(el, path):
    n = el.find(path, NS)
    if n is None:
        n = el.find(path.replace("h:", ""))
    return (n.text or "").strip() if n is not None and n.text else ""


def dvr_offset() -> timedelta:
    import time as _time
    now = _time.monotonic()
    if now - _offset_cache["at"] < 300:
        return _offset_cache["offset"]
    offset = timedelta(hours=-5)
    try:
        text = dvr_req("/ISAPI/System/time", timeout=8).decode("utf-8", "replace")
        found = re.search(r"<localTime>.*?([+-]\d{2}:\d{2})</localTime>", text)
        if found:
            sign = 1 if found.group(1)[0] == "+" else -1
            hours, minutes = found.group(1)[1:].split(":")
            offset = timedelta(hours=sign * int(hours), minutes=sign * int(minutes))
    except (HTTPError, URLError, OSError, ET.ParseError):
        pass
    _offset_cache["at"] = now
    _offset_cache["offset"] = offset
    return offset


def remember_basis(basis: str) -> None:
    if basis in ("localz", "utc"):
        _offset_cache["basis"] = basis


def stamp_naive(value: str) -> datetime:
    return datetime.strptime(value.strip()[:19], "%Y-%m-%dT%H:%M:%S")


def detect_basis(items: list[dict], local_now: datetime) -> str:
    """Hikvision labels the DVR local clock with a Z. Real UTC is the other case."""
    if not items:
        return _offset_cache.get("basis") or "localz"
    naive = stamp_naive(items[-1]["end"])
    local_naive = local_now.replace(tzinfo=None)
    utc_naive = local_now.astimezone(timezone.utc).replace(tzinfo=None)
    delta_local = abs((naive - local_naive).total_seconds())
    delta_utc = abs((naive - utc_naive).total_seconds())
    if delta_utc + 1800 < delta_local:
        return "utc"
    return "localz"


def localz_to_utc(value: str, offset: timedelta) -> str:
    utc = (stamp_naive(value) - offset).replace(tzinfo=timezone.utc)
    return utc.strftime("%Y-%m-%dT%H:%M:%SZ")


def utc_to_localz(value: str, offset: timedelta) -> str:
    local = stamp_naive(value).replace(tzinfo=timezone.utc) + offset
    return local.strftime("%Y-%m-%dT%H:%M:%SZ")


def dvr_now() -> datetime:
    try:
        text = dvr_req("/ISAPI/System/time", timeout=8).decode("utf-8", "replace")
        found = re.search(r"<localTime>(.*?)</localTime>", text)
        if found:
            return datetime.fromisoformat(found.group(1))
    except (HTTPError, URLError, OSError, ValueError):
        pass
    return datetime.now(timezone.utc) + dvr_offset()


def search_recordings(track_id: int, start: datetime, end: datetime, limit: int = 40) -> list[dict]:
    out = []
    position = 0
    while position < 500:
        sid = str(uuid.uuid4())
        body = f"""<?xml version="1.0" encoding="utf-8"?>
<CMSearchDescription>
  <searchID>{sid}</searchID>
  <trackList><trackID>{track_id}</trackID></trackList>
  <timeSpanList>
    <timeSpan>
      <startTime>{start.strftime("%Y-%m-%dT%H:%M:%SZ")}</startTime>
      <endTime>{end.strftime("%Y-%m-%dT%H:%M:%SZ")}</endTime>
    </timeSpan>
  </timeSpanList>
  <maxResults>{limit}</maxResults>
  <searchResultPostion>{position}</searchResultPostion>
  <metadataList>
    <metadataDescriptor>//recordType.meta.std-cgi.com</metadataDescriptor>
  </metadataList>
</CMSearchDescription>""".encode()
        raw = dvr_req("/ISAPI/ContentMgmt/search", "POST", body, timeout=25)
        root = ET.fromstring(raw)
        items = root.findall(".//h:searchMatchItem", NS) or root.findall(".//searchMatchItem")
        if not items:
            break
        for it in items:
            start_s = xml_text(it, "h:timeSpan/h:startTime")
            end_s = xml_text(it, "h:timeSpan/h:endTime")
            uri = xml_text(it, "h:mediaSegmentDescriptor/h:playbackURI")
            codec = xml_text(it, "h:mediaSegmentDescriptor/h:codecType")
            rec_type = xml_text(it, "h:metadataMatches/h:metadataDescriptor")
            if not start_s or not end_s:
                continue
            file_name, file_size = file_ids(uri)
            out.append(
                {
                    "track": track_id,
                    "start": start_s,
                    "end": end_s,
                    "name": file_name,
                    "size": file_size,
                    "codec": codec,
                    "type": rec_type.split("/")[-1] if rec_type else "record",
                    "has_uri": bool(uri),
                }
            )
        status = xml_text(root, "h:responseStatusStrg") or xml_text(root, "responseStatusStrg")
        position += len(items)
        if status != "MORE":
            break
    unique = []
    seen = set()
    for item in sorted(out, key=lambda row: row["start"]):
        key = (item["start"], item.get("name") or "")
        if key in seen:
            continue
        seen.add(key)
        unique.append(item)
    return unique


_archive_lock = threading.Lock()
_archive_cache: dict = {"at": 0.0, "key": 0, "body": None}
# 120 days covers what this recorder still keeps; the disk overwrites older clips.
_ARCHIVE_DAYS = 120


def archive_recordings(days: int = _ARCHIVE_DAYS) -> dict:
    days = min(_ARCHIVE_DAYS, max(1, int(days)))
    offset = dvr_offset()
    local_now = dvr_now()
    end_local = local_now.replace(tzinfo=None)
    start_local = end_local - timedelta(days=days)

    def one(cam: int):
        try:
            items = search_recordings(cam * 100 + 1, start_local, end_local, limit=40)
            return cam, items, None
        except (HTTPError, URLError, OSError, ET.ParseError, ValueError) as exc:
            return cam, [], public_err(exc)

    with ThreadPoolExecutor(max_workers=3) as pool:
        found = list(pool.map(one, range(1, MAX_CAM + 1)))

    sample = [items[-1] for _cam, items, _err in found if items]
    basis = detect_basis(sample, local_now)
    remember_basis(basis)
    if basis == "localz":
        for _cam, items, _err in found:
            for item in items:
                item["start"] = localz_to_utc(item["start"], offset)
                item["end"] = localz_to_utc(item["end"], offset)

    minutes = int(offset.total_seconds() // 60)
    sign = "+" if minutes >= 0 else "-"
    hh, mm = divmod(abs(minutes), 60)
    cameras = []
    total = 0
    for cam, items, err in found:
        total += len(items)
        cameras.append(
            {
                "cam": cam,
                "count": len(items),
                "truncated": len(items) >= 500,
                "error": err,
                "items": items,
            }
        )
    return {
        "days": days,
        "tz": f"{sign}{hh:02d}:{mm:02d}",
        "offsetMinutes": minutes,
        "now": local_now.isoformat(),
        "count": total,
        "cameras": cameras,
    }


def file_ids(uri: str) -> tuple[str, str]:
    query = parse_qs(urlparse(uri).query)
    name = (query.get("name") or [""])[0]
    size = (query.get("size") or [""])[0]
    if not re.fullmatch(r"\d{6,24}", name):
        name = ""
    if not re.fullmatch(r"\d{1,16}", size):
        size = ""
    return name, size


def hik_time(iso: str) -> str:
    s = iso.strip().replace("-", "").replace(":", "")
    s = re.sub(r"\.\d+", "", s)
    return s


def resolve_playback(track_id: int, start_utc: str, offset: timedelta, basis: str):
    """Open the DVR file that contains this instant.

    Search spans and RTSP starttime on this firmware are the DVR local clock
    with a Z. A range that crosses files does not seek.
    """
    stamp_naive(start_utc)
    if basis == "utc":
        when = stamp_naive(start_utc)
    else:
        when = (stamp_naive(start_utc).replace(tzinfo=timezone.utc) + offset).replace(tzinfo=None)
    items = search_recordings(
        track_id,
        when - timedelta(hours=3),
        when + timedelta(minutes=2),
        limit=20,
    )
    target = when.strftime("%Y-%m-%dT%H:%M:%SZ")
    chosen = None
    seek = None
    # endtime == starttime makes this firmware answer RTSP 400, so a point
    # that is only the end of a file is not inside it. The next file, which
    # starts on that same second, is the one that can play.
    inside = [
        it for it in items
        if it.get("name") and it.get("size") and it["start"] <= target < it["end"]
    ]
    if inside:
        chosen = inside[0]
        seek = target
    else:
        later = [it for it in items if it.get("name") and it.get("size") and it["start"] >= target]
        prev = [it for it in items if it.get("name") and it.get("size") and it["end"] <= target]
        if later:
            chosen = later[0]
            seek = chosen["start"]
        elif prev:
            chosen = prev[-1]
            tail = stamp_naive(chosen["end"]) - timedelta(seconds=5)
            begin = stamp_naive(chosen["start"])
            if tail < begin:
                tail = begin
            seek = tail.strftime("%Y-%m-%dT%H:%M:%SZ")
    if not chosen or not seek:
        raise ValueError("gap")
    if basis == "utc":
        opened_utc = seek
        end_utc = chosen["end"]
    else:
        opened_utc = localz_to_utc(seek, offset)
        end_utc = localz_to_utc(chosen["end"], offset)
    return opened_utc, end_utc, seek, chosen["end"], chosen["name"], chosen["size"]


def set_go2rtc_playback(track_id: int, start: str, end: str, file_name: str, file_size: str, video: str = "h264") -> str:
    st = hik_time(start)
    en = hik_time(end)
    if not re.fullmatch(r"\d{8}T\d{6}Z", st) or not re.fullmatch(r"\d{8}T\d{6}Z", en) or st >= en:
        raise ValueError("time")
    if not re.fullmatch(r"\d{6,24}", file_name) or not re.fullmatch(r"\d{1,16}", file_size):
        raise ValueError("file")
    rtsp = (
        f"rtsp://{DVR_USER}:{DVR_PASS}@{DVR}:554/Streaming/tracks/{track_id}/"
        f"?starttime={st}&endtime={en}&name={file_name}&size={file_size}"
    )
    stream = "dvr_playback"
    # Drop the previous file first. PUT replaces memory, but the yaml entry
    # keeps ffmpeg on the old RTSP until this DELETE. One producer only.
    # G.711 is stored on the file; AAC is what MSE/HLS will play.
    # h264/phone is level 5.1. rtsp/play reads at 1x so a file does not
    # fill the DVR 100 Mbps link. The template name has no spaces.
    try:
        urlopen(
            Request(f"{GO2RTC}/api/streams?{urlencode([('src', stream)])}", method="DELETE"),
            timeout=5,
        ).read()
    except HTTPError as e:
        if e.code not in (400, 404):
            raise
    # iPhone HLS is MPEG-TS, which plays H.264. The stored H.265 is what
    # the download keeps. The G.711 track stalls the first picture, so the
    # iPhone player is video only. Other clients keep AAC.
    if video == "ios":
        ffmpeg = f"ffmpeg:{rtsp}#video=h264/phone#input=rtsp/play"
    else:
        ffmpeg = f"ffmpeg:{rtsp}#video=h264/phone#audio=aac#input=rtsp/play"
    query = urlencode([("name", stream), ("src", ffmpeg)])
    urlopen(Request(f"{GO2RTC}/api/streams?{query}", method="PUT"), timeout=8).read()
    return stream


def public_err(exc: BaseException) -> str:
    text = re.sub(r"//[^/\s:]+:[^/\s@]+@", "//REDACTED@", str(exc))
    return text[:180]


def export_plan(cam: int, start_utc: str, end_utc: str):
    """Clips of the main track that overlap a UTC window. Times are DVR-local if basis is localz."""
    offset = dvr_offset()
    basis = _offset_cache.get("basis") or "localz"
    start_n = stamp_naive(start_utc)
    end_n = stamp_naive(end_utc)
    if basis == "utc":
        win_s, win_e = start_n, end_n
    else:
        win_s = (start_n.replace(tzinfo=timezone.utc) + offset).replace(tzinfo=None)
        win_e = (end_n.replace(tzinfo=timezone.utc) + offset).replace(tzinfo=None)
    if win_e <= win_s:
        raise ValueError("order")
    span = (win_e - win_s).total_seconds()
    if span < 60:
        raise ValueError("short")
    if span > _EXPORT_MAX_S:
        raise ValueError("long")
    items = search_recordings(
        cam * 100 + 1,
        win_s - timedelta(minutes=1),
        win_e + timedelta(minutes=1),
        limit=40,
    )
    plans = []
    for it in items:
        if not it.get("name") or not it.get("size"):
            continue
        fs = stamp_naive(it["start"])
        fe = stamp_naive(it["end"])
        clip_s = max(fs, win_s)
        clip_e = min(fe, win_e)
        if clip_e <= clip_s:
            continue
        plans.append(
            {
                "file_start": fs,
                "file_end": fe,
                "offset": (clip_s - fs).total_seconds(),
                "dur": (clip_e - clip_s).total_seconds(),
                "name": it["name"],
                "size": it["size"],
                "track": cam * 100 + 1,
            }
        )
    return plans, win_s, win_e, int(span)


def _plan_bytes(plan: dict) -> int:
    wall = (plan["file_end"] - plan["file_start"]).total_seconds()
    try:
        size = int(plan["size"])
    except (TypeError, ValueError):
        return 0
    if wall <= 0 or size <= 0:
        return 0
    return int(round(size * float(plan["dur"]) / wall))


def _export_estimate(plans: list[dict]) -> tuple[int, int]:
    seconds = int(round(sum(float(p["dur"]) for p in plans)))
    return seconds, sum(_plan_bytes(p) for p in plans)


def _export_room(nbytes: int, parts: int) -> tuple[bool, int]:
    free = shutil.disk_usage("/tmp").free
    # One file is written once. Several files sit together until the join.
    factor = 1.4 if parts <= 1 else 2.6
    need = int(nbytes * factor) + 64 * 1024 * 1024
    return free >= need, free


def _sweep_exports() -> None:
    now = time.time()
    for token, item in list(_export_ready.items()):
        if item.get("state") == "building":
            continue
        if now - float(item.get("at") or 0) < 900:
            continue
        path = item.get("path") or ""
        if path:
            Path(path).unlink(missing_ok=True)
        _export_ready.pop(token, None)


def _job_update(job: dict, **fields) -> None:
    with _jobs_lock:
        job.update(fields)


def _watch_add(job: dict, path: str) -> None:
    with _jobs_lock:
        watch = [item for item in (job.get("watch") or []) if item != path]
        watch.append(path)
        job["watch"] = watch
        job["fileDone"] = max(0, len(watch) - 1)


def _export_detail(exc: BaseException) -> str:
    code = str(exc)
    if code == "tiempo":
        return "The recorder took too long on that span."
    if code == "duracion":
        return "The file duration did not match that span."
    print("export", public_err(exc), flush=True)
    return "The file for that span could not be built."


def _export_snapshot(token: str) -> dict | None:
    with _jobs_lock:
        job = _export_ready.get(token)
        if not job:
            return None
        state = job.get("state") or "building"
        total = int(job.get("bytes") or 0)
        watch = list(job.get("watch") or [])
        path = job.get("path") or ""
        shown = int(job.get("shown") or 0)
        base = {
            "ok": state != "error",
            "state": state,
            "token": token,
            "name": job.get("name") or "",
            "seconds": int(job.get("seconds") or 0),
            "bytes": total,
            "files": int(job.get("files") or 1),
            "fileDone": int(job.get("fileDone") or 0),
            "detail": job.get("detail") or "",
        }
    if state == "ready":
        done = total
        pct = 100
    elif state == "error":
        done = 0
        pct = 0
    else:
        done = 0
        for item in watch:
            try:
                done += Path(item).stat().st_size
            except OSError:
                continue
        if path and path not in watch:
            try:
                done += Path(path).stat().st_size
            except OSError:
                pass
        # Joining files creates a second copy. Keep the bar on the larger view
        # of the work already finished, and never move it backwards.
        done = max(done, shown)
        if total > 0:
            done = min(done, total)
            pct = min(99, int(done * 100 / total))
        else:
            pct = 0
        with _jobs_lock:
            if job.get("state") == "building":
                job["shown"] = max(int(job.get("shown") or 0), done)
    base["done"] = done
    base["pct"] = pct
    return base


def _start_export(plans: list[dict], win_s: datetime, win_e: datetime, cam: int, recorded: int, nbytes: int) -> dict:
    global _export_job
    key = f"{cam}|{win_s.isoformat()}|{win_e.isoformat()}"
    with _jobs_lock:
        current = _export_job
    if current and current.get("state") == "building":
        if current.get("key") == key:
            return current
        raise RuntimeError("busy")
    if (
        current
        and current.get("state") == "ready"
        and current.get("key") == key
        and time.time() - float(current.get("at") or 0) < 120
        and current.get("path")
        and Path(current["path"]).exists()
    ):
        return current
    if not _export_lock.acquire(blocking=False):
        raise RuntimeError("busy")
    token = uuid.uuid4().hex
    name = f"cam{cam}-{win_s.strftime('%Y%m%d-%H%M%S')}-{win_e.strftime('%H%M%S')}.mp4"
    job = {
        "token": token,
        "key": key,
        "state": "building",
        "bytes": nbytes,
        "shown": 0,
        "seconds": recorded,
        "files": len(plans),
        "fileDone": 0,
        "name": name,
        "path": "",
        "watch": [],
        "detail": "",
        "at": time.time(),
    }
    with _jobs_lock:
        _export_job = job
        _export_ready[token] = job
    dest = Path("/tmp") / f"cam-export-{token}.mp4"
    title = f"cam{cam} {win_s.strftime('%Y-%m-%d %H:%M:%S')}"

    def run() -> None:
        try:
            _sweep_exports()
            got = _build_export(plans, dest, title, job)
            size = dest.stat().st_size
            _job_update(
                job,
                state="ready",
                bytes=size,
                shown=size,
                seconds=int(round(got)),
                path=str(dest),
                watch=[str(dest)],
                fileDone=len(plans),
                at=time.time(),
            )
        except Exception as exc:
            dest.unlink(missing_ok=True)
            _job_update(job, state="error", detail=_export_detail(exc), at=time.time())
        finally:
            _export_lock.release()

    try:
        threading.Thread(target=run, name="cam-export", daemon=True).start()
    except Exception as exc:
        _export_lock.release()
        _job_update(job, state="error", detail=_export_detail(exc), at=time.time())
    return job


def _export_rtsp(plan: dict) -> str:
    clip_s = plan["file_start"] + timedelta(seconds=float(plan["offset"]))
    if clip_s >= plan["file_end"]:
        clip_s = plan["file_start"]
    st = clip_s.strftime("%Y%m%dT%H%M%SZ")
    en = plan["file_end"].strftime("%Y%m%dT%H%M%SZ")
    if st >= en:
        raise RuntimeError("time")
    return (
        f"rtsp://{DVR_USER}:{DVR_PASS}@{DVR}:554/Streaming/tracks/{int(plan['track'])}/"
        f"?starttime={st}&endtime={en}&name={plan['name']}&size={plan['size']}"
    )


def _run_ffmpeg(cmd: list[str], timeout: float) -> tuple[int, str]:
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        start_new_session=True,
    )
    try:
        _, err = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except OSError:
            proc.kill()
        proc.wait(timeout=5)
        raise RuntimeError("tiempo")
    text = public_err(Exception(err.decode("utf-8", "replace")[-400:]))
    return proc.returncode, text


def _probe_duration(path: Path) -> float:
    proc = subprocess.run(
        [
            _FFPROBE, "-v", "error",
            "-show_entries", "format=duration",
            "-of", "csv=p=0",
            str(path),
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    try:
        return float(proc.stdout.strip())
    except ValueError:
        return 0.0


def _file_uri(plan: dict) -> str:
    st = plan["file_start"].strftime("%Y%m%dT%H%M%SZ")
    en = plan["file_end"].strftime("%Y%m%dT%H%M%SZ")
    return (
        f"rtsp://{DVR}:554/Streaming/tracks/{int(plan['track'])}/"
        f"?starttime={st}&endtime={en}&name={plan['name']}&size={plan['size']}"
    )


def _prefix_bytes(plan: dict) -> int:
    wall = (plan["file_end"] - plan["file_start"]).total_seconds()
    try:
        size = int(plan["size"])
    except (TypeError, ValueError):
        return 0
    need = float(plan["offset"]) + float(plan["dur"])
    if wall <= 0 or size <= 0 or need <= 0:
        return 0
    # Extra bytes cover a VBR burst so the last second is in the prefix.
    estimate = int(size * min(need, wall) / wall * 1.2) + 128 * 1024
    return min(size + 128 * 1024, estimate)


def _http_faster(plan: dict) -> bool:
    """True when reading the file prefix at link speed beats 1x playback."""
    prefix = _prefix_bytes(plan)
    dur = float(plan["dur"])
    if prefix <= 0 or dur <= 0:
        return False
    http_s = prefix * 8 / _HTTP_LINK_BPS
    # 8x playback is the fast RTSP path. HTTP wins only when the prefix
    # arrives sooner than that, which is the start of a long file.
    if http_s + 1.5 >= min(dur, dur / 8 + 4):
        return False
    free = shutil.disk_usage("/tmp").free
    if prefix + _plan_bytes(plan) + 64 * 1024 * 1024 > free:
        return False
    return True


def _bump_shown(job: dict | None, shown: int) -> None:
    if job is None:
        return
    with _jobs_lock:
        job["shown"] = max(int(job.get("shown") or 0), int(shown))


def _read_prefix(plan: dict, dest: Path, limit: int, job: dict | None, base: int, part: int) -> None:
    uri = _file_uri(plan).replace("&", "&amp;")
    xml = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<downloadRequest version="1.0" xmlns="http://www.isapi.org/ver20/XMLSchema">'
        f"<playbackURI>{uri}</playbackURI>"
        "</downloadRequest>"
    ).encode()
    req = Request(
        f"http://{DVR}/ISAPI/ContentMgmt/download",
        data=xml,
        headers={"Content-Type": "application/xml"},
        method="POST",
    )
    resp = dvr_opener().open(req, timeout=20)
    got = 0
    try:
        first = resp.read(4096)
        if not first:
            raise RuntimeError("vacio")
        if first.startswith(b"IMKH"):
            first = first[40:]
        mark = first.find(b"\x00\x00\x01\xba")
        if mark < 0:
            raise RuntimeError("formato")
        pending = first[mark:]
        with dest.open("wb") as fh:
            while pending and got < limit:
                take = pending[: limit - got]
                fh.write(take)
                got += len(take)
                if part > 0 and job is not None:
                    frac = min(1.0, got / limit)
                    _bump_shown(job, base + int(frac * part * 0.92))
                if got >= limit:
                    break
                pending = resp.read(256 * 1024)
                if not pending:
                    break
    finally:
        resp.close()
    if got < 1000:
        raise RuntimeError("vacio")


def _part_ok(dest: Path, dur: float) -> bool:
    if not dest.exists() or dest.stat().st_size < 1000:
        return False
    got = _probe_duration(dest)
    return got > 0 and abs(got - dur) <= max(3.0, dur * 0.08)


def _trim_prefix(src: Path, dest: Path, plan: dict, title: str) -> None:
    dur = float(plan["dur"])
    cmd = [
        _FFMPEG, "-hide_banner", "-loglevel", "error",
        "-ss", f"{float(plan['offset']):.3f}",
        "-i", str(src),
        "-t", f"{dur:.3f}",
        "-map", "0:v:0",
        "-c:v", "copy",
        "-avoid_negative_ts", "make_zero",
        "-movflags", "+faststart",
    ]
    if title:
        cmd += ["-metadata", f"title={title}"]
    cmd += ["-y", str(dest)]
    _run_ffmpeg(cmd, max(30.0, dur / 4 + 20))


def _write_part_http(plan: dict, dest: Path, title: str, job: dict | None) -> None:
    limit = _prefix_bytes(plan)
    part = _plan_bytes(plan)
    base = int(job.get("shown") or 0) if job is not None else 0
    fd, name = tempfile.mkstemp(prefix="cam-prefix-", suffix=".ps", dir="/tmp")
    os.close(fd)
    raw = Path(name)
    try:
        _read_prefix(plan, raw, limit, job, base, part)
        _trim_prefix(raw, dest, plan, title)
    finally:
        raw.unlink(missing_ok=True)
    if not _part_ok(dest, float(plan["dur"])):
        dest.unlink(missing_ok=True)
        raise RuntimeError("trim")


def _ffmpeg_copy(plan: dict, dest: Path, title: str, readrate: str | None, timeout: float) -> None:
    dur = float(plan["dur"])
    cmd = [
        _FFMPEG, "-hide_banner", "-loglevel", "error",
        "-rtsp_transport", "tcp",
        "-timeout", "8000000",
    ]
    if readrate:
        cmd += ["-readrate", readrate]
    cmd += [
        "-i", _export_rtsp(plan),
        "-t", f"{dur:.3f}",
        "-map", "0:v:0",
        "-c:v", "copy",
        "-avoid_negative_ts", "make_zero",
        "-movflags", "+faststart",
    ]
    if title:
        cmd += ["-metadata", f"title={title}"]
    cmd += ["-y", str(dest)]
    _code, err = _run_ffmpeg(cmd, timeout)
    if not _part_ok(dest, dur):
        dest.unlink(missing_ok=True)
        raise RuntimeError(err or "trim")


def _write_part_scale(plan: dict, dest: Path, title: str) -> None:
    """Pull the clip at Scale 8. This firmware tops out at 8x, about 15 Mbps."""
    dur = float(plan["dur"])
    base = _file_uri(plan)
    video_uri = base.replace("/?", "/trackID=video?", 1)
    audio_uri = base.replace("/?", "/trackID=audio?", 1)

    def md5(value: str) -> str:
        return hashlib.md5(value.encode()).hexdigest()

    class Buf:
        def __init__(self, sock: socket.socket):
            self.sock = sock
            self.buf = b""

        def read_msg(self) -> tuple[str, bytes]:
            self.sock.settimeout(8)
            while b"\r\n\r\n" not in self.buf:
                chunk = self.sock.recv(8192)
                if not chunk:
                    break
                self.buf += chunk
            head, _, rest = self.buf.partition(b"\r\n\r\n")
            text = head.decode("latin1", "replace")
            length = 0
            for line in text.split("\r\n"):
                if line.lower().startswith("content-length:"):
                    length = int(line.split(":", 1)[1].strip() or 0)
            while len(rest) < length:
                chunk = self.sock.recv(8192)
                if not chunk:
                    break
                rest += chunk
            self.buf = rest[length:]
            return text, rest[:length]

    def bridge(client: socket.socket, port: int) -> None:
        dvr = socket.create_connection((DVR, 554), timeout=8)
        cin, din = Buf(client), Buf(dvr)
        cseq = 1
        challenge = ""
        dvr_session = ""
        deadline = time.monotonic() + max(20.0, dur / 5 + 15)

        def auth(method: str, target: str) -> str:
            if not challenge:
                return ""
            realm = re.search(r'realm="([^"]*)"', challenge).group(1)
            nonce = re.search(r'nonce="([^"]*)"', challenge).group(1)
            qop_m = re.search(r'qop="?([^",\s]+)', challenge)
            opaque_m = re.search(r'opaque="([^"]*)"', challenge)
            qop = "auth" if qop_m and "auth" in qop_m.group(1) else ""
            ha1 = md5(f"{DVR_USER}:{realm}:{DVR_PASS}")
            ha2 = md5(f"{method}:{target}")
            if qop:
                nc = f"{cseq:08d}"
                cnonce = secrets.token_hex(8)
                resp = md5(f"{ha1}:{nonce}:{nc}:{cnonce}:{qop}:{ha2}")
                extra = f', qop={qop}, nc={nc}, cnonce="{cnonce}"'
            else:
                resp = md5(f"{ha1}:{nonce}:{ha2}")
                extra = ""
            opaque = f', opaque="{opaque_m.group(1)}"' if opaque_m else ""
            return (
                "Authorization: "
                f'Digest username="{DVR_USER}", realm="{realm}", nonce="{nonce}", '
                f'uri="{target}", response="{resp}"{extra}{opaque}\r\n'
            )

        def dvr_send(method: str, target: str, headers: str) -> tuple[str, bytes]:
            nonlocal cseq
            dvr.sendall(f"{method} {target} RTSP/1.0\r\nCSeq: {cseq}\r\n{headers}\r\n".encode())
            cseq += 1
            return din.read_msg()

        def reply(req: str, status: str, extra: str, body: bytes = b"") -> None:
            n = "1"
            for line in req.split("\r\n"):
                if line.lower().startswith("cseq:"):
                    n = line.split(":", 1)[1].strip()
            msg = f"RTSP/1.0 {status}\r\nCSeq: {n}\r\n{extra}"
            if body:
                msg += f"Content-Length: {len(body)}\r\n\r\n"
                client.sendall(msg.encode() + body)
            else:
                client.sendall((msg + "\r\n").encode())

        try:
            while time.monotonic() < deadline:
                req, _ = cin.read_msg()
                if not req.strip():
                    break
                method = req.split(" ", 1)[0].upper()
                target = req.split(" ", 2)[1] if req.count(" ") >= 1 else ""
                if method == "OPTIONS":
                    reply(req, "200 OK", "Public: OPTIONS, DESCRIBE, SETUP, PLAY, TEARDOWN\r\n")
                elif method == "DESCRIBE":
                    first, body = dvr_send("DESCRIBE", base, "Accept: application/sdp\r\n")
                    if " 401 " in first.split("\r\n", 1)[0]:
                        for line in first.split("\r\n"):
                            if line.lower().startswith("www-authenticate:"):
                                challenge = line.split(":", 1)[1].strip()
                        first, body = dvr_send(
                            "DESCRIBE", base,
                            "Accept: application/sdp\r\n" + auth("DESCRIBE", base),
                        )
                    if " 200 " not in first.split("\r\n", 1)[0]:
                        reply(req, "502 Bad Gateway", "")
                        break
                    reply(
                        req, "200 OK",
                        "Content-Type: application/sdp\r\n"
                        f"Content-Base: rtsp://127.0.0.1:{port}/v/\r\n",
                        body,
                    )
                elif method == "SETUP":
                    audio = "audio" in target
                    track_uri = audio_uri if audio else video_uri
                    transport = (
                        "RTP/AVP/TCP;unicast;interleaved=2-3"
                        if audio else "RTP/AVP/TCP;unicast;interleaved=0-1"
                    )
                    headers = f"Transport: {transport}\r\n"
                    if dvr_session:
                        headers += f"Session: {dvr_session}\r\n"
                    got, _ = dvr_send("SETUP", track_uri, headers + auth("SETUP", track_uri))
                    for line in got.split("\r\n"):
                        if line.lower().startswith("session:"):
                            dvr_session = line.split(":", 1)[1].split(";")[0].strip()
                    if " 200 " not in got.split("\r\n", 1)[0]:
                        reply(req, "461 Unsupported Transport", "")
                        if audio:
                            continue
                        break
                    reply(req, "200 OK", f"Session: 1\r\nTransport: {transport}\r\n")
                elif method == "PLAY":
                    got, _ = dvr_send(
                        "PLAY", base,
                        f"Session: {dvr_session}\r\nRange: npt=0.000-\r\nScale: 8.0\r\n"
                        + auth("PLAY", base),
                    )
                    if " 200 " not in got.split("\r\n", 1)[0]:
                        reply(req, "454 Session Not Found", "")
                        break
                    reply(req, "200 OK", "Session: 1\r\nRange: npt=0.000-\r\nScale: 8.0\r\n")
                    if din.buf:
                        client.sendall(din.buf)
                        din.buf = b""
                    dvr.settimeout(2)
                    while time.monotonic() < deadline:
                        try:
                            chunk = dvr.recv(256 * 1024)
                        except socket.timeout:
                            continue
                        if not chunk:
                            break
                        try:
                            client.sendall(chunk)
                        except OSError:
                            return
                    return
                elif method == "TEARDOWN":
                    reply(req, "200 OK", "Session: 1\r\n")
                    return
                else:
                    reply(req, "405 Method Not Allowed", "")
                    return
        finally:
            try:
                dvr.close()
            except OSError:
                pass
            try:
                client.close()
            except OSError:
                pass

    srv = socket.socket()
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    port = srv.getsockname()[1]
    cmd = [
        _FFMPEG, "-hide_banner", "-loglevel", "error",
        "-rtsp_transport", "tcp", "-timeout", "8000000",
        "-i", f"rtsp://127.0.0.1:{port}/v",
        "-t", f"{dur:.3f}",
        "-map", "0:v:0", "-c:v", "copy",
        "-avoid_negative_ts", "make_zero", "-movflags", "+faststart",
    ]
    if title:
        cmd += ["-metadata", f"title={title}"]
    cmd += ["-y", str(dest)]
    proc = subprocess.Popen(
        cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, start_new_session=True,
    )
    srv.settimeout(12)
    try:
        try:
            client, _ = srv.accept()
        except socket.timeout as exc:
            raise RuntimeError("tiempo") from exc
        try:
            bridge(client, port)
        except (ConnectionResetError, BrokenPipeError, TimeoutError, OSError):
            pass
        try:
            _, err = proc.communicate(timeout=max(20.0, dur / 5 + 15))
        except subprocess.TimeoutExpired:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except OSError:
                proc.kill()
            proc.wait(timeout=5)
            raise RuntimeError("tiempo")
        if not _part_ok(dest, dur):
            dest.unlink(missing_ok=True)
            raise RuntimeError(public_err(Exception(err.decode("utf-8", "replace")[-180:])) or "trim")
    finally:
        srv.close()
        if proc.poll() is None:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except OSError:
                proc.kill()


def _write_part(plan: dict, dest: Path, title: str, job: dict | None = None) -> None:
    if job is not None:
        _watch_add(job, str(dest))
    dur = float(plan["dur"])
    if _http_faster(plan):
        try:
            _write_part_http(plan, dest, title, job)
            print(f"export http dur={dur:.0f}", flush=True)
            return
        except Exception as exc:
            dest.unlink(missing_ok=True)
            print("export http-fallback", public_err(exc), flush=True)
    try:
        _write_part_scale(plan, dest, title)
        print(f"export scale dur={dur:.0f}", flush=True)
        return
    except Exception as exc:
        dest.unlink(missing_ok=True)
        print("export scale-fallback", public_err(exc), flush=True)
    # The DVR sends ordinary playback at about 1x. The extra 45s covers the
    # connect and the idle timeout after the last packet.
    _ffmpeg_copy(plan, dest, title, "8", dur + 45.0)
    print(f"export rtsp dur={dur:.0f}", flush=True)


def _retag_hvc1(dest: Path) -> None:
    """iPhone plays HEVC in MP4 only when the sample is tagged hvc1."""
    try:
        proc = subprocess.run(
            [
                _FFPROBE, "-v", "error", "-select_streams", "v:0",
                "-show_entries", "stream=codec_name,codec_tag_string",
                "-of", "default=nw=1", str(dest),
            ],
            capture_output=True, text=True, timeout=20, check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return
    text = proc.stdout or ""
    if "codec_name=hevc" not in text or "codec_tag_string=hvc1" in text:
        return
    tmp = dest.with_name(dest.stem + "-hvc1.mp4")
    try:
        try:
            code, _err = _run_ffmpeg(
                [
                    _FFMPEG, "-hide_banner", "-loglevel", "error",
                    "-i", str(dest),
                    "-map", "0:v:0", "-c:v", "copy", "-tag:v", "hvc1",
                    "-movflags", "+faststart",
                    "-y", str(tmp),
                ],
                120,
            )
        except RuntimeError:
            return
        if code != 0 or not tmp.exists() or tmp.stat().st_size < 1000:
            return
        tmp.replace(dest)
    finally:
        tmp.unlink(missing_ok=True)


def _build_export(plans: list[dict], dest: Path, title: str, job: dict | None = None) -> float:
    expected = sum(float(p["dur"]) for p in plans)
    with tempfile.TemporaryDirectory(prefix="cam-dl-", dir="/tmp") as tmp:
        root = Path(tmp)
        parts: list[Path] = []
        for i, plan in enumerate(plans):
            part = root / f"p{i}.mp4"
            _write_part(plan, part, title if len(plans) == 1 else "", job)
            parts.append(part)
            if job is not None:
                _job_update(job, fileDone=i + 1)
        if len(parts) == 1:
            shutil.move(str(parts[0]), dest)
            if job is not None:
                _job_update(job, path=str(dest), watch=[str(dest)])
        else:
            listing = root / "list.txt"
            listing.write_text(
                "".join(f"file '{part.as_posix()}'\n" for part in parts),
                encoding="utf-8",
            )
            code, err = _run_ffmpeg(
                [
                    _FFMPEG, "-hide_banner", "-loglevel", "error",
                    "-f", "concat", "-safe", "0", "-i", str(listing),
                    "-c", "copy",
                    "-movflags", "+faststart",
                    "-metadata", f"title={title}",
                    "-y", str(dest),
                ],
                max(90.0, expected),
            )
            if code != 0 or not dest.exists():
                raise RuntimeError(err or "union")
            if job is not None:
                _job_update(job, path=str(dest), watch=[str(dest)], shown=dest.stat().st_size)
    _retag_hvc1(dest)
    if job is not None and dest.exists():
        _job_update(job, path=str(dest), watch=[str(dest)], shown=dest.stat().st_size)
    got = _probe_duration(dest)
    if abs(got - expected) > max(3.0, expected * 0.05):
        dest.unlink(missing_ok=True)
        raise RuntimeError("duracion")
    return got


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(ROOT), **kwargs)

    def end_headers(self):
        self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Frame-Options", "SAMEORIGIN")
        super().end_headers()

    def log_message(self, fmt, *args):
        pass

    def _json(self, code: int, obj):
        b = json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path in ("", "/"):
            self.path = "/index.html"
            return super().do_GET()
        if parsed.path == "/api/recordings":
            return self._recordings(parsed.query)
        if parsed.path == "/api/recordings/archive":
            return self._archive(parsed.query)
        if parsed.path == "/api/recordings/export":
            return self._export(parsed.query)
        if parsed.path == "/api/health":
            return self._json(200, {"ok": True, "recorder": bool(DVR and DVR_USER and DVR_PASS)})
        if parsed.path == "/api/inputs":
            with _live_lock:
                channels = list(_live_cache["channels"])
            if not channels:
                try:
                    refresh_live_sources()
                except Exception:
                    return self._json(503, {"error": "dvr"})
                with _live_lock:
                    channels = list(_live_cache["channels"])
            if not channels:
                return self._json(503, {"error": "dvr"})
            return self._json(200, {"channels": channels})
        return super().do_GET()

    def do_POST(self):
        parsed = urlparse(self.path)
        if parsed.path == "/api/recordings/play":
            n = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(n) if n else b"{}"
            try:
                body = json.loads(raw.decode() or "{}")
            except json.JSONDecodeError:
                return self._json(400, {"error": "json"})
            return self._play(body)
        self.send_error(404)

    def _recordings(self, query: str):
        q = parse_qs(query)
        try:
            cam = int(q.get("cam", ["1"])[0])
            days = int(q.get("days", ["1"])[0])
        except ValueError:
            return self._json(400, {"error": "cam"})
        if cam < 1 or cam > MAX_CAM:
            return self._json(400, {"error": "cam range"})
        days = min(7, max(1, days))
        offset = dvr_offset()
        local_now = dvr_now()
        day = q.get("date", [local_now.strftime("%Y-%m-%d")])[0]
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", day):
            return self._json(400, {"error": "date"})
        end_local = datetime.strptime(day, "%Y-%m-%d")
        start_local = end_local - timedelta(days=days - 1)
        end_local_excl = end_local + timedelta(days=1)
        start_utc = (start_local - offset).replace(tzinfo=timezone.utc)
        end_utc = (end_local_excl - offset).replace(tzinfo=timezone.utc)
        try:
            # This DVR searches in local time written with a Z, not real UTC.
            items = search_recordings(cam * 100 + 1, start_local, end_local_excl)
            basis = detect_basis(items, local_now)
            if basis == "utc":
                items = search_recordings(cam * 100 + 1, start_utc, end_utc)
            elif not items:
                utc_items = search_recordings(cam * 100 + 1, start_utc, end_utc)
                if utc_items and detect_basis(utc_items, local_now) == "utc":
                    items = utc_items
                    basis = "utc"
        except (HTTPError, URLError, OSError, ET.ParseError, ValueError) as e:
            return self._json(502, {"error": "dvr", "detail": str(e)[:200]})
        remember_basis(basis)
        if basis == "localz":
            for item in items:
                item["start"] = localz_to_utc(item["start"], offset)
                item["end"] = localz_to_utc(item["end"], offset)
        minutes = int(offset.total_seconds() // 60)
        sign = "+" if minutes >= 0 else "-"
        hh, mm = divmod(abs(minutes), 60)
        return self._json(
            200,
            {
                "cam": cam,
                "date": day,
                "days": days,
                "tz": f"{sign}{hh:02d}:{mm:02d}",
                "offsetMinutes": minutes,
                "timeBasis": basis,
                "now": local_now.isoformat(),
                "windowStart": start_utc.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "windowEnd": end_utc.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "quality": "main",
                "count": len(items),
                "items": items,
            },
        )

    def _archive(self, query: str):
        q = parse_qs(query)
        fresh = q.get("fresh", ["0"])[0] == "1"
        try:
            days = int(q.get("days", [str(_ARCHIVE_DAYS)])[0])
        except ValueError:
            days = _ARCHIVE_DAYS
        days = min(_ARCHIVE_DAYS, max(1, days))
        now = datetime.now(timezone.utc).timestamp()
        with _archive_lock:
            cached = _archive_cache
            if not fresh and cached["body"] and cached["key"] == days and now - cached["at"] < 60:
                return self._json(200, cached["body"])
            try:
                body = archive_recordings(days)
            except (HTTPError, URLError, OSError, ET.ParseError, ValueError) as exc:
                return self._json(502, {"error": "dvr", "detail": public_err(exc)})
            cached["at"] = now
            cached["key"] = days
            cached["body"] = body
            return self._json(200, body)

    def _export(self, query: str):
        q = parse_qs(query)
        if q.get("progress", ["0"])[0] == "1":
            return self._export_progress(q.get("token", [""])[0])
        if q.get("file", [""])[0]:
            return self._export_file(q.get("file", [""])[0])
        try:
            cam = int(q.get("cam", ["1"])[0])
            start = q.get("start", [""])[0]
            end = q.get("end", [""])[0]
        except ValueError:
            return self._json(400, {"error": "params", "detail": "Invalid camera."})
        if cam < 1 or cam > MAX_CAM or not re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", start):
            return self._json(400, {"error": "params", "detail": "Choose a camera, a start, and an end."})
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", end):
            return self._json(400, {"error": "params", "detail": "Choose the end time."})
        try:
            plans, win_s, win_e, span = export_plan(cam, start, end)
        except ValueError as e:
            code = str(e)
            if code == "long":
                return self._json(400, {"error": "range", "detail": "The longest download is 4 hours."})
            if code == "short":
                return self._json(400, {"error": "range", "detail": "Choose at least one minute."})
            if code == "order":
                return self._json(400, {"error": "range", "detail": "The end time has to be after the start."})
            return self._json(400, {"error": "params", "detail": "That span could not be read."})
        except (HTTPError, URLError, OSError, ET.ParseError) as e:
            return self._json(502, {"error": "dvr", "detail": "The recorder did not answer the search."})
        plans = [plan for plan in plans if float(plan["dur"]) >= 1]
        if not plans:
            return self._json(404, {"error": "gap", "detail": "No recording in that span."})
        recorded, nbytes = _export_estimate(plans)
        fits, free = _export_room(nbytes, len(plans))
        if not fits:
            free_mb = max(0, int(free / 1_000_000))
            weight = round(nbytes / 1_000_000, 1)
            return self._json(
                507,
                {
                    "error": "disk",
                    "detail": f"That span is about {weight} MB and the viewer has {free_mb} MB free. Choose a shorter span.",
                    "seconds": recorded,
                    "bytes": nbytes,
                },
            )
        if q.get("check", ["0"])[0] == "1":
            return self._json(
                200,
                {
                    "ok": True,
                    "cam": cam,
                    "files": len(plans),
                    "seconds": recorded,
                    "span": span,
                    "bytes": nbytes,
                    "approxMb": round(nbytes / 1_000_000, 1),
                    "ext": "mp4",
                },
            )
        if q.get("build", ["0"])[0] != "1":
            return self._json(400, {"error": "params", "detail": "Use the Download button."})
        try:
            job = _start_export(plans, win_s, win_e, cam, recorded, nbytes)
        except RuntimeError:
            return self._json(409, {"error": "busy", "detail": "A download is already running."})
        snap = _export_snapshot(job["token"]) or {"ok": True, "state": "building", "token": job["token"]}
        return self._json(200, snap)

    def _export_progress(self, token: str):
        if not re.fullmatch(r"[0-9a-f]{32}", token):
            return self._json(400, {"error": "file", "state": "error", "detail": "That download is not valid."})
        snap = _export_snapshot(token)
        if not snap:
            return self._json(404, {"error": "file", "state": "error", "detail": "That download is gone. Ask for it again."})
        return self._json(200, snap)

    def _export_file(self, token: str):
        if not re.fullmatch(r"[0-9a-f]{32}", token):
            return self._json(400, {"error": "file", "detail": "That download is not valid."})
        item = _export_ready.get(token)
        path = Path(item["path"]) if item and item.get("path") else None
        if item and item.get("state") == "building":
            return self._json(409, {"error": "busy", "detail": "The file is still being prepared."})
        if item and item.get("state") == "error":
            return self._json(502, {"error": "export", "detail": item.get("detail") or "The file for that span could not be built."})
        if path is None or not path.exists():
            return self._json(404, {"error": "file", "detail": "That download is gone. Ask for it again."})
        _job_update(item, at=time.time())
        try:
            self.send_response(200)
            self.send_header("Content-Type", "video/mp4")
            self.send_header("Content-Length", str(path.stat().st_size))
            self.send_header("Content-Disposition", f'attachment; filename="{item["name"]}"')
            self.close_connection = True
            self.end_headers()
            with path.open("rb") as fh:
                shutil.copyfileobj(fh, self.wfile, length=256 * 1024)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _play(self, body: dict):
        try:
            cam = int(body.get("cam", 1))
            start = str(body.get("start", ""))
            end = str(body.get("end", ""))
        except (TypeError, ValueError):
            return self._json(400, {"error": "params"})
        if cam < 1 or cam > MAX_CAM or "T" not in start or "T" not in end:
            return self._json(400, {"error": "params"})
        offset = dvr_offset()
        basis = _offset_cache.get("basis") or "localz"
        try:
            opened, file_end, seek, file_stop, file_name, file_size = resolve_playback(
                cam * 100 + 1, start, offset, basis
            )
        except ValueError:
            return self._json(404, {"error": "gap", "detail": "No recording at that point."})
        except (HTTPError, URLError, OSError, ET.ParseError) as e:
            return self._json(502, {"error": "dvr", "detail": public_err(e)})
        try:
            with _play_lock:
                video = str(body.get("codec") or "h264")
            if video not in ("h264", "ios"):
                video = "h264"
            stream = set_go2rtc_playback(
                cam * 100 + 1, seek, file_stop, file_name, file_size, video
            )
        except Exception as e:
            return self._json(502, {"error": "go2rtc", "detail": public_err(e)})
        return self._json(
            200,
            {
                "stream": stream,
                "cam": cam,
                "start": opened,
                "end": file_end,
                "opened": opened,
                "quality": "main",
            },
        )


def main():
    demo = os.environ.get("VISOR_DEMO", "") == "1"
    if not demo and not (DVR and DVR_USER and DVR_PASS):
        raise SystemExit(
            "DVR_HOST, DVR_USER, and DVR_PASS are missing. "
            "Set them in the environment, or start with VISOR_DEMO=1 to open the page without a recorder."
        )
    ROOT.mkdir(parents=True, exist_ok=True)
    if not demo:
        threading.Thread(target=_live_loop, name="live-sources", daemon=True).start()
    httpd = ThreadingHTTPServer((HOST, PORT), Handler)
    httpd.serve_forever()


if __name__ == "__main__":
    main()
