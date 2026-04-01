"""
Telegram channel posting utilities shared by the manifest and social pipelines.

Loads optional ``.env`` for ``TELEGRAM_BOT_TOKEN`` and ``TELEGRAM_CHANNEL_ID``,
renders the daily caption from ``captions/template.txt``, and sends one video
either by public URL (Telegram fetches the file) or by multipart file upload.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import requests

# Project root: directory containing main.py and data/
ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
# Day number and optional manifest index
COUNTER_PATH = DATA / "counter.json"
# Caption template; must include the literal {{day}} placeholder
TEMPLATE_PATH = ROOT / "captions" / "template.txt"
TELEGRAM_API = "https://api.telegram.org"


def load_dotenv_local() -> None:
    """
    Load environment variables from the first existing file: ``ROOT/.env`` or
    ``ROOT.parent/.env`` (so a parent workspace can hold secrets).

    No-op if ``python-dotenv`` is not installed or no file exists.
    """
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    for env_file in (ROOT / ".env", ROOT.parent / ".env"):
        if env_file.is_file():
            load_dotenv(env_file)
            break


def read_json(path: Path) -> dict:
    """Read a JSON object from ``path`` (UTF-8)."""
    with path.open(encoding="utf-8") as f:
        return json.load(f)


def write_json(path: Path, data: dict) -> None:
    """Write ``data`` as pretty-printed JSON with trailing newline; create parents if needed."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
        f.write("\n")


def render_caption(day: int) -> str:
    """
    Replace ``{{day}}`` in ``TEMPLATE_PATH`` with the integer ``day``.

    The template uses HTML parse mode when sent (e.g. ``<pre>`` blocks).
    """
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
    """
    Call ``sendVideo`` for a channel.

    Exactly one of ``video_url`` or ``video_path`` must be set:

    * ``video_url`` — Telegram servers fetch the resource (must be a direct file URL).
    * ``video_path`` — Uploaded as multipart form data from disk.

    ``caption`` is sent with ``parse_mode=HTML``.

    Returns:
        Parsed JSON ``result`` object from Telegram on success.

    Raises:
        RuntimeError: If the API returns ``ok: false``.
        ValueError: If neither or both sources are provided.
    """
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
    """
    Load dotenv then return ``(TELEGRAM_BOT_TOKEN, TELEGRAM_CHANNEL_ID)``.

    Stripped strings; may be empty if unset.
    """
    load_dotenv_local()
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.environ.get("TELEGRAM_CHANNEL_ID", "").strip()
    return token, chat_id
