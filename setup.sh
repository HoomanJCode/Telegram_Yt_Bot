#!/bin/bash

# YouTube Downloader Telegram Bot - Setup Script
# This script sets up the virtual environment and installs all dependencies

set -e  # Exit on error

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

echo -e "${BLUE}========================================${NC}"
echo -e "${BLUE}  YouTube Downloader Bot - Setup Script ${NC}"
echo -e "${BLUE}========================================${NC}"
echo ""

# Check Python version
echo -e "${YELLOW}Checking Python installation...${NC}"
if command -v python3 &> /dev/null; then
    PYTHON_VERSION=$(python3 --version 2>&1 | awk '{print $2}')
    echo -e "${GREEN}✓ Python $PYTHON_VERSION found${NC}"
    
    # Check if Python version is 3.8+
    PYTHON_MAJOR=$(echo $PYTHON_VERSION | cut -d. -f1)
    PYTHON_MINOR=$(echo $PYTHON_VERSION | cut -d. -f2)
    
    if [ "$PYTHON_MAJOR" -lt 3 ] || ([ "$PYTHON_MAJOR" -eq 3 ] && [ "$PYTHON_MINOR" -lt 8 ]); then
        echo -e "${RED}✗ Python 3.8 or higher is required${NC}"
        exit 1
    fi
else
    echo -e "${RED}✗ Python 3 not found. Please install Python 3.8+${NC}"
    exit 1
fi

# Check if python3-venv is installed
echo -e "${YELLOW}Checking for python3-venv...${NC}"
if ! dpkg -l | grep -q python3-venv; then
    echo -e "${YELLOW}Installing python3-venv...${NC}"
    sudo apt update
    sudo apt install -y python3-venv python3-full
    echo -e "${GREEN}✓ python3-venv installed${NC}"
else
    echo -e "${GREEN}✓ python3-venv is installed${NC}"
fi

# Create virtual environment
VENV_DIR="venv"
if [ ! -d "$VENV_DIR" ]; then
    echo -e "${YELLOW}Creating virtual environment...${NC}"
    python3 -m venv $VENV_DIR
    echo -e "${GREEN}✓ Virtual environment created in ./$VENV_DIR${NC}"
else
    echo -e "${YELLOW}Virtual environment already exists.${NC}"
    echo -e "${YELLOW}Do you want to recreate it? (y/N)${NC}"
    read -r response
    if [[ "$response" =~ ^[Yy]$ ]]; then
        echo -e "${YELLOW}Removing old virtual environment...${NC}"
        rm -rf $VENV_DIR
        python3 -m venv $VENV_DIR
        echo -e "${GREEN}✓ Virtual environment recreated${NC}"
    fi
fi

# Activate virtual environment
echo -e "${YELLOW}Activating virtual environment...${NC}"
source $VENV_DIR/bin/activate

# Upgrade pip
echo -e "${YELLOW}Upgrading pip...${NC}"
python -m pip install --upgrade pip
echo -e "${GREEN}✓ pip upgraded${NC}"

# Install requirements
echo -e "${YELLOW}Installing Python packages...${NC}"
if [ -f "requirements.txt" ]; then
    pip install -r requirements.txt
    echo -e "${GREEN}✓ All packages installed successfully${NC}"
else
    echo -e "${RED}✗ requirements.txt not found${NC}"
    exit 1
fi

# Create necessary directories
echo -e "${YELLOW}Creating required directories...${NC}"
mkdir -p downloads
echo -e "${GREEN}✓ Downloads directory created${NC}"

# Create .env file if it doesn't exist
if [ ! -f ".env" ]; then
    echo -e "${YELLOW}Creating .env configuration file...${NC}"
    cat > .env << 'EOL'
# Telegram Bot Configuration
BOT_TOKEN=your_bot_token_here
BASE_DOWNLOAD_LINK=http://your-server-ip:8000
WHITELIST_USERS=
EOL
    echo -e "${GREEN}✓ .env file created - Please edit it with your configuration${NC}"
    echo -e "${YELLOW}  Edit .env file and add your BOT_TOKEN from @BotFather${NC}"
else
    echo -e "${GREEN}✓ .env file already exists${NC}"
fi

# Create start script
echo -e "${YELLOW}Creating start scripts...${NC}"

# Bot start script
cat > start_bot.sh << 'EOL'
#!/bin/bash
source venv/bin/activate
echo "Starting YouTube Downloader Bot..."
python bot.py
EOL

# File server start script
cat > start_server.sh << 'EOL'
#!/bin/bash
source venv/bin/activate
echo "Starting File Server on port 8000..."
python serve_files.py
EOL

# Make scripts executable
chmod +x start_bot.sh start_server.sh
echo -e "${GREEN}✓ Start scripts created (start_bot.sh, start_server.sh)${NC}"

# Deactivate virtual environment
deactivate

echo ""
echo -e "${GREEN}========================================${NC}"
echo -e "${GREEN}  Setup Complete! 🎉${NC}"
echo -e "${GREEN}========================================${NC}"
echo ""
echo -e "${YELLOW}Next Steps:${NC}"
echo ""
echo -e "1. ${BLUE}Edit the .env file:${NC}"
echo -e "   ${GREEN}nano .env${NC}"
echo -e "   Add your Telegram Bot Token from @BotFather"
echo -e "   Set your server IP for BASE_DOWNLOAD_LINK"
echo -e "   (Optional) Add whitelisted user IDs"
echo ""
echo -e "2. ${BLUE}Start the bot:${NC}"
echo -e "   ${GREEN}./start_bot.sh${NC}"
echo ""
echo -e "3. ${BLUE}Start the file server (in another terminal):${NC}"
echo -e "   ${GREEN}./start_server.sh${NC}"
echo ""
echo -e "4. ${BLUE}Get your Telegram Bot Token:${NC}"
echo -e "   Chat with @BotFather on Telegram"
echo -e "   Create a new bot with /newbot"
echo ""
echo -e "5. ${BLUE}Get your Telegram User ID:${NC}"
echo -e "   Chat with @userinfobot on Telegram"
echo ""
echo -e "${YELLOW}For manual commands:${NC}"
echo -e "   Activate environment: ${GREEN}source venv/bin/activate${NC}"
echo -e "   Run bot:            ${GREEN}python bot.py${NC}"
echo -e "   Run server:         ${GREEN}python serve_files.py${NC}"
echo -e "   Deactivate:         ${GREEN}deactivate${NC}"
echo ""
echo -e "${RED}⚠️  IMPORTANT: Edit .env before starting the bot!${NC}"
echo ""