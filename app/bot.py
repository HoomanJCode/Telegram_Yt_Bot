# app/bot.py
"""Main bot class with shared state"""
import asyncio, os, time, logging
from pathlib import Path
from typing import Dict, List, Tuple
from app.models import VideoRecord
from app.fileserver import FileServer
from app.utils import check_ffmpeg, load_data, save_data

logger = logging.getLogger('yt_bot')

DATA_DIR = Path('data')
DOWNLOADS_DIR = Path('downloads')

# Output extensions yt-dlp writes when a download successfully completes.
# Anything else found in DOWNLOADS_DIR is by definition an orphaned fragment
# or partial and is safe to delete at startup.
_KNOWN_OUTPUT_EXT = {
    '.mp4', '.mkv', '.webm',         # video containers
    '.mp3', '.m4a', '.opus',         # audio containers
    '.jpg', '.webp', '.png',         # thumbnails
    '.srt', '.vtt',                  # subtitles
    '.info.json',                    # yt-dlp metadata sidecar (re-fetched on demand)
}

# yt-dlp fragment / partial extensions removed unconditionally at startup.
_ORPHAN_EXT = {'.ytdl', '.part'}

class YouTubeDownloaderBot:
    def __init__(self):
        from config import Config
        self.config = Config()
        self.base_url = self.config.BASE_DOWNLOAD_LINK.rstrip('/')
        try: port = int(self.base_url.split(':')[-1]) if ':' in self.base_url.split('/')[2] else 8000
        except: port = 8000
        for d in (DATA_DIR, DOWNLOADS_DIR): d.mkdir(parents=True, exist_ok=True)
        self._cookie_file_ids: Dict[int, str] = {}
        self._cookie_data: Dict[int, bytes] = {}
        self._cookie_tmpfiles: Dict[int, str] = {}
        self._user_langs: Dict[int, str] = {}
        self._user_settings: Dict[int, dict] = {}  # uid -> {default_delivery: 'ask'|'telegram'|'link'}
        self._bot = None
        self._bot_username = None
        self.videos: Dict[int, List[VideoRecord]] = {}
        self._pending_urls: Dict[int, tuple] = {}
        self._nav_stack: Dict[int, List[Tuple[str, any]]] = {}
        self._tokens: Dict[str, dict] = {}
        self._group_admins: Dict[int, set] = {}
        self._global_file_ids: Dict[str, str] = {}
        self._download_semaphore = asyncio.Semaphore(1)
        self.has_ffmpeg = check_ffmpeg()
        self.file_server = FileServer(port=port)
        load_data(self)
        self._cleanup_orphans()

    def save(self):
        save_data(self)

    def _cleanup_orphans(self):
        """Enforce the `STORAGE_DAYS` retention that the UI promises.

        Two passes, each run once at startup before any handler fires:

        1. Unconditional sweep — remove any `.ytdl`/`.part` file or anything
           whose name contains `.tmp.`/`.frag.` and any file whose extension
           is not in `_KNOWN_OUTPUT_EXT`.  These are by definition dead
           fragments left behind by a crashed or killed download.
        2. Retention sweep — remove `_KNOWN_OUTPUT_EXT` files whose mtime
           is older than `STORAGE_DAYS` AND that aren't currently pinned in
           `self.videos`.  This keeps the `2d retention` promise honest even
           when `bot.videos` never hit the 20-item cap.

        Logs the bytes freed so an operator can spot a runaway disk.
        """
        if not DOWNLOADS_DIR.exists():
            return
        pinned = set()
        for records in self.videos.values():
            for r in records:
                try:
                    pinned.add(os.path.normcase(os.path.abspath(str(r.file_path))))
                except (OSError, ValueError):
                    continue
        try:
            cutoff = time.time() - int(self.config.STORAGE_DAYS) * 86400
        except (TypeError, ValueError):
            cutoff = time.time() - 2 * 86400  # mirror default STORAGE_DAYS

        removed_bytes = 0
        removed_files = 0
        try:
            entries = list(DOWNLOADS_DIR.iterdir())
        except OSError as e:
            logger.warning('startup cleanup: iterdir failed: %s', e)
            return

        for f in entries:
            try:
                if not f.is_file():
                    continue
                ap = os.path.normcase(os.path.abspath(str(f)))
                if ap in pinned:
                    continue
                name = f.name
                ext = f.suffix.lower()
                is_temp = (
                    ext in _ORPHAN_EXT
                    or '.tmp.' in name
                    or '.frag.' in name
                )
                is_orphan_ext = ext not in _KNOWN_OUTPUT_EXT
                if is_temp or is_orphan_ext:
                    try:
                        sz = f.stat().st_size
                        f.unlink()
                        removed_bytes += sz
                        removed_files += 1
                    except OSError:
                        continue
                    continue
                # Output-format file -> retention check
                try:
                    mtime = f.stat().st_mtime
                except FileNotFoundError:
                    continue
                if mtime < cutoff:
                    try:
                        sz = f.stat().st_size
                        f.unlink()
                        removed_bytes += sz
                        removed_files += 1
                    except OSError:
                        continue
            except OSError:
                continue

        if removed_files:
            logger.info(
                'startup cleanup freed %d files, %.2f MB',
                removed_files, removed_bytes / 1024 / 1024)

    async def _router(self, u, c):
        from app.handlers.navigation import router
        await router(self, u, c)