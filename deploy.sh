#!/bin/bash

# ============================================
# TelegramYtBot - Manual Deployment Script
# ============================================
# Usage: bash deploy.sh [branch]
# Example: bash deploy.sh master

set -e

BRANCH=${1:-master}
PROJECT_DIR="/opt/TelegramYtBot"
SERVICE_NAME="telegramytbot"
REPO_URL="https://github.com/HoomanJCode/Telegram_Yt_Bot.git"

echo "========================================"
echo "  TelegramYtBot Deployment"
echo "========================================"
echo "📦 Repository: $REPO_URL"
echo "🌿 Branch: $BRANCH"
echo "📁 Directory: $PROJECT_DIR"
echo "========================================"

if systemctl is-active --quiet $SERVICE_NAME; then
    echo "⏹️  Stopping $SERVICE_NAME..."
    systemctl stop $SERVICE_NAME
    sleep 2
fi

if [ -d "$PROJECT_DIR" ]; then
    BACKUP="${PROJECT_DIR}_backup_$(date +%Y%m%d_%H%M%S)"
    cp -r "$PROJECT_DIR" "$BACKUP"
    echo "💾 Backup: $BACKUP"
    ls -dt ${PROJECT_DIR}_backup_* 2>/dev/null | tail -n +4 | xargs -r rm -rf
fi

echo "📦 Installing system dependencies..."
apt-get update -qq
apt-get install -y -qq python3 python3-pip python3-venv ffmpeg curl git

if ! command -v deno &>/dev/null; then
    echo "🔧 Installing Deno..."
    curl -fsSL https://deno.land/install.sh | sh
fi
export PATH="$HOME/.deno/bin:$PATH"

if [ -d "$PROJECT_DIR/.git" ]; then
    echo "📥 Pulling latest code..."
    cd "$PROJECT_DIR"
    git fetch origin
    git reset --hard origin/$BRANCH
else
    echo "📥 Cloning repository..."
    rm -rf "$PROJECT_DIR"
    git clone --branch $BRANCH --single-branch $REPO_URL "$PROJECT_DIR"
    cd "$PROJECT_DIR"
fi

echo "🐍 Setting up Python..."
[ -d venv ] && rm -rf venv
python3 -m venv venv
source venv/bin/activate
pip install --upgrade pip -q
pip install -r requirements.txt -q
pip install --upgrade yt-dlp yt-dlp-ejs -q

if [ ! -f ".env" ]; then
    IP=$(hostname -I | awk '{print $1}')
    cat > .env << EOF
BOT_TOKEN=your_bot_token_here
BASE_DOWNLOAD_LINK=http://${IP}:8000
WHITELIST_USERS=
STORAGE_DAYS=2
EOF
    echo "⚠️  .env created - edit it: nano $PROJECT_DIR/.env"
fi

mkdir -p data/cookies downloads /var/log/$SERVICE_NAME

cat > /etc/systemd/system/$SERVICE_NAME.service << EOF
[Unit]
Description=TelegramYtBot - YouTube Downloader
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory=$PROJECT_DIR
Environment=PATH=$PROJECT_DIR/venv/bin:/usr/local/bin:/usr/bin:/bin:/root/.deno/bin
ExecStart=$PROJECT_DIR/venv/bin/python $PROJECT_DIR/bot.py
Restart=always
RestartSec=10
StandardOutput=append:/var/log/$SERVICE_NAME/bot.log
StandardError=append:/var/log/$SERVICE_NAME/bot_error.log

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable $SERVICE_NAME
systemctl start $SERVICE_NAME

sleep 5

if systemctl is-active --quiet $SERVICE_NAME; then
    echo ""
    echo "✅ TelegramYtBot deployed successfully!"
    echo ""
    echo "📋 Commands:"
    echo "   systemctl status $SERVICE_NAME"
    echo "   journalctl -u $SERVICE_NAME -f"
    echo "   tail -f /var/log/$SERVICE_NAME/bot.log"
    echo "   systemctl restart $SERVICE_NAME"
else
    echo "❌ Failed to start!"
    journalctl -u $SERVICE_NAME -n 20 --no-pager
    exit 1
fi