import os
from dotenv import load_dotenv

load_dotenv()

class Config:
    # Bot Configuration
    BOT_TOKEN = os.getenv('BOT_TOKEN', 'YOUR_BOT_TOKEN_HERE')
    
    # Server Configuration
    BASE_DOWNLOAD_LINK = os.getenv('BASE_DOWNLOAD_LINK', 'http://your-server-ip:8000')
    
    # Whitelist of user IDs (comma-separated in .env)
    WHITELIST_USERS = os.getenv('WHITELIST_USERS', '')
    
    # Download directory
    DOWNLOAD_DIR = 'downloads'
    
    # Maximum file size for Telegram upload (50MB)
    MAX_TELEGRAM_FILE_SIZE = 50 * 1024 * 1024
    
    # File storage duration in days
    STORAGE_DAYS = 2
    
    @classmethod
    def get_whitelist(cls):
        """Get whitelist as set of integers"""
        if not cls.WHITELIST_USERS:
            return set()
        return set(int(uid.strip()) for uid in cls.WHITELIST_USERS.split(',') if uid.strip())