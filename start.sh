#!/bin/bash

################################################################################
# YouTube Downloader Telegram Bot - Setup & Start Script
# 
# This script handles:
# - Virtual environment creation
# - Dependency installation
# - Bot and file server startup
# - Multiple installation options
################################################################################

set -e  # Exit on error

# Color codes for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Configuration
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_DIR="$SCRIPT_DIR/venv"
REQUIREMENTS_FILE="$SCRIPT_DIR/requirements.txt"
BOT_SCRIPT="$SCRIPT_DIR/bot.py"
SERVER_SCRIPT="$SCRIPT_DIR/serve_files.py"
ENV_FILE="$SCRIPT_DIR/.env"

################################################################################
# Helper Functions
################################################################################

print_header() {
    echo -e "${BLUE}===============================================${NC}"
    echo -e "${BLUE}  YouTube Downloader Telegram Bot${NC}"
    echo -e "${BLUE}  Educational Project - Setup & Launcher${NC}"
    echo -e "${BLUE}===============================================${NC}"
    echo ""
}

print_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

print_success() {
    echo -e "${GREEN}[SUCCESS]${NC} $1"
}

print_warning() {
    echo -e "${YELLOW}[WARNING]${NC} $1"
}

print_info() {
    echo -e "${BLUE}[INFO]${NC} $1"
}

check_command() {
    if ! command -v "$1" &> /dev/null; then
        print_error "$1 is not installed. Please install it first."
        return 1
    fi
    return 0
}

################################################################################
# Environment Setup Functions
################################################################################

create_virtual_env() {
    print_info "Creating Python virtual environment..."
    
    if [ -d "$VENV_DIR" ]; then
        print_warning "Virtual environment already exists."
        read -p "Do you want to recreate it? (y/N): " -n 1 -r
        echo
        if [[ $REPLY =~ ^[Yy]$ ]]; then
            rm -rf "$VENV_DIR"
        else
            print_info "Using existing virtual environment."
            return 0
        fi
    fi
    
    python3 -m venv "$VENV_DIR"
    print_success "Virtual environment created at: $VENV_DIR"
}

install_dependencies() {
    print_info "Installing Python dependencies..."
    
    # Activate virtual environment
    source "$VENV_DIR/bin/activate"
    
    # Upgrade pip
    print_info "Upgrading pip..."
    pip install --upgrade pip
    
    # Install requirements
    print_info "Installing packages from requirements.txt..."
    pip install -r "$REQUIREMENTS_FILE"
    
    print_success "Dependencies installed successfully!"
    
    # Deactivate virtual environment
    deactivate
}

setup_env_file() {
    if [ ! -f "$ENV_FILE" ]; then
        print_warning ".env file not found!"
        print_info "Creating sample .env file..."
        
        cat > "$ENV_FILE" << 'EOF'
# Required: Your Telegram Bot Token from @BotFather
BOT_TOKEN=your_bot_token_here

# Required: Your server's public IP/domain for download links
BASE_DOWNLOAD_LINK=http://your-server-ip:8000

# Optional: Comma-separated list of authorized user IDs
# Leave empty to allow all users
WHITELIST_USERS=
EOF
        
        print_warning "Please edit the .env file with your configuration:"
        print_info "  nano $ENV_FILE"
        echo ""
        print_info "Required configurations:"
        print_info "  1. BOT_TOKEN - Get from @BotFather on Telegram"
        print_info "  2. BASE_DOWNLOAD_LINK - Your server IP/domain"
        print_info "  3. WHITELIST_USERS - (Optional) Your Telegram user ID"
        echo ""
        read -p "Press Enter after editing the .env file..."
    else
        print_success ".env file exists."
    fi
}

create_directories() {
    print_info "Creating required directories..."
    mkdir -p "$SCRIPT_DIR/downloads"
    print_success "Directories created."
}

################################################################################
# Service Management Functions
################################################################################

start_bot() {
    print_info "Starting the Telegram bot..."
    
    # Activate virtual environment
    source "$VENV_DIR/bin/activate"
    
    # Check if .env is configured
    if grep -q "your_bot_token_here" "$ENV_FILE"; then
        print_error "Please configure your BOT_TOKEN in the .env file first!"
        print_info "Edit the file: nano $ENV_FILE"
        deactivate
        return 1
    fi
    
    print_info "Bot is starting... Press Ctrl+C to stop."
    echo ""
    
    # Start bot
    python3 "$BOT_SCRIPT"
    
    # Deactivate when bot stops
    deactivate
}

