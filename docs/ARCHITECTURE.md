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

```mermaid
flowchart TB
    subgraph "Single Process (asyncio Event Loop)"
        direction TB
        EP["bot.py<br/><i>Entry Point</i>"]
        BOOT["app/__init__.py<br/><i>main()</i>"]
        
        subgraph "Three Concerns"
            direction LR
            TG["Telegram Polling<br/><i>python-telegram-bot</i>"]
            FS["File Server<br/><i>aiohttp</i>"]
            DL["Downloader<br/><i>yt-dlp</i>"]
        end
        
        subgraph "Handler Modules"
            CMD["commands.py<br/>/start, /help, etc."]
            MSG["messages.py<br/>Link processing"]
            FMT["formats.py<br/>Format choice & delivery"]
            NAV["navigation.py<br/>Menu & settings"]
            COOK["cookies.py<br/>Cookie upload"]
            INL["inline.py<br/>@botname queries"]
            TOK["tokens.py<br/>Deep links & file send"]
        end
    end

    subgraph "External Services"
        YT["YouTube API"]
        TGAPI["Telegram Bot API"]
        CLIENT["User's Browser"]
    end

    EP --> BOOT
    BOOT --> TG
    BOOT --> FS
    BOOT --> DL

    TG <--> TGAPI
    TG --> CMD & MSG & FMT & NAV & COOK & INL & TOK
    
    DL --> YT
    FS --> CLIENT
    
    MSG & FMT & NAV --> DL
    FMT & TOK --> TGAPI
    FS -.->|"Serves<br/>files"| CLIENT

    style EP fill:#1a1a2e,color:#fff,stroke:#16213e
    style BOOT fill:#1a1a2e,color:#fff,stroke:#16213e
    style TG fill:#0f3460,color:#fff,stroke:#112d4e
    style FS fill:#0f3460,color:#fff,stroke:#112d4e
    style DL fill:#0f3460,color:#fff,stroke:#112d4e
    style CMD fill:#533483,color:#fff
    style MSG fill:#533483,color:#fff
    style FMT fill:#533483,color:#fff
    style NAV fill:#533483,color:#fff
    style COOK fill:#533483,color:#fff
    style INL fill:#533483,color:#fff
    style TOK fill:#533483,color:#fff
    style YT fill:#c62828,color:#fff
    style TGAPI fill:#1565c0,color:#fff
    style CLIENT fill:#2e7d32,color:#fff
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

```mermaid
sequenceDiagram
    participant User as Telegram User
    participant Bot as Bot Process
    participant YT as YouTube
    participant TG as Telegram API

    User->>Bot: Sends YouTube link
    activate Bot

    Bot->>Bot: on_msg()
    Note over Bot: extract_url() → extract_video_id()

    alt Group Chat
        Bot->>Bot: _group_download()
        Bot->>Bot: download_task('video')
    else Private Chat
        alt Auto-format set (video/audio/thumb)
            Bot->>Bot: download_task(mt)
        else Default (ask)
            Bot->>User: show_format_choice() keyboard
            User->>Bot: Clicks format button
            Bot->>Bot: choose_format() → download_task(mt)
        end
    end

    Bot->>+YT: yt-dlp extract_info + download
    Note over Bot,YT: Runs in ThreadPoolExecutor
    YT-->>-Bot: File on disk

    Bot->>Bot: Post-processing
    Note over Bot: Subtitle embed/separate<br/>AAC transcode (TV fix)<br/>Filename sanitize

    Bot->>User: show_delivery() keyboard

    User->>Bot: Choose delivery method
    
    alt Send via Telegram
        Bot->>TG: send_file() upload
        TG-->>User: File in chat
    else Get Download Link
        Bot->>User: HTTP/HTTPS download URL
        User->>Bot: GET /filename
        Bot-->>User: File stream (aiohttp)
    end

    deactivate Bot
