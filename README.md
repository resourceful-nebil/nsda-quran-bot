# NSDA Daily Quran Telegram automation

Automates **one video post per day** to a Telegram **channel**: caption from a template (day number, challenge text), plus either **URLs you host** (manifest mode) or **download from social profiles** via [yt-dlp](https://github.com/yt-dlp/yt-dlp) (social mode).

---

## What you need

| Requirement | Notes |
|-------------|--------|
| **Python 3.10+** (3.12+ recommended) | [python.org](https://www.python.org/downloads/). On Windows, enable **Add python.exe to PATH** or use the full path to `python.exe`. |
| **Telegram bot** | Create with [@BotFather](https://t.me/BotFather). You need the **bot token**. |
| **Channel** | Bot must be **admin** on the channel with permission to **post messages**. Use the channel id (often like `-100…`). |
| **GitHub** (optional) | For scheduled runs without your PC: push this repo and use **Actions** + **secrets**. |

---

## Quick start (local)

1. **Clone / open** this folder (`nsda-quran-bot` is the git repo root).

2. **Install dependencies**

   ```bash
   python -m pip install -r requirement.txt
   ```

   This installs `requests`, `python-dotenv`, and `yt-dlp`.

3. **Configure secrets** — copy `.env.example` to `.env` (same folder as `main.py` or one level up in the parent workspace):

   ```env
   TELEGRAM_BOT_TOKEN=123456:ABC...
   TELEGRAM_CHANNEL_ID=-1001234567890
   ```

   Never commit `.env` (it is listed in `.gitignore`).

4. **Choose a mode** (see below) and run the matching command once.

---

## Two ways it works

### A) Social mode (default in GitHub Actions) — `tools/daily_social_post.py`

**Idea:** Each run picks **one** video from a **rotating list of creator feeds** (TikTok profile, YouTube Shorts tab, Instagram reels URL, etc.), prefers items **not posted before**, downloads the file with **yt-dlp**, uploads it to Telegram, then updates state.

**Flow in detail:**

1. Read **`data/sources.json`**: ordered **`slots`**, each with `name` and `url` (profile / playlist page).
2. Read **`data/ingest_state.json`**: **`slot_index`** (which slot to use this run) and **`posted_ids`** (yt-dlp-style keys so the same clip is not chosen again until you allow repeats).
3. Take the slot at `slot_index`. Ask yt-dlp for a **flat playlist** of up to **`max_playlist_scan`** items (default 80).
4. **Shuffle** those entries and pick the first whose id is **not** in `posted_ids` → effectively a **random unseen** video among the scanned window.
5. If **no** slot yields a candidate, **`posted_ids` is cleared once** and the selection runs again (cycle repeats for continuous posting).
6. **Download** the chosen video to a temp folder (MP4 when possible).
7. If file is **> ~49 MB**, abort (Telegram Bot API upload limit for many bots).
8. Build caption from **`captions/template.txt`** using **`data/counter.json`** → `day` (placeholder `{{day}}`).
9. **`sendVideo`** to the channel (multipart upload).
10. Increment **`day`** in `counter.json`, append id to **`posted_ids`**, set **`slot_index`** to **next** slot (round-robin), save `ingest_state.json`.

**yt-dlp on Windows:** the script uses `yt-dlp` on PATH if present; otherwise **`python -m yt_dlp`** so it works even when `Scripts` is not on PATH.

**TikTok / Instagram:** often need a logged-in session. In `sources.json` set `"cookies_from_browser": "chrome"` (or `edge`, `firefox`) and run **locally** with that browser installed. GitHub-hosted runners frequently fail for these sites without extra setup.

**Run locally:**

```bash
cd nsda-quran-bot
python tools/daily_social_post.py
```

---

### B) Manifest mode — `main.py`

**Idea:** No download. The next video is the next entry in **`data/manifest.json`** — either a **direct HTTPS URL** to an MP4 (Telegram’s servers fetch it) or a **local path** relative to the repo (good for testing on your PC).

**Flow:**

1. Read **`manifest.json`** → list **`videos`**.
2. Read **`counter.json`** → **`index`** (position in list) and **`day`** (caption number).
3. Post that entry, then **`index += 1`**, **`day += 1`**.

**Run locally:**

```bash
python main.py
```

Copy **`data/manifest.example.json`** to **`data/manifest.json`** and fill in URLs or paths.

---

## Data files

| File | Used by | Purpose |
|------|---------|---------|
| `.env` | Both | Bot token and channel id (not committed). |
| `captions/template.txt` | Both | Caption; must contain `{{day}}` for the daily number. HTML allowed (`<pre>` etc.). |
| `data/counter.json` | Both | `day` = next caption number; `index` optional (manifest position). |
| `data/sources.json` | Social | Slots + yt-dlp options. Copy from `sources.example.json`. |
| `data/ingest_state.json` | Social | `slot_index`, `posted_ids`. Commit this if GitHub Actions should stay in sync. |
| `data/manifest.json` | Manifest | Ordered list of video URLs or paths. |
| `data/staging/` | Optional | Used by `tools/fetch_videos.py`; gitignored. |

---

## GitHub Actions

Workflow: **`.github/workflows/daily-quran.yml`**

- **Schedule:** cron `0 2 * * *` = **02:00 UTC every day** = **05:00 (5:00 AM) in UTC+3** (e.g. Addis Ababa, around Fajr). Ethiopia does not use daylight saving. GitHub only accepts **UTC** in `cron`. [crontab.guru](https://crontab.guru/#0_2_*_*_*) decodes the five fields.
- **Manual run:** Actions tab → **Daily Quran post** → **Run workflow** (any time, no wait for cron).

**Will it really run daily?** For a normal active repo: **yes** — one run per day at the scheduled UTC time (GitHub may start it a few minutes late when load is high). Requirements: Actions stay **enabled**, this workflow file stays on the **default branch** (usually `main`), and secrets remain set. If a run **fails** (e.g. TikTok blocks the runner), nothing posts that day until the next successful run. Very long **inactive** repositories can have scheduled workflows **disabled by GitHub** until you turn them back on in the Actions tab.

**First night had no scheduled run:** The timer only fires at **the next** `cron` time **after** this workflow (with `schedule:`) exists on **`main`**. If you merged **after** that day’s 02:00 UTC (5:00 AM Addis), the first automatic run is **the following** day at the same UTC time — GitHub does not backfill. Use **Run workflow** anytime to test. In the Actions UI, click **“Daily Quran post”** in the left sidebar to see this workflow’s runs; the event column shows **scheduled** vs **push**.

**When `schedule` never runs (push / “Run workflow” work):** GitHub’s cron is **not guaranteed** for every repo; runs can be **skipped or delayed a long time**, especially on **private** repositories. Check **Settings → Actions → General** (Actions enabled, **Read and write** permissions for workflows). If it still never shows a **scheduled** event, use the **reliable trigger** below instead of relying on `schedule` alone.

### Reliable daily time (recommended): `repository_dispatch` + external cron

The workflow also listens for **`repository_dispatch`** with event type **`daily_quran_post`**. Any service that can send one HTTPS POST per day at **your** local **5:00 AM** (or any time you choose) can start the **same** job (install, post, commit).

1. Create a **Personal Access Token** (GitHub → Settings → Developer settings):
   - **Classic:** enable scope **`repo`** (private repo) or **`public_repo`** (public only).
   - Or a **fine-grained** token with **Contents: Read** on this repository (and permission to create repository dispatch events — GitHub documents the exact box as *Contents* access for dispatch; if dispatch fails, use classic `repo`).

2. On [cron-job.org](https://cron-job.org) (or similar): create a job with timezone **Africa/Addis_Ababa**, schedule **05:00** daily, **URL**:

   `https://api.github.com/repos/YOUR_USER/nsda-quran-bot/dispatches`

   **Method:** POST  
   **Headers:**

   - `Accept: application/vnd.github+json`
   - `Authorization: Bearer YOUR_PAT_HERE`
   - `X-GitHub-Api-Version: 2022-11-28`

   **Body (JSON):**

   ```json
   {"event_type":"daily_quran_post"}
   ```

3. Save the PAT only in the cron service’s secret store, not in the repo.

After a run, the Actions list should show **repository_dispatch** as the event. You can **disable** the `schedule:` block in the YAML if you only want the external cron (optional).

**Secrets** (repository → Settings → Secrets and variables → Actions):

- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_CHANNEL_ID`

After a successful social post, the workflow **commits** `data/counter.json` and `data/ingest_state.json` with `[skip ci]` so it does not re-trigger itself.

**Switch to manifest-only posting:** in the workflow, change the run step to `python main.py` and ensure `manifest.json` is valid (you can drop `yt-dlp` from `requirement.txt` only if nothing else needs it).

---

## Optional tool: batch download by URL list

**`tools/fetch_videos.py`** reads a text file (one URL per line, `#` comments) and runs yt-dlp into **`data/staging/`**. Use when you want files locally before uploading to a CDN and switching to **manifest** mode.

```bash
python tools/fetch_videos.py tools/urls.example.txt
```

Requires `yt-dlp` on PATH **or** install via pip and use the same terminal where `python -m yt_dlp` works.

---

## Troubleshooting

| Issue | What to try |
|-------|-------------|
| `python` not found (Windows) | Reopen terminal after install; disable Store **app execution aliases** for `python.exe`; or use full path under `%LocalAppData%\Programs\Python\...`. |
| yt-dlp / TikTok errors | Run locally; add `"cookies_from_browser": "chrome"` in `sources.json`; update yt-dlp: `python -m pip install -U yt-dlp`. |
| GitHub Action fails on TikTok | Expected sometimes; run **social** script on a home PC + **Task Scheduler** daily, or use **manifest** + hosted MP4 URLs. |
| **`schedule` never fires** | GitHub cron is unreliable for some repos; use **repository_dispatch** + [cron-job.org](https://cron-job.org) (see README above). Confirm **Settings → Actions** enabled and workflow permissions **Read and write**. |
| Video too large | Telegram bot upload limit (~50 MB); pick another clip or compress. |
| Wrong channel | `TELEGRAM_CHANNEL_ID` must be the **channel** id (often negative `-100…`). |

---

## Responsibility

Respect **creator rights** and each platform’s **Terms of Service**. This project is a technical helper; you are responsible for what you post and how you source it.