start_server() {
    print_info "Starting HTTP file server..."
    
    # Activate virtual environment
    source "$VENV_DIR/bin/activate"
    
    print_info "File server is running on http://0.0.0.0:8000"
    print_info "Press Ctrl+C to stop."
    echo ""
    
    # Start file server
    python3 "$SERVER_SCRIPT"
    
    # Deactivate when server stops
    deactivate
}

start_bot_background() {
    print_info "Starting bot in background..."
    
    source "$VENV_DIR/bin/activate"
    
    if grep -q "your_bot_token_here" "$ENV_FILE"; then
        print_error "Please configure your BOT_TOKEN in the .env file first!"
        deactivate
        return 1
    fi
    
    nohup python3 "$BOT_SCRIPT" > "$SCRIPT_DIR/bot.log" 2>&1 &
    BOT_PID=$!
    echo $BOT_PID > "$SCRIPT_DIR/bot.pid"
    
    print_success "Bot started with PID: $BOT_PID"
    print_info "Logs: $SCRIPT_DIR/bot.log"
    
    deactivate
}

start_server_background() {
    print_info "Starting file server in background..."
    
    source "$VENV_DIR/bin/activate"
    
    nohup python3 "$SERVER_SCRIPT" > "$SCRIPT_DIR/server.log" 2>&1 &
    SERVER_PID=$!
    echo $SERVER_PID > "$SCRIPT_DIR/server.pid"
    
    print_success "File server started with PID: $SERVER_PID"
    print_info "Logs: $SCRIPT_DIR/server.log"
    
    deactivate
}

stop_services() {
    print_info "Stopping services..."
    
    if [ -f "$SCRIPT_DIR/bot.pid" ]; then
        BOT_PID=$(cat "$SCRIPT_DIR/bot.pid")
        if kill -0 "$BOT_PID" 2>/dev/null; then
            kill "$BOT_PID"
            print_success "Bot stopped (PID: $BOT_PID)"
        else
            print_warning "Bot is not running."
        fi
        rm "$SCRIPT_DIR/bot.pid"
    fi
    
    if [ -f "$SCRIPT_DIR/server.pid" ]; then
        SERVER_PID=$(cat "$SCRIPT_DIR/server.pid")
        if kill -0 "$SERVER_PID" 2>/dev/null; then
            kill "$SERVER_PID"
            print_success "File server stopped (PID: $SERVER_PID)"
        else
            print_warning "File server is not running."
        fi
        rm "$SCRIPT_DIR/server.pid"
    fi
}

check_status() {
    print_info "Checking service status..."
    
    if [ -f "$SCRIPT_DIR/bot.pid" ]; then
        BOT_PID=$(cat "$SCRIPT_DIR/bot.pid")
        if kill -0 "$BOT_PID" 2>/dev/null; then
            print_success "Bot is running (PID: $BOT_PID)"
        else
            print_warning "Bot is not running."
        fi
    else
        print_warning "Bot is not running."
    fi
    
    if [ -f "$SCRIPT_DIR/server.pid" ]; then
        SERVER_PID=$(cat "$SCRIPT_DIR/server.pid")
        if kill -0 "$SERVER_PID" 2>/dev/null; then
            print_success "File server is running (PID: $SERVER_PID)"
        else
            print_warning "File server is not running."
        fi
    else
        print_warning "File server is not running."
    fi
}

view_logs() {
    if [ -f "$SCRIPT_DIR/bot.log" ]; then
        print_info "Bot logs (last 20 lines):"
        tail -n 20 "$SCRIPT_DIR/bot.log"
    else
        print_warning "No bot logs found."
    fi
    
    echo ""
    
    if [ -f "$SCRIPT_DIR/server.log" ]; then
        print_info "Server logs (last 20 lines):"
        tail -n 20 "$SCRIPT_DIR/server.log"
    else
        print_warning "No server logs found."
    fi
}

################################################################################
# Alternative Installation Methods
################################################################################

