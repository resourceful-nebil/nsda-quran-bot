"""
Shared Telegram posting helpers (manifest flow and social ingest both use this).
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
COUNTER_PATH = DATA / "counter.json"
TEMPLATE_PATH = ROOT / "captions" / "template.txt"
TELEGRAM_API = "https://api.telegram.org"


def load_dotenv_local() -> None:
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    for env_file in (ROOT / ".env", ROOT.parent / ".env"):
        if env_file.is_file():
            load_dotenv(env_file)
            break


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


def telegram_credentials() -> tuple[str, str]:
    load_dotenv_local()
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.environ.get("TELEGRAM_CHANNEL_ID", "").strip()
    return token, chat_id
