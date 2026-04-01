"""
Batch-download videos from explicit URLs using yt-dlp.

Unlike ``daily_social_post.py``, this does **not** post to Telegram. It is meant
for building a local library under ``data/staging/``, then uploading files to a
CDN and switching to **manifest** mode (``main.py``).

Run on your machine only (not designed for GitHub Actions). Install deps with
``python -m pip install -r requirement.txt`` so ``python -m yt_dlp`` is available
even when ``yt-dlp`` is not on PATH (Windows).

Usage::

    python tools/fetch_videos.py path/to/urls.txt
    python tools/fetch_videos.py tools/urls.example.txt --cookies-from-browser chrome
"""

from __future__ import annotations

import argparse
import importlib.util
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUT = ROOT / "data" / "staging"


def ytdlp_argv() -> list[str]:
    """Return argv prefix to run yt-dlp (PATH binary or ``python -m yt_dlp``)."""
    for name in ("yt-dlp", "yt-dlp.exe"):
        p = shutil.which(name)
        if p:
            return [p]
    if importlib.util.find_spec("yt_dlp") is not None:
        return [sys.executable, "-m", "yt_dlp"]
    raise RuntimeError("yt-dlp not found. Install: python -m pip install yt-dlp")


def read_urls(path: Path) -> list[str]:
    """Read non-empty, non-comment lines from a UTF-8 text file (``#`` comments)."""
    lines: list[str] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        s = line.strip()
        if s and not s.startswith("#"):
            lines.append(s)
    return lines


def main() -> int:
    """
    Parse CLI, run yt-dlp once per URL into the output directory.

    Returns:
        0 on success, non-zero on missing files or yt-dlp failure.
    """
    p = argparse.ArgumentParser(description="Download videos via yt-dlp into data/staging/")
    p.add_argument(
        "urls_file",
        type=Path,
        help="Text file: one URL per line (# starts a comment)",
    )
    p.add_argument(
        "-o",
        "--output-dir",
        type=Path,
        default=DEFAULT_OUT,
        help=f"Output folder (default: {DEFAULT_OUT})",
    )
    p.add_argument(
        "--cookies-from-browser",
        metavar="BROWSER",
        help="Optional: yt-dlp cookie extraction, e.g. chrome, firefox, edge",
    )
    args = p.parse_args()

    try:
        ytdlp_prefix = ytdlp_argv()
    except RuntimeError as e:
        print(str(e), file=sys.stderr)
        return 1

    if not args.urls_file.is_file():
        print(f"File not found: {args.urls_file}", file=sys.stderr)
        return 1

    urls = read_urls(args.urls_file)
    if not urls:
        print("No URLs in file.", file=sys.stderr)
        return 1

    args.output_dir.mkdir(parents=True, exist_ok=True)
    template = str(args.output_dir / "%(autonumber)03d_%(title).80B_%(id)s.%(ext)s")

    for url in urls:
        cmd: list[str] = [*ytdlp_prefix]
        if args.cookies_from_browser:
            cmd += ["--cookies-from-browser", args.cookies_from_browser]
        cmd += [
            "--no-playlist",
            "--restrict-filenames",
            "--trim-filenames",
            "100",
            "-o",
            template,
            url,
        ]

        print("Running yt-dlp for URL ...", flush=True)
        r = subprocess.run(cmd, cwd=str(ROOT))
        if r.returncode != 0:
            print(f"yt-dlp failed for: {url}", file=sys.stderr)
            return r.returncode

    print(f"\nDone. Files in: {args.output_dir.resolve()}")
    print("Next: upload MP4s to your bucket/CDN, then add HTTPS URLs to data/manifest.json.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