install_with_pipx() {
    print_info "Installing with pipx (system-wide isolated environment)..."
    
    if ! check_command pipx; then
        print_error "pipx is not installed."
        print_info "Install it with: sudo apt install pipx"
        return 1
    fi
    
    print_warning "pipx installation is not fully supported for this project."
    print_info "Using virtual environment is recommended."
    print_info "Use option 1 from the main menu instead."
}

install_system_packages() {
    print_info "Installing system packages via apt..."
    print_warning "This method may install older versions of packages."
    
    print_info "Available system packages:"
    apt-cache search python3-telegram-bot 2>/dev/null || print_warning "python3-telegram-bot not found in apt"
    
    print_warning "Using virtual environment is recommended for latest versions."
    print_info "Use option 1 from the main menu instead."
}

################################################################################
# Alternative Start Methods
################################################################################

start_with_pm2() {
    print_info "Starting with PM2 process manager..."
    
    if ! check_command pm2; then
        print_error "PM2 is not installed."
        print_info "Install with: npm install -g pm2"
        return 1
    fi
    
    source "$VENV_DIR/bin/activate"
    
    # Start bot with PM2
    pm2 start "$BOT_SCRIPT" --name "youtube-bot" --interpreter python3
    pm2 start "$SERVER_SCRIPT" --name "youtube-server" --interpreter python3
    
    pm2 save
    
    print_success "Services started with PM2"
    print_info "Commands:"
    print_info "  pm2 status          - View status"
    print_info "  pm2 logs            - View logs"
    print_info "  pm2 stop all        - Stop all services"
    
    deactivate
}

start_with_systemd() {
    print_info "Creating systemd services..."
    print_warning "This requires sudo privileges."
    
    # Create systemd service for bot
    sudo tee /etc/systemd/system/youtube-bot.service > /dev/null << EOF
[Unit]
Description=YouTube Downloader Telegram Bot
After=network.target

[Service]
Type=simple
User=$USER
WorkingDirectory=$SCRIPT_DIR
Environment=PATH=$VENV_DIR/bin:/usr/local/bin:/usr/bin:/bin
ExecStart=$VENV_DIR/bin/python3 $BOT_SCRIPT
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
EOF
    
    # Create systemd service for file server
    sudo tee /etc/systemd/system/youtube-server.service > /dev/null << EOF
[Unit]
Description=YouTube Bot File Server
After=network.target

[Service]
Type=simple
User=$USER
WorkingDirectory=$SCRIPT_DIR
Environment=PATH=$VENV_DIR/bin:/usr/local/bin:/usr/bin:/bin
ExecStart=$VENV_DIR/bin/python3 $SERVER_SCRIPT
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
EOF
    
    sudo systemctl daemon-reload
    
    print_success "Systemd services created!"
    print_info "Commands:"
    print_info "  sudo systemctl start youtube-bot     - Start bot"
    print_info "  sudo systemctl start youtube-server  - Start server"
    print_info "  sudo systemctl enable youtube-bot    - Auto-start on boot"
    print_info "  sudo systemctl status youtube-bot    - Check status"
    
    read -p "Do you want to start services now? (y/N): " -n 1 -r
    echo
    if [[ $REPLY =~ ^[Yy]$ ]]; then
        sudo systemctl start youtube-bot
        sudo systemctl start youtube-server
        print_success "Services started!"
    fi
}

################################################################################
# Quick Start Functions
################################################################################

quick_setup() {
    print_header
    print_info "Quick Setup - This will set up everything automatically"
    echo ""
    
    # Run all setup steps
    create_virtual_env
    install_dependencies
    create_directories
    setup_env_file
    
    echo ""
    print_success "Setup complete! You can now start the bot."
    print_info "Choose option 5 or 6 from the main menu to start services."
    echo ""
}

full_start() {
    print_header
    
    # Check if virtual environment exists
    if [ ! -d "$VENV_DIR" ]; then
        print_error "Virtual environment not found. Run setup first (option 1)."
        return 1
    fi
    
    # Check .env configuration
    if [ ! -f "$ENV_FILE" ] || grep -q "your_bot_token_here" "$ENV_FILE"; then
        print_error "Please configure your .env file first!"
        print_info "Edit the file: nano $ENV_FILE"
        return 1
    fi
    
    print_info "Starting both services..."
    echo ""
    
    # Start services in background
    start_bot_background
    start_server_background
    
    echo ""
    print_success "All services started!"
    print_info "Bot logs: tail -f $SCRIPT_DIR/bot.log"
    print_info "Server logs: tail -f $SCRIPT_DIR/server.log"
}

