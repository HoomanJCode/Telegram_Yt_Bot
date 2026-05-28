# YouTube Video Downloader Telegram Bot

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

This Telegram bot demonstrates the integration of:
- Telegram Bot API with `python-telegram-bot`
- YouTube video information extraction with `yt-dlp`
- User data persistence and cookie management
- Interactive conversation handling
- File serving via HTTP

**Development Methodology:** This project was created using **Vibe Coding** - an AI-assisted development approach where code is generated through natural language interaction with DeepSeek AI. It showcases how modern AI tools can assist in rapid prototyping and educational code development.

---

## 🚀 Features

- 📥 Download YouTube videos (educational demonstration)
- 🍪 Cookie-based authentication management
- 📤 Two sharing methods:
  - Direct Telegram upload (videos up to 50MB)
  - Download link generation
- ⚙️ User-specific settings and preferences
- 👥 Whitelist system for access control
- 🔒 Local cookie storage per user
- 📱 Interactive inline keyboard interface
- 📊 Download progress tracking

---

## 📋 Prerequisites

### System Requirements
- Python 3.8 or higher
- Linux/macOS/Windows
- Internet connection
- Telegram account

### Required Python Packages
```bash
python-telegram-bot==20.7
yt-dlp==2024.3.10
python-dotenv==1.0.0
```

