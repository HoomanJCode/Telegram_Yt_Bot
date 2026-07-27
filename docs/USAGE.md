# Usage Guide

> **How to use the YouTube Downloader Telegram Bot**

---

## Table of Contents

- [Quick Start](#quick-start)
- [Commands](#commands)
- [Download Flow](#download-flow)
- [Settings](#settings)
- [Inline Mode](#inline-mode)
- [Tips & Tricks](#tips--tricks)
- [FAQ](#faq)

---

## Quick Start

```
1. Start a chat:   @BotFather → find your bot → /start
2. Upload cookies: /cookies → send your cookies.txt file
3. Send a link:    Paste any YouTube URL
4. Choose format:  Video, Audio, or Thumbnail
5. Get your file:  Telegram upload or download link
```

---

## Commands

| Command | Description |
|---------|-------------|
| `/start` | Welcome message with main menu |
| `/help` | Quick help with available commands |
| `/settings` | View and change your preferences |
| `/cookies` | Upload a YouTube cookies.txt file |
| `/recent` | View your recent downloads |
| `/status` | Check bot health and proxy status |
| `/cancel` | Cancel the current operation |

### /start

Two entry points:
1. **Normal**: Shows the welcome message with navigation menu
2. **Deep link** (`/start dl_<token>`): Used by inline mode results to serve or download a file

### /cookies

Required before the first download. The bot accepts a **Netscape-format cookies.txt** file exported from your browser.

**Important security notes:**
- 🍪 Cookies are stored in RAM only, not written to disk
- 🔐 A Telegram file_id is saved for automatic cookie restoration
- 🔒 On shared bots, only admin users can upload cookies (see `ADMIN_USERS` in [Configuration](./CONFIGURATION.md))
- ⏳ Cookies persist until bot restart (or until the configurable COOKIE_TTL_HOURS)

**How to export cookies from Chrome:**
1. Install a cookies.txt export extension (e.g., "Get cookies.txt LOCALLY")
2. Log into YouTube in your browser
3. Use the extension to export cookies
4. Send the resulting `.txt` file to the bot via `/cookies`

### /recent

Shows your last 20 downloads organized in pages. Each entry shows:
- File title and type (🎬 video, 🎵 audio, 🖼️ thumbnail)
- Download status (✅ exists / 🗑️ deleted)
- Tap an entry to open its delivery menu

### /status

Reports bot health information:
- Cloudflare Warp proxy status (enabled/disabled, connected/disconnected)
- warp-cli installation status
- Proxy port reachability

### /settings

Opens an interactive menu to configure:
- 🎬 Video quality
- 🎵 Audio quality
- 📝 Subtitle mode
- 🎞️ Video container
- 📤 Default delivery method
- ⚡ Auto-format

---

## Download Flow

### Step 1: Send a YouTube Link

Just paste any YouTube URL in the chat. The bot supports:
- `https://youtube.com/watch?v=...`
- `https://youtu.be/...`
- `https://youtube.com/shorts/...`
- `https://youtube.com/embed/...`
- Links with or without `www.`
- Links with or without `https://`

### Step 2: Choose Format

The bot shows a keyboard with your options:

```
🎬 Video (MKV) — best quality + auto-subs
🎬 Video (MP4) — universal compat, subs separate
🎵 Audio (MP3)     [or M4A if FFmpeg is missing]
🖼️ Thumbnails
🔙 Back
```

- **Already downloaded** formats show a ✅ checkmark
- **Video (MKV)**: Best quality, supports subtitle embedding. Recommended for personal use.
- **Video (MP4)**: Universal compatibility (iOS, WhatsApp, older devices). Subtitles come as separate files.
- **Audio**: MP3 if FFmpeg is installed, M4A otherwise
- **Thumbnails**: Downloads the video thumbnail image only (no full download)

### Step 3: Choose Delivery

After download, choose how to receive your file:

```
📤 Send via Telegram    → File uploaded directly in chat
📋 Get Download Link    → HTTP/HTTPS link (expires after STORAGE_DAYS)
🔙 Back to formats      → Download another format of same video
➕ Also get [other]     → Download audio/thumb for same video
```

### Default Delivery

You can set a default delivery method in `/settings`:

- **Ask each time** (default): Shows the delivery keyboard
- **Telegram**: Files are automatically uploaded via Telegram
- **Link**: Download links are automatically generated

---

## Settings

The `/settings` command shows your current preferences and lets you change them via inline buttons.

### Available Settings

| Setting | Options | Default |
|---------|---------|---------|
| **Video Quality** | 🏆 Best, 📺 4K, 📺 1440p, 📺 1080p, 📺 720p, 📺 480p, 📺 360p, ⬇️ Worst | Best |
| **Audio Quality** | 🏆 Best, 🎵 320kbps, 🎵 256kbps, 🎵 192kbps, 🎵 128kbps, 🎵 96kbps, ⬇️ Worst | Best |
| **Subtitle Mode** | 🔗 Embed (MKV), 📎 Separate file, 🚫 Off | Embed |
| **Video Container** | 🔀 Auto (best codec match), 🎬 MP4 (universal compat) | Auto |
| **Delivery Method** | ❓ Ask each time, 📤 Telegram, 📋 Link | Ask |
| **Auto Format** | ❓ Ask each time, 🎬 Auto Video, 🎵 Auto Audio, 🖼️ Auto Thumb | Ask |

---

## Inline Mode

You can use the bot in **any Telegram chat** without adding it:

1. Type `@YourBotName <youtube_url>` in any chat's message field
2. The bot shows inline results for Video, Audio, and Thumbnail
3. Tap a result to either:
   - Get the file immediately (if cached)
   - Start a download (opens private chat with the bot)

**Inline result types:**
- ✅ **Cached**: Shows "Cached" with a file size — instant delivery
- ⏳ **Ready**: Shows "Ready" — click the "Get File" button
- 📥 **Download**: Shows "Download <Type>" — click "Start Download" to begin

Results update in real-time: clicking again after a download completes shows the cached result.

---

## Tips & Tricks

### Auto-Format

If you always download the same media type, set it in `/settings`:
- **Auto Video**: Skip the format picker, go straight to video download
- **Auto Audio**: Always download audio
- **Auto Thumb**: Always download thumbnails

### Get All Formats

After downloading one format:
1. Click **"🔙 Back to formats"** on the delivery screen
2. The format picker shows ✅ on already-downloaded formats
3. Download the remaining formats

Or use the **"➕ Also get..."** buttons on the delivery screen directly.

### Group Chats

When the bot is added to a group:
- Only video downloads are supported (no format choice)
- At least one admin of the group must be whitelisted
- Each group member can use the bot independently (per-user cookies)

### Inline Mode Tips

- Inline results expire after a few minutes — if you see an error, just type again
- The bot shows your language preference from Telegram's settings
- Cached results (✅ "Cached") are instant for everyone, not just you

---

## FAQ

### Why do I need to upload cookies?

YouTube requires authentication to download many videos, especially:
- Age-restricted content
- Private or members-only videos
- Some geo-blocked content
- Higher quality streams

The cookies let the bot use your YouTube session.

### My cookies stopped working?

Cookies expire. Re-export them from your browser and re-upload via `/cookies`.

### Download failed: "Requested quality unavailable in H.264"

This means the selected quality (e.g., 4K, 1440p) is only available in VP9 or AV1 codec on YouTube. The bot pins to H.264 (AVC) for universal TV compatibility. Try a lower quality like 1080p or 720p.

### Download failed: "Video downloaded without subtitles"

YouTube rate-limited the subtitle fetch. The video was downloaded successfully — try again in a minute if you need subtitles.

### "Bot storage full" error

The VPS disk is nearly full. Either:
- Free up space: `journalctl --vacuum-time=3d`
- Increase `MIN_DISK_FREE_MB` in `.env`
- Reduce `STORAGE_DAYS` to auto-delete files faster

### File link not loading?

- Ensure the port in `BASE_DOWNLOAD_LINK` matches the bot's port
- Check firewall rules: `ufw status`
- If using Cloudflare Tunnel, ensure `cloudflared` is running
- Check bot logs: `journalctl -u telegramytbot -n 50`

### How do I get my Telegram User ID?

Send a message to [@userinfobot](https://t.me/userinfobot) on Telegram. It will reply with your numeric user ID.

---

**Next:** [Configuration Reference](./CONFIGURATION.md) → [SSL with Cloudflare](./SSL_CLOUDFLARE.md) → [Development Guide](./DEVELOPMENT.md)
