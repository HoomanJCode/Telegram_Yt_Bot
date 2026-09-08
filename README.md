# 🎥 YouTube Downloader Telegram Bot

> **Download YouTube videos, MP3 audio, and thumbnails directly from Telegram — self-hosted, free, and private.**

A self-hosted **YouTube downloader bot for Telegram** built with **Python**, **yt-dlp**, and the **python-telegram-bot** framework. Paste any YouTube link into a chat, pick video (MP4/MKV), audio (MP3/M4A), or thumbnail, and get the file delivered straight to Telegram or via a direct download link — no YouTube Premium, no third-party services, no data leaving your server.

Run it anywhere with **Docker** in minutes, or install it manually with pip. The bot handles cookies, duplicate detection, auto-cleanup, subtitle embedding, smart-TV audio transcoding, and even works as an **inline bot** (`@YourBotName <link>`) in any chat.

[![Python 3.8+](https://img.shields.io/badge/Python-3.8+-blue?logo=python&logoColor=white)](https://python.org)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Telegram Bot API](https://img.shields.io/badge/Telegram-Bot%20API-2CA5E0?logo=telegram&logoColor=white)](https://core.telegram.org/bots/api)
[![yt-dlp](https://img.shields.io/badge/Downloader-yt--dlp-2CA5E0?logo=youtube)](https://github.com/yt-dlp/yt-dlp)
[![Docker](https://img.shields.io/badge/Deploy-Docker-2496ED?logo=docker&logoColor=white)](https://www.docker.com/)

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

## 📚 Table of Contents

- [📚 Documentation](#-documentation)
- [✨ Features](#-features)
- [🐳 Docker Deployment (Recommended)](#-docker-deployment-recommended)
- [📋 Prerequisites](#-prerequisites)
- [📦 Quick Install (Manual)](#-quick-install-manual)
- [⚙️ Configuration](#️-configuration)
- [📱 Usage & Commands](#-usage--commands)
- [🔒 HTTPS Options](#-https-options)
- [🗂️ Project Structure](#️-project-structure)
- [🔄 CI/CD Pipeline](#-cicd-pipeline)
- [🧪 Running Tests](#-running-tests)
- [🛡️ Security Notes](#️-security-notes)
- [🤝 Contributing](#-contributing)
- [📄 License](#-license)
- [🙏 Acknowledgements](#-acknowledgements)

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
| **[🤝 CONTRIBUTING.md](./CONTRIBUTING.md)** | How to contribute: PR process, code style, commit guidelines |
| **[🔒 SECURITY.md](./SECURITY.md)** | Vulnerability reporting and operator hardening checklist |

---

## ✨ Features

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
- 📱 **4-Button Main Menu** — Streamlined layout: My Downloads, Cookies, Quick Settings, Help
- ⚙️ **Quick Settings** — Consolidated settings screen: change all 7 settings from one place, no back-and-forth
- 🧭 **Smart Back-Stack** — Per-message navigation history, Back returns to where you came from
- 🌐 **Built-in File Server** — No separate HTTP server needed (aiohttp)
- 🔒 **Native HTTPS** — TLS termination without reverse proxy (optional)
- 🔄 **Cloudflare Warp Proxy** — Route downloads through Warp (optional)
- 📺 **Smart TV Audio Fix** — Automatic Opus → AAC transcode for universal codec support
- 📱 **Inline Mode** — Use `@YourBotName <link>` in any chat
- 🔗 **Deep Link Tokens** — Share downloads via `t.me/YourBot?start=dl_<token>`
- 🔐 **Privacy** — No sensitive data in logs, cookies in RAM only
- 📦 **Self-Contained** — Single process runs bot + file server + downloads

---

## 🐳 Docker Deployment (Recommended)

Using **Docker** is the easiest way to run the bot on a server — no need to install Python, FFmpeg, or any dependencies manually.

### Quick Start (build locally)

```bash
# 1. Clone the repo
git clone https://github.com/HoomanJCode/Telegram_Yt_Bot.git
cd Telegram_Yt_Bot

# 2. Create your .env file
cp .env.example .env
nano .env   # Edit with your BOT_TOKEN and BASE_DOWNLOAD_LINK

# 3. Build and run
docker compose up -d
```

### Quick Start (use pre-built image)

```bash
# 1. Pull the latest image
docker pull ghcr.io/hoomanjcode/telegram_yt_bot:latest

# 2. Create your .env file
mkdir -p telegram-yt-bot && cd telegram-yt-bot
cat > .env << EOF
BOT_TOKEN=your_bot_token_here
BASE_DOWNLOAD_LINK=http://your-server-ip:8000
STORAGE_DAYS=2
EOF

# 3. Run
docker compose up -d
```

### Useful Commands

```bash
docker compose up -d        # Start in background
docker compose down         # Stop the bot
docker compose logs -f      # Watch live logs
docker compose restart      # Restart the bot
docker compose pull         # Pull latest pre-built image
docker compose build        # Rebuild from source (local build)
```

### What You Need on Your Server

- [Docker](https://docs.docker.com/engine/install/) installed
- [Docker Compose](https://docs.docker.com/compose/install/) (usually included with Docker)

---

## 📋 Prerequisites

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

## 📦 Quick Install (Manual)

```bash
# 1. Clone and enter the project
git clone https://github.com/HoomanJCode/Telegram_Yt_Bot.git
cd Telegram_Yt_Bot

# 2. Create virtual environment
python3 -m venv venv
source venv/bin/activate

# 3. Install dependencies
pip install -r requirements.txt
pip install yt-dlp-ejs

# 4. Configure environment
cp .env.example .env
# Edit .env with your bot token and settings

# 5. Create required directories
mkdir -p data downloads

# 6. Run
python bot.py
```

> **For production deployment** (systemd service, CI/CD, SSL), see the [Deployment Guide](./docs/DEPLOYMENT.md).

---

## ⚙️ Configuration

All settings are configured via **environment variables** (see [`.env.example`](./.env.example) for the complete annotated template).

| Variable | Description | Default |
|----------|-------------|---------|
| `BOT_TOKEN` | Telegram Bot API token | **Required** |
| `BASE_DOWNLOAD_LINK` | Server URL for download links | `http://localhost:8000` |
| `WHITELIST_USERS` | Comma-separated authorized user IDs | Empty (all allowed) |
| `ADMIN_USERS` | Comma-separated IDs allowed to upload cookies | Empty (all whitelisted) |
| `STORAGE_DAYS` | Days before files auto-delete | `2` |

[→ Full configuration reference](./docs/CONFIGURATION.md)

---

## 📱 Usage & Commands

### Main Menu

After `/start`, you see a clean **4-button main menu**:

```
📹 My Downloads (0)     — View and manage past downloads
🍪 Upload               — Upload cookies.txt (shows ✅ Active when set; also shows status toast)
⚙️ Quick Settings        — All settings in one screen (video/audio quality, subs, delivery, …)
❓ Help / Commands       — Quick reference
```

### Basic Download Flow

1. **Upload Cookies** — Tap `🍪 Upload Cookies` or send `/cookies` (required first step)
2. **Send YouTube Link** — Paste any YouTube URL
3. **Choose Format** — Video (MKV / MP4) / Audio (MP3 / M4A) / Thumbnail
4. **Choose Delivery** — Telegram upload or download link

### Commands

| Command | Description |
|---------|-------------|
| `/start` | Welcome message + 4-button main menu |
| `/help` | Quick command reference + main menu |
| `/cookies` | Upload YouTube cookies file (also via 🍪 button) |
| `/recent` | View recent downloads (also via 📹 button) |
| `/settings` | Open Quick Settings screen (also via ⚙️ button) |
| `/status` | Check bot health and proxy status |
| `/cancel` | Cancel current operation |

[→ Full usage guide](./docs/USAGE.md)

---

## 🔒 HTTPS Options

Four ways to serve download links over HTTPS:

| Method | Description | Guide |
|--------|-------------|-------|
| **Shared Caddy (recommended)** | Automatic Let's Encrypt, zero config, multi-app | [Guide](./docs/CADDY.md) |
| **Cloudflare Tunnel** | No open ports, fully managed TLS | [Guide](./docs/SSL_CLOUDFLARE.md#approach-1-cloudflare-tunnel-recommended) |
| **Reverse Proxy** | Nginx/Caddy + Cloudflare proxied DNS | [Guide](./docs/SSL_CLOUDFLARE.md#approach-2-proxied-dns--reverse-proxy) |
| **Native HTTPS** | Bot terminates TLS itself (Origin CA) | [Guide](./docs/SSL_CLOUDFLARE.md#approach-3-native-https-with-origin-ca) |

### Automatic HTTPS via Shared Caddy (Default)

The deploy workflow handles everything automatically:

1. Set `BASE_DOWNLOAD_LINK=https://yt.yourdomain.com` (a **GitHub Secret**)
2. Point DNS for `yt.yourdomain.com` to your VPS
3. Deploy — Caddy auto-provisions the certificate

The domain is derived from `BASE_DOWNLOAD_LINK` (skipped for `http://` URLs). If the shared Caddy isn't running on the VPS yet, it is created on first deploy. Multiple apps on the same VPS share one Caddy — see [docs/CADDY.md](./docs/CADDY.md) for the full integration guide.

---

## 🗂️ Project Structure

```
Telegram_Yt_Bot/
├── bot.py                  # Entry point (calls app.main())
├── config.py               # Configuration parser (env vars)
├── requirements.txt        # Python dependencies
├── Dockerfile              # Docker image build
├── docker-compose.yml      # Docker Compose config
├── .env.example            # Environment variable template
├── README.md               # This file
│
├── .github/workflows/
│   ├── ci.yml              # Tests (on push/PR)
│   └── release.yml         # Build image + Release + Deploy (on tag)
│
├── app/                    # Main application package
│   ├── __init__.py         # Bootstrap: logging, wiring, main()
│   ├── bot.py              # YouTubeDownloaderBot (central state)
│   ├── downloader.py       # yt-dlp download functions
│   ├── fileserver.py       # aiohttp async file server
│   ├── models.py           # VideoRecord data class
│   └── utils.py            # Utilities, constants, error classification
│
├── app/handlers/           # Telegram update handlers
│   ├── commands.py         # Slash commands (/start, /help, etc.)
│   ├── cookies.py          # Cookie upload conversation
│   ├── formats.py          # Format choice & delivery keyboards
│   ├── inline.py           # Inline query mode (@botname)
│   ├── messages.py         # Plain-text YouTube link processing
│   ├── navigation.py       # Menu system & settings UI
│   └── tokens.py           # Deep-link tokens & file delivery
│
├── docs/                   # Documentation
├── tests/                  # Unit tests
├── data/                   # Persistent state (gitignored)
└── downloads/              # Downloaded media files (gitignored)
```

---

## 🔄 CI/CD Pipeline

### Tests (on every push/PR)

- Python unit tests via `unittest`

### Release (on tag push `v*`)

1. **Run tests** — ensures code is working
2. **Build Docker image** → pushed to [GitHub Container Registry](https://github.com/HoomanJCode/Telegram_Yt_Bot/pkgs/container/telegram_yt_bot) (public)
3. **GitHub Release** → created with changelog and pull commands
4. **Deploy to VPS** → auto-deploys via Docker (if secrets configured)

### How to release

```bash
git tag v0.1.0
git push origin v0.1.0
```

The pipeline will test, build, release, and deploy automatically.

### VPS Secrets (optional)

**Step 1: Generate SSH key on your VPS**

```bash
ssh-keygen -t ed25519 -C "github-deploy" -f ~/.ssh/github_deploy_key -N ""
cat ~/.ssh/github_deploy_key.pub >> ~/.ssh/authorized_keys
chmod 600 ~/.ssh/authorized_keys
```

**Step 2: Add secrets in GitHub**

Go to **Settings → Secrets and variables → Actions** and add:

| Secret | Value |
|--------|-------|
| `VPS_HOST` | Your server IP |
| `VPS_SSH_PRIVATE_KEY` | Output of `cat ~/.ssh/github_deploy_key` (the **private** key) |
| `BOT_TOKEN` | Telegram bot token |
| `BASE_DOWNLOAD_LINK` | Public URL for download links |
| `WHITELIST_USERS` | Comma-separated user IDs (optional) |
| `ADMIN_USERS` | Comma-separated admin IDs (optional) |

> ⚠️ The `VPS_SSH_PRIVATE_KEY` must be the **private** key, not the public key. Include the full `-----BEGIN...` and `-----END...` lines.

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

This project is licensed under the [**MIT License**](./LICENSE) — free to use, modify, and distribute with attribution.

> ⚠️ **Note:** This project was created as an **educational exercise** and is not intended for production deployment. Downloading YouTube videos may violate YouTube's Terms of Service — respect content creators' rights and all applicable laws and terms of service.

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