### Telegram Requirements
- A Telegram Bot Token (obtain from [@BotFather](https://t.me/BotFather))
- Your Telegram User ID (for whitelist)
- YouTube cookies file (exported from browser)

---

## 📦 Installation

### Step 1: Clone or Download the Project

```bash
git clone <repository-url>
cd youtube_downloader_bot
```

### Step 2: Create Virtual Environment (Recommended)

```bash
# Linux/macOS
python3 -m venv venv
source venv/bin/activate

# Windows
python -m venv venv
venv\Scripts\activate
```

### Step 3: Install Dependencies

```bash
pip install -r requirements.txt
```

### Step 4: Create Required Directories

```bash
mkdir downloads
```

The `downloads` directory will store downloaded files (educational demo only).

---

## ⚙️ Configuration

### Environment Variables

Create or edit the `.env` file in the project root:

```env
# Required: Your Telegram Bot Token from @BotFather
BOT_TOKEN=123456789:ABCdefGHIjklMNOpqrsTUVwxyz

# Required: Your server's public IP/domain for download links
BASE_DOWNLOAD_LINK=http://your-server-ip:8000

# Optional: Comma-separated list of authorized user IDs
# Leave empty to allow all users
WHITELIST_USERS=123456789,987654321
```

### Configuration Options

| Variable | Description | Required | Default |
|----------|-------------|----------|---------|
| `BOT_TOKEN` | Telegram Bot API token | Yes | - |
| `BASE_DOWNLOAD_LINK` | Server URL for download links | Yes | `http://localhost:8000` |
| `WHITELIST_USERS` | Authorized Telegram user IDs | No | Empty (all allowed) |

### Getting Your Telegram User ID

1. Start a chat with [@userinfobot](https://t.me/userinfobot)
2. Send any message
3. The bot will reply with your User ID

---

## 🚀 Running the Bot

### Start the Telegram Bot

```bash
python bot.py
```

### Start the File Server (in another terminal)

```bash
python serve_files.py
```

### For Production (Optional)

Use systemd service (Linux) or PM2 (cross-platform) to keep the bot running:

**PM2 Example:**
```bash
npm install -g pm2
pm2 start bot.py --interpreter python3
pm2 start serve_files.py --interpreter python3
pm2 save
```

---

## 📱 Bot Commands

| Command | Description |
|---------|-------------|
| `/start` | Display welcome message and main menu |
| `/download` | Start video download process |
| `/cookies` | Upload YouTube cookies file |
| `/settings` | Configure sharing preferences |
| `/help` | Show help and usage information |
| `/cancel` | Cancel current operation |

---

## 🎮 How to Use

### 1. **Start the Bot**
Send `/start` to your bot on Telegram. You'll see a welcome message with the main menu.

### 2. **Upload Cookies** (Required)
Before downloading any video, you must upload cookies:

1. Export YouTube cookies from your browser:
   - **Chrome/Edge**: Use [Get cookies.txt LOCALLY](https://chrome.google.com/webstore/detail/get-cookiestxt-locally/cclelndahbckbenkjhflpdbgdldlbecc) extension
   - **Firefox**: Use [cookies.txt](https://addons.mozilla.org/en-US/firefox/addon/cookies-txt/) extension
   
2. Send `/cookies` command to the bot
3. Read and acknowledge the security warning
4. Upload the `.txt` cookies file

**⚠️ Security Warning:** Cookies contain sensitive login information. Only use this feature if you understand the risks. The developers are not responsible for any security issues.

### 3. **Download a Video**
1. Send `/download` command
2. Paste a YouTube video URL
3. Wait for the bot to process
4. Receive the video via your preferred method

### 4. **Configure Settings**
Send `/settings` to choose your default sharing method:

- **Download Link** 📎: Get a direct HTTP link to download
- **Telegram Upload** 📤: Receive video directly in chat (max 50MB)

### 5. **Access Downloaded Files**
Files are available at: `http://your-server-ip:8000/downloads/`

---

## 🔧 Project Structure

```
youtube_downloader_bot/
├── bot.py                 # Main bot application
├── config.py              # Configuration handler
├── serve_files.py         # HTTP file server
├── requirements.txt       # Python dependencies
├── .env                   # Environment variables
├── README.md             # Documentation
├── user_cookies.json     # Stored cookie paths
├── user_settings.json    # User preferences
└── downloads/            # Downloaded files directory
```

---

## 🛡️ Security Considerations

### Risks
- **Cookie Security**: Uploaded cookies contain session data
- **File Access**: Download links are publicly accessible
- **Data Privacy**: User data stored locally on server

### Best Practices
1. Run behind HTTPS for production use
2. Implement file access expiration
3. Regularly clean up downloaded files
4. Use firewall to restrict file server access
5. Never share your `.env` file
6. Monitor bot usage and logs

### Disclaimer of Liability
```
THIS SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND.
THE AUTHORS ASSUME NO RESPONSIBILITY FOR:
- Violation of YouTube Terms of Service
- Copyright infringement
- Cookie/data security breaches
- Any damages or legal issues arising from use
```

---

## 🐛 Troubleshooting

### Common Issues

**Bot not responding:**
- Check if the bot is running
- Verify `BOT_TOKEN` is correct
- Ensure internet connectivity

**Download fails:**
- Verify cookies are uploaded and valid
- Check YouTube URL format
- Ensure yt-dlp is updated: `pip install --upgrade yt-dlp`

**File server not accessible:**
- Check if server is running
- Verify firewall settings
- Confirm `BASE_DOWNLOAD_LINK` configuration

**File too large for Telegram:**
- Videos over 50MB automatically switch to download link
- Use download link for large files

---

## 📝 Educational Objectives

This project demonstrates:

1. **Telegram Bot Development**
   - Conversation handlers
   - Inline keyboards
   - User state management

2. **Python Programming**
   - Async/await patterns
   - File I/O operations
   - JSON data persistence

3. **Web Integration**
   - HTTP file serving
   - API integration
   - Cookie management

4. **Software Engineering**
   - Configuration management
   - Error handling
   - User data storage

---

## 🤖 Vibe Coding with DeepSeek

This entire project was created through **Vibe Coding** - a collaborative development approach between human direction and AI assistance using DeepSeek. The methodology involved:

- **Natural Language Specification**: Features and requirements described in plain English
- **AI Code Generation**: DeepSeek generated the complete codebase
- **Iterative Refinement**: Multiple rounds of improvements based on feedback
- **Educational Focus**: Code structured for learning and understanding

This demonstrates how AI tools can accelerate educational software development while maintaining code quality and best practices.

---

## 📄 License

This project is created for **educational purposes only**. 

- Code can be used for learning and educational demonstrations
- Not intended for production deployment
- No warranties or guarantees provided
- Respect all applicable laws and terms of service

---

## 🤝 Contributing

This is an educational project. Feel free to:
- Fork for learning purposes
- Experiment with modifications
- Study the code structure
- Share educational insights

---

## ⚠️ Final Disclaimer

**This bot is a programming demonstration.** 

- The developers do not endorse or encourage violating any platform's Terms of Service
- Users assume all responsibility for their actions
- This code is provided for educational study only
- Respect intellectual property and content creators' rights

---

**Built with ❤️ using Vibe Coding & DeepSeek AI**  
*For educational purposes only*