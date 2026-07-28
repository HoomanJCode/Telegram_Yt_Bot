# YouTube Downloader Telegram Bot

> **Download YouTube videos, audio, and thumbnails directly from Telegram**

[![Python](https://img.shields.io/badge/Python-3.8+-blue?logo=python)](https://python.org)
[![License](https://img.shields.io/badge/License-Educational-red)](LICENSE)
[![Telegram Bot API](https://img.shields.io/badge/Telegram-Bot%20API-2CA5E0?logo=telegram)](https://core.telegram.org/bots/api)

---

> **⚠️ DISCLAIMER: EDUCATIONAL PROJECT**
>
> This project is created for **educational purposes only**. It demonstrates Python programming concepts, Telegram Bot API integration, and web scraping techniques.
>
> - This bot is **NOT intended for production use** or actual video downloading
> - Downloading YouTube videos may violate YouTube's Terms of Service
> - Respect content creators' rights and intellectual property
> - Users are solely responsible for complying with applicable laws and regulations
> - The developers assume **NO liability** for any misuse of this software
> - This project was built as a coding exercise using **Vibe Coding** methodology with DeepSeek AI assistance

---

## 📚 Documentation

| Document | Description |
|----------|-------------|
| **[📖 USAGE.md](./docs/USAGE.md)** | Commands, download flow, inline mode, settings, and FAQ |
| **[🗺️ USER_FLOWS.md](./docs/USER_FLOWS.md)** | Complete map of all menus, settings, user paths, and callback reference |
| **[🔧 CONFIGURATION.md](./docs/CONFIGURATION.md)** | All environment variables explained with examples |
| **[📦 DEPLOYMENT.md](./docs/DEPLOYMENT.md)** | VPS setup, CI/CD via GitHub Actions, systemd service |
| **[🏗️ ARCHITECTURE.md](./docs/ARCHITECTURE.md)** | Codebase structure, data flow, design decisions |
| **[🔒 SSL_CLOUDFLARE.md](./docs/SSL_CLOUDFLARE.md)** | HTTPS setup with Cloudflare (3 approaches) |
| **[👩‍💻 DEVELOPMENT.md](./docs/DEVELOPMENT.md)** | Local setup, testing, conventions, adding features |

---

## 🚀 Features

- 🎬 **Video Download** — Full video with quality selection (Best, 4K, 1440p, 1080p, 720p, 480p, 360p, Worst)
- 🎵 **Audio Download** — MP3 (with FFmpeg) or M4A, quality selection (Best, 320/256/192/128/96 kbps, Worst)
- 🖼️ **Thumbnail Download** — Video thumbnails without full download
- 📝 **Subtitle Handling** — Embed subs into MKV (default), send as separate `.srt` file, or off
- 🔄 **Multi-Format** — Download all formats of the same video from one delivery screen
- 📤 **Two Delivery Methods** — Telegram upload (cached) or direct download link
- 💾 **Duplicate Detection** — Prevents re-downloading the same content (per-variant: MKV vs MP4)
- 🗑️ **Auto-Cleanup** — Files deleted after configurable days (default: 2)
- 🍪 **Cookie Management** — Per-user cookie storage in RAM only
- 👥 **Whitelist System** — Restrict bot to specific users
- 👑 **Admin Gating** — Lock `/cookies` to specific Telegram user IDs
- 📱 **Interactive Menus** — Inline keyboard navigation with back-stack
- 🌐 **Built-in File Server** — No separate HTTP server needed (aiohttp)
- 🔒 **Native HTTPS** — TLS termination without reverse proxy (optional)
- 🔄 **Cloudflare Warp Proxy** — Route downloads through Warp (optional)
- 📺 **Smart TV Audio Fix** — Automatic Opus → AAC transcode for universal codec support
- 📱 **Inline Mode** — Use `@YourBotName <link>` in any chat
- 🔗 **Deep Link Tokens** — Share downloads via `t.me/YourBot?start=dl_<token>`
- 🔐 **Privacy** — No sensitive data in logs, cookies in RAM only
- 📦 **Self-Contained** — Single process runs bot + file server + downloads

---

## 📋 Prerequisites

### System Requirements
- Python 3.8+
- Linux (recommended) / macOS / Windows
- FFmpeg (optional, for MP3 audio conversion & subtitle embedding)
- Deno JavaScript runtime (required for yt-dlp YouTube extraction)
- Telegram Bot Token from [@BotFather](https://t.me/BotFather)

### Install FFmpeg (recommended)
```bash
# Ubuntu/Debian
apt-get install -y ffmpeg

# macOS
brew install ffmpeg

# Without FFmpeg, audio downloads as M4A instead of MP3
# and subtitles cannot be embedded into MKV
```

### Install Deno (required for YouTube)
```bash
curl -fsSL https://deno.land/install.sh | sh
export PATH="$HOME/.deno/bin:$PATH"
echo 'export PATH="$HOME/.deno/bin:$PATH"' >> ~/.bashrc
```

---

## 📦 Quick Install

```bash
# 1. Clone and enter the project
git clone https://github.com/yourusername/Telegram_Yt_Bot.git
cd Telegram_Yt_Bot

# 2. Create virtual environment
python3 -m venv venv
source venv/bin/activate

# 3. Install dependencies
pip install -r requirements.txt
pip install yt-dlp-ejs

# 4. Configure environment
cp env.example .env
# Edit .env with your bot token and settings

# 5. Create required directories
mkdir -p data downloads

# 6. Run
python bot.py
```

> **For production deployment** (systemd service, CI/CD, SSL), see the [Deployment Guide](./docs/DEPLOYMENT.md).

---

## ⚙️ Quick Configuration

| Variable | Description | Default |
|----------|-------------|---------|
| `BOT_TOKEN` | Telegram Bot API token | **Required** |
| `BASE_DOWNLOAD_LINK` | Server URL for download links | `http://localhost:8000` |
| `WHITELIST_USERS` | Comma-separated authorized user IDs | Empty (all allowed) |
| `ADMIN_USERS` | Comma-separated IDs allowed to upload cookies | Empty (all whitelisted) |
| `STORAGE_DAYS` | Days before files auto-delete | `2` |

[→ Full configuration reference](./docs/CONFIGURATION.md)

---

## 📱 Quick Usage

### Basic Flow
1. **Upload Cookies** — `/cookies` — Send a cookies.txt file (required first step)
2. **Send YouTube Link** — Paste any YouTube URL
3. **Choose Format** — Video (MKV/MP4) / Audio (MP3/M4A) / Thumbnails
4. **Choose Delivery** — Telegram upload or download link

### Commands

| Command | Description |
|---------|-------------|
| `/start` | Welcome message and main menu |
| `/help` | Help and usage information |
| `/cookies` | Upload YouTube cookies file |
| `/recent` | View recent downloads |
| `/settings` | Change quality, format, and delivery defaults |
| `/status` | Check bot health and proxy status |
| `/cancel` | Cancel current operation |

[→ Full usage guide](./docs/USAGE.md)

---

## 🔒 HTTPS Options

Three ways to serve download links over HTTPS:

| Method | Description | Guide |
|--------|-------------|-------|
| **Cloudflare Tunnel** | No open ports, fully managed TLS | [Guide](./docs/SSL_CLOUDFLARE.md#approach-1-cloudflare-tunnel-recommended) |
| **Reverse Proxy** | Nginx/Caddy + Cloudflare proxied DNS | [Guide](./docs/SSL_CLOUDFLARE.md#approach-2-proxied-dns--reverse-proxy) |
| **Native HTTPS** | Bot terminates TLS itself (Origin CA) | [Guide](./docs/SSL_CLOUDFLARE.md#approach-3-native-https-with-origin-ca) |

---

## 🗂️ Project Structure

```
Telegram_Yt_Bot/
├── bot.py                 # Entry point (calls app.main())
├── config.py              # Configuration parser (env vars)
├── serve_files.py         # Standalone HTTP server (alternative)
├── requirements.txt       # Python dependencies
├── env.example            # Environment variable template
├── deploy.sh              # Automated deployment script
├── README.md              # This file
│
├── app/                   # Main application package
│   ├── __init__.py        # Bootstrap: logging, wiring, main()
│   ├── bot.py             # YouTubeDownloaderBot (central state)
│   ├── downloader.py      # yt-dlp download functions
│   ├── fileserver.py      # aiohttp async file server
│   ├── models.py          # VideoRecord data class
│   └── utils.py           # Utilities, constants, error classification
│
├── app/handlers/          # Telegram update handlers
│   ├── commands.py        # Slash commands (/start, /help, etc.)
│   ├── cookies.py         # Cookie upload conversation
│   ├── formats.py         # Format choice & delivery keyboards
│   ├── inline.py          # Inline query mode (@botname)
│   ├── messages.py        # Plain-text YouTube link processing
│   ├── navigation.py      # Menu system & settings UI
│   └── tokens.py          # Deep-link tokens & file delivery
│
├── docs/                  # Documentation
│   ├── ARCHITECTURE.md    # Codebase architecture
│   ├── CONFIGURATION.md   # Environment variables
│   ├── DEPLOYMENT.md      # Deployment guide
│   ├── DEVELOPMENT.md     # Developer setup & conventions
│   ├── SSL_CLOUDFLARE.md  # HTTPS with Cloudflare
│   ├── USAGE.md           # User guide
│   └── USER_FLOWS.md      # Complete user interaction map
│
├── tests/                 # Unit tests
├── data/                  # Persistent state (JSON)
└── downloads/             # Downloaded media files
```

---

## 🧪 Running Tests

```bash
# Run all tests
python -m pytest tests/ -v

# Run with coverage
python -m pytest tests/ --cov=app --cov-report=term-missing
```

---

## 🛡️ Security Notes

- 🍪 **Cookies in RAM**: Cookie bytes are kept in memory, never written to disk (except temporary yt-dlp files)
- 🔐 **Admin gating**: `/cookies` can be locked to specific Telegram IDs via `ADMIN_USERS`
- 👥 **Whitelist**: Restrict bot access to specific users via `WHITELIST_USERS`
- 🗑️ **Auto-cleanup**: Files auto-delete after `STORAGE_DAYS` (default: 2)
- 🔒 **SSL validation**: On misconfiguration, bot exits with code 78 — no silent HTTP fallback
- 📝 **No sensitive logs**: API tokens and cookie contents are never logged
- 🚫 **No user data collection**: No analytics, no tracking, no external calls (except yt-dlp to YouTube)

---

## 🤝 Contributing

See [Development Guide](./docs/DEVELOPMENT.md) for:
- Local setup instructions
- Running tests
- Code style and conventions
- How to add a new feature
- AI Rule for maintaining comments

---

## 📄 License

**Educational project.** Code can be used for learning purposes. Not intended for production deployment. Respect all applicable laws and terms of service.

---

## 🙏 Acknowledgements

- [yt-dlp](https://github.com/yt-dlp/yt-dlp) — YouTube video extraction
- [python-telegram-bot](https://github.com/python-telegram-bot/python-telegram-bot) — Telegram Bot API framework
- [aiohttp](https://docs.aiohttp.org/) — Async HTTP server
- [FFmpeg](https://ffmpeg.org/) — Media processing
- [Deno](https://deno.land/) — JavaScript runtime for yt-dlp

---

**Built with ❤️ using Vibe Coding & DeepSeek AI**  
*For educational purposes only*