################################################################################
# Main Menu
################################################################################

show_menu() {
    clear
    print_header
    
    echo "Please select an option:"
    echo ""
    echo -e "${GREEN}Setup Options:${NC}"
    echo "  1) Quick Setup (Recommended for first time)"
    echo "  2) Create Virtual Environment Only"
    echo "  3) Install Dependencies Only"
    echo "  4) Configure .env File"
    echo ""
    echo -e "${BLUE}Start Options:${NC}"
    echo "  5) Start Everything (Background)"
    echo "  6) Start Bot Only (Foreground)"
    echo "  7) Start File Server Only (Foreground)"
    echo ""
    echo -e "${YELLOW}Management Options:${NC}"
    echo "  8) Stop All Services"
    echo "  9) Check Status"
    echo "  10) View Logs"
    echo ""
    echo -e "${RED}Advanced Options:${NC}"
    echo "  11) Install with PM2"
    echo "  12) Install as Systemd Service"
    echo "  13) Alternative Install Methods"
    echo ""
    echo "  0) Exit"
    echo ""
}

################################################################################
# Main Program
################################################################################

main() {
    # Check for Python
    if ! check_command python3; then
        print_error "Python3 is required but not installed."
        print_info "Install with: sudo apt install python3 python3-venv python3-full"
        exit 1
    fi
    
    # Check for pip
    if ! check_command pip3; then
        print_error "pip3 is required but not installed."
        print_info "Install with: sudo apt install python3-pip"
        exit 1
    fi
    
    # Handle command line arguments
    case "${1:-}" in
        --quick-setup)
            quick_setup
            exit 0
            ;;
        --start)
            full_start
            exit 0
            ;;
        --stop)
            stop_services
            exit 0
            ;;
        --status)
            check_status
            exit 0
            ;;
        --logs)
            view_logs
            exit 0
            ;;
        --help|-h)
            echo "Usage: $0 [OPTION]"
            echo ""
            echo "Options:"
            echo "  --quick-setup    Run automated setup"
            echo "  --start          Start all services in background"
            echo "  --stop           Stop all running services"
            echo "  --status         Check service status"
            echo "  --logs           View service logs"
            echo "  --help           Show this help message"
            echo ""
            echo "Run without arguments for interactive menu."
            exit 0
            ;;
    esac
    
    # Interactive menu
    while true; do
        show_menu
        read -p "Enter your choice [0-13]: " choice
        echo ""
        
        case $choice in
            1)
                quick_setup
                ;;
            2)
                create_virtual_env
                ;;
            3)
                if [ ! -d "$VENV_DIR" ]; then
                    print_warning "Virtual environment not found. Creating one first..."
                    create_virtual_env
                fi
                install_dependencies
                ;;
            4)
                setup_env_file
                ;;
            5)
                full_start
                ;;
            6)
                if [ ! -d "$VENV_DIR" ]; then
                    print_error "Virtual environment not found. Run setup first (option 1)."
                else
                    start_bot
                fi
                ;;
            7)
                if [ ! -d "$VENV_DIR" ]; then
                    print_error "Virtual environment not found. Run setup first (option 1)."
                else
                    start_server
                fi
                ;;
            8)
                stop_services
                ;;
            9)
                check_status
                ;;
            10)
                view_logs
                ;;
            11)
                start_with_pm2
                ;;
            12)
                start_with_systemd
                ;;
            13)
                echo "Alternative installation methods:"
                echo ""
                echo "1. Using pipx (isolated environment):"
                echo "   sudo apt install pipx"
                echo "   pipx install python-telegram-bot"
                echo ""
                echo "2. Using --break-system-packages (NOT recommended):"
                echo "   pip install --break-system-packages -r requirements.txt"
                echo ""
                echo "3. Using apt (older versions):"
                echo "   sudo apt search python3-telegram-bot"
                echo ""
                print_warning "Virtual environment is the recommended method."
                ;;
            0)
                print_info "Goodbye!"
                exit 0
                ;;
            *)
                print_error "Invalid option. Please try again."
                ;;
        esac
        
        echo ""
        read -p "Press Enter to continue..."
    done
}

# Run main function
main "$@"