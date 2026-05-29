# YouTube Downloader Telegram Bot

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

## 📚 About This Project

This Telegram bot downloads YouTube content in multiple formats (Video, Audio, Thumbnails) with built-in file serving. It demonstrates integration of Telegram Bot API, yt-dlp, async I/O, and HTTP file serving in a single Python application.

**Development Methodology:** Created using **Vibe Coding** - AI-assisted development through natural language interaction with DeepSeek AI.

---

## 🚀 Features

- 🎬 **Video Download** - Full MP4 video
- 🎵 **Audio Download** - MP3 (with FFmpeg) or M4A audio extraction
- 🖼️ **Thumbnail Download** - Video thumbnails without full download
- 🔄 **Multi-Format** - Download all formats of the same video
- 📤 **Two Delivery Methods** - Telegram upload or direct download link
- 💾 **Duplicate Detection** - Prevents re-downloading same content
- 🗑️ **Auto-Cleanup** - Files deleted after configurable days (default: 2)
- 🍪 **Cookie Management** - Per-user cookie storage
- 👥 **Whitelist System** - Restrict bot to specific users
- 📱 **Interactive Menus** - Inline keyboard navigation
- 🌐 **Built-in File Server** - No separate HTTP server needed
- 🔒 **Privacy** - No sensitive data in logs

---

## 📋 Prerequisites

### System Requirements
- Python 3.8+
- Linux (recommended) / macOS / Windows
- FFmpeg (optional, for MP3 audio conversion)
- Deno JavaScript runtime (for yt-dlp YouTube extraction) 
- Telegram Bot Token from [@BotFather](https://t.me/BotFather)

### Install FFmpeg (recommended)
```bash
# Ubuntu/Debian
apt-get install -y ffmpeg

# macOS
brew install ffmpeg

# Without FFmpeg, audio downloads as M4A instead of MP3
```

### Install Deno (required for YouTube)
```bash
curl -fsSL https://deno.land/install.sh | sh
export PATH="$HOME/.deno/bin:$PATH"
echo 'export PATH="$HOME/.deno/bin:$PATH"' >> ~/.bashrc
```

---

## 📦 Installation

### Step 1: Clone and Setup
```bash
git clone <repository-url>
cd youtube_downloader_bot
python3 -m venv venv
source venv/bin/activate
```

### Step 2: Install Dependencies
```bash
pip install -r requirements.txt
pip install yt-dlp-ejs
```

### Step 3: Configure Environment
Edit `.env` file:
```env
BOT_TOKEN=your_bot_token_here
BASE_DOWNLOAD_LINK=http://your-server-ip:8000
WHITELIST_USERS=123456789,987654321
```

### Step 4: Create Required Directories
```bash
mkdir -p data/cookies downloads
```

### Step 5: Run
```bash
python bot.py
```

That's it! File server starts automatically on the port specified in `BASE_DOWNLOAD_LINK`.

---

## ⚙️ Configuration

| Variable | Description | Default |
|----------|-------------|---------|
| `BOT_TOKEN` | Telegram Bot API token | Required |
| `BASE_DOWNLOAD_LINK` | Server URL with port for downloads | `http://localhost:8000` |
| `WHITELIST_USERS` | Comma-separated authorized user IDs | Empty (all allowed) |
| `STORAGE_DAYS` | Days before files auto-delete | 2 |
| `MAX_TELEGRAM_FILE_SIZE` | Max size for Telegram upload (bytes) | 50MB |

---

## 📱 Usage

### Basic Flow
1. **Upload Cookies** - `/cookies` - Required first step
2. **Send YouTube Link** - Just paste any YouTube URL
3. **Choose Format** - Video (MP4) / Audio (MP3/M4A) / Thumbnails
4. **Choose Delivery** - Telegram upload or download link

### Commands

| Command | Description |
|---------|-------------|
| `/start` | Welcome message and main menu |
| `/cookies` | Upload YouTube cookies file |
| `/recent` | View recent downloads |
| `/help` | Help and usage information |

### Format Options
- **🎬 Video (MP4)** - Full video in MP4 format
- **🎵 Audio (MP3/M4A)** - Audio only (MP3 with FFmpeg, M4A without)
- **🖼️ Thumbnails** - Video thumbnails (no full download)

### Download All Formats
After downloading one format, click "Back to formats" to download other formats of the same video. Already downloaded formats show ✅.

---

## 🗂️ Project Structure

```
youtube_downloader_bot/
├── bot.py                 # Main bot + file server
├── config.py              # Configuration handler
├── requirements.txt       # Python dependencies
├── .env                   # Environment variables
├── README.md             # Documentation
├── data/                  # User data storage
│   ├── cookies/          # Per-user cookie files
│   ├── user_cookies.json # Cookie paths
│   └── user_videos.json  # Download records
└── downloads/            # Downloaded files directory
```

---

## 🔧 Troubleshooting

### "No supported JavaScript runtime" warning
```bash
# Install Deno
curl -fsSL https://deno.land/install.sh | sh
export PATH="$HOME/.deno/bin:$PATH"
```

### Audio download fails with FFmpeg error
```bash
# Install FFmpeg
apt-get install -y ffmpeg
# Or audio will download as M4A automatically
```

### 403 Forbidden errors
- Upload fresh cookies (log into YouTube, export again)
- Update yt-dlp: `pip install --upgrade yt-dlp yt-dlp-ejs`

### File server not accessible
- File server runs inside bot (no separate process)
- Check port in `BASE_DOWNLOAD_LINK` matches `.env`
- Ensure firewall allows the port: `ufw allow 8000`

### Single-core VPS optimization
- Bot uses async I/O for Telegram API
- File server runs on daemon thread
- No separate processes needed

---

## 🛡️ Security Notes

- Cookies stored locally per user in `data/cookies/`
- No sensitive data in logs (tokens masked)
- Whitelist system for access control
- Files auto-deleted after configured days
- **Never share your `.env` file or cookies**

---

## 📄 License

Educational project. Code can be used for learning purposes. Not intended for production deployment. Respect all applicable laws and terms of service.

---

**Built with ❤️ using Vibe Coding & DeepSeek AI**  
*For educational purposes only*
```