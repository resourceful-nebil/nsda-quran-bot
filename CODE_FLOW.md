# Code flow (NSDA Quran bot)

This document describes how the project is structured, how data moves through it, and how the two posting modes relate. Diagrams use [Mermaid](https://mermaid.js.org/) (render in GitHub, VS Code preview, or any Mermaid-capable viewer).

---

## 1. Big picture

Two independent ways to post one video per run to Telegram; both share **`posting.py`** for credentials, captions, and `sendVideo`.

```mermaid
flowchart TB
  subgraph shared [Shared layer]
    P[posting.py]
    T[captions/template.txt]
    C[data/counter.json]
  end

  subgraph manifest [Manifest pipeline]
    M[main.py]
    MF[data/manifest.json]
    M --> MF
    M --> P
    M --> C
  end

  subgraph social [Social ingest pipeline]
    D[tools/daily_social_post.py]
    S[data/sources.json]
    I[data/ingest_state.json]
    YT[yt-dlp]
    D --> S
    D --> I
    D --> YT
    D --> P
    D --> C
  end

  subgraph helper [Optional helper — no Telegram]
    F[tools/fetch_videos.py]
    ST[data/staging/]
    F --> ST
    F --> YT
  end

  GH[GitHub Actions<br/>daily-quran.yml] --> D
```

| Entry point | Primary data | Typical use |
|-------------|--------------|-------------|
| `main.py` | `manifest.json` + `counter.json` | Videos already at HTTPS URLs or local paths |
| `tools/daily_social_post.py` | `sources.json` + `ingest_state.json` + `counter.json` | Pull from creator URLs/playlists, download, upload to Telegram |
| `tools/fetch_videos.py` | URL list file → `data/staging/` | Build a library, then upload to CDN and switch to manifest mode |

---

## 2. Shared module: `posting.py`

```mermaid
flowchart LR
  subgraph inputs
    ENV[TELEGRAM_* env / .env]
    TP[captions/template.txt]
    DAY[day number from counter]
  end

  subgraph posting_py [posting.py]
    LD[load_dotenv_local]
    TC[telegram_credentials]
    RC[render_caption]
    SV[send_video]
    RJ[read_json / write_json]
  end

  subgraph output
    TG[Telegram sendVideo API]
  end

  ENV --> LD --> TC
  TP --> RC
  DAY --> RC
  RC --> SV
  TC --> SV
  SV --> TG
```

- **`ROOT`**: directory containing `main.py` (used to resolve `data/`, `captions/`).
- **`render_caption(day)`**: replaces `{{day}}` in the template; caption is sent with HTML parse mode.
- **`send_video`**: exactly one of public **`video_url`** (Telegram fetches) or **`video_path`** (multipart upload). Timeouts differ (URL vs file).

---

## 3. Manifest pipeline: `main.py`

```mermaid
flowchart TD
  A[Start] --> B{Token + channel ID?}
  B -->|no| Z[stderr + exit 1]
  B -->|yes| C{manifest.json exists?}
  C -->|no| Z
  C -->|yes| D{videos list non-empty?}
  D -->|no| Z
  D -->|yes| E[Read counter: day, index]
  E --> F{index < len videos?}
  F -->|no| Z
  F -->|yes| G[Parse entry: str URL/path or dict url/path]
  G --> H{Valid + file exists if path?}
  H -->|no| Z
  H -->|yes| I[render_caption day]
  I --> J[send_video]
  J --> K[day++, index++]
  K --> L[write counter.json]
  L --> M[exit 0]
```

**Entry resolution**

- String entry: `http(s)://…` → URL; otherwise treated as path (relative to `ROOT` if not absolute).
- Dict entry: `url` or `path` (same path rules).

**State**: `counter.json` holds both **`day`** (caption) and **`index`** (position in `manifest.videos`).

---

## 4. Social ingest pipeline: `tools/daily_social_post.py`

This is what **GitHub Actions** runs by default (`python tools/daily_social_post.py`).

```mermaid
flowchart TD
  A[Start] --> B{Creds OK?}
  B -->|no| Z[exit 1]
  B -->|yes| C{sources.json + slots?}
  C -->|no| Z
  C -->|yes| L[Load ingest_state: slot_index, posted_ids]
  L --> P[pick_one_video: rotate slots, yt-dlp flat playlist, shuffle, skip posted]
  P --> Q{Found candidate?}
  Q -->|no, first pass| R[Clear posted_ids, retry from slot 0]
  R --> P
  Q -->|still no| Z2[save_state, exit 1]
  Q -->|yes| D[Download to temp under data/ via yt-dlp]
  D --> E{File exists + size ≤ ~49 MB?}
  E -->|no| Z
  E -->|yes| F[render_caption + send_video upload]
  F --> G[Remove temp dir]
  G --> H[day++ in counter.json]
  H --> I[Append id, advance slot_index mod N]
  I --> J[save ingest_state]
  J --> K[exit 0]
```

**`pick_one_video` (simplified)**

```mermaid
flowchart TD
  SI[slot_index] --> TRY[For each slot in round-robin]
  TRY --> LIST[yt-dlp -J --flat-playlist --playlist-end N]
  LIST --> SHUF[Shuffle entries]
  SHUF --> LOOP[First entry with key not in posted_ids and valid watch URL]
  LOOP -->|found| OUT[Return entry, key, URL, slot index, label]
  LOOP -->|exhausted| NEXT[next slot]
  NEXT --> TRY
```

**Dedupe key**: `entry_key` → `extractor:id` (or best-effort from yt-dlp fields).

**State files**

- **`ingest_state.json`**: `slot_index`, `posted_ids` (trimmed to last 3000 IDs).
- **`counter.json`**: `day` incremented; `index` preserved if present (manifest compatibility).

---

## 5. CI/CD: `.github/workflows/daily-quran.yml`

```mermaid
sequenceDiagram
  participant Cron as schedule / dispatch
  participant GA as GitHub Actions
  participant Py as daily_social_post.py
  participant TG as Telegram
  participant Repo as Git repo

  Cron->>GA: trigger (cron, workflow_dispatch, repository_dispatch)
  GA->>GA: checkout, Python 3.12, pip install
  GA->>Py: TELEGRAM_* secrets
  Py->>TG: sendVideo
  Py->>Repo: counter.json, ingest_state.json updated on disk
  GA->>Repo: git add/commit/push if changed
```

Notes from the workflow comments:

- Scheduled runs are best-effort; external cron + `repository_dispatch` is documented for reliability.
- TikTok/Instagram may need cookies; local **Task Scheduler** is suggested when Actions fails.
- For **manifest-only** (no yt-dlp), the run step can be switched to `python main.py` and the yt-dlp install step removed.

---

## 6. Helper: `tools/fetch_videos.py`

No Telegram; batch download only.

```mermaid
flowchart LR
  UF[urls.txt] --> R[read_urls]
  R --> YT[yt-dlp per URL]
  YT --> ST[data/staging/]
  ST --> CDN[Manual: upload to CDN]
  CDN --> MAN[data/manifest.json]
  MAN --> MP[main.py]
```

---

## 7. Data file relationships

```mermaid
erDiagram
  counter_json ||--o{ main_py : "day, index"
  counter_json ||--o{ daily_social_post : "day"
  manifest_json ||--|| main_py : "videos queue"
  sources_json ||--|| daily_social_post : "slots, yt-dlp options"
  ingest_state_json ||--|| daily_social_post : "slot_index, posted_ids"
  template_txt ||--|| posting_py : "{{day}}"
```

---

## 8. Dependency summary

| Component | Depends on |
|-----------|------------|
| `main.py` | `posting`, `manifest.json`, `counter.json` |
| `daily_social_post.py` | `posting`, `sources.json`, `ingest_state.json`, `counter.json`, yt-dlp |
| `fetch_videos.py` | yt-dlp only |
| `posting.py` | `requests`, optional `python-dotenv` |

---

*Generated for the nsda-quran-bot codebase. Adjust this file if entry points or workflow steps change.*
