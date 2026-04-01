"""
Post the next DailyQuran video to a Telegram channel.
Videos are listed by URL in data/manifest.json (host files on a CDN/bucket, not in git).
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
COUNTER_PATH = DATA / "counter.json"
MANIFEST_PATH = DATA / "manifest.json"
TEMPLATE_PATH = ROOT / "captions" / "template.txt"

TELEGRAM_API = "https://api.telegram.org"


def load_dotenv_local() -> None:
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    env_file = ROOT.parent / ".env"
    if env_file.is_file():
        load_dotenv(env_file)


def read_json(path: Path) -> dict:
    with path.open(encoding="utf-8") as f:
        return json.load(f)


def write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
        f.write("\n")


def render_caption(day: int) -> str:
    raw = TEMPLATE_PATH.read_text(encoding="utf-8")
    return raw.replace("{{day}}", str(day))


def send_video(
    token: str,
    chat_id: str,
    *,
    video_url: str | None = None,
    video_path: Path | None = None,
    caption: str,
) -> dict:
    url = f"{TELEGRAM_API}/bot{token}/sendVideo"
    # Channel captions: HTML matches screenshot (monospace block via <pre>)
    data = {"chat_id": chat_id, "caption": caption, "parse_mode": "HTML"}

    if video_url:
        data["video"] = video_url
        r = requests.post(url, data=data, timeout=120)
    elif video_path is not None:
        with video_path.open("rb") as vf:
            files = {"video": (video_path.name, vf, "video/mp4")}
            r = requests.post(url, data=data, files=files, timeout=300)
    else:
        raise ValueError("Need video_url or video_path")

    body = r.json()
    if not body.get("ok"):
        raise RuntimeError(f"Telegram API error: {body}")
    return body


def main() -> int:
    load_dotenv_local()
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.environ.get("TELEGRAM_CHANNEL_ID", "").strip()
    if not token or not chat_id:
        print(
            "Set TELEGRAM_BOT_TOKEN and TELEGRAM_CHANNEL_ID (env or .env in repo root).",
            file=sys.stderr,
        )
        return 1

    if not MANIFEST_PATH.is_file():
        print(
            f"Missing {MANIFEST_PATH}. Copy data/manifest.example.json to manifest.json and add video URLs.",
            file=sys.stderr,
        )
        return 1

    manifest = read_json(MANIFEST_PATH)
    videos = manifest.get("videos")
    if not isinstance(videos, list) or not videos:
        print("manifest.json: add a non-empty 'videos' list (URLs or local paths).", file=sys.stderr)
        return 1

    counter = read_json(COUNTER_PATH)
    day = int(counter.get("day", 1))
    index = int(counter.get("index", 0))

    if index >= len(videos):
        print(f"No more videos (index {index} >= {len(videos)}). Append URLs or reset index.", file=sys.stderr)
        return 1

    entry = videos[index]
    video_url: str | None = None
    video_path: Path | None = None
    if isinstance(entry, str):
        if entry.startswith("http://") or entry.startswith("https://"):
            video_url = entry
        else:
            p = Path(entry)
            if not p.is_absolute():
                p = ROOT / p
            video_path = p
    elif isinstance(entry, dict):
        u = entry.get("url")
        p = entry.get("path")
        if u:
            video_url = str(u)
        elif p:
            path = Path(p)
            if not path.is_absolute():
                path = ROOT / path
            video_path = path
    else:
        print("Each manifest entry must be a URL string or {\"url\": ...} / {\"path\": ...}.", file=sys.stderr)
        return 1

    if video_path is not None and not video_path.is_file():
        print(f"Video file not found: {video_path}", file=sys.stderr)
        return 1

    caption = render_caption(day)
    send_video(token, chat_id, video_url=video_url, video_path=video_path, caption=caption)

    counter["day"] = day + 1
    counter["index"] = index + 1
    write_json(COUNTER_PATH, counter)
    print(f"Posted day #{day} (manifest index {index}).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
