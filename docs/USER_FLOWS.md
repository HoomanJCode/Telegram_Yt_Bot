# User Flows & Menus Reference

> **Complete map of all user interactions, menus, settings, and navigation paths**

---

## Table of Contents

- [Entry Points](#entry-points)
- [The Main Menu](#the-main-menu)
- [Complete Settings Tree](#complete-settings-tree)
- [Download Flow](#download-flow)
- [Delivery Options](#delivery-options)
- [Inline Mode](#inline-mode)
- [Cookie Upload](#cookie-upload)
- [Recent Downloads](#recent-downloads)
- [Navigation & Back Button](#navigation--back-button)
- [Group Chat Flow](#group-chat-flow)
- [Callback Data Reference](#callback-data-reference)
- [Settings Reference Tables](#settings-reference-tables)

---

## Entry Points

There are **5 ways** a user can begin interacting with the bot:

| Entry Point | Trigger | What Happens |
|-------------|---------|--------------|
| **/start** | User sends `/start` in private chat | Welcome message + main menu. If called with `dl_<token>`, handles a deep link from inline mode. |
| **Paste URL** | User sends any YouTube link | Extract URL → auto-download or show format picker (see [Download Flow](#download-flow)) |
| **/help** | User sends `/help` | Quick command reference + main menu |
| **/settings** | User sends `/settings` | Text summary of all current settings + main menu |
| **@botname** | User types `@botname <url>` in ANY chat | Inline query results (see [Inline Mode](#inline-mode)) |

```mermaid
flowchart TB
    START(["User opens bot"]) --> CHOICE{Action?}

    CHOICE -->|"/start"| START_CMD["start_cmd()"]
    CHOICE -->|"Paste YouTube link"| LINK_HANDLER["on_msg()"]
    CHOICE -->|"/help"| HELP["help_cmd()<br/>Quick reference + menu"]
    CHOICE -->|"/settings"| SETTINGS["settings_cmd()<br/>Summary + menu"]
    CHOICE -->|"/recent"| RECENT_CMD["recent_cmd()<br/>Paginated list"]
    CHOICE -->|"/status"| STATUS["status_cmd()<br/>Warp proxy health"]
    CHOICE -->|"/cookies"| COOKIES["ask_cookies()<br/>Conversation handler"]
    CHOICE -->|"/cancel"| CANCEL["cancel_cmd()<br/>Reset + menu"]

    START_CMD --> DEEP{Has dl_<token>?}
    DEEP -->|"Yes"| TOKEN["handle_token_start()<br/>Serve or download file"]
    DEEP -->|"No"| WELCOME["Welcome text<br/>+ main menu"]

    LINK_HANDLER --> PRIVATE{Private chat?}
    PRIVATE -->|"Yes"| DOWNLOAD_FLOW["See Download Flow"]
    PRIVATE -->|"No (Group)"| GROUP_FLOW["See Group Chat Flow"]

    style START fill:#2e7d32,color:#fff
    style START_CMD fill:#1565c0,color:#fff
    style LINK_HANDLER fill:#1565c0,color:#fff
    style WELCOME fill:#1a1a2e,color:#fff
    style TOKEN fill:#e65100,color:#fff
```

---

## The Main Menu

The **main inline keyboard** is shown after `/start`, `/help`, `/settings`, or when pressing the **🔙 Back** button until reaching the root. It is the central hub for all navigation.

```mermaid
flowchart TB
    subgraph MENU["Main Menu — menu(bot, uid)"]
        direction TB
        R1["Row 1: 📹 Recent Downloads<br/><i>callback: r</i>"]
        R2["Row 2: 🍪 Upload Cookies<br/><i>callback: c</i>"]
        R3["Row 3: 🎬 Video: {Best/2160P/1080P/...}<br/><i>vq</i> | 🎵 Audio: {Best/320k/...}<br/><i>aq</i> | 📝 Subs: {MKV/SRT/Off}<br/><i>sm</i>"]
        R4["Row 4: 🌐 Language: {EN/FA/...}<br/><i>lang</i> | 📤 Delivery: {Ask/TG/Link}<br/><i>delivery</i> | 🍪 {✅/❌}<br/><i>cs</i>"]
        R5["Row 5: ⚡ Auto: {Ask/Video/Audio/Thumb}<br/><i>af</i> | 🎞️ Container: {MKV/MP4}<br/><i>cn</i>"]
        R6["Row 6: 📦 {count} files<br/><i>vc</i>"]
    end

    R1 --- R2 --- R3 --- R4 --- R5 --- R6
```

> **Note about 🍪 Upload Cookies**: The `c` callback is **not** dispatched by `router()`. It is intercepted by PTB's `ConversationHandler` registration in `app/__init__.py` before reaching the router. All other callbacks go through the router.

### Button Reference

| Button | Callback | Action |
|--------|----------|--------|
| 📹 **Recent Downloads** | `r` | Opens paginated recent downloads list |
| 🍪 **Upload Cookies** | `c` | Starts `/cookies` conversation handler (intercepted by ConversationHandler, not router) |
| 🎬 **Video: {q}** | `vq` | Opens video quality picker |
| 🎵 **Audio: {q}** | `aq` | Opens audio quality picker |
| 📝 **Subs: {mode}** | `sm` | Opens subtitle mode picker |
| 🌐 **Language: {lang}** | `lang` | Opens subtitle language picker |
| 📤 **Delivery: {mode}** | `delivery` | Opens default delivery method picker |
| 🍪 **{✅/❌}** | `cs` | Shows cookie status (✅ active / ❌ upload needed) |
| ⚡ **Auto: {format}** | `af` | Opens auto-format default picker |
| 🎞️ **Container: {c}** | `cn` | Opens video container picker (Auto/MKV vs MP4) |
| 📦 **{count} files** | `vc` | Shows file count message |

### Dynamic Labels

Several button labels change based on current settings:

- **Video quality**: Shows `Best`, `4K`, `1080p`, `720p`, `480p`, `360p`, or `~` (worst)
- **Audio quality**: Shows `Best`, `320k`, `256k`, `192k`, `128k`, `96k`, or `~` (worst)
- **Subs**: Shows `MKV` (embed), `SRT` (separate), or `Off`. **When MP4 + embed is set**, the effective mode cascades to **SRT** and the label reflects this!
- **Container**: Shows `MKV` (auto) or `MP4` (forced)
- **Cookie**: ✅ green check if cookies are loaded in RAM; ❌ red X otherwise
- **Language**: Shows the 2-letter code in uppercase (EN, FA, AR, RU, ES)

### Router Dispatch

All inline button callbacks flow through a single **`router()`** function in `navigation.py`:

```mermaid
flowchart TD
    CB["User clicks inline button<br/><i>callback_query.data</i>"] --> ROUTER["router(bot, u, c)<br/><br/>Note: 'c' (cookies) is intercepted<br/>by PTB ConversationHandler<br/>BEFORE the router"]

    ROUTER --> B{"data == 'b'?"}
    ROUTER --> R{"data == 'r'?"}
    ROUTER --> NAV{"data in<br/>nav/settings?"}
    ROUTER --> SET{"data starts with<br/>'set...'?"}
    ROUTER --> FMT{"data starts with<br/>'fmt_'?"}
    ROUTER --> DEL{"data in<br/>{tg_send, lk_send,<br/>backfmt, morefmt_*}?"}
    ROUTER --> RECENT{"data starts with<br/>'sel_', 'd_', 'p_'?"}
    ROUTER --> OTHER{"Other?"}

    B -->|"Yes"| BACK["handle_back()<br/>Pop per-message nav stack"]
    R -->|"Yes"| REC["show_recent()<br/>Push NAV_MAIN → show list"]
    NAV -->|"Yes<br/>lang, delivery, vq, aq,<br/>sm, af, cn, cs, vc"| PICKERS["Open setting picker<br/>or show status"]
    SET -->|"Yes<br/>setlang_*, setdelivery_*,<br/>setvq_*, setaq_*, setsm_*,<br/>setaf_*, setcn_*"| PERSIST["Persist setting<br/>+ show menu"]
    FMT -->|"Yes<br/>fmt_video_mkv, fmt_video_mp4,<br/>fmt_audio, fmt_thumb"| CHOOSE["choose_format()<br/>Dedup check → download"]
    DEL -->|"Yes"| DELIVERY["Delivery handlers<br/>send_telegram(), send_link(),<br/>back_to_formats(),<br/>also_get_other_format()"]
    RECENT -->|"Yes"| REC_ACTIONS["_select(), _delete(),<br/>show_recent(page)<br/>_clear_all()"]
    OTHER --> IGNORE["No-op"]

    style CB fill:#2e7d32,color:#fff
    style ROUTER fill:#1a1a2e,color:#fff
    style BACK fill:#1565c0,color:#fff
    style REC fill:#1565c0,color:#fff
    style PICKERS fill:#533483,color:#fff
    style PERSIST fill:#2e7d32,color:#fff
    style CHOOSE fill:#e65100,color:#fff
    style DELIVERY fill:#e65100,color:#fff
```

---

## Complete Settings Tree

Every setting follows a **two-step pattern**: a *change* function opens a picker keyboard, and a *set* function persists the choice and returns to the main menu.

```mermaid
flowchart TD
    MAIN["Main Menu"] --> VQ["🎬 Video Quality<br/><i>_change_video_quality()</i>"]
    MAIN --> AQ["🎵 Audio Quality<br/><i>_change_audio_quality()</i>"]
    MAIN --> SM["📝 Subtitle Mode<br/><i>_change_subtitle_mode()</i>"]
    MAIN --> LANG["🌐 Language<br/><i>_change_language()</i>"]
    MAIN --> DEL["📤 Delivery<br/><i>_change_delivery()</i>"]
    MAIN --> AF["⚡ Auto Format<br/><i>_change_auto_format()</i>"]
    MAIN --> CN["🎞️ Container<br/><i>_change_video_container()</i>"]

    VQ --> VQ_PICK["Picker: 🏆 Best, 📺 4K,<br/>📺 1440p, 📺 1080p,<br/>📺 720p, 📺 480p, 📺 360p,<br/>⬇️ Worst"]
    VQ_PICK -->|"setvq_{opt}"| VQ_SET["_set_video_quality()<br/>✅ Show confirmation + menu"]

    AQ --> AQ_PICK["Picker: 🏆 Best, 🎵 320k,<br/>🎵 256k, 🎵 192k,<br/>🎵 128k, 🎵 96k,<br/>⬇️ Worst"]
    AQ_PICK -->|"setaq_{opt}"| AQ_SET["_set_audio_quality()<br/>✅ Show confirmation + menu"]

    SM --> SM_PICK["Picker: 🔗 Embed (MKV),<br/>📎 Separate file (.srt),<br/>🚫 Off"]
    SM_PICK -->|"setsm_{opt}"| SM_SET["_set_subtitle_mode()<br/>✅ Show confirmation + menu"]

    LANG --> LANG_PICK["Picker: ✅/⬜ English,<br/>✅/⬜ فارسی, ✅/⬜ العربية,<br/>✅/⬜ Русский, ✅/⬜ Español"]
    LANG_PICK -->|"setlang_{code}"| LANG_SET["_set_language()<br/>✅ Show confirmation + menu"]

    DEL --> DEL_PICK["Picker: ❓ Ask every time,<br/>📤 Send via Telegram,<br/>📋 Get Download Link"]
    DEL_PICK -->|"setdelivery_{method}"| DEL_SET["_set_delivery()<br/>✅ Show confirmation + menu"]

    AF --> AF_PICK["Picker: ❓ Ask (show keyboard),<br/>🎬 Auto Video, 🎵 Auto Audio,<br/>🖼️ Auto Thumb"]
    AF_PICK -->|"setaf_{fmt}"| AF_SET["_set_auto_format()<br/>✅ Show confirmation + menu"]

    CN --> CN_PICK["Picker: 🔀 Auto (best codec),<br/>🎬 MP4 (universal compat)"]
    CN_PICK -->|"setcn_{opt}"| CN_SET["_set_video_container()<br/>✅ Show confirmation + menu<br/>⚠️ Warn if MP4+embed cascade"]

    VQ_SET & AQ_SET & SM_SET & LANG_SET & DEL_SET & AF_SET & CN_SET --> MAIN

    style MAIN fill:#1a1a2e,color:#fff,stroke:#16213e
    style VQ_PICK fill:#1565c0,color:#fff
    style AQ_PICK fill:#1565c0,color:#fff
    style SM_PICK fill:#1565c0,color:#fff
    style LANG_PICK fill:#1565c0,color:#fff
    style DEL_PICK fill:#1565c0,color:#fff
    style AF_PICK fill:#1565c0,color:#fff
    style CN_PICK fill:#1565c0,color:#fff
    style VQ_SET fill:#2e7d32,color:#fff
    style AQ_SET fill:#2e7d32,color:#fff
    style SM_SET fill:#2e7d32,color:#fff
    style LANG_SET fill:#2e7d32,color:#fff
    style DEL_SET fill:#2e7d32,color:#fff
    style AF_SET fill:#2e7d32,color:#fff
    style CN_SET fill:#2e7d32,color:#fff
```

---

## Download Flow

### Overview: From URL to File

```mermaid
sequenceDiagram
    participant User as 👤 User
    participant Bot as 🤖 Bot
    participant YT as 📺 YouTube
    participant TG as ☁️ Telegram

    User->>Bot: Send YouTube link
    activate Bot
    Bot->>Bot: extract_url() → extract_video_id()

    alt Private Chat
        alt Auto-format set (video/audio/thumb)
            Bot->>Bot: Skip format picker
            Bot->>User: "⏳ Downloading {type}..."
        else Auto-format = 'ask' (default)
            Bot->>YT: fetch_info() — metadata only
            YT-->>Bot: title, duration, thumbnail, comments
            Bot->>User: Format picker keyboard<br/>(video MKV, video MP4, audio, thumbnails)
            User->>Bot: Click format button
            Bot->>Bot: choose_format() — dedup check
        end
    else Group Chat
        Bot->>Bot: Admin check (_check_group)
        Bot->>Bot: Force video-only download
    end

    Bot->>+YT: yt-dlp download
    Note over Bot,YT: ThreadPoolExecutor<br/>Semaphore(1) — one at a time
    YT-->>-Bot: File on disk

    Bot->>Bot: Post-processing
    Note over Bot: Subtitle embed/separate<br/>AAC transcode (TV fix)<br/>Filename sanitize

    Bot->>User: Delivery screen

    alt Default: Telegram
        Bot->>TG: Upload file
        TG-->>User: File in chat ✅
    else Default: Link
        Bot->>User: HTTP/HTTPS download URL
        User->>Bot: GET /filename
        Bot-->>User: File stream (aiohttp)
    else Ask (default)
        User->>Bot: Click delivery button
        Bot->>TG: Upload or
        Bot->>User: Download link
    end

    deactivate Bot
```

### Format Picker Flow (Private Chat)

```mermaid
flowchart TD
    URL["User sends<br/>YouTube link"] --> EXTRACT["extract_url()<br/>extract_video_id()"]
    EXTRACT --> INVALID{"Valid<br/>video_id?"}
    INVALID -->|"No"| ERR["❌ Invalid URL"]

    INVALID -->|"Yes"| COOKIES{"Cookies<br/>loaded?"}
    COOKIES -->|"No"| COOKIE_ERR["❌ Upload cookies first!<br/>/cookies"]

    COOKIES -->|"Yes"| AUTO{"get_auto_format()<br/>!= 'ask'?"}
    AUTO -->|"Yes (video/audio/thumb)"| DEDUP{"find_existing()<br/>Already downloaded?"}
    DEDUP -->|"Yes"| DELIVERY["show_delivery()<br/>Reuse cached record"]
    DEDUP -->|"No"| AUTO_DL["download_task(mt)<br/>Skip picker → download"]

    AUTO -->|"No (ask)"| FETCH["show_format_choice()"]
    FETCH --> INFO["🔍 Fetching info..."]
    INFO --> YT_DL["fetch_info()<br/>yt-dlp extract_info<br/>download=False"]
    YT_DL --> RENDER["Render format picker<br/>+ thumbnail, title,<br/>duration, comments"]
    RENDER --> PICKER["Format Picker Keyboard"]

    PICKER --> V_MKV["🎬 Video (MKV)<br/><i>fmt_video_mkv</i>"]
    PICKER --> V_MP4["🎬 Video (MP4)<br/><i>fmt_video_mp4</i>"]
    PICKER --> AUD["🎵 Audio<br/><i>fmt_audio</i>"]
    PICKER --> THUMB["🖼️ Thumbnails<br/><i>fmt_thumb</i>"]
    PICKER --> BACK_FMT["🔙 Back<br/><i>b</i>"]

    V_MKV & V_MP4 & AUD & THUMB --> CHOOSE["choose_format()"]

    CHOOSE --> DEDUP2{"Container-aware<br/>dedup check"}
    DEDUP2 -->|"Already exists"| DELIVERY
    DEDUP2 -->|"New download"| DL["download_task(mt,<br/>container_override)"]

    AUTO_DL & DL --> PROGRESS["⏳ Downloading {type}..."]
    PROGRESS --> DONE["✅ Delivery screen"]

    style URL fill:#2e7d32,color:#fff
    style PICKER fill:#1a1a2e,color:#fff
    style DONE fill:#2e7d32,color:#fff
    style ERR fill:#c62828,color:#fff
    style COOKIE_ERR fill:#c62828,color:#fff
```

### Format Picker Keyboard (Rendered)

The keyboard shown varies based on what's already downloaded:

| Condition | Button Label |
|-----------|-------------|
| Not downloaded | `🎬 Video (MKV) — best quality + auto-subs` |
| MKV already cached | `✅ 🎬 Video (MKV) - Downloaded` |
| Not downloaded | `🎬 Video (MP4) — universal compat, subs separate` |
| MP4 already cached | `✅ 🎬 Video (MP4) - Downloaded` |
| Not downloaded | `🎵 Audio (MP3)` or `🎵 Audio (M4A)` (no FFmpeg) |
| Audio already cached | `✅ 🎵 Audio - Downloaded` |
| Not downloaded | `🖼️ Thumbnails` |
| Thumbnails already cached | `✅ 🖼️ Thumbnails - Downloaded` |
| (always present) | `🔙 Back` |

> **Note**: Video MKV and MP4 are independently tracked. Having an MKV does NOT mark MP4 as cached — the user might want a fresh MP4 for iOS compatibility.

---

## Delivery Options

After a download completes, the **delivery screen** appears:

### Private Chat Delivery Keyboard

```
📤 Send via Telegram    → Uploads the file to the chat
📋 Get Download Link    → Generates an HTTP/HTTPS URL
🔙 Back to formats      → Re-shows the format picker
➕ 🎵 Audio / 🖼️ Thumb   → "Also get" buttons for other formats
```

### Default Delivery Behavior

```mermaid
flowchart TD
    RECORD["Download complete<br/>VideoRecord created"] --> DEFAULT{"get_default_delivery(bot, uid)"}

    DEFAULT -->|"ask"| SHOW_KB["Show delivery keyboard<br/>User chooses"]
    DEFAULT -->|"telegram"| TG["send_telegram_direct()<br/>Upload immediately"]
    DEFAULT -->|"link"| LINK["send_link_direct()<br/>Show link immediately"]

    SHOW_KB --> PICK{User clicks}
    PICK -->|"tg_send"| TG2["send_telegram()<br/>Resolve via _delivery_screen<br/>Upload + delete kb msg"]
    PICK -->|"lk_send"| LINK2["send_link()<br/>Resolve via _delivery_screen<br/>Show URL + delete kb msg"]
    PICK -->|"backfmt"| BACK["back_to_formats()<br/>Re-render format picker"]
    PICK -->|"morefmt_video/audio/thumb"| ALSO["also_get_other_format()<br/>Download other media type"]

    TG & TG2 --> UPLOAD["send_file()<br/>Try global file_id cache<br/>→ per-record cache<br/>→ disk upload<br/>→ cache file_id"]
    UPLOAD --> CHAT["✅ File in chat"]

    LINK & LINK2 --> BUILD["_build_dl_url()<br/>Guarantee https:// scheme<br/>URL-encode filename"]
    BUILD --> SHOW["📥 Downloadable URL<br/>+ Download button"]

    style RECORD fill:#2e7d32,color:#fff
    style DEFAULT fill:#1a1a2e,color:#fff
    style SHOW_KB fill:#1565c0,color:#fff
    style TG fill:#e65100,color:#fff
    style LINK fill:#e65100,color:#fff
    style CHAT fill:#2e7d32,color:#fff
    style SHOW fill:#2e7d32,color:#fff
```

### Unavailable Record Handling

If a delivery keyboard's underlying record is gone (file deleted, server migration):

```mermaid
flowchart LR
    CLICK["User clicks<br/>delivery button"] --> RESOLVE["_resolve_delivery_record()"]
    RESOLVE --> FOUND{Found?}
    FOUND -->|"Yes + file exists"| SERVE["Deliver normally ✅"]
    FOUND -->|"Yes + file gone"| POP["Pop dead entry<br/>from _delivery_screen"]
    FOUND -->|"No"| UNAVAIL["_unavailable_message()"]
    POP --> UNAVAIL
    UNAVAIL --> RECOVERY["⚠️ That entry is no longer<br/>available. Try /recent.<br/><br/>[📹 Recent] [🔙 Menu]"]

    style SERVE fill:#2e7d32,color:#fff
    style UNAVAIL fill:#c62828,color:#fff
```

---

## Inline Mode

The bot supports Telegram's **inline query** mode: type `@botname <url>` in **any chat** without adding the bot.

```mermaid
sequenceDiagram
    participant User as 👤 User (any chat)
    participant TG as ☁️ Telegram
    participant Bot as 🤖 Bot
    participant YT as 📺 YouTube

    User->>TG: Type "@botname https://youtu.be/abc123"
    TG->>Bot: inline_query(query="https://youtu.be/abc123")
    activate Bot

    Bot->>Bot: extract_url() → extract_video_id()
    Bot->>Bot: ok(uid)? _ensure(uid)?

    loop For each media_type: video, audio, thumb
        alt Global file_id cache hit
            Bot->>Bot: Return CachedVideo/Audio/Photo result
        else Per-user record + telegram_file_id
            Bot->>Bot: Return Cached* result<br/>Promote to global cache
        else File on disk (no file_id)
            Bot->>Bot: Generate token → "Ready" result<br/>+ "📥 Get File" button
        else No file
            Bot->>Bot: Generate token → "Download" result<br/>+ "📥 Start Download" button
        end
    end

    Bot-->>TG: inline_query.answer(results, cache_time=0)
    TG-->>User: Shows 3 inline results

    User->>TG: Clicks a result
    TG->>Bot: /start dl_<token> (private chat)

    par Token status: completed
        Bot->>TG: send_file() — immediate delivery
        TG-->>User: File in chat ✅
    and Token status: pending
        Bot->>User: "⏳ Starting download..."
        Bot->>+YT: Background download (_do_download)
        YT-->>-Bot: File on disk
        User->>Bot: Click "🔄 Check Progress"
        Bot->>TG: send_file()
        TG-->>User: File in chat ✅
    end

    deactivate Bot
```

### Inline Result Types

| Status | Result Type | Button | What Happens on Click |
|--------|------------|--------|----------------------|
| **Cached (global)** | `CachedVideo/Audio/Photo` | None (instant) | Telegram sends the cached file directly |
| **Ready** | `Article` with title | `📥 Get File` | Opens `/start dl_<token>` → immediate `send_file()` |
| **Download** | `Article` with title | `📥 Start Download` | Opens `/start dl_<token>` → background download starts |

### Deep Link Token Lifecycle

```mermaid
stateDiagram-v2
    [*] --> Pending: User clicks "Start Download"
    Pending --> Downloading: _do_download() starts
    Downloading --> Completed: Download succeeds
    Downloading --> Failed: Download error
    Completed --> [*]: send_file() serves the result
    Failed --> [*]: Error message shown
    Pending --> [*]: Token expires (no click within timeout)
```

---

## Cookie Upload

The `/cookies` command starts a **ConversationHandler** that accepts a Netscape-format cookies.txt file.

```mermaid
stateDiagram-v2
    [*] --> ask_cookies: /cookies or 🍪 button

    state ask_cookies {
        [*] --> Gate1: ok(uid)?
        Gate1 --> Gate2: Config.is_admin(uid)?
        Gate2 --> Prompt: "📤 Send cookies.txt"
        Gate1 --> END1: ❌ Not whitelisted
        Gate2 --> END2: ❌ Not admin
    }

    state recv_cookies {
        Prompt --> CheckDoc: User sends file
        CheckDoc --> Validate: Is it a document?
        CheckDoc --> RePrompt: ❌ Not a .txt file → re-prompt
        Validate --> Download: get_file() → download_as_bytearray()
        Download --> Store: bot._cookie_data[uid] = bytes
        Store --> SaveID: bot._cookie_file_ids[uid] = file_id
        SaveID --> Clean: Unlink old temp file
        Clean --> Success: ✅ "Cookies saved!" + menu
        Download --> Retry: Exception → re-prompt
    }

    Success --> [*]
    END1 --> [*]
    END2 --> [*]

    note right of Store
        Cookies stored in RAM only
        File ID saved for auto-restore
        Temp file written on demand
        for yt-dlp (recreated each time)
    end note
```

### Cookie Auto-Restore

On bot restart, cookies are automatically restored from Telegram using the saved `file_id`:

```
Bot starts → load_data() → cookie_file_ids loaded from JSON
  → User sends link → _ensure(bot, uid)
    → uid in _cookie_data? No → uid in _cookie_file_ids?
      → Yes → _load_cookies() → bot._bot.get_file(file_id)
        → download_as_bytearray() → bot._cookie_data[uid] = bytes
```

---

## Recent Downloads

The `/recent` command and 📹 button show a **paginated list** (5 entries per page) of the user's last 20 downloads.

```mermaid
flowchart TD
    ENTRY["/recent or 📹 button"] --> PRUNE["prune_missing(bot, uid)<br/>Remove records with deleted files"]
    PRUNE --> EMPTY{Any records<br/>left?}
    EMPTY -->|"No"| NO_FILES["📭 No files.<br/>[🔙 Menu]"]

    EMPTY -->|"Yes"| PAGE["Calculate page<br/>5 entries per page<br/>max 4 pages (20 entries)"]

    PAGE --> RENDER["Render entry rows:<br/>✅/🗑️ 🎬/🎵/🖼️ {index}. {title}<br/>📦 {size}MB | {date}<br/>⚠️ {days}d retention"]

    RENDER --> KB["Keyboard:<br/>• Select button per entry<br/>• 🗑️ Delete per entry<br/>• 🗑️ Clear All<br/>• ⬅️ ➡️ Pagination<br/>• 🔙 Menu"]

    KB --> ACTIONS["User clicks..."]

    ACTIONS -->|"sel_{idx}"| SELECT["_select()<br/>Prune → push NAV_RECENT<br/>→ show_delivery(record)"]
    ACTIONS -->|"d_{idx}"| DELETE["_delete()<br/>Unlink file → pop record<br/>→ save → '🗑️ Deleted.'"]
    ACTIONS -->|"clear_all"| CLEAR["_clear_all()<br/>Unlink ALL files<br/>→ pop user → save"]
    ACTIONS -->|"p_{page}"| PAGE
    ACTIONS -->|"b"| BACK["handle_back()<br/>→ Welcome + menu"]

    style ENTRY fill:#2e7d32,color:#fff
    style KB fill:#1a1a2e,color:#fff
    style SELECT fill:#1565c0,color:#fff
    style DELETE fill:#c62828,color:#fff
    style CLEAR fill:#c62828,color:#fff
```

### Page Layout

```
📹 Downloads (1/3)
🗑️ Cleaned 2 missing entries.

✅ 🎬 1. Video Title Here (first 50 chars)
   📦 45.23MB | 2026-07-28 14:30

✅ 🎵 2. Audio Title Here...
   📦 8.12MB | 2026-07-27 09:15

🗑️ 🖼️ 3. Thumbnail (deleted file)
   📦 0.15MB | 2026-07-26 22:00

...

⚠️ 14d retention.

[🎬 1. Title...] [🗑️ #1]
[🎵 2. Title...] [🗑️ #2]
[🖼️ 3. Title...] [🗑️ #3]
[🗑️ Clear All]
[⬅️] [➡️]
[🔙 Menu]
```

---

## Navigation & Back Button

The bot uses a **per-message navigation stack** so that pressing 🔙 **Back** on an old keyboard doesn't confuse it with a new flow.

```mermaid
flowchart TD
    subgraph "Per-Message Nav Stack Example"
        direction LR
        MSG42["Message 42 (video #1)<br/>nav_stack = []"]
        MSG55["Message 55 (video #2)<br/>nav_stack = []"]
    end

    PUSH1["show_delivery() on msg 42<br/>→ nav_push(chat, 42, NAV_RECENT)"] --> MSG42_STACK["Message 42<br/>nav_stack = [(NAV_RECENT,None)]"]

    PUSH2["show_delivery() on msg 55<br/>→ nav_push(chat, 55, NAV_FORMAT, data)"] --> MSG55_STACK["Message 55<br/>nav_stack = [(NAV_FORMAT, (url, vid))]"]

    CLICK42["User clicks 🔙 on msg 42"] --> POP42["nav_pop(chat, 42)<br/>→ (NAV_RECENT, None)"]
    POP42 --> SHOW_RECENT["show_recent() ✅"]

    CLICK55["User clicks 🔙 on msg 55"] --> POP55["nav_pop(chat, 55)<br/>→ (NAV_FORMAT, (url, vid))"]
    POP55 --> SHOW_FORMAT["show_format_choice(url, vid) ✅"]

    CLICK42 -.->|"Does NOT go<br/>to format picker<br/>for video #2!"| POP55

    style MSG42 fill:#0f3460,color:#fff
    style MSG55 fill:#0f3460,color:#fff
    style MSG42_STACK fill:#1565c0,color:#fff
    style MSG55_STACK fill:#1565c0,color:#fff
    style SHOW_RECENT fill:#2e7d32,color:#fff
    style SHOW_FORMAT fill:#2e7d32,color:#fff
```

### Navigation Stack Constants

| Constant | Meaning | Push Location |
|----------|---------|--------------|
| `NAV_MAIN` | Return to welcome screen | Menu button in `/recent`, format picker, delivery screen |
| `NAV_RECENT` | Return to recent downloads | Delivery screen (`show_delivery`) |
| `NAV_FORMAT` | Return to format picker | Delivery screen (back to format picker for same video) |
| `NAV_DELIVERY` | Return to delivery screen | (reserved, not yet used) |

### Back Button Resolution

```mermaid
flowchart TD
    CLICK["User clicks 🔙 Back<br/><i>callback: b</i>"] --> POP["nav_pop(chat_id, message_id)"]
    POP --> PREV{Previous frame?}

    PREV -->|"NAV_MAIN (or empty)"| WELCOME["Welcome text + main menu<br/>Delete old msg"]
    PREV -->|"NAV_RECENT"| RECENT["show_recent()<br/>Delete old msg"]
    PREV -->|"NAV_FORMAT"| FORMAT["show_format_choice(url, video_id)<br/>Delete old msg"]
    PREV -->|"Other"| WELCOME

    style CLICK fill:#1a1a2e,color:#fff
    style WELCOME fill:#2e7d32,color:#fff
    style RECENT fill:#1565c0,color:#fff
    style FORMAT fill:#1565c0,color:#fff
```

---

## Group Chat Flow

In groups, the bot only downloads **video** (no format choice) and requires an **admin** of the group to be whitelisted.

```mermaid
flowchart TD
    LINK["User sends YouTube link<br/>in a group chat"] --> EXTRACT["extract_url()<br/>extract_video_id()"]

    EXTRACT --> ADMIN{"_check_group()<br/>Any admin whitelisted?"}
    ADMIN -->|"No"| SILENT["Silent ignore<br/>(no response)"]

    ADMIN -->|"Yes"| COOKIES{"_ensure(uid)<br/>Cookies loaded?"}
    COOKIES -->|"No"| SILENT

    COOKIES -->|"Yes"| SEM["Acquire _download_semaphore"]

    SEM --> DEDUP{"find_existing()<br/>Already downloaded?"}
    DEDUP -->|"Yes"| DEFAULT_DEL{"get_default_delivery()"}
    DEDUP -->|"No"| DL["download(media_type='video')<br/>ThreadPoolExecutor"]
    DL --> SAVE["VideoRecord → bot.videos[uid]<br/>→ cap at 20 → bot.save()"]

    SAVE --> SUBS{"Subtitle files<br/>produced?"}
    SUBS -->|"Yes"| SEND_SUBS["Send .srt files as documents"]
    SUBS -->|"No"| DEFAULT_DEL

    DEFAULT_DEL --> DEL_CHOICE{Default delivery?}
    DEL_CHOICE -->|"telegram"| TG["send_file() — upload immediately"]
    DEL_CHOICE -->|"link"| LINK["Show download link immediately"]
    DEL_CHOICE -->|"ask"| KB["Group delivery keyboard:<br/>📤 Send via Telegram<br/>📋 Get Download Link"]

    KB --> CLICK{User clicks}
    CLICK -->|"tg_send"| TG2["send_telegram()<br/>via _delivery_screen"]
    CLICK -->|"lk_send"| LINK2["send_link()<br/>via _delivery_screen"]

    TG & TG2 --> DONE["✅ File in group chat"]
    LINK & LINK2 --> URL["📥 Download link in group chat"]

    style LINK fill:#2e7d32,color:#fff
    style ADMIN fill:#1a1a2e,color:#fff
    style SILENT fill:#546e7a,color:#aaa
    style DL fill:#e65100,color:#fff
    style DONE fill:#2e7d32,color:#fff
    style URL fill:#2e7d32,color:#fff
```

### Group Admin Whitelist

The admin check is **cached per chat_id** after the first successful lookup:

```
_check_group(chat_id):
  1. If chat_id in _group_admins and non-empty → return True
  2. get_chat_administrators(chat_id) via Telegram API
  3. Filter admins: ok(bot, admin.user.id)
  4. Store result set → _group_admins[chat_id]
  5. Return True if any admin matched
```

### Group vs Private Chat Differences

| Feature | Private Chat | Group Chat |
|---------|-------------|-----------|
| Format choice | ✅ Yes (pick Video/Audio/Thumb) | ❌ Video only |
| Auto-format | ✅ Respects user setting | ❌ N/A |
| Delivery keyboard | 📤 Send / 📋 Link / 🔙 Back / ➕ Also get | 📤 Send / 📋 Link only |
| Per-user cookies | ✅ User's own cookies | ✅ Each member uses their own |
| Admin required | ❌ (unless WHITELIST_USERS set) | ✅ Group admin must be whitelisted |

---

## Callback Data Reference

### Main Menu & Navigation

| Callback | Handler | Description |
|----------|---------|-------------|
| `b` | `handle_back()` | Back: pop per-message nav stack |
| `r` | `show_recent()` | Recent downloads list |
| `c` | `ask_cookies()` | Start cookie upload |
| `vc` | (inline) | Show file count |
| `cs` | (inline) | Show cookie status |
| `clear_all` | `_clear_all()` | Delete all user files |

### Settings — Open Picker

| Callback | Handler | Picker |
|----------|---------|--------|
| `vq` | `_change_video_quality()` | Video quality |
| `aq` | `_change_audio_quality()` | Audio quality |
| `sm` | `_change_subtitle_mode()` | Subtitle mode |
| `lang` | `_change_language()` | Subtitle language |
| `delivery` | `_change_delivery()` | Default delivery |
| `af` | `_change_auto_format()` | Auto-format default |
| `cn` | `_change_video_container()` | Container default |

### Settings — Persist Choice

| Callback Pattern | Handler | Example |
|-----------------|---------|---------|
| `setvq_{opt}` | `_set_video_quality()` | `setvq_1080p` |
| `setaq_{opt}` | `_set_audio_quality()` | `setaq_320k` |
| `setsm_{opt}` | `_set_subtitle_mode()` | `setsm_separate` |
| `setlang_{code}` | `_set_language()` | `setlang_fa` |
| `setdelivery_{method}` | `_set_delivery()` | `setdelivery_telegram` |
| `setaf_{fmt}` | `_set_auto_format()` | `setaf_video` |
| `setcn_{opt}` | `_set_video_container()` | `setcn_mp4` |

### Download Flow

| Callback | Handler | Description |
|----------|---------|-------------|
| `fmt_video_mkv` | `choose_format()` | Download video (auto container → MKV) |
| `fmt_video_mp4` | `choose_format()` | Download video (forced MP4) |
| `fmt_audio` | `choose_format()` | Download audio (MP3/M4A) |
| `fmt_thumb` | `choose_format()` | Download thumbnails |

### Delivery

| Callback | Handler | Description |
|----------|---------|-------------|
| `tg_send` | `send_telegram()` | Upload via Telegram |
| `lk_send` | `send_link()` | Generate download link |
| `backfmt` | `back_to_formats()` | Back to format picker |
| `morefmt_video` | `also_get_other_format()` | Also download video |
| `morefmt_audio` | `also_get_other_format()` | Also download audio |
| `morefmt_thumb` | `also_get_other_format()` | Also download thumbnails |

### Recent Downloads

| Callback Pattern | Handler | Description |
|-----------------|---------|-------------|
| `sel_{idx}` | `_select()` | Open delivery for entry at index |
| `d_{idx}` | `_delete()` | Delete entry at index |
| `p_{page}` | `show_recent(page)` | Navigate to page |

---

## Settings Reference Tables

### Video Quality

| Value | Label | yt-dlp behavior |
|-------|-------|----------------|
| `best` | 🏆 Best | Highest available quality |
| `2160p` | 📺 4K | Max height 2160, H.264 forced |
| `1440p` | 📺 1440p | Max height 1440, H.264 forced |
| `1080p` | 📺 1080p | Max height 1080, H.264 forced |
| `720p` | 📺 720p | Max height 720, H.264 forced |
| `480p` | 📺 480p | Max height 480, H.264 forced |
| `360p` | 📺 360p | Max height 360, H.264 forced |
| `worst` | ⬇️ Worst | Lowest available quality |

### Audio Quality

| Value | Label | yt-dlp behavior |
|-------|-------|----------------|
| `best` | 🏆 Best | Highest available bitrate |
| `320k` | 🎵 320kbps | Max 320 kbps |
| `256k` | 🎵 256kbps | Max 256 kbps |
| `192k` | 🎵 192kbps | Max 192 kbps |
| `128k` | 🎵 128kbps | Max 128 kbps |
| `96k` | 🎵 96kbps | Max 96 kbps |
| `worst` | ⬇️ Worst | Lowest available bitrate |

### Subtitle Mode

| Value | Label | Behavior |
|-------|-------|----------|
| `embed` | 🔗 Embed (MKV) | Soft-subs muxed into MKV container. **If container=MP4, cascades to separate!** |
| `separate` | 📎 Separate file | Subtitles delivered as a separate .srt document |
| `off` | 🚫 Off | No subtitles downloaded |

### Subtitle Language

| Value | Label |
|-------|-------|
| `en` | 🇬🇧 English |
| `fa` | 🇮🇷 فارسی |
| `ar` | 🇸🇦 العربية |
| `ru` | 🇷🇺 Русский |
| `es` | 🇪🇸 Español |

### Default Delivery Method

| Value | Label | Behavior |
|-------|-------|----------|
| `ask` | ❓ Ask every time | Show delivery keyboard after each download |
| `telegram` | 📤 Send via Telegram | Auto-upload file to chat |
| `link` | 📋 Get Download Link | Auto-generate HTTP/HTTPS link |

### Auto-Format

| Value | Label | Behavior |
|-------|-------|----------|
| `ask` | ❓ Ask | Show format picker when link is sent |
| `video` | 🎬 Auto Video | Skip picker, download video immediately (respecting container setting) |
| `audio` | 🎵 Auto Audio | Skip picker, download audio immediately |
| `thumb` | 🖼️ Auto Thumb | Skip picker, download thumbnail immediately |

### Video Container

| Value | Label | Behavior |
|-------|-------|----------|
| `auto` | 🔀 Auto | yt-dlp picks best container. Subtitles can be embedded (MKV mux). |
| `mp4` | 🎬 MP4 | Forces MP4 output. Subtitles come as separate .srt files. |

---

## Complete User Journey Map

This diagram shows every possible path a user can take from initial contact through to file receipt:

```mermaid
flowchart TB
    START(["👤 User contacts bot"]) --> ENTRY{Entry point}

    ENTRY -->|"/start"| WELCOME["Welcome + main menu"]
    ENTRY -->|"YouTube link"| LINK["on_msg()"]
    ENTRY -->|"@botname url"| INLINE["inline_query()"]
    ENTRY -->|"/help"| HELP["Quick reference + menu"]
    ENTRY -->|"/settings"| SETTINGS_SUM["Settings summary + menu"]
    ENTRY -->|"/recent"| RECENT_PAGE["Paginated recent list"]
    ENTRY -->|"/status"| STATUS_PAGE["Warp proxy health"]
    ENTRY -->|"/cookies"| COOKIE_FLOW["Cookie upload conversation"]
    ENTRY -->|"/cancel"| CANCEL_FLOW["Reset to menu"]

    WELCOME --> MENU["Main Menu"]
    HELP --> MENU
    SETTINGS_SUM --> MENU
    STATUS_PAGE("Status text + menu<br/><i>shown together</i>") --> MENU

    MENU --> SETTINGS["Any setting button"]
    SETTINGS --> PICKER["Picker keyboard"]
    PICKER -->|"set*_{value}"| CONFIRM["✅ Confirmation"]
    CONFIRM --> MENU

    LINK --> PRIVATE{Private?}
    PRIVATE -->|"Yes"| AUTO_FORMAT{"Auto-format?"}
    AUTO_FORMAT -->|"Yes"| DOWNLOAD["Download task"]
    AUTO_FORMAT -->|"No (ask)"| FORMAT_PICKER["Format picker"]
    FORMAT_PICKER -->|"Click format"| DOWNLOAD
    PRIVATE -->|"No (Group)"| GROUP_DL["Video-only download<br/>+ admin check"]

    DOWNLOAD --> DELIVERY_SCREEN["Delivery screen"]
    GROUP_DL --> DELIVERY_SCREEN

    DELIVERY_SCREEN --> TG_SEND["📤 Telegram upload"]
    DELIVERY_SCREEN --> DL_LINK["📋 Download link"]
    DELIVERY_SCREEN --> BACK_FMT["🔙 Back to formats"]
    DELIVERY_SCREEN --> ALSO_GET["➕ Also get other format"]

    TG_SEND --> DONE(["✅ File received"])
    DL_LINK --> DONE
    BACK_FMT --> FORMAT_PICKER
    ALSO_GET --> DOWNLOAD

    INLINE --> INLINE_RESULTS["3 results:<br/>video, audio, thumb"]
    INLINE_RESULTS -->|"Click"| TOKEN_START["/start dl_<token>"]
    TOKEN_START -->|"Completed"| DONE
    TOKEN_START -->|"Pending"| BG_DL["Background download"]
    BG_DL -->|"Check progress"| DONE

    RECENT_PAGE --> SELECT_ENTRY["Select entry"]
    SELECT_ENTRY --> DELIVERY_SCREEN
    RECENT_PAGE --> DELETE_ENTRY["Delete single entry"]
    RECENT_PAGE --> CLEAR_ALL["Clear all entries"]

    style START fill:#2e7d32,color:#fff
    style WELCOME fill:#1565c0,color:#fff
    style MENU fill:#1a1a2e,color:#fff,stroke:#16213e
    style DOWNLOAD fill:#e65100,color:#fff
    style DELIVERY_SCREEN fill:#533483,color:#fff
    style DONE fill:#2e7d32,color:#fff
    style FORMAT_PICKER fill:#0f3460,color:#fff
```

---

### Auto-Delete on Overflow

When a user reaches 21+ downloads, the **oldest record is automatically deleted**:

```
bot.videos[uid].insert(0, record)     # Newest at index 0
while len(bot.videos[uid]) > 20:       # Cap at 20
    old = bot.videos[uid].pop()         # Remove oldest (index 20)
    Path(old.file_path).unlink()        # Delete from disk
bot.save()
```

This means the **last 20 downloads** are always preserved, and older ones are silently removed.

### /cancel in Contexts

- **Normal context**: `/cancel` calls `nav_clear_user()` and returns to main menu with "❌ Cancelled."
- **Inside cookie upload**: `/cancel` returns `ConversationHandler.END` to exit the conversation, then also resets to the main menu
- `/cancel` does NOT affect in-flight downloads — those are handled by the `_download_semaphore`

---

**Next:** [Usage Guide](./USAGE.md) → [Architecture Guide](./ARCHITECTURE.md) → [Configuration Reference](./CONFIGURATION.md)
