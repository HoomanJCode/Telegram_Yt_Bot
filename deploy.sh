#!/bin/bash
set -e

# ============================================================
# YouTube Downloader Bot - Deploy Script
# Run by GitHub Actions or manually on VPS
# ============================================================

APP_DIR="/root/Telegram_Yt_Bot"
SERVICE_NAME="yt-bot"
VENV_DIR="$APP_DIR/venv"
LOG_FILE="/var/log/yt_bot.log"

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

echo -e "${GREEN}========================================${NC}"
echo -e "${GREEN}  YouTube Bot Deploy${NC}"
echo -e "${GREEN}========================================${NC}"

# -----------------------------------------------------------
# 1. Install system dependencies
# -----------------------------------------------------------
echo -e "${YELLOW}[1/5] System dependencies...${NC}"

if [ -f /etc/debian_version ]; then
    apt-get update -qq
    apt-get install -y -qq python3 python3-venv python3-pip ffmpeg curl 2>/dev/null
fi

# Deno
if ! command -v deno &>/dev/null; then
    curl -fsSL https://deno.land/install.sh | sh
    grep -q "deno/bin" ~/.bashrc || echo 'export PATH="$HOME/.deno/bin:$PATH"' >> ~/.bashrc
fi
export PATH="$HOME/.deno/bin:$PATH"

echo -e "${GREEN}  ✓ Done${NC}"

# -----------------------------------------------------------
# 2. Python environment
# -----------------------------------------------------------
echo -e "${YELLOW}[2/5] Python environment...${NC}"

cd "$APP_DIR"
[ ! -d "$VENV_DIR" ] && python3 -m venv "$VENV_DIR"
source "$VENV_DIR/bin/activate"
pip install --upgrade pip -q
pip install -r requirements.txt -q
pip install --upgrade yt-dlp yt-dlp-ejs -q

echo -e "${GREEN}  ✓ Done${NC}"
# -----------------------------------------------------------
# 3. Configuration
# -----------------------------------------------------------
echo -e "${YELLOW}[3/5] Configuration...${NC}"

mkdir -p "$APP_DIR/data/cookies" "$APP_DIR/downloads"

# Get server IP as fallback
IP=$(hostname -I | awk '{print $1}')

# Use secrets if provided, otherwise keep existing .env
if [ -n "$BOT_TOKEN" ]; then
    cat > "$APP_DIR/.env" << EOF
BOT_TOKEN=${BOT_TOKEN}
BASE_DOWNLOAD_LINK=${BASE_URL:-http://${IP}:8000}
WHITELIST_USERS=${WHITELIST_USERS:-}
STORAGE_DAYS=${STORAGE_DAYS:-2}
EOF
    echo -e "${GREEN}  ✓ .env created from secrets${NC}"
elif [ ! -f "$APP_DIR/.env" ]; then
    cat > "$APP_DIR/.env" << EOF
BOT_TOKEN=your_bot_token_here
BASE_DOWNLOAD_LINK=http://${IP}:8000
WHITELIST_USERS=
STORAGE_DAYS=2
EOF
    echo -e "${RED}  ⚠ Created .env - EDIT IT: nano $APP_DIR/.env${NC}"
else
    echo -e "${GREEN}  ✓ .env exists${NC}"
fi

# Open firewall port
PORT=$(echo "${BASE_URL:-http://${IP}:8000}" | grep -oP ':\K\d+')
PORT=${PORT:-8000}
if command -v ufw &>/dev/null; then
    ufw allow "$PORT" 2>/dev/null || true
fi

echo -e "${GREEN}  ✓ Done${NC}"

# -----------------------------------------------------------
# 4. Systemd service
# -----------------------------------------------------------
echo -e "${YELLOW}[4/5] Systemd service...${NC}"

cat > "/etc/systemd/system/${SERVICE_NAME}.service" << EOF
[Unit]
Description=YouTube Downloader Telegram Bot
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory=$APP_DIR
Environment=PATH=$VENV_DIR/bin:/usr/local/bin:/usr/bin:/bin:/root/.deno/bin
ExecStart=$VENV_DIR/bin/python bot.py
Restart=always
RestartSec=10
StandardOutput=append:$LOG_FILE
StandardError=append:$LOG_FILE

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable "$SERVICE_NAME" 2>/dev/null || true

echo -e "${GREEN}  ✓ Done${NC}"

# -----------------------------------------------------------
# 5. Restart
# -----------------------------------------------------------
echo -e "${YELLOW}[5/5] Restarting bot...${NC}"

systemctl stop "$SERVICE_NAME" 2>/dev/null || true
sleep 2
systemctl start "$SERVICE_NAME"
sleep 3

if systemctl is-active --quiet "$SERVICE_NAME"; then
    echo -e "${GREEN}========================================${NC}"
    echo -e "${GREEN}  ✅ Bot running!${NC}"
    echo -e "${GREEN}  systemctl status $SERVICE_NAME${NC}"
    echo -e "${GREEN}========================================${NC}"
else
    echo -e "${RED}========================================${NC}"
    echo -e "${RED}  ❌ Failed!${NC}"
    journalctl -u "$SERVICE_NAME" -n 20 --no-pager
    echo -e "${RED}========================================${NC}"
    exit 1
fi