```

### State Persistence Flow

```mermaid
flowchart TD
    START(["Bot Starts"]) --> INIT[YouTubeDownloaderBot.__init__]
    
    INIT --> LOAD[load_data]
    
    subgraph "Startup Loading"
        LOAD --> UV[user_videos.json<br/><i>Per-user VideoRecord list</i>]
        LOAD --> CF[cookie_file_ids.json<br/><i>Telegram file_ids for cookies</i>]
        LOAD --> GF[global_file_ids.json<br/><i>Cross-user file_id cache</i>]
        LOAD --> UL[user_langs.json<br/><i>Language preferences</i>]
        LOAD --> US[user_settings.json<br/><i>Quality, delivery, etc.</i>]
    end
    
    INIT --> CLEAN[_cleanup_orphans]
    
    subgraph "Startup Cleanup"
        CLEAN --> SWEEP1[Remove .ytdl / .part fragments]
        CLEAN --> SWEEP2[Remove files > STORAGE_DAYS old<br/>that aren't pinned in videos[] ]
        CLEAN --> LOG[Log bytes freed]
    end
    
    CLEAN --> RUNNING[Bot is Running]
    
    RUNNING --> HANDLER[Handler mutates state]
    HANDLER --> SAVE[bot.save]
    SAVE --> WRITE[Write all 5 JSON files]
    WRITE --> RUNNING
    
    RUNNING -.-> CRASH([Process killed])
    CRASH -.-> START
    
    style START fill:#2e7d32,color:#fff
    style RUNNING fill:#1565c0,color:#fff
    style CRASH fill:#c62828,color:#fff
    style INIT fill:#1a1a2e,color:#fff
    style LOAD fill:#533483,color:#fff
    style CLEAN fill:#533483,color:#fff
    style SAVE fill:#e65100,color:#fff
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

```mermaid
flowchart LR
    subgraph "Before (Per-User Slots)"
        OLD_KB["Delivery Keyboard<br/>callback = 'tg_0'"]
        OLD_DL["New download inserts<br/>at videos[uid][0]"]
        OLD_ERR["Keyboard now points to<br/>WRONG record! ❌"]
        OLD_KB -.->|"index 0"| OLD_DL
        OLD_DL --> OLD_ERR
    end

    subgraph "After (Per-Message Keys)"
        NEW_KB["Delivery Keyboard<br/>attached to msg 42"]
        NEW_STATE["bot._delivery_screen<br/>[(chat_id, 42)] = record"]
        NEW_DL["New download uses<br/>msg 55 keys"]
        NEW_OK["Both keyboards resolve<br/>to correct records ✅"]
        NEW_KB -->|"(chat_id, 42)"| NEW_STATE
        NEW_DL -->|"(chat_id, 55)"| NEW_STATE
        NEW_STATE --> NEW_OK
    end

    style OLD_ERR fill:#c62828,color:#fff
    style NEW_OK fill:#2e7d32,color:#fff
```

### 2. Native HTTPS Without Reverse Proxy

The bot can terminate TLS itself using `aiohttp`'s `ssl_context` parameter:

- `SSL_CERT_FILE` and `SSL_KEY_FILE` env vars point to PEM files
- `_build_ssl_context()` validates and constructs an `ssl.SSLContext`
- On misconfiguration, bot exits with code 78 (`EX_CONFIG`) — systemd does NOT restart-loop

```mermaid
flowchart TD
    ENV[".env variables"] --> CHECK{Both set?}
    CHECK -->|"Both empty"| NONE[Return None<br/>→ Plain HTTP]
    CHECK -->|"Only one set"| RAISE[Raise SSLConfigError<br/>Exit code 78]
    CHECK -->|"Both set"| FILES{Both files<br/>exist?}
    FILES -->|"No"| RAISE
    FILES -->|"Yes"| BUILD[Build ssl.SSLContext]
    BUILD --> WARN{Key mode<br/>0o077?}
    WARN -->|"Yes"| LOGWARN[Log security warning]
    WARN -->|"No"| HTTPS["Pass to FileServer<br/>→ HTTPS on port"]
    LOGWARN --> HTTPS
    HTTPS --> CROSS{base_url<br/>starts with http://?}
    CROSS -->|"Yes"| LOGHTTP[Log warning: scheme mismatch]
    CROSS -->|"No"| OK[✅ HTTPS active]

    style NONE fill:#1565c0,color:#fff
    style RAISE fill:#c62828,color:#fff
    style HTPS fill:#2e7d32,color:#fff
    style OK fill:#2e7d32,color:#fff
    style LOGHTTP fill:#e65100,color:#fff
```

### 3. AAC Transcode (TV Fix, 2026-06-21)

Many smart TVs lack Opus hardware decoders. The bot optionally transcodes Opus → AAC (192kbps) after download:

- `AAC_TRANSCODE=true` (default) enables the transcode
- `_probe_audio_codec()` skips the transcode if audio is already AAC
- Cost: ~30-90s on a single-core VPS for Opus-source videos

```mermaid
flowchart TD
    DL[yt-dlp merge complete] --> GATE{AAC_TRANSCODE<br/>&& has_ffmpeg?}
    GATE -->|"No"| DONE[✅ Deliver file]
    GATE -->|"Yes"| PROBE[ffprobe a:0 codec]
    PROBE --> AAC{Already AAC?}
    AAC -->|"Yes"| DONE
    AAC -->|"No (Opus etc.)"| TRANS[ffmpeg re-encode<br/>Opus → AAC 192kbps]
    TRANS --> FAIL{Success?}
    FAIL -->|"Yes"| DONE
    FAIL -->|"No"| DONE
    GATE -.->|"Smart TV<br/>user feedback"| TV[📺 TV plays audio ✅]

    style DL fill:#1a1a2e,color:#fff
    style DONE fill:#2e7d32,color:#fff
    style TRANS fill:#e65100,color:#fff
    style TV fill:#1565c0,color:#fff
```

### 4. Warp Proxy Integration (2026-06-26)

When `USE_WARP=true`, yt-dlp routes through Cloudflare Warp (`127.0.0.1:40000`). On transient connection errors, it retries once without the proxy so downloads succeed when Warp is temporarily down.

```mermaid
flowchart TD
    START[yt-dlp call] --> WARP{USE_WARP?}
    WARP -->|"No"| DIRECT[Call directly<br/>no proxy]
    DIRECT --> YT[YouTube]
    
    WARP -->|"Yes"| PROXY["Call via<br/>Warp 127.0.0.1:40000"]
    PROXY --> ERR{Transient<br/>error?}
    ERR -->|"No"| YT
    ERR -->|"Yes"| RETRY[Log retry<br/>Call without proxy]
    RETRY --> YT
    
    YT --> RESULT(("Return result<br/>or raise"))
    
    style START fill:#1a1a2e,color:#fff
    style PROXY fill:#e65100,color:#fff
    style RETRY fill:#1565c0,color:#fff
    style RESULT fill:#2e7d32,color:#fff
```

### 5. yt-dlp Info Cache (2026-06-28)

`fetch_info` results are cached per `(uid, url)` with a 300-second TTL. This prevents repeated YouTube round-trips when the user bounces between `show_format_choice` and the delivery keyboard.

```mermaid
flowchart TD
    CALL[fetch_info uid, url] --> CACHE{Hit in<br/>_INFO_CACHE?}
    CACHE -->|"Yes (TTL ≤ 300s)"| HIT[Return cached info<br/>✅ DEBUG log]
    CACHE -->|"Miss / Stale"| FETCH[yt-dlp extract_info<br/>download=False]
    FETCH --> STORE[_info_cache_set uid, url]
    STORE --> RESULT[Return fresh info]
    
    HIT --> DONE(["Done"])
    RESULT --> DONE
    
    style CALL fill:#1a1a2e,color:#fff
    style HIT fill:#2e7d32,color:#fff
    style FETCH fill:#e65100,color:#fff
    style DONE fill:#1565c0,color:#fff
```

---

## State Management

### Persistent State (JSON files in `data/`)

```mermaid
flowchart LR
    subgraph "data/ Directory"
        UV[user_videos.json]
        CF[cookie_file_ids.json]
        GF[global_file_ids.json]
        UL[user_langs.json]
        US[user_settings.json]
    end
    
    subgraph "YouTubeDownloaderBot"
        V["bot.videos<br/>Dict[int, List[VideoRecord]]"]
        C["bot._cookie_file_ids<br/>Dict[int, str]"]
        G["bot._global_file_ids<br/>Dict[str, str]"]
        L["bot._user_langs<br/>Dict[int, str]"]
        S["bot._user_settings<br/>Dict[int, dict]"]
    end
    
    UV <-->|load/save| V
    CF <-->|load/save| C
    GF <-->|load/save| G
    UL <-->|load/save| L
    US <-->|load/save| S

    style UV fill:#533483,color:#fff
    style CF fill:#533483,color:#fff
    style GF fill:#533483,color:#fff
    style UL fill:#533483,color:#fff
    style US fill:#533483,color:#fff
    style V fill:#1a1a2e,color:#fff
    style C fill:#1a1a2e,color:#fff
    style G fill:#1a1a2e,color:#fff
    style L fill:#1a1a2e,color:#fff
    style S fill:#1a1a2e,color:#fff
```

### Ephemeral State (memory only, bounded LRU)

| Structure | Key Type | Max Size | Purpose |
|-----------|----------|----------|---------|
| `_delivery_screen` | `(chat_id, message_id)` | 1024 | Delivery keyboard → VideoRecord |
| `_pending_urls` | `(chat_id, message_id)` | 1024 | Format picker → URL |
| `_nav_stack` | `(chat_id, message_id)` | 1024 | Menu navigation back-stack |
| `_tokens` | `str` (token) | Unbounded | Deep-link tokens (cleaned on use) |
| `_INFO_CACHE` | `(uid, url)` | 1000 | yt-dlp info cache (300s TTL) |

```mermaid
flowchart TD
    subgraph "OrderedDict LRU (max 1024)"
        DIRECTION TB
        E1["(chat: 100, msg: 42) → Record{...}"]
        E2["(chat: 100, msg: 55) → Record{...}"]
        E3["(chat: 200, msg: 12) → Record{...}"]
        DOT["... (up to 1024 entries)"]
    end
    
    CLICK[User clicks button] --> LOOKUP["bot._delivery_screen.get((cid, mid))"]
    LOOKUP --> FOUND{FOUND?}
    FOUND -->|"Yes + file on disk"| MOVE[LRU move_to_end]
    MOVE --> SERVE[Deliver file ✅]
    FOUND -->|"Yes + gone"| POP[Pop entry]
    POP --> UNAVAIL[Show 'no longer available' ❌]
    FOUND -->|"No"| UNAVAIL
    
    NEW[New delivery screen] --> PUT["_delivery_screen[key] = record<br/>move_to_end(key)"]
    PUT --> FULL{len > 1024?}
    FULL -->|"Yes"| EVICT["popitem(last=False)<br/>evict oldest"]
    FULL -->|"No"| DONE2(["Done"])

    style E1 fill:#1a1a2e,color:#fff,stroke:#16213e
    style E2 fill:#1a1a2e,color:#fff,stroke:#16213e
    style E3 fill:#1a1a2e,color:#fff,stroke:#16213e
    style DOT fill:#1a1a2e,color:#aaa
    style SERVE fill:#2e7d32,color:#fff
    style UNAVAIL fill:#c62828,color:#fff
    style EVICT fill:#e65100,color:#fff
```

---

## Threading & Concurrency

```mermaid
flowchart TB
    subgraph "Main Thread — asyncio Event Loop"
        direction TB
        POLL[Telegram Polling<br/><i>python-telegram-bot</i>]
        AHTTP[aiohttp File Server<br/><i>Range requests</i>]
        HANDLERS[Handler Coroutines<br/><i>commands, menus, etc.</i>]
        SEM[Download Semaphore<br/><i>asyncio.Semaphore(1)</i>]
        
        POLL & AHTTP & HANDLERS --- EVENT[Event Loop]
    end

    subgraph "Thread Pool — run_in_executor"
        direction TB
        DL[download()<br/><i>yt-dlp sync</i>]
        INF[fetch_info()<br/><i>yt-dlp sync</i>]
        SUB[_run_sync()<br/><i>subprocess.run</i>]
    end

    GATHER[asyncio.gather<br/><i>for /status probes</i>]

    HANDLERS --> |"Acquire"| SEM
    SEM --> |"Release"| DL
    HANDLERS --> INF
    HANDLERS --> GATHER
    
    DL --> SUB
    
    GATHER --> P1[warp-cli status]
    GATHER --> P2[socket probe port 40000]
    GATHER --> P3[systemctl is-active]

    style POLL fill:#0f3460,color:#fff
    style AHTTP fill:#0f3460,color:#fff
    style HANDLERS fill:#533483,color:#fff
    style SEM fill:#e65100,color:#fff
    style DL fill:#1a1a2e,color:#fff
    style INF fill:#1a1a2e,color:#fff
    style SUB fill:#1a1a2e,color:#fff
    style GATHER fill:#1565c0,color:#fff
    style P1 fill:#2e7d32,color:#fff
    style P2 fill:#2e7d32,color:#fff
    style P3 fill:#2e7d32,color:#fff
```

### Concurrency Rules

| Rule | Mechanism | Why |
|------|-----------|-----|
| **One download at a time** | `asyncio.Semaphore(1)` | yt-dlp saturates CPU + disk I/O |
| **No blocking the event loop** | `run_in_executor` for yt-dlp & subprocess | Telegram polling and file server must stay responsive |
| **Stuck process protection** | 30-300s timeouts on all `subprocess.run()` | A frozen ffmpeg/yt-dlp can't block the bot forever |
| **Parallel probes** | `asyncio.gather` for `/status` | Bound total latency to the slowest probe (≤3s) |
| **Thread-safe state** | All state mutated only from async handlers | yt-dlp is sync but doesn't write to `bot.*` directly |

---

## Error Handling Patterns

### yt-dlp Error Classification (`utils.py`)

`classify_yt_error()` maps error text to user-friendly messages:

```mermaid
flowchart TD
    ERR[yt-dlp Exception] --> CLASSIFY[classify_yt_error]
    CLASSIFY --> C1{live_not_started?}
    CLASSIFY --> C2{geo_blocked?}
    CLASSIFY --> C3{age_restricted?}
    CLASSIFY --> C4{format_unavailable?}
    CLASSIFY --> C5{disk_error?}
    CLASSIFY --> C6{members_only?}
    CLASSIFY --> C7{subtitle_throttled?}
    CLASSIFY --> C8{playability?}
    
    C1 -->|"⏳ Not started yet"| MSG
    C2 -->|"🌍 Geo blocked"| MSG
    C3 -->|"🔞 Age restricted"| MSG
    C4 -->|"📺 Try lower quality"| MSG
    C5 -->|"💾 Storage full"| MSG
    C6 -->|"🔒 Members only"| MSG
    C7 -->|"✅ Video OK, no subs"| MSG
    C8 -->|"🚫 Refused"| MSG
    
    C1 & C2 & C3 & C4 & C5 & C6 & C7 & C8 -.->|"Fallback"| UNKNOWN
    UNKNOWN -->|"❌ Try again"| MSG
    
    MSG[Show user-friendly message<br/>+ inline menu]

    style ERR fill:#c62828,color:#fff
    style CLASSIFY fill:#1a1a2e,color:#fff
    style MSG fill:#2e7d32,color:#fff
    style UNKNOWN fill:#e65100,color:#fff
```

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
