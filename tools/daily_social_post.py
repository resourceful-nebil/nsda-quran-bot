"""
One video per run: round-robin across slots (by platform/account), random pick among
videos not yet posted (tracked in data/ingest_state.json), download with yt-dlp, post
to Telegram, bump data/counter.json.

Designed to run on YOUR machine (or a VPS) where yt-dlp works; TikTok/Instagram often
need cookies (--cookies-from-browser or cookies file). GitHub-hosted runners are flaky
for logged-in sites.

Ensure you have rights to repost; platform ToS may still restrict automated access.
"""

from __future__ import annotations

import json
import random
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from posting import (  # noqa: E402
    COUNTER_PATH,
    read_json,
    render_caption,
    send_video,
    telegram_credentials,
    write_json,
)

SOURCES_PATH = ROOT / "data" / "sources.json"
STATE_PATH = ROOT / "data" / "ingest_state.json"
MAX_POSTED_IDS = 3000


def ytdlp_bin() -> str | None:
    return shutil.which("yt-dlp")


def run_ytdlp(args: list[str], *, cwd: Path) -> None:
    bin_path = ytdlp_bin()
    if not bin_path:
        raise RuntimeError("yt-dlp not on PATH. Install: pip install yt-dlp")
    cmd = [bin_path, *args]
    p = subprocess.run(cmd, cwd=str(cwd), capture_output=True, text=True)
    if p.returncode != 0:
        err = (p.stderr or p.stdout or "").strip()
        raise RuntimeError(f"yt-dlp failed ({p.returncode}): {err[:2000]}")


def playlist_flat_json(playlist_url: str, max_end: int, extra: list[str], cookies_browser: str | None) -> dict:
    args = [
        "-J",
        "--flat-playlist",
        "--playlist-end",
        str(max_end),
        "--no-warnings",
        *extra,
        playlist_url,
    ]
    if cookies_browser:
        args = ["--cookies-from-browser", cookies_browser, *args]

    bin_path = ytdlp_bin()
    if not bin_path:
        raise RuntimeError("yt-dlp not on PATH")
    cmd = [bin_path, *args]
    p = subprocess.run(cmd, cwd=str(ROOT), capture_output=True, text=True)
    if p.returncode != 0:
        raise RuntimeError(f"yt-dlp list failed: {(p.stderr or p.stdout)[:1500]}")
    return json.loads(p.stdout)


def entry_key(entry: dict) -> str:
    ex = entry.get("extractor") or entry.get("ie_key") or "unknown"
    vid = entry.get("id") or entry.get("url") or ""
    return f"{ex}:{vid}"


def pick_watch_url(entry: dict) -> str:
    for k in ("url", "webpage_url", "original_url"):
        v = entry.get(k)
        if isinstance(v, str) and v.startswith("http"):
            return v
    raise RuntimeError(f"No watch URL in entry: {entry.keys()}")


def load_state() -> dict:
    if not STATE_PATH.is_file():
        return {"slot_index": 0, "posted_ids": []}
    return read_json(STATE_PATH)


def save_state(state: dict) -> None:
    ids = state.get("posted_ids")
    if isinstance(ids, list) and len(ids) > MAX_POSTED_IDS:
        state = {**state, "posted_ids": ids[-MAX_POSTED_IDS:]}
    write_json(STATE_PATH, state)


def pick_one_video(
    slots: list[dict],
    posted: set[str],
    slot_index: int,
    max_scan: int,
    extra: list[str],
    cookies_browser: str | None,
) -> tuple[dict, str, str, int, str] | None:
    """
    Returns (chosen_entry, chosen_key, watch_url, used_slot_index, slot_label) or None.
    """
    n = len(slots)
    tries = 0
    last_err: str | None = None

    while tries < n:
        slot = slots[slot_index]
        url = slot.get("url")
        if not isinstance(url, str) or not url.startswith("http"):
            print(f"Invalid slot url at index {slot_index}: {slot!r}", file=sys.stderr)
            return None
        label = str(slot.get("name") or url)
        try:
            blob = playlist_flat_json(url, max_scan, extra, cookies_browser)
        except Exception as e:
            last_err = str(e)
            slot_index = (slot_index + 1) % n
            tries += 1
            continue

        entries = [e for e in (blob.get("entries") or []) if isinstance(e, dict)]
        random.shuffle(entries)
        for e in entries:
            k = entry_key(e)
            if k in posted:
                continue
            try:
                w = pick_watch_url(e)
            except RuntimeError:
                continue
            return (e, k, w, slot_index, label)

        last_err = (
            f"No new videos in slot '{label}' (raise max_playlist_scan, add slots, or allow repeats)."
        )
        slot_index = (slot_index + 1) % n
        tries += 1

    if last_err:
        print(last_err, file=sys.stderr)
    return None


