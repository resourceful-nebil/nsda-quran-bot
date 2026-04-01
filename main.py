"""
Manifest pipeline: post the next DailyQuran video from ``data/manifest.json``.

Each successful run consumes one entry (by ``index``), sends it to the Telegram
channel with the caption for the current ``day``, then increments both counters
in ``data/counter.json``. Use this when videos are already hosted (HTTPS URLs) or
available as local files; no yt-dlp or social scraping involved.

Run: ``python main.py`` from the project root (same directory as this file).
"""

from __future__ import annotations

import sys
from pathlib import Path

from posting import (
    COUNTER_PATH,
    ROOT,
    read_json,
    render_caption,
    send_video,
    telegram_credentials,
    write_json,
)

MANIFEST_PATH = ROOT / "data" / "manifest.json"
"""Ordered queue of video entries (strings or small dicts with url/path)."""


def main() -> int:
    """
    Post ``manifest.videos[counter.index]`` and advance ``counter``.

    Returns:
        0 on success, 1 on configuration or IO errors (message on stderr).
    """
    token, chat_id = telegram_credentials()
    if not token or not chat_id:
        print(
            "Set TELEGRAM_BOT_TOKEN and TELEGRAM_CHANNEL_ID (env or .env).",
            file=sys.stderr,
        )
        return 1

    if not MANIFEST_PATH.is_file():
        print(
            f"Missing {MANIFEST_PATH}. Copy data/manifest.example.json to manifest.json.",
            file=sys.stderr,
        )
        return 1

    manifest = read_json(MANIFEST_PATH)
    videos = manifest.get("videos")
    if not isinstance(videos, list) or not videos:
        print("manifest.json: add a non-empty 'videos' list.", file=sys.stderr)
        return 1

    counter = read_json(COUNTER_PATH)
    day = int(counter.get("day", 1))
    index = int(counter.get("index", 0))

    if index >= len(videos):
        print(f"No more videos (index {index} >= {len(videos)}).", file=sys.stderr)
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
