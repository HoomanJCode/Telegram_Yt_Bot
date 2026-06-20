import os
from dotenv import load_dotenv

load_dotenv()

def _env_bool(key, default='false'):
    """Parse an env var as bool. Truthy = 1/true/yes/on (case-insensitive)."""
    return os.getenv(key, default).strip().lower() in ('1', 'true', 'yes', 'on')

class Config:
    BOT_TOKEN = os.getenv('BOT_TOKEN', 'YOUR_BOT_TOKEN_HERE')
    BASE_DOWNLOAD_LINK = os.getenv('BASE_DOWNLOAD_LINK', 'http://your-server-ip:8000')
    WHITELIST_USERS = os.getenv('WHITELIST_USERS', '')
    DOWNLOAD_DIR = 'downloads'
    MAX_TELEGRAM_FILE_SIZE = 50 * 1024 * 1024  # 50MB
    STORAGE_DAYS = int(os.getenv('STORAGE_DAYS', '2'))
    COOKIE_TTL_HOURS = int(os.getenv('COOKIE_TTL_HOURS', '0'))  # 0 = until restart
    # Route yt-dlp traffic through Cloudflare Warp at 127.0.0.1:40000.
    # When False (default) yt-dlp connects directly and the Warp fallback
    # retry logic in app/downloader.py is skipped entirely.
    # NOTE: the truthy set below is mirrored by the shell case normalization
    # in .github/workflows/deploy.yml — keep both in sync.
    USE_WARP = _env_bool('USE_WARP', 'false')
    
    @classmethod
    def get_whitelist(cls):
        if not cls.WHITELIST_USERS:
            return set()
        return set(int(uid.strip()) for uid in cls.WHITELIST_USERS.split(',') if uid.strip())