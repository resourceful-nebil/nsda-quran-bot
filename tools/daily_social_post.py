"""
Social ingest pipeline: one video per run for a Telegram channel.

Steps (see README for the full picture):

1. Load ``data/sources.json`` (ordered slots: creator profile / playlist URLs).
2. Load ``data/ingest_state.json`` (round-robin ``slot_index``, dedupe ``posted_ids``).
3. For the current slot, list up to ``max_playlist_scan`` items via yt-dlp, shuffle,
   pick a random entry whose id is not in ``posted_ids``.
4. Download to a temp file, enforce ~50 MB bot limit, ``sendVideo`` with caption from
   ``counter.json``'s ``day``.
5. Bump ``day``, append id, advance ``slot_index``, persist state.

Intended for **local or VPS** runs. TikTok/Instagram often need browser cookies;
GitHub Actions may fail on those sites. Ensure you have rights to repost content.

Run: ``python tools/daily_social_post.py`` from the project root.
"""

from __future__ import annotations

import importlib.util
import json
import os
import random
import re
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
"""Trim ``posted_ids`` to this length (oldest dropped) to keep JSON small."""
_YTDLP_SCHEME = re.compile(r"^[a-zA-Z][a-zA-Z0-9_+-]*:.+")
"""http(s) URLs plus yt-dlp native schemes such as ``tiktokuser:<secUid>``."""
_COOKIES_TMP: Path | None = None
"""Netscape cookies file written from ``YTDLP_COOKIES`` for this process."""


def ytdlp_argv() -> list[str]:
    """
    Build argv prefix to invoke yt-dlp.

    Prefer the ``yt-dlp`` / ``yt-dlp.exe`` executable on PATH; otherwise use
    ``sys.executable -m yt_dlp`` so Windows works when ``Scripts`` is not on PATH.

    Returns:
        A non-empty argv prefix, e.g. ``["yt-dlp"]`` or ``["python", "-m", "yt_dlp"]``.

    Raises:
        RuntimeError: If yt-dlp is not installed as a CLI or module.
    """
    for name in ("yt-dlp", "yt-dlp.exe"):
        p = shutil.which(name)
        if p:
            return [p]
    if importlib.util.find_spec("yt_dlp") is not None:
        return [sys.executable, "-m", "yt_dlp"]
    raise RuntimeError("yt-dlp not found. Install: python -m pip install yt-dlp")


def is_slot_url(url: object) -> bool:
    """
    True if ``url`` is a webpage URL or a yt-dlp extractor URI.

    ``https://www.tiktok.com/@handle`` works until TikTok hides ``secUid`` on the
    public profile page. Then yt-dlp's workaround is ``tiktokuser:<secUid>``,
    which is not ``http`` — we still have to accept it.
    """
    if not isinstance(url, str):
        return False
    u = url.strip()
    if u.startswith(("http://", "https://")):
        return True
    return bool(_YTDLP_SCHEME.match(u))


def cookies_cli_args(cookies_browser: str | None) -> list[str]:
    """
    yt-dlp cookie flags: file secret first, then browser name from sources.json.

    Order:
    1. ``YTDLP_COOKIES_FILE`` — path already on disk (GitHub Actions temp file).
    2. ``YTDLP_COOKIES`` — Netscape cookies.txt contents (repo secret / ``.env``).
    3. ``cookies_from_browser`` — local Chrome/Edge/Firefox (not on hosted runners).

    Returns:
        Zero or two argv tokens, e.g. ``["--cookies", "C:/tmp/cookies.txt"]``.
    """
    global _COOKIES_TMP
    path = (os.environ.get("YTDLP_COOKIES_FILE") or "").strip()
    if path:
        return ["--cookies", path]
    raw = os.environ.get("YTDLP_COOKIES")
    if raw and raw.strip():
        if _COOKIES_TMP is None:
            fd, tmp = tempfile.mkstemp(prefix="ytdlp_cookies_", suffix=".txt")
            os.close(fd)
            _COOKIES_TMP = Path(tmp)
            _COOKIES_TMP.write_text(raw, encoding="utf-8")
        return ["--cookies", str(_COOKIES_TMP)]
    if cookies_browser:
        return ["--cookies-from-browser", cookies_browser]
    return []


def run_ytdlp(args: list[str], *, cwd: Path) -> None:
    """
    Run yt-dlp with the given arguments; raise if exit code is non-zero.

    Args:
        args: Arguments only (no program name); combined with :func:`ytdlp_argv`.
        cwd: Working directory for the subprocess.

    Raises:
        RuntimeError: On non-zero exit; stderr/stdout snippet included.
    """
    cmd = [*ytdlp_argv(), *args]
    p = subprocess.run(cmd, cwd=str(cwd), capture_output=True, text=True)
    if p.returncode != 0:
        err = (p.stderr or p.stdout or "").strip()
        raise RuntimeError(f"yt-dlp failed ({p.returncode}): {err[:2000]}")


