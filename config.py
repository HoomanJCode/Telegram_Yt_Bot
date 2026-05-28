import os
from dotenv import load_dotenv

load_dotenv()

class Config:
    BOT_TOKEN = os.getenv('BOT_TOKEN', 'YOUR_BOT_TOKEN_HERE')
    BASE_DOWNLOAD_LINK = os.getenv('BASE_DOWNLOAD_LINK', 'http://your-server-ip:8000')
    WHITELIST_USERS = os.getenv('WHITELIST_USERS', '')
    DOWNLOAD_DIR = 'downloads'
    MAX_TELEGRAM_FILE_SIZE = 50 * 1024 * 1024  # 50MB
    STORAGE_DAYS = 2
    
    @classmethod
    def get_whitelist(cls):
        if not cls.WHITELIST_USERS:
            return set()
        return set(int(uid.strip()) for uid in cls.WHITELIST_USERS.split(',') if uid.strip())