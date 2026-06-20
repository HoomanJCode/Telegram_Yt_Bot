import os
from dotenv import load_dotenv

load_dotenv()

def _env_bool(key, default='false'):
    """Parse an env var as bool. Truthy = 1/true/yes/on (case-insensitive)."""
    return os.getenv(key, default).strip().lower() in ('1', 'true', 'yes', 'on')


def _env_int(key, default):
    """Parse an env var as a positive-or-zero int. Returns `default` on
    missing/empty/non-numeric input instead of raising — config errors must
    never prevent the bot from starting. Negative values are clamped to 0
    (i.e. min_bytes=0 = check disabled), so a fat-fingered `-1` cannot
    silently turn the disk-full check off without leaving a clear intent.
    """
    raw = os.getenv(key)
    if raw is None or raw.strip() == '':
        return default
    try:
        return int(raw.strip())
    except ValueError:
        return default

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
    # Minimum free disk space (in MB) required on DOWNLOADS_DIR's filesystem
    # before starting a download. Below this threshold the bot short-circuits
    # with a `disk_error` message instead of letting yt-dlp hit ENOSPC mid
    # download. Default 1024 MB (1 GB) comfortably covers typical 1080p mux
    # peaks (separate audio + video + fragments + final mp4 coexisting) on a
    # small VPS. Tune up if you monitor ENOSPC probes, tune down for tight
    # disks where occasional mid-download failures are acceptable. Set to 0
    # to disable the pre-flight check entirely (debug only — the operator
    # must own the consequence of an ENOSPC mid-download). Parsed via
    # _env_int so missing/empty/non-numeric values fall back to the default
    # instead of crashing bot startup.
    MIN_DISK_FREE_MB = max(0, _env_int('MIN_DISK_FREE_MB', 1024))
    # NOTE: this value is captured into app.downloader.MIN_DISK_FREE_BYTES
    # at module import time. A bot restart is required for changes to
    # MIN_DISK_FREE_MB to take effect — live-tweaking Config at runtime
    # does not propagate to the pre-flight check.
    
    @classmethod
    def get_whitelist(cls):
        if not cls.WHITELIST_USERS:
            return set()
        return set(int(uid.strip()) for uid in cls.WHITELIST_USERS.split(',') if uid.strip())