def playlist_flat_json(
    playlist_url: str,
    max_end: int,
    extra: list[str],
    cookies_browser: str | None,
) -> dict:
    """
    Return yt-dlp's JSON playlist dump (flat, no per-video download).

    Uses ``-J --flat-playlist --playlist-end N`` so each entry has id/url metadata
    without fetching full media.

    Args:
        playlist_url: Profile, tab, or playlist URL understood by yt-dlp.
        max_end: Maximum number of playlist items to enumerate.
        extra: Extra yt-dlp CLI args from ``sources.json`` (``yt_dlp_extra_args``).
        cookies_browser: If set, ``--cookies-from-browser NAME`` is prepended.

    Returns:
        Parsed top-level JSON (expects an ``entries`` list).

    Raises:
        RuntimeError: If yt-dlp fails or stdout is not valid JSON.
    """
    args = [
        "-J",
        "--flat-playlist",
        "--playlist-end",
        str(max_end),
        *extra,
        playlist_url,
    ]
    cookie_args = cookies_cli_args(cookies_browser)
    if cookie_args:
        args = [*cookie_args, *args]

    cmd = [*ytdlp_argv(), *args]
    p = subprocess.run(cmd, cwd=str(ROOT), capture_output=True, text=True)
    if p.returncode != 0:
        raise RuntimeError(f"yt-dlp list failed: {(p.stderr or p.stdout)[:1500]}")
    return json.loads(p.stdout)


def entry_key(entry: dict) -> str:
    """Stable dedupe key: ``extractor:id`` (or best-effort from yt-dlp fields)."""
    ex = entry.get("extractor") or entry.get("ie_key") or "unknown"
    vid = entry.get("id") or entry.get("url") or ""
    return f"{ex}:{vid}"


def pick_watch_url(entry: dict) -> str:
    """
    Extract a single watch page URL from a flat playlist entry.

    Raises:
        RuntimeError: If no suitable HTTP URL field is present.
    """
    for k in ("url", "webpage_url", "original_url"):
        v = entry.get(k)
        if isinstance(v, str) and v.startswith("http"):
            return v
    raise RuntimeError(f"No watch URL in entry: {entry.keys()}")


def load_state() -> dict:
    """Load ``ingest_state.json`` or return default ``slot_index=0``, ``posted_ids=[]``."""
    if not STATE_PATH.is_file():
        return {"slot_index": 0, "posted_ids": []}
    return read_json(STATE_PATH)


def save_state(state: dict) -> None:
    """Write ``ingest_state.json``, trimming ``posted_ids`` if longer than :data:`MAX_POSTED_IDS`."""
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
    Try each slot in rotation starting at ``slot_index`` until one yields a candidate.

    For each slot, list videos, shuffle, return the first not in ``posted``.

    Args:
        slots: ``sources.json`` slot dicts with ``url`` and optional ``name``.
        posted: Already-posted :func:`entry_key` values.
        slot_index: Starting slot (0-based).
        max_scan: ``--playlist-end`` value.
        extra: Extra yt-dlp args.
        cookies_browser: Optional browser name for cookies.

    Returns:
        Tuple ``(entry, chosen_key, watch_url, used_slot_index, slot_label)``, or
        ``None`` if every slot was tried without success.
    """
    n = len(slots)
    tries = 0

    while tries < n:
        slot = slots[slot_index]
        url = slot.get("url")
        label = str(slot.get("name") or url)
        if not is_slot_url(url):
            print(f"Invalid slot url at index {slot_index}: {slot!r}", file=sys.stderr)
            slot_index = (slot_index + 1) % n
            tries += 1
            continue
        try:
            blob = playlist_flat_json(url, max_scan, extra, cookies_browser)
        except Exception as e:
            print(f"Slot '{label}' list failed: {e}", file=sys.stderr)
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

        print(
            f"No new videos in slot '{label}' (raise max_playlist_scan, add slots, or allow repeats).",
            file=sys.stderr,
        )
        slot_index = (slot_index + 1) % n
        tries += 1

    return None


def main() -> int:
    """
    Execute one full social post cycle.

    If no video can be picked, clears ``posted_ids`` once and retries; if still
    failing, saves state and returns 1.

    Returns:
        0 on successful post, 1 on error.
    """
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
    cookie_args = cookies_cli_args(cookies_browser)
    if cookie_args:
        dl_args = [*cookie_args, *dl_args]

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
