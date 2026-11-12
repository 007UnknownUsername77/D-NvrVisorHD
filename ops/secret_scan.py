#!/usr/bin/env python3
"""Fail if the tree contains a private network, RTSP credentials, or a private key.

Prints CLEAN, or LEAK plus the path and the rule name. Never prints the line.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SKIP_DIRS = {".git", "__pycache__", ".venv"}
BAD_NAMES = {".env", "cameras.json", "go2rtc.yaml"}
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".ico"}

IP = re.compile(
    r"\b(?:"
    + r"10\.\d{1,3}\.\d{1,3}\.\d{1,3}"
    + r"|192\.168\.\d{1,3}\.\d{1,3}"
    + r"|172\.(?:1[6-9]|2\d|3[01])\.\d{1,3}\.\d{1,3}"
    + r")\b"
)
RTSP = "rtsp" + "://"
# Skip the template marker, code braces, and a regex character class.
CRED = re.compile(
    RTSP
    + r"(?!USER:PASSWORD@)"
    + r"(?!\{)"
    + r"(?!\[)"
    + r"[^/\s:{}\[\]\\]+:[^/\s@{}\\]+@"
)
KEY = re.compile(r"BEGIN (?:OPENSSH |RSA |EC )?PRIVATE KEY")
ASSIGNED = re.compile(
    r"(?i)(?:password|passwd|secret|api_token)\s*[:=]\s*['\"][^'\"]{8,}['\"]"
)


def rules(text: str) -> list[str]:
    found = []
    if IP.search(text):
        found.append("private-net")
    if CRED.search(text):
        found.append("rtsp")
    if KEY.search(text):
        found.append("private-key")
    if ASSIGNED.search(text):
        found.append("assigned-secret")
    return found


def main() -> int:
    leaks = 0
    for path in ROOT.rglob("*"):
        if not path.is_file():
            continue
        if any(part in SKIP_DIRS for part in path.parts):
            continue
        rel = path.relative_to(ROOT)
        if path.name in BAD_NAMES:
            print(f"LEAK {rel} name")
            leaks += 1
            continue
        if path.suffix.lower() in IMAGE_SUFFIXES:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            print(f"LEAK {rel} binario")
            leaks += 1
            continue
        for rule in rules(text):
            print(f"LEAK {rel} {rule}")
            leaks += 1
    if leaks:
        return 1
    print("CLEAN")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
