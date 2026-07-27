# Deployment Guide

> **Deploy the Telegram YouTube Downloader Bot to a VPS**

---

## Table of Contents

- [Prerequisites](#prerequisites)
- [Option 1: Manual Deployment (deploy.sh)](#option-1-manual-deployment-deploysh)
- [Option 2: GitHub Actions CI/CD](#option-2-github-actions-cicd)
- [Option 3: Manual Step-by-Step](#option-3-manual-step-by-step)
- [Systemd Service Management](#systemd-service-management)
- [Firewall Configuration](#firewall-configuration)
- [Post-Deployment Checklist](#post-deployment-checklist)
- [Updating the Bot](#updating-the-bot)

---

## Prerequisites

- **VPS** with Ubuntu 20.04+ or Debian 11+
- **Domain name** pointed to your VPS (optional, for HTTPS)
- **Telegram Bot Token** from [@BotFather](https://t.me/BotFather)
- **Python 3.8+**, `ffmpeg`, `git`

---

## Option 1: Manual Deployment (deploy.sh)

The project includes a deployment script that automates the entire setup:

```bash
# As root, run:
bash deploy.sh master

# The script will:
# 1. Install system dependencies (python3, ffmpeg, curl, git)
# 2. Install Deno JavaScript runtime (for yt-dlp)
# 3. Clone/pull the repository to /opt/TelegramYtBot
# 4. Set up a Python virtual environment
# 5. Install Python dependencies
# 6. Create a .env file (you MUST edit it with your token)
# 7. Create required directories
# 8. Install and start the systemd service
```

After the script finishes:

```bash
# Edit the .env with your bot token
nano /opt/TelegramYtBot/.env

# Restart the bot
systemctl restart telegramytbot

# Check status
systemctl status telegramytbot
```

---

## Option 2: GitHub Actions CI/CD

### Prerequisites

- GitHub repository with the code pushed
- VPS with SSH key access
- GitHub Secrets configured

### GitHub Secrets to Configure

Go to your repository → **Settings** → **Secrets and variables** → **Actions** → Add these secrets:

| Secret | Description |
|--------|-------------|
| `VPS_SSH_PRIVATE_KEY` | Private SSH key for VPS access |
| `VPS_HOST` | VPS IP address or hostname |
| `VPS_USER` | SSH username (usually `root`) |
| `BOT_TOKEN` | Telegram Bot API token |
| `BASE_DOWNLOAD_LINK` | Public URL for file downloads |
| `WHITELIST_USERS` | Comma-separated allowed Telegram user IDs |
| `USE_WARP` | `true`/`false` for Cloudflare Warp proxy |
| `ADMIN_USERS` | Comma-separated admin Telegram IDs |
| `MIN_DISK_FREE_MB` | Minimum free disk space in MB |
| `MAX_COMMENTS` | Number of YouTube comments to fetch |
| `SSL_CERT_B64` | (Optional) base64-encoded SSL certificate |
| `SSL_KEY_B64` | (Optional) base64-encoded SSL private key |

### SSL Certificates via CI/CD

If using native HTTPS (`SSL_CERT_FILE`/`SSL_KEY_FILE`), encode your certificates as base64 and store them as secrets:

```bash
# On your VPS where the certs are
base64 -w0 /etc/letsencrypt/live/yourdomain.com/fullchain.pem
# Copy the output → paste as SSL_CERT_B64 in GitHub Secrets

base64 -w0 /etc/letsencrypt/live/yourdomain.com/privkey.pem
# Copy the output → paste as SSL_KEY_B64 in GitHub Secrets
```

The deploy workflow will:

1. Decode the secrets to `/opt/TelegramYtBot/ssl/cert.pem` and `ssl/key.pem`
2. Append `SSL_CERT_FILE` / `SSL_KEY_FILE` to `.env`

### Triggering a Deployment

- **Automatic**: Every push to the `master` branch
- **Manual**: Go to **Actions** → **Deploy TelegramYtBot to VPS** → **Run workflow**

---

## Option 3: Manual Step-by-Step

```bash
# 1. Clone the repository
git clone https://github.com/yourusername/Telegram_Yt_Bot.git /opt/TelegramYtBot
cd /opt/TelegramYtBot

# 2. Install system dependencies
apt-get update
apt-get install -y python3 python3-pip python3-venv ffmpeg curl git

# 3. Install Deno (required for yt-dlp)
curl -fsSL https://deno.land/install.sh | sh
export PATH="$HOME/.deno/bin:$PATH"
echo 'export PATH="$HOME/.deno/bin:$PATH"' >> ~/.bashrc

# 4. Set up Python virtual environment
python3 -m venv venv
source venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
pip install --upgrade yt-dlp yt-dlp-ejs

# 5. Create .env file
cp env.example .env
nano .env

# 6. Create required directories
mkdir -p data downloads

# 7. Run the bot (for testing)
python bot.py

# 8. Install as a systemd service (for production)
# Copy the service definition from deploy.sh or create manually
```

---

## Systemd Service Management

### Service File

The `deploy.sh` script creates `/etc/systemd/system/telegramytbot.service`:

```ini
[Unit]
Description=TelegramYtBot - YouTube Downloader
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory=/opt/TelegramYtBot
Environment=PATH=/opt/TelegramYtBot/venv/bin:/usr/local/bin:/usr/bin:/bin:/root/.deno/bin
ExecStart=/opt/TelegramYtBot/venv/bin/python /opt/TelegramYtBot/bot.py
Restart=always
RestartSec=10
# Exit code 78 = SSL config error → do NOT auto-restart
RestartPreventExitStatus=78
StandardOutput=append:/var/log/telegramytbot/bot.log
StandardError=append:/var/log/telegramytbot/bot_error.log

[Install]
WantedBy=multi-user.target
```

### Useful Commands

```bash
# View service status
systemctl status telegramytbot

# View live logs
journalctl -u telegramytbot -f

# View last 50 log lines
journalctl -u telegramytbot -n 50 --no-pager

# View file logs
tail -f /var/log/telegramytbot/bot.log
tail -f /var/log/telegramytbot/bot_error.log

# Restart the bot
systemctl restart telegramytbot

# Stop the bot
systemctl stop telegramytbot

# Start the bot
systemctl start telegramytbot

# Disable auto-start
systemctl disable telegramytbot
```

---

## Firewall Configuration

### UFW (Uncomplicated Firewall)

```bash
# Allow SSH
ufw allow 22

# Allow the file server port (if not using Cloudflare Tunnel)
ufw allow 8000

# If using native HTTPS on port 443
ufw allow 443

# Enable the firewall
ufw enable
```

### Cloudflare Tunnel (No Open Ports Needed)

If using [Cloudflare Tunnel](./SSL_CLOUDFLARE.md#approach-1-cloudflare-tunnel-recommended), you do NOT need to open any ports except SSH. The `cloudflared` service creates an outbound-only encrypted tunnel to Cloudflare's edge.

---

## Post-Deployment Checklist

After deployment, verify everything is working:

- [ ] **Bot responds**: Send `/start` to the bot on Telegram
- [ ] **Status command**: Send `/status` — check all probes pass
- [ ] **Cookies**: Upload a cookies.txt file via `/cookies`
- [ ] **Download test**: Send a YouTube link → choose format → get file
- [ ] **File server**: Open the download link in a browser
- [ ] **Logs**: Check `journalctl -u telegramytbot` for errors
- [ ] **Auto-start**: Reboot the VPS and confirm the bot starts automatically

---

## Updating the Bot

### Manual Update

```bash
cd /opt/TelegramYtBot
git pull origin master
source venv/bin/activate
pip install -r requirements.txt
pip install --upgrade yt-dlp yt-dlp-ejs
systemctl restart telegramytbot
```

### CI/CD Update

Simply push to `master` — the GitHub Actions workflow handles everything.

---

**Next:** [SSL with Cloudflare Guide](./SSL_CLOUDFLARE.md) → [Configuration Reference](./CONFIGURATION.md) → [Usage Guide](./USAGE.md)
