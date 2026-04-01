"""
Download videos from URLs supported by yt-dlp (TikTok, YouTube Shorts, etc.).

Run on YOUR machine only — not from GitHub Actions. Install yt-dlp first:
  pip install yt-dlp
  (or use the standalone binary from https://github.com/yt-dlp/yt-dlp/releases)

Before bulk-downloading creators' work: "public on TikTok" does not automatically
mean you may repost elsewhere. Prefer written permission, clear attribution in
your Telegram captions, and respect each platform's Terms of Service.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUT = ROOT / "data" / "staging"


def read_urls(path: Path) -> list[str]:
    lines: list[str] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        s = line.strip()
        if s and not s.startswith("#"):
            lines.append(s)
    return lines


def main() -> int:
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

    ytdlp = shutil.which("yt-dlp")
    if not ytdlp:
        print("yt-dlp not found on PATH. Install: pip install yt-dlp", file=sys.stderr)
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
        cmd = [
            ytdlp,
            "--no-playlist",
            "--restrict-filenames",
            "--trim-filenames",
            "100",
            "-o",
            template,
            url,
        ]
        if args.cookies_from_browser:
            cmd.insert(1, "--cookies-from-browser")
            cmd.insert(2, args.cookies_from_browser)

        print("Running:", " ".join(cmd[:6]), "...", flush=True)
        r = subprocess.run(cmd, cwd=str(ROOT))
        if r.returncode != 0:
            print(f"yt-dlp failed for: {url}", file=sys.stderr)
            return r.returncode

    print(f"\nDone. Files in: {args.output_dir.resolve()}")
    print("Next: upload MP4s to your bucket/CDN, then add HTTPS URLs to data/manifest.json.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