def main() -> int:
    token, chat_id = telegram_credentials()
    if not token or not chat_id:
        print("Set TELEGRAM_BOT_TOKEN and TELEGRAM_CHANNEL_ID.", file=sys.stderr)
        return 1

    if not SOURCES_PATH.is_file():
        print(
            f"Missing {SOURCES_PATH}. Copy data/sources.example.json to sources.json and edit slots.",
            file=sys.stderr,
        )
        return 1

    cfg = read_json(SOURCES_PATH)
    slots = cfg.get("slots")
    if not isinstance(slots, list) or not slots:
        print("sources.json: need non-empty 'slots' list.", file=sys.stderr)
        return 1

    max_scan = int(cfg.get("max_playlist_scan", 80))
    extra = cfg.get("yt_dlp_extra_args") or []
    if not isinstance(extra, list):
        extra = []
    extra = [str(x) for x in extra]
    cookies_browser = cfg.get("cookies_from_browser")
    if cookies_browser is not None:
        cookies_browser = str(cookies_browser).strip() or None

    state = load_state()
    slot_index = int(state.get("slot_index", 0)) % len(slots)
    posted: set[str] = set(state.get("posted_ids") or [])

    picked = None
    for allow_reset in (False, True):
        picked = pick_one_video(slots, posted, slot_index, max_scan, extra, cookies_browser)
        if picked is not None:
            break
        if not allow_reset:
            print(
                "Could not pick a new video; clearing posted_ids once to allow repeats.",
                file=sys.stderr,
            )
            posted = set()
            state["posted_ids"] = []
            slot_index = 0

    if picked is None:
        print("Still no video. Fix sources.json, cookies, or yt-dlp.", file=sys.stderr)
        save_state(state)
        return 1

    chosen, chosen_key, watch_url, used_slot_index, slot_label = picked

    tmpdir = Path(tempfile.mkdtemp(prefix="dq_", dir=ROOT / "data"))
    out_tmpl = str(tmpdir / "clip.%(id)s.%(ext)s")
    dl_args = [
        "-f",
        "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best",
        "--merge-output-format",
        "mp4",
        "-o",
        out_tmpl,
        "--no-warnings",
        *extra,
        watch_url,
    ]
    if cookies_browser:
        dl_args = ["--cookies-from-browser", cookies_browser, *dl_args]

    try:
        run_ytdlp(dl_args, cwd=ROOT)
    except Exception as e:
        print(e, file=sys.stderr)
        shutil.rmtree(tmpdir, ignore_errors=True)
        return 1

    files = sorted(tmpdir.glob("clip.*"))
    if not files:
        shutil.rmtree(tmpdir, ignore_errors=True)
        print("yt-dlp produced no file.", file=sys.stderr)
        return 1

    video_path = files[0]
    size_mb = video_path.stat().st_size / (1024 * 1024)
    if size_mb > 49:
        shutil.rmtree(tmpdir, ignore_errors=True)
        print(f"File too large for Telegram bot (~{size_mb:.1f} MB). Skip or compress.", file=sys.stderr)
        return 1

    counter = read_json(COUNTER_PATH)
    day = int(counter.get("day", 1))

    try:
        caption = render_caption(day)
        send_video(token, chat_id, video_path=video_path, caption=caption)
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)

    counter["day"] = day + 1
    if "index" in counter:
        counter["index"] = int(counter.get("index", 0))
    write_json(COUNTER_PATH, counter)

    posted.add(chosen_key)
    state["posted_ids"] = list(posted)
    state["slot_index"] = (used_slot_index + 1) % len(slots)
    save_state(state)

    title = chosen.get("title") or ""
    print(
        f"Posted day #{day} from [{slot_label}] ({title[:80]}). "
        f"Next slot index: {state['slot_index']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
