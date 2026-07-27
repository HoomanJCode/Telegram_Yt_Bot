# Architecture Guide

> **Understanding the Telegram YouTube Downloader Bot codebase**

---

## Table of Contents

- [High-Level Overview](#high-level-overview)
- [Package Structure](#package-structure)
- [Data Flow](#data-flow)
- [Key Design Decisions](#key-design-decisions)
- [State Management](#state-management)
- [Threading & Concurrency](#threading--concurrency)
- [Error Handling Patterns](#error-handling-patterns)

---

## High-Level Overview

The bot is a **single-process Python application** that combines three concerns:

1. **Telegram Bot API** (polling + responding) via `python-telegram-bot` v20.x
2. **YouTube downloading** via `yt-dlp` (run in a thread pool executor)
3. **HTTP file serving** via `aiohttp` (run in the same event loop)

All three run in one process — no separate file server, no reverse proxy needed (though supported).

```
┌──────────────────────────────────────────────────────┐
│                   bot.py (entry point)                │
│                      │                                │
│              app/__init__.py::main()                  │
│                      │                                │
│         ┌────────────┼────────────┐                   │
│         │            │            │                   │
│    Telegram       File       Downloader               │
│    Polling       Server      (yt-dlp)                 │
│    (PTB)        (aiohttp)    ThreadPool               │
│         │            │            │                   │
│    Handler         HTTP        YouTube                │
│    Modules        Range         API                   │
│                   Req.                                │
└──────────────────────────────────────────────────────┘
```

---

## Package Structure

```
Telegram_Yt_Bot/
├── bot.py                   # Entry point (calls app.main())
├── config.py                # Environment config parser
├── serve_files.py           # Standalone HTTP server (alternative)
│
├── app/
│   ├── __init__.py          # Bootstrap: logging, wiring, main()
│   ├── bot.py               # YouTubeDownloaderBot (central state)
│   ├── downloader.py        # yt-dlp sync functions
│   ├── fileserver.py        # aiohttp async file server
│   ├── models.py            # VideoRecord data class
│   └── utils.py             # Shared utilities, constants, error classification
│
├── app/handlers/
│   ├── __init__.py          # Package marker
│   ├── commands.py          # /start, /help, /status, /recent, /settings, /cancel
│   ├── cookies.py           # /cookies conversation handler
│   ├── formats.py           # Format-choice & delivery keyboards
│   ├── inline.py            # @botname inline queries
│   ├── messages.py          # Plain-text YouTube link processing
│   ├── navigation.py        # Menu system, back-stack, settings UI
│   └── tokens.py            # Deep-link tokens & file delivery
│
├── tests/                   # Unit tests
├── docs/                    # Documentation
├── data/                    # Persistent JSON state
└── downloads/               # Downloaded media files
```

### Module Responsibilities

| Module | Role | Key Classes/Functions |
|--------|------|----------------------|
| `app/__init__.py` | Bootstrap | `main()`, `WAITING_FOR_COOKIES` |
| `app/bot.py` | Central state | `YouTubeDownloaderBot`, `SSLConfigError`, `_build_ssl_context()` |
| `app/downloader.py` | YouTube downloads | `download()`, `fetch_info()`, `download_thumb()`, `_merge_subs_into_mkv()` |
| `app/fileserver.py` | HTTP server | `FileServer`, `_AiohttpNoiseFilter` |
| `app/models.py` | Data model | `VideoRecord` |
| `app/utils.py` | Utilities | `esc()`, `extract_url()`, `classify_yt_error()` |
| `handlers/commands.py` | Slash commands | `start_cmd`, `help_cmd`, `status_cmd` |
| `handlers/formats.py` | Delivery flow | `show_delivery()`, `format_choice_kb()`, `send_telegram()` |
| `handlers/navigation.py` | Menu system | `router()`, `show_recent()`, `settings_menu()` |
| `handlers/tokens.py` | Deep links | `handle_token_start()`, `send_file()` |

---

## Data Flow

### User Downloads a Video

```
User sends YouTube link
        │
        ▼
on_msg() ──→ extract_url() ──→ extract_video_id()
        │
        ├── Group chat? → _group_download() → download_task()
        │
        └── Private chat?
            ├── auto_format set? → download_task() (skip menu)
            └── default → show_format_choice()
                               │
                               ▼
                    User clicks format button
                               │
                               ▼
                    choose_format() → download_task()
                               │
                               ▼
               download() [runs in thread pool]
                 - yt-dlp extract_info + download
                 - Subtitle handling (embed/separate/off)
                 - AAC transcode (TV fix)
                 - Filename sanitization
                               │
                               ▼
                    show_delivery() keyboard
                               │
                               ▼
          User chooses: Telegram upload or Download link
```

### State Persistence Flow

```
YouTubeDownloaderBot.__init__()
        │
        ▼
  load_data() ←─────────────────────┐
  - Reads user_videos.json          │
  - Reads cookie_file_ids.json       │  save_data() → writes JSON files
  - Reads global_file_ids.json       │  (called after every mutation)
  - Reads user_langs.json            │
  - Reads user_settings.json         │
        │                            │
  _cleanup_orphans()                 │
  - Removes temp/fragment files      │
  - Enforces STORAGE_DAYS retention  │
        │                            │
        ▼                            │
  Bot is running ────────────────────┘
  (handlers modify state → save)
```

---

## Key Design Decisions

### 1. Per-Message State (2026-07-15 Fix)

**Problem**: Previously, the bot used per-user slots (`bot.videos[uid]` + index-based callbacks). Starting a second download before clicking the first delivery button would overwrite the first download's keyboard mapping.

**Solution**: All ephemeral state is now keyed by `(chat_id, message_id)`:

- `bot._delivery_screen[(chat_id, msg_id)] = VideoRecord`
- `bot._pending_urls[(chat_id, msg_id)] = (url, video_id, ...)`
- `bot._nav_stack[(chat_id, msg_id)] = [entry, ...]`

Bounded by an `OrderedDict` LRU cap (1024 entries) to prevent memory leaks.

### 2. Native HTTPS Without Reverse Proxy

The bot can terminate TLS itself using `aiohttp`'s `ssl_context` parameter:

- `SSL_CERT_FILE` and `SSL_KEY_FILE` env vars point to PEM files
- `_build_ssl_context()` validates and constructs an `ssl.SSLContext`
- On misconfiguration, bot exits with code 78 (`EX_CONFIG`) — systemd does NOT restart-loop

### 3. AAC Transcode (TV Fix, 2026-06-21)

Many smart TVs lack Opus hardware decoders. The bot optionally transcodes Opus → AAC (192kbps) after download:

- `AAC_TRANSCODE=true` (default) enables the transcode
- `_probe_audio_codec()` skips the transcode if audio is already AAC
- Cost: ~30-90s on a single-core VPS for Opus-source videos

### 4. Warp Proxy Integration (2026-06-26)

When `USE_WARP=true`, yt-dlp routes through Cloudflare Warp (`127.0.0.1:40000`). On transient connection errors, it retries once without the proxy so downloads succeed when Warp is temporarily down.

### 5. yt-dlp Info Cache (2026-06-28)

`fetch_info` results are cached per `(uid, url)` with a 300-second TTL. This prevents repeated YouTube round-trips when the user bounces between `show_format_choice` and the delivery keyboard.

---

## State Management

### Persistent State (JSON files in `data/`)

| File | Content | Format |
|------|---------|--------|
| `user_videos.json` | Per-user download records | `{uid: [VideoRecord {...}, ...]}` |
| `cookie_file_ids.json` | Telegram file_ids for cookie files | `{uid: file_id}` |
| `global_file_ids.json` | Cross-user Telegram file_id cache | `{video_id:media_type: file_id}` |
| `user_langs.json` | Per-user Telegram language codes | `{uid: lang}` |
| `user_settings.json` | Per-user preferences | `{uid: {default_delivery, video_quality, ...}}` |

### Ephemeral State (memory only, bounded LRU)

| Structure | Key Type | Max Size | Purpose |
|-----------|----------|----------|---------|
| `_delivery_screen` | `(chat_id, message_id)` | 1024 | Delivery keyboard → VideoRecord |
| `_pending_urls` | `(chat_id, message_id)` | 1024 | Format picker → URL |
| `_nav_stack` | `(chat_id, message_id)` | 1024 | Menu navigation back-stack |
| `_tokens` | `str` (token) | Unbounded | Deep-link tokens (cleaned on use) |
| `_INFO_CACHE` | `(uid, url)` | 1000 | yt-dlp info cache (300s TTL) |

---

## Threading & Concurrency

```
┌─────────────────────────────────────┐
│         asyncio Event Loop          │
│                                     │
│  Telegram Polling (PTB)             │
│  File Server (aiohttp)              │
│  Handler coroutines                 │
│         │                           │
│  ┌──────┴──────┐                   │
│  │ Thread Pool │ (run_in_executor) │
│  │             │                   │
│  │ download()  │  ← yt-dlp (sync) │
│  │ fetch_info()│  ← yt-dlp (sync) │
│  │ _run_sync() │  ← subprocess    │
│  └─────────────┘                   │
└─────────────────────────────────────┘
```

- **Download semaphore**: `bot._download_semaphore = asyncio.Semaphore(1)` ensures only one download runs at a time (yt-dlp can saturate CPU + disk).
- **Subprocess timeouts**: Every `subprocess.run()` has a hard timeout to prevent stuck processes from blocking the bot.
- **Asyncio.gather**: Used in `/status` to probe warp-svc, TCP, and warp-cli concurrently.

---

## Error Handling Patterns

### yt-dlp Error Classification (`utils.py`)

`classify_yt_error()` maps error text to user-friendly messages:

| Category | Example Message |
|----------|----------------|
| `live_not_started` | ⏳ Live stream hasn't started yet |
| `geo_blocked` | 🌍 Not available in your country |
| `age_restricted` | 🔞 Age-restricted video |
| `members_only` | 🔒 Members-only content |
| `format_unavailable` | 📺 Quality unavailable in H.264 |
| `disk_error` | 💾 Bot storage full |
| `unknown` | ❌ Failed to fetch video info |

### SSL Configuration Errors

`SSLConfigError` is caught in `main()` before the polling loop starts. The bot exits with code 78 (`EX_CONFIG`) so systemd's `RestartPreventExitStatus=78` prevents restart-looping.

### Callback Expiration

All `await q.answer()` calls are wrapped in `try/except BadRequest` because inline keyboard callbacks can expire (Telegram deletes them after ~30 minutes or when the message is too old).

---

**Next:** [Deployment Guide](./DEPLOYMENT.md) → [Configuration Reference](./CONFIGURATION.md) → [Development Guide](./DEVELOPMENT.